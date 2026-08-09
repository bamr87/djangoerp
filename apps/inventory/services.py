from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.core.audit import log_event
from apps.core.models import DocumentSequence

from .models import StockLevel, StockMove

QTY_PLACES = Decimal('0.001')
COST_PLACES = Decimal('0.0001')
VALUE_PLACES = Decimal('0.01')


class InsufficientStockError(serializers.ValidationError):
    pass


def create_move(*, product, warehouse, move_type, quantity, move_date=None, unit_cost=None,
                source_reference='', created_by=None):
    """
    Create a draft stock move. Nothing counts until complete_move() is called —
    the draft/done split mirrors journal entries, where only posted rows exist
    for reporting purposes.
    """
    if move_type not in dict(StockMove.MOVE_TYPES):
        raise serializers.ValidationError(f'Unknown move type: {move_type}')
    if product.product_type != 'stocked':
        raise serializers.ValidationError(f'{product.sku} is not a stocked product')
    quantity = Decimal(quantity).quantize(QTY_PLACES)
    if quantity <= 0:
        raise serializers.ValidationError('Stock move quantity must be positive')
    return StockMove.objects.create(
        reference=DocumentSequence.next_number('SM'),
        product=product,
        warehouse=warehouse,
        move_type=move_type,
        quantity=quantity,
        unit_cost=Decimal(unit_cost).quantize(COST_PLACES) if unit_cost is not None else Decimal('0'),
        move_date=move_date or timezone.now().date(),
        source_reference=source_reference,
        created_by=created_by,
    )


def complete_move(move, user=None, total_value=None):
    """
    Apply a draft move to the stock ledger under a row lock on its StockLevel.

    Inbound moves arrive at move.unit_cost (or an explicit total_value, used by
    production receipts so the finished-good value equals the consumed component
    value to the cent) and re-weight the moving average. Outbound moves leave at
    the current moving average and cannot drive on-hand negative.

    Returns the completed move with unit_cost/total_value set to the valuation
    actually applied — callers post exactly move.total_value to the GL so the
    stock ledger and the general ledger can never disagree.
    """
    with transaction.atomic():
        move = StockMove.objects.select_for_update().get(pk=move.pk)
        if move.status != 'draft':
            raise serializers.ValidationError(f'Only draft moves can be completed (move is {move.status})')

        level, _ = StockLevel.objects.select_for_update().get_or_create(
            product=move.product, warehouse=move.warehouse
        )

        if move.is_inbound:
            if total_value is not None:
                move.total_value = Decimal(total_value).quantize(VALUE_PLACES)
                move.unit_cost = (move.total_value / move.quantity).quantize(COST_PLACES)
            else:
                move.total_value = (move.quantity * move.unit_cost).quantize(VALUE_PLACES)
            new_quantity = level.quantity_on_hand + move.quantity
            current_value = level.quantity_on_hand * level.average_cost
            level.average_cost = ((current_value + move.total_value) / new_quantity).quantize(COST_PLACES)
            level.quantity_on_hand = new_quantity
        else:
            if level.quantity_on_hand < move.quantity:
                raise InsufficientStockError(
                    f'Insufficient stock for {move.product.sku} in {move.warehouse.code}: '
                    f'on hand {level.quantity_on_hand}, requested {move.quantity}'
                )
            move.unit_cost = level.average_cost
            move.total_value = (move.quantity * move.unit_cost).quantize(VALUE_PLACES)
            level.quantity_on_hand -= move.quantity
            # average_cost is left untouched on issues; residual rounding value on a
            # zeroed level is absorbed by the next receipt's re-weighting.

        level.save()
        move.status = 'done'
        move.save(update_fields=['status', 'unit_cost', 'total_value', 'updated_at'])
        log_event(user, 'stock_move.complete', move, {
            'product': move.product.sku,
            'warehouse': move.warehouse.code,
            'move_type': move.move_type,
            'quantity': str(move.quantity),
            'total_value': str(move.total_value),
        })
        return move


def cancel_move(move, user=None):
    """Cancel a draft move. Done moves are history — correct them with an opposing move."""
    if move.status != 'draft':
        raise serializers.ValidationError('Only draft moves can be cancelled; post an opposing adjustment instead')
    move.status = 'cancelled'
    move.save(update_fields=['status', 'updated_at'])
    log_event(user, 'stock_move.cancel', move)
    return move


def get_on_hand(product, warehouse):
    level = StockLevel.objects.filter(product=product, warehouse=warehouse).first()
    return level.quantity_on_hand if level else Decimal('0')


def rebuild_stock_levels(warehouse=None):
    """
    Reconciliation: rebuild the StockLevel cache from the done-move ledger.
    Replays moves in order, summing signed quantities and the stored valuation
    amounts, so a drifted cache can always be restored to what the ledger says.
    """
    moves = StockMove.objects.filter(status='done').order_by('move_date', 'id')
    if warehouse is not None:
        moves = moves.filter(warehouse=warehouse)

    state = {}
    for move in moves.select_related('product', 'warehouse'):
        key = (move.product_id, move.warehouse_id)
        quantity, value = state.get(key, (Decimal('0'), Decimal('0')))
        if move.is_inbound:
            state[key] = (quantity + move.quantity, value + move.total_value)
        else:
            state[key] = (quantity - move.quantity, value - move.total_value)

    with transaction.atomic():
        levels = StockLevel.objects.select_for_update()
        if warehouse is not None:
            levels = levels.filter(warehouse=warehouse)
        levels = {(lvl.product_id, lvl.warehouse_id): lvl for lvl in levels}
        for key, (quantity, value) in state.items():
            level = levels.pop(key, None) or StockLevel(product_id=key[0], warehouse_id=key[1])
            level.quantity_on_hand = quantity
            level.average_cost = (value / quantity).quantize(COST_PLACES) if quantity else Decimal('0')
            level.save()
        for level in levels.values():
            level.quantity_on_hand = Decimal('0')
            level.average_cost = Decimal('0')
            level.save()
