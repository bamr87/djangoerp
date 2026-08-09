from datetime import date
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from core.demo import build_demo_company
from core.models import DocumentSequence
from inventory import services as inventory_services

from . import services
from .models import WorkOrder


class BOMTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)

    def test_component_cannot_be_its_own_product(self):
        widget = self.demo['products']['widget']
        response = self.client.post(reverse('billofmaterials-list'), {
            'product': widget.id, 'quantity': '1', 'is_active': False,
            'lines': [{'component': widget.id, 'quantity': '1'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_circular_bom_rejected(self):
        # WIDGET already includes FRAME; a BOM making FRAME out of WIDGET loops.
        frame = self.demo['products']['frame']
        widget = self.demo['products']['widget']
        response = self.client.post(reverse('billofmaterials-list'), {
            'product': frame.id, 'quantity': '1',
            'lines': [{'component': widget.id, 'quantity': '1'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Circular', str(response.data))

    def test_second_active_bom_rejected(self):
        widget = self.demo['products']['widget']
        frame = self.demo['products']['frame']
        response = self.client.post(reverse('billofmaterials-list'), {
            'product': widget.id, 'quantity': '1',
            'lines': [{'component': frame.id, 'quantity': '2'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('active BOM', str(response.data))


class WorkOrderFlowTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)
        self.warehouse = self.demo['warehouse']

    def stock(self, product, quantity, unit_cost):
        move = inventory_services.create_move(
            product=product, warehouse=self.warehouse, move_type='adjustment_in',
            quantity=Decimal(quantity), unit_cost=Decimal(unit_cost), created_by=self.user,
        )
        inventory_services.complete_move(move, user=self.user)

    def make_wo(self, quantity='5'):
        return WorkOrder.objects.create(
            number=DocumentSequence.next_number('WO'), product=self.demo['products']['widget'],
            bom=self.demo['bom'], warehouse=self.warehouse, quantity=Decimal(quantity),
            due_date=date(2026, 6, 9), created_by=self.user,
        )

    def test_complete_requires_component_stock(self):
        wo = self.make_wo('5')
        services.confirm_work_order(wo, user=self.user)
        with self.assertRaises(Exception):
            services.complete_work_order(wo, user=self.user)
        wo.refresh_from_db()
        self.assertEqual(wo.status, 'confirmed')

    def test_complete_consumes_components_and_rolls_up_cost(self):
        self.stock(self.demo['products']['frame'], '10', '40')
        self.stock(self.demo['products']['wheel'], '20', '15')
        wo = self.make_wo('5')
        services.confirm_work_order(wo, user=self.user)
        services.start_work_order(wo, user=self.user)
        result = services.complete_work_order(wo, user=self.user, completion_date=date(2026, 6, 9))
        wo.refresh_from_db()
        self.assertEqual(wo.status, 'completed')
        widget = self.demo['products']['widget']
        self.assertEqual(inventory_services.get_on_hand(widget, self.warehouse), Decimal('5.000'))
        # 5 frames @ 40 + 10 wheels @ 15 = 350 -> widget unit cost 70.
        self.assertEqual(result['receipt_move'].total_value, Decimal('350.00'))
        self.assertEqual(result['receipt_move'].unit_cost, Decimal('70.0000'))
        entry = result['journal_entry']
        self.assertEqual(sum(line.debit for line in entry.lines.all()),
                         sum(line.credit for line in entry.lines.all()))

    def test_bom_must_match_product_via_api(self):
        response = self.client.post(reverse('workorder-list'), {
            'product': self.demo['products']['frame'].id, 'bom': self.demo['bom'].id,
            'warehouse': self.warehouse.id, 'quantity': '1',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
