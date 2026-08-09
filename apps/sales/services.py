from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.core.audit import log_event
from apps.core.models import DocumentSequence
from apps.inventory import services as inventory_services
from apps.journal.services import post_entry

VALUE_PLACES = Decimal('0.01')


def confirm_sales_order(order, user=None):
    if order.status != 'draft':
        raise serializers.ValidationError(f'Only draft orders can be confirmed (order is {order.status})')
    if not order.lines.exists():
        raise serializers.ValidationError('Cannot confirm a sales order with no lines')
    order.status = 'confirmed'
    order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'sales_order.confirm', order, {'customer': order.customer.code})
    return order


def ship_sales_order(order, user=None, shipments=None, ship_date=None):
    """
    Ship goods against a confirmed order (fully by default, or the given
    {line_id: quantity} subset), as one shipment (SHP) event:

    - one done stock move per line, issued at the warehouse's moving-average cost;
    - one balanced journal entry: Dr COGS / Cr inventory (per product category)
      at exactly the stock-move values.

    Revenue is not recognised here — that happens when the invoice posts
    (AR/revenue), keeping the shipment and billing events separately auditable.
    """
    if order.status not in order.OPEN_STATUSES:
        raise serializers.ValidationError(f'Only confirmed orders can ship (order is {order.status})')

    ship_date = ship_date or timezone.now().date()
    lines = list(order.lines.select_related('product__category'))

    if shipments is None:
        to_ship = [(line, line.open_quantity) for line in lines if line.open_quantity > 0]
    else:
        by_id = {line.id: line for line in lines}
        to_ship = []
        for line_id, quantity in shipments.items():
            line = by_id.get(int(line_id))
            if line is None:
                raise serializers.ValidationError(f'Line {line_id} does not belong to {order.number}')
            to_ship.append((line, Decimal(quantity)))

    if not to_ship:
        raise serializers.ValidationError('Nothing left to ship on this order')

    for line, quantity in to_ship:
        if quantity <= 0:
            raise serializers.ValidationError(f'Shipment quantity for {line.product.sku} must be positive')
        if quantity > line.open_quantity:
            raise serializers.ValidationError(
                f'Cannot ship {quantity} of {line.product.sku}: only {line.open_quantity} open'
            )
        category = line.product.category if line.product.category_id else None
        if line.product.product_type == 'stocked' and not (
            category and category.inventory_account_id and category.cogs_account_id
        ):
            raise serializers.ValidationError(
                f'{line.product.sku} needs category inventory_account and cogs_account configured before shipping'
            )

    with transaction.atomic():
        shipment_number = DocumentSequence.next_number('SHP')
        moves = []
        cogs_by_account = {}
        credit_by_account = {}
        for line, quantity in to_ship:
            move = inventory_services.create_move(
                product=line.product,
                warehouse=order.warehouse,
                move_type='shipment',
                quantity=quantity,
                move_date=ship_date,
                source_reference=shipment_number,
                created_by=user,
            )
            move = inventory_services.complete_move(move, user=user)
            moves.append(move)
            category = line.product.category
            cogs_by_account[category.cogs_account] = \
                cogs_by_account.get(category.cogs_account, Decimal('0.00')) + move.total_value
            credit_by_account[category.inventory_account] = \
                credit_by_account.get(category.inventory_account, Decimal('0.00')) + move.total_value
            line.shipped_quantity += quantity
            line.save(update_fields=['shipped_quantity'])

        journal_entry = post_entry(
            date=ship_date,
            description=f'Shipment {shipment_number} for {order.number} ({order.customer.name})',
            lines=[
                {'account': account, 'debit': amount, 'reference': shipment_number}
                for account, amount in cogs_by_account.items()
            ] + [
                {'account': account, 'credit': amount, 'reference': shipment_number}
                for account, amount in credit_by_account.items()
            ],
            created_by=user,
            source_reference=f'shipment:{shipment_number}',
        )

        # Roll the status up from the line objects mutated above, not from
        # order.lines.all(): the viewset's queryset prefetches `lines`, so on an
        # order that arrived via get_object() that manager call is served from the
        # prefetch cache and still reports the pre-shipment quantities.
        order.status = 'shipped' if all(line.open_quantity <= 0 for line in lines) \
            else 'partially_shipped'
        order.save(update_fields=['status', 'updated_at'])
        log_event(user, 'sales_order.ship', order, {
            'shipment': shipment_number,
            'journal_entry': journal_entry.entry_number,
            'moves': [move.reference for move in moves],
        })
        return {'shipment_number': shipment_number, 'moves': moves, 'journal_entry': journal_entry, 'order': order}


def create_invoice_from_order(order, user=None, due_date=None):
    """
    Build a draft customer invoice from the order's shipped quantities. The
    invoice posts revenue when invoices.services.post_invoice runs — creating
    it here only prepares the billing document and links it for traceability.
    """
    from apps.invoices.models import Invoice, InvoiceLineItem

    if order.status != 'shipped':
        raise serializers.ValidationError(f'Only fully shipped orders can be invoiced (order is {order.status})')
    if Invoice.objects.filter(sales_order=order).exists():
        raise serializers.ValidationError(f'{order.number} already has an invoice')

    with transaction.atomic():
        total = Decimal('0.00')
        lines = []
        for line in order.lines.select_related('product'):
            amount = (line.shipped_quantity * line.unit_price).quantize(VALUE_PLACES)
            total += amount
            lines.append((line, amount))

        invoice = Invoice.objects.create(
            invoice_number=DocumentSequence.next_number('INV'),
            customer=order.customer.name,
            partner=order.customer,
            sales_order=order,
            due_date=due_date or order.requested_date or timezone.now().date(),
            status='draft',
            total=total,
            created_by=user,
        )
        for line, amount in lines:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description=line.description or line.product.name,
                quantity=line.shipped_quantity,
                unit_price=line.unit_price,
                total=amount,
                product=line.product,
            )
        order.status = 'invoiced'
        order.save(update_fields=['status', 'updated_at'])
        log_event(user, 'sales_order.invoice', order, {'invoice': invoice.invoice_number})
        return invoice


def cancel_sales_order(order, user=None):
    if order.status not in ('draft', 'confirmed'):
        raise serializers.ValidationError('Only draft or confirmed orders with no shipments can be cancelled')
    if order.lines.filter(shipped_quantity__gt=0).exists():
        raise serializers.ValidationError('Orders with shipments cannot be cancelled — process a return instead')
    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'sales_order.cancel', order)
    return order
