"""
The MRP planning engine: classic regenerative, level-by-level (Orlicky) netting.

    net requirement = demand + safety stock - on hand - scheduled receipts

Demand:    open confirmed sales-order lines, component needs of open work
           orders, safety-stock top-ups, and (during the run) component demand
           exploded from planned make orders.
Supply:    on-hand stock, open confirmed purchase-order lines, open work orders.
Lot size:  lot-for-lot, floored to Product.min_order_qty and rounded up to
           Product.order_multiple.
Timing:    planned orders are due when their demand is due and released
           lead_time_days earlier; releases landing before the run's as_of_date
           are clamped and flagged expedited (capacity-infinite MRP I —
           routings/work centers are roadmap).

Documents converted from a run stay invisible to planning until confirmed
(draft orders are not scheduled receipts), so confirm them before re-running.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from inventory.models import StockLevel
from manufacturing.models import BillOfMaterials, WorkOrder
from products.models import Product
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales.models import SalesOrder, SalesOrderLine

from .models import PlannedOrder

QTY_PLACES = Decimal('0.001')


@shared_task
def run_mrp(run_id):
    """
    Celery task wrapper following reports.generate_report: flip the run to
    running, execute, record completed/failed.
    """
    from .models import MRPRun

    run = None
    try:
        run = MRPRun.objects.get(id=run_id)
        run.status = 'running'
        run.save(update_fields=['status', 'updated_at'])
        with transaction.atomic():
            log = execute_run(run)
        run.status = 'completed'
        run.log = log
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'log', 'completed_at', 'updated_at'])
    except Exception as e:
        if run:
            run.status = 'failed'
            run.error_message = str(e)
            run.save(update_fields=['status', 'error_message', 'updated_at'])
        raise


def compute_low_level_codes(bom_by_product):
    """
    Low-level code per product id: 0 for top-level items, and for every BOM edge
    parent->component, component level >= parent level + 1. Iterates to fixpoint;
    the iteration cap only trips on a cyclic BOM graph, which serializer
    validation is supposed to make impossible.
    """
    levels = defaultdict(int)
    edges = [
        (product_id, line.component_id)
        for product_id, bom in bom_by_product.items()
        for line in bom.lines.all()
    ]
    for _ in range(len(bom_by_product) + 1):
        changed = False
        for parent_id, component_id in edges:
            if levels[component_id] < levels[parent_id] + 1:
                levels[component_id] = levels[parent_id] + 1
                changed = True
        if not changed:
            return levels
    raise ValueError('BOM graph contains a cycle — cannot compute low-level codes')


def lot_size(product, shortfall):
    """Apply the product's lot-sizing policy to a net shortfall."""
    quantity = max(shortfall, product.min_order_qty or Decimal('0'))
    multiple = product.order_multiple or Decimal('0')
    if multiple > 0:
        quantity = (quantity / multiple).to_integral_value(rounding=ROUND_CEILING) * multiple
    return quantity.quantize(QTY_PLACES)


def execute_run(run):
    warehouse = run.warehouse
    as_of = run.as_of_date
    warnings = []

    products = {
        p.id: p for p in Product.objects.filter(is_active=True, product_type='stocked')
        .select_related('default_supplier')
    }
    bom_by_product = {
        bom.product_id: bom
        for bom in BillOfMaterials.objects.filter(is_active=True, product_id__in=products)
        .prefetch_related('lines')
    }
    levels = compute_low_level_codes(bom_by_product)

    on_hand = {
        level.product_id: level.quantity_on_hand
        for level in StockLevel.objects.filter(warehouse=warehouse, product_id__in=products)
    }

    scheduled = defaultdict(Decimal)
    for line in PurchaseOrderLine.objects.filter(
        order__warehouse=warehouse, order__status__in=PurchaseOrder.OPEN_STATUSES,
        product_id__in=products,
    ).select_related('order'):
        if line.open_quantity > 0:
            scheduled[line.product_id] += line.open_quantity

    open_work_orders = list(
        WorkOrder.objects.filter(warehouse=warehouse, status__in=WorkOrder.OPEN_STATUSES)
        .select_related('bom').prefetch_related('bom__lines')
    )
    for wo in open_work_orders:
        scheduled[wo.product_id] += wo.quantity

    # Demand elements: {product_id: [{qty, due, ref, parent}]}
    demand = defaultdict(list)
    demand_count = 0
    for line in SalesOrderLine.objects.filter(
        order__warehouse=warehouse, order__status__in=SalesOrder.OPEN_STATUSES,
        product_id__in=products,
    ).select_related('order', 'product'):
        open_qty = line.open_quantity
        if open_qty > 0:
            demand[line.product_id].append({
                'qty': open_qty,
                'due': line.order.requested_date or line.order.order_date,
                'ref': f'{line.order.number} ({line.product.sku})',
                'parent': None,
            })
            demand_count += 1

    # Components an already-open work order will consume when it completes.
    for wo in open_work_orders:
        factor = wo.quantity / wo.bom.quantity
        for line in wo.bom.lines.all():
            demand[line.component_id].append({
                'qty': (line.quantity * factor).quantize(QTY_PLACES),
                'due': wo.due_date or as_of,
                'ref': f'{wo.number} components',
                'parent': None,
            })
            demand_count += 1

    planned_count = 0
    expedited_count = 0
    # Level-by-level: parents are netted before the components their planned
    # make orders explode into, so exploded demand always lands on a product
    # not yet processed.
    for product_id in sorted(products, key=lambda pid: (levels.get(pid, 0), pid)):
        product = products[product_id]
        elements = demand.get(product_id, [])

        available = on_hand.get(product_id, Decimal('0')) + scheduled.get(product_id, Decimal('0')) \
            - (product.safety_stock or Decimal('0'))
        if available < 0:
            elements = elements + [{
                'qty': -available, 'due': as_of, 'ref': 'safety stock', 'parent': None,
            }]
            available = Decimal('0')
        if not elements:
            continue

        for element in sorted(elements, key=lambda e: e['due']):
            covered = min(available, element['qty'])
            available -= covered
            shortfall = element['qty'] - covered
            if shortfall <= 0:
                continue

            quantity = lot_size(product, shortfall)
            available += quantity - shortfall
            release = element['due'] - timedelta(days=product.lead_time_days)
            expedited = release < as_of
            if expedited:
                release = as_of
                expedited_count += 1

            planned = PlannedOrder.objects.create(
                run=run,
                product=product,
                order_type=product.procurement_type,
                quantity=quantity,
                due_date=element['due'],
                release_date=release,
                expedited=expedited,
                demand_reference=element['ref'],
                parent=element['parent'],
            )
            planned_count += 1

            if product.procurement_type == 'make':
                bom = bom_by_product.get(product_id)
                if bom is None:
                    warnings.append(f'{product.sku} is make but has no active BOM — cannot explode')
                    continue
                factor = quantity / bom.quantity
                for line in bom.lines.all():
                    demand[line.component_id].append({
                        'qty': (line.quantity * factor).quantize(QTY_PLACES),
                        'due': release,
                        'ref': f'make {product.sku} ({element["ref"]})',
                        'parent': planned,
                    })
                    demand_count += 1
            elif product.procurement_type == 'buy' and not product.default_supplier_id:
                warnings.append(f'{product.sku} is buy but has no default_supplier — conversion will fail')

    return {
        'warehouse': warehouse.code,
        'as_of_date': str(as_of),
        'demand_elements': demand_count,
        'planned_orders': planned_count,
        'expedited': expedited_count,
        'warnings': warnings,
    }
