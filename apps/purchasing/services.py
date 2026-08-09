from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.core.audit import log_event
from apps.core.models import DocumentSequence
from apps.inventory import services as inventory_services
from apps.journal.services import post_entry

VALUE_PLACES = Decimal('0.01')


def confirm_purchase_order(order, user=None):
    if order.status != 'draft':
        raise serializers.ValidationError(f'Only draft orders can be confirmed (order is {order.status})')
    if not order.lines.exists():
        raise serializers.ValidationError('Cannot confirm a purchase order with no lines')
    order.status = 'confirmed'
    order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'purchase_order.confirm', order, {'supplier': order.supplier.code})
    return order


def receive_purchase_order(order, user=None, receipts=None, receipt_date=None):
    """
    Receive goods against a confirmed order (fully by default, or the given
    {line_id: quantity} subset), as one goods receipt (GRN) event:

    - one done stock move per line, valued at the PO unit price;
    - one balanced journal entry: Dr inventory (per product category) at exactly
      the stock-move values, Cr the supplier's payable account with the accrual
      (receipt accrues AP; vendor-bill three-way matching is on the roadmap).

    Idempotency: each receipt claims a fresh GRN number, and the journal posting
    is keyed to it via source_reference.
    """
    if order.status not in order.OPEN_STATUSES:
        raise serializers.ValidationError(f'Only confirmed orders can receive goods (order is {order.status})')
    if not order.supplier.payable_account_id:
        raise serializers.ValidationError(
            f'Supplier {order.supplier.code} has no payable_account configured — set it before receiving'
        )

    receipt_date = receipt_date or timezone.now().date()
    lines = list(order.lines.select_related('product__category'))

    if receipts is None:
        to_receive = [(line, line.open_quantity) for line in lines if line.open_quantity > 0]
    else:
        by_id = {line.id: line for line in lines}
        to_receive = []
        for line_id, quantity in receipts.items():
            line = by_id.get(int(line_id))
            if line is None:
                raise serializers.ValidationError(f'Line {line_id} does not belong to {order.number}')
            to_receive.append((line, Decimal(quantity)))

    if not to_receive:
        raise serializers.ValidationError('Nothing left to receive on this order')

    for line, quantity in to_receive:
        if quantity <= 0:
            raise serializers.ValidationError(f'Receipt quantity for {line.product.sku} must be positive')
        if quantity > line.open_quantity:
            raise serializers.ValidationError(
                f'Cannot receive {quantity} of {line.product.sku}: only {line.open_quantity} open'
            )
        if line.product.product_type == 'stocked' and not (
            line.product.category_id and line.product.category.inventory_account_id
        ):
            raise serializers.ValidationError(
                f'{line.product.sku} has no category inventory_account configured — set it before receiving'
            )

    with transaction.atomic():
        grn_number = DocumentSequence.next_number('GRN')
        moves = []
        debit_by_account = {}
        for line, quantity in to_receive:
            move = inventory_services.create_move(
                product=line.product,
                warehouse=order.warehouse,
                move_type='receipt',
                quantity=quantity,
                move_date=receipt_date,
                unit_cost=line.unit_price,
                source_reference=grn_number,
                created_by=user,
            )
            move = inventory_services.complete_move(move, user=user)
            moves.append(move)
            account = line.product.category.inventory_account
            debit_by_account[account] = debit_by_account.get(account, Decimal('0.00')) + move.total_value
            line.received_quantity += quantity
            line.save(update_fields=['received_quantity'])

        total = sum(debit_by_account.values(), Decimal('0.00'))
        journal_entry = post_entry(
            date=receipt_date,
            description=f'Goods receipt {grn_number} for {order.number} ({order.supplier.name})',
            lines=[
                {'account': account, 'debit': amount, 'reference': grn_number}
                for account, amount in debit_by_account.items()
            ] + [
                {'account': order.supplier.payable_account, 'credit': total, 'reference': grn_number},
            ],
            created_by=user,
            source_reference=f'grn:{grn_number}',
        )

        # Roll the status up from the line objects mutated above, not from
        # order.lines.all(): the viewset's queryset prefetches `lines`, so on an
        # order that arrived via get_object() that manager call is served from the
        # prefetch cache and still reports the pre-receipt quantities.
        order.status = 'received' if all(line.open_quantity <= 0 for line in lines) \
            else 'partially_received'
        order.save(update_fields=['status', 'updated_at'])
        log_event(user, 'purchase_order.receive', order, {
            'grn': grn_number,
            'journal_entry': journal_entry.entry_number,
            'moves': [move.reference for move in moves],
        })
        return {'grn_number': grn_number, 'moves': moves, 'journal_entry': journal_entry, 'order': order}


def cancel_purchase_order(order, user=None):
    if order.status not in ('draft', 'confirmed'):
        raise serializers.ValidationError('Only draft or confirmed orders with no receipts can be cancelled')
    if order.lines.filter(received_quantity__gt=0).exists():
        raise serializers.ValidationError('Orders with receipts cannot be cancelled — return the goods instead')
    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'purchase_order.cancel', order)
    return order
