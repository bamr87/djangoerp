from datetime import date
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.test_helpers import create_user
from apps.core.demo import build_demo_company
from apps.core.models import DocumentSequence
from apps.inventory import services as inventory_services
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
from apps.purchasing.services import confirm_purchase_order
from apps.sales.models import SalesOrder, SalesOrderLine
from apps.sales.services import confirm_sales_order

from . import services
from .engine import run_mrp
from .models import MRPRun


class MRPEngineTests(APITestCase):
    def setUp(self):
        self.user = create_user('planner', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)
        self.warehouse = self.demo['warehouse']
        self.widget = self.demo['products']['widget']
        self.frame = self.demo['products']['frame']
        self.wheel = self.demo['products']['wheel']
        self.day = date(2026, 6, 1)
        # Silence safety-stock noise for the netting-focused tests.
        self.frame.safety_stock = Decimal('0')
        self.frame.save()
        self.wheel.safety_stock = Decimal('0')
        self.wheel.save()

    def stock(self, product, quantity, unit_cost='10'):
        move = inventory_services.create_move(
            product=product, warehouse=self.warehouse, move_type='adjustment_in',
            quantity=Decimal(quantity), unit_cost=Decimal(unit_cost), created_by=self.user,
        )
        inventory_services.complete_move(move, user=self.user)

    def demand(self, quantity='5', requested=date(2026, 6, 11)):
        so = SalesOrder.objects.create(
            number=DocumentSequence.next_number('SO'), customer=self.demo['customer'],
            warehouse=self.warehouse, order_date=self.day, requested_date=requested,
            created_by=self.user,
        )
        SalesOrderLine.objects.create(order=so, product=self.widget,
                                      quantity=Decimal(quantity), unit_price=Decimal('250'))
        confirm_sales_order(so, user=self.user)
        return so

    def plan(self):
        run = MRPRun.objects.create(
            number=DocumentSequence.next_number('MRP'), warehouse=self.warehouse,
            as_of_date=self.day, created_by=self.user,
        )
        run_mrp(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed', run.error_message)
        return run

    def test_on_hand_stock_nets_demand(self):
        self.stock(self.widget, '2', '70')
        self.demand('5')
        run = self.plan()
        widget_plan = run.planned_orders.get(product=self.widget)
        self.assertEqual(widget_plan.quantity, Decimal('3.000'))

    def test_open_purchase_orders_count_as_supply(self):
        self.demand('5')
        po = PurchaseOrder.objects.create(
            number=DocumentSequence.next_number('PO'), supplier=self.demo['supplier'],
            warehouse=self.warehouse, order_date=self.day, created_by=self.user,
        )
        PurchaseOrderLine.objects.create(order=po, product=self.frame,
                                         quantity=Decimal('5'), unit_price=Decimal('40'))
        confirm_purchase_order(po, user=self.user)
        run = self.plan()
        # Frame need (5 for the make order) is fully covered by the open PO.
        self.assertFalse(run.planned_orders.filter(product=self.frame).exists())
        self.assertTrue(run.planned_orders.filter(product=self.wheel).exists())

    def test_lot_sizing_applies_min_and_multiple(self):
        self.wheel.min_order_qty = Decimal('25')
        self.wheel.order_multiple = Decimal('10')
        self.wheel.save()
        self.demand('5')  # raw wheel need: 10
        run = self.plan()
        wheel_plan = run.planned_orders.get(product=self.wheel)
        # max(10, 25) rounded up to a multiple of 10 -> 30.
        self.assertEqual(wheel_plan.quantity, Decimal('30.000'))

    def test_fully_stocked_plan_is_empty(self):
        self.stock(self.widget, '5', '70')
        self.demand('5')
        run = self.plan()
        self.assertEqual(run.planned_orders.count(), 0)

    def test_conversion_requires_default_supplier(self):
        self.frame.default_supplier = None
        self.frame.save()
        self.demand('5')
        run = self.plan()
        with self.assertRaises(Exception) as ctx:
            services.convert_run(run, user=self.user)
        self.assertIn('FRAME', str(ctx.exception))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
    def test_api_run_and_convert(self):
        self.demand('5')
        response = self.client.post(reverse('mrprun-list'), {
            'warehouse': self.warehouse.id, 'as_of_date': str(self.day),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        run_id = response.data['id']
        detail = self.client.get(reverse('mrprun-detail', args=[run_id]))
        self.assertEqual(detail.data['status'], 'completed')
        self.assertEqual(detail.data['planned_order_count'], 3)

        convert = self.client.post(reverse('mrprun-convert', args=[run_id]), {}, format='json')
        self.assertEqual(convert.status_code, status.HTTP_200_OK)
        self.assertEqual(len(convert.data['purchase_orders']), 1)
        self.assertEqual(len(convert.data['work_orders']), 1)

    def test_viewer_cannot_plan(self):
        viewer = create_user('viewer', role='viewer')
        self.client.force_authenticate(user=viewer)
        response = self.client.post(reverse('mrprun-list'), {
            'warehouse': self.warehouse.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
