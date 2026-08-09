from collections import defaultdict

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.core.audit import log_event
from apps.core.models import DocumentSequence
from apps.manufacturing.models import BillOfMaterials, WorkOrder
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine


def convert_run(run, user=None, planned_order_ids=None):
    """
    Convert a completed run's planned orders into draft documents: buy orders
    grouped into one purchase order per supplier (Odoo's vendor merge), make
    orders into one work order each. Drafts still need human confirmation —
    planning proposes, people commit.

    All-or-nothing: missing default suppliers or active BOMs fail the whole
    conversion with the full list of products to fix.
    """
    if run.status != 'completed':
        raise serializers.ValidationError(f'Only completed runs can be converted (run is {run.status})')

    planned = run.planned_orders.filter(status='planned').select_related('product__default_supplier')
    if planned_order_ids is not None:
        planned = planned.filter(id__in=planned_order_ids)
    planned = list(planned)
    if not planned:
        raise serializers.ValidationError('No planned orders left to convert on this run')

    missing_suppliers = sorted({
        order.product.sku for order in planned
        if order.order_type == 'buy' and not order.product.default_supplier_id
    })
    if missing_suppliers:
        raise serializers.ValidationError(
            'Set default_supplier on these products before converting: ' + ', '.join(missing_suppliers)
        )

    make_orders = [order for order in planned if order.order_type == 'make']
    boms = {
        bom.product_id: bom
        for bom in BillOfMaterials.objects.filter(
            is_active=True, product_id__in={order.product_id for order in make_orders}
        )
    }
    missing_boms = sorted({
        order.product.sku for order in make_orders if order.product_id not in boms
    })
    if missing_boms:
        raise serializers.ValidationError(
            'Create an active BOM for these products before converting: ' + ', '.join(missing_boms)
        )

    with transaction.atomic():
        purchase_orders = []
        by_supplier = defaultdict(list)
        for order in planned:
            if order.order_type == 'buy':
                by_supplier[order.product.default_supplier].append(order)

        for supplier, orders in by_supplier.items():
            po = PurchaseOrder.objects.create(
                number=DocumentSequence.next_number('PO'),
                supplier=supplier,
                warehouse=run.warehouse,
                status='draft',
                order_date=timezone.now().date(),
                expected_date=min(order.due_date for order in orders),
                notes=f'Converted from {run.number}',
                source_reference=run.number,
                created_by=user,
            )
            quantities = defaultdict(lambda: None)
            for order in orders:
                line = quantities[order.product_id]
                if line is None:
                    quantities[order.product_id] = PurchaseOrderLine.objects.create(
                        order=po,
                        product=order.product,
                        quantity=order.quantity,
                        unit_price=order.product.standard_cost,
                    )
                else:
                    line.quantity += order.quantity
                    line.save(update_fields=['quantity'])
                order.status = 'converted'
                order.converted_reference = po.number
                order.save(update_fields=['status', 'converted_reference', 'updated_at'])
            purchase_orders.append(po)

        work_orders = []
        for order in make_orders:
            wo = WorkOrder.objects.create(
                number=DocumentSequence.next_number('WO'),
                product=order.product,
                bom=boms[order.product_id],
                warehouse=run.warehouse,
                quantity=order.quantity,
                status='draft',
                due_date=order.due_date,
                source_reference=run.number,
                created_by=user,
            )
            order.status = 'converted'
            order.converted_reference = wo.number
            order.save(update_fields=['status', 'converted_reference', 'updated_at'])
            work_orders.append(wo)

        log_event(user, 'mrp_run.convert', run, {
            'purchase_orders': [po.number for po in purchase_orders],
            'work_orders': [wo.number for wo in work_orders],
        })
        return {'purchase_orders': purchase_orders, 'work_orders': work_orders}


def cancel_planned_order(planned_order, user=None):
    if planned_order.status != 'planned':
        raise serializers.ValidationError('Only planned (unconverted) orders can be cancelled')
    planned_order.status = 'cancelled'
    planned_order.save(update_fields=['status', 'updated_at'])
    log_event(user, 'planned_order.cancel', planned_order)
    return planned_order
