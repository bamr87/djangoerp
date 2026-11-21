from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from core.audit import log_event
from inventory import services as inventory_services
from inventory.models import StockLevel
from journal.services import post_entry

QTY_PLACES = Decimal('0.001')
VALUE_PLACES = Decimal('0.01')


def component_requirements(work_order):
    """Component quantities needed to complete the work order, scaled off the BOM."""
    bom = work_order.bom
    factor = work_order.quantity / bom.quantity
    return [
        (line.component, (line.quantity * factor).quantize(QTY_PLACES))
        for line in bom.lines.select_related('component__category')
    ]


def confirm_work_order(work_order, user=None):
    if work_order.status != 'draft':
        raise serializers.ValidationError(f'Only draft work orders can be confirmed (order is {work_order.status})')
    if not work_order.bom.lines.exists():
        raise serializers.ValidationError('Cannot confirm a work order whose BOM has no lines')
    work_order.status = 'confirmed'
    work_order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'work_order.confirm', work_order, {'product': work_order.product.sku})
    return work_order


def start_work_order(work_order, user=None):
    if work_order.status != 'confirmed':
        raise serializers.ValidationError(f'Only confirmed work orders can start (order is {work_order.status})')
    work_order.status = 'in_progress'
    work_order.started_at = timezone.now()
    work_order.save(update_fields=['status', 'started_at', 'updated_at'])
    log_event(user, 'work_order.start', work_order)
    return work_order


def clean_conversion_costs(conversion_costs):
    """
    Validate and merge the conversion costs a work-order completion absorbs.

    ``conversion_costs`` is an iterable of ``{'account': Account, 'amount': Decimal}``
    dicts (an optional ``label`` is kept for the audit trail). Amounts are quantized
    to the cent and must not be negative; zero amounts are dropped. Returns
    ``(by_account, total)``: the credit per absorption account and their sum.
    """
    by_account = {}
    for cost in conversion_costs or ():
        account = cost.get('account')
        if account is None:
            raise serializers.ValidationError('Every conversion cost needs the account it is absorbed from')
        amount = Decimal(cost.get('amount') or 0).quantize(VALUE_PLACES)
        if amount < 0:
            raise serializers.ValidationError('Conversion costs cannot be negative')
        if amount:
            by_account[account] = by_account.get(account, Decimal('0.00')) + amount
    return by_account, sum(by_account.values(), Decimal('0.00'))


def complete_work_order(work_order, user=None, completion_date=None, conversion_costs=None):
    """
    Complete the whole order in one transaction:

    - issue every BOM component from the warehouse at moving-average cost;
    - receive the finished good, valued at exactly the total component value
      consumed plus any absorbed conversion costs, which re-weights the
      finished good's moving average;
    - post Dr finished-good inventory for that value / Cr component inventory
      for the components / Cr each absorption account for its conversion cost.

    ``conversion_costs`` (optional, see ``clean_conversion_costs``) is absorption
    costing: the labor and overhead a production line spent on the order move
    into inventory value, credited to the accounts that absorb them (normally a
    contra-expense such as "production costs absorbed", so actual costs posted
    elsewhere net to the under/over-absorbed variance). Without it the receipt is
    valued at component cost alone, as before.

    Availability is pre-checked across all components so a partially consumed
    order can never be left behind by a mid-flight shortage.
    """
    if work_order.status not in work_order.OPEN_STATUSES:
        raise serializers.ValidationError(
            f'Only confirmed or in-progress work orders can complete (order is {work_order.status})'
        )
    completion_date = completion_date or timezone.now().date()
    conversion_by_account, conversion_total = clean_conversion_costs(conversion_costs)
    requirements = component_requirements(work_order)
    if not requirements:
        raise serializers.ValidationError('Work order BOM has no lines')

    product = work_order.product
    if not (product.category_id and product.category.inventory_account_id):
        raise serializers.ValidationError(
            f'{product.sku} has no category inventory_account configured — set it before completing'
        )

    shortages = []
    for component, required in requirements:
        if not (component.category_id and component.category.inventory_account_id):
            raise serializers.ValidationError(
                f'Component {component.sku} has no category inventory_account configured'
            )
        on_hand = inventory_services.get_on_hand(component, work_order.warehouse)
        if on_hand < required:
            shortages.append(f'{component.sku} (need {required}, on hand {on_hand})')
    if shortages:
        raise serializers.ValidationError('Insufficient component stock: ' + ', '.join(shortages))

    with transaction.atomic():
        issue_moves = []
        credit_by_account = {}
        total_value = Decimal('0.00')
        for component, required in requirements:
            move = inventory_services.create_move(
                product=component,
                warehouse=work_order.warehouse,
                move_type='production_issue',
                quantity=required,
                move_date=completion_date,
                source_reference=work_order.number,
                created_by=user,
            )
            move = inventory_services.complete_move(move, user=user)
            issue_moves.append(move)
            account = component.category.inventory_account
            credit_by_account[account] = credit_by_account.get(account, Decimal('0.00')) + move.total_value
            total_value += move.total_value

        receipt = inventory_services.create_move(
            product=product,
            warehouse=work_order.warehouse,
            move_type='production_receipt',
            quantity=work_order.quantity,
            move_date=completion_date,
            source_reference=work_order.number,
            created_by=user,
        )
        receipt = inventory_services.complete_move(
            receipt, user=user, total_value=total_value + conversion_total,
        )

        journal_entry = post_entry(
            date=completion_date,
            description=f'Production {work_order.number}: {work_order.quantity} x {product.sku}',
            lines=[
                {'account': product.category.inventory_account, 'debit': receipt.total_value,
                 'reference': work_order.number},
            ] + [
                {'account': account, 'credit': amount, 'reference': work_order.number}
                for account, amount in credit_by_account.items()
            ] + [
                {'account': account, 'credit': amount, 'reference': work_order.number}
                for account, amount in conversion_by_account.items()
            ],
            created_by=user,
            source_reference=f'wo:{work_order.number}:complete',
        )

        work_order.status = 'completed'
        work_order.completed_at = timezone.now()
        work_order.save(update_fields=['status', 'completed_at', 'updated_at'])
        log_event(user, 'work_order.complete', work_order, {
            'journal_entry': journal_entry.entry_number,
            'consumed_value': str(total_value),
            'conversion_value': str(conversion_total),
            'moves': [move.reference for move in issue_moves + [receipt]],
        })
        return {
            'work_order': work_order,
            'issue_moves': issue_moves,
            'receipt_move': receipt,
            'journal_entry': journal_entry,
        }


def cancel_work_order(work_order, user=None):
    if work_order.status not in ('draft', 'confirmed'):
        raise serializers.ValidationError('Only draft or confirmed work orders can be cancelled')
    work_order.status = 'cancelled'
    work_order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'work_order.cancel', work_order)
    return work_order


def component_availability(work_order):
    """Availability check used by the API to preview whether completion can run."""
    rows = []
    for component, required in component_requirements(work_order):
        level = StockLevel.objects.filter(product=component, warehouse=work_order.warehouse).first()
        on_hand = level.quantity_on_hand if level else Decimal('0')
        rows.append({
            'component': component.sku,
            'required': required,
            'on_hand': on_hand,
            'sufficient': on_hand >= required,
        })
    return rows
