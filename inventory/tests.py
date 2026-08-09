from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from products.models import Product, UnitOfMeasure

from . import services
from .models import StockLevel, Warehouse


class StockLedgerTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        uom = UnitOfMeasure.objects.create(code='EA', name='Each')
        self.product = Product.objects.create(sku='PART', name='Part', uom=uom)
        self.warehouse = Warehouse.objects.create(code='WH1', name='Main')

    def receive(self, quantity, unit_cost):
        move = services.create_move(
            product=self.product, warehouse=self.warehouse, move_type='receipt',
            quantity=Decimal(quantity), unit_cost=Decimal(unit_cost), created_by=self.user,
        )
        return services.complete_move(move, user=self.user)

    def test_moving_average_reweights_on_receipts(self):
        self.receive('10', '10')
        self.receive('10', '20')
        level = StockLevel.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(level.quantity_on_hand, Decimal('20.000'))
        self.assertEqual(level.average_cost, Decimal('15.0000'))

    def test_issues_leave_at_average_and_average_holds(self):
        self.receive('10', '10')
        self.receive('10', '20')
        issue = services.create_move(
            product=self.product, warehouse=self.warehouse, move_type='shipment',
            quantity=Decimal('5'), created_by=self.user,
        )
        issue = services.complete_move(issue, user=self.user)
        self.assertEqual(issue.unit_cost, Decimal('15.0000'))
        self.assertEqual(issue.total_value, Decimal('75.00'))
        level = StockLevel.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(level.quantity_on_hand, Decimal('15.000'))
        self.assertEqual(level.average_cost, Decimal('15.0000'))

    def test_negative_stock_is_blocked(self):
        self.receive('5', '10')
        issue = services.create_move(
            product=self.product, warehouse=self.warehouse, move_type='shipment',
            quantity=Decimal('6'), created_by=self.user,
        )
        with self.assertRaises(services.InsufficientStockError):
            services.complete_move(issue, user=self.user)
        level = StockLevel.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(level.quantity_on_hand, Decimal('5.000'))

    def test_only_done_moves_count(self):
        services.create_move(
            product=self.product, warehouse=self.warehouse, move_type='receipt',
            quantity=Decimal('99'), unit_cost=Decimal('1'), created_by=self.user,
        )
        self.assertEqual(services.get_on_hand(self.product, self.warehouse), Decimal('0'))

    def test_completed_moves_are_immutable_history(self):
        move = self.receive('5', '10')
        with self.assertRaises(Exception):
            services.complete_move(move, user=self.user)
        response = self.client.patch(
            reverse('stockmove-detail', args=[move.id]), {'quantity': '50'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rebuild_matches_ledger(self):
        self.receive('10', '10')
        self.receive('10', '20')
        issue = services.create_move(
            product=self.product, warehouse=self.warehouse, move_type='adjustment_out',
            quantity=Decimal('4'), created_by=self.user,
        )
        services.complete_move(issue, user=self.user)
        StockLevel.objects.update(quantity_on_hand=0, average_cost=0)  # simulate drift
        services.rebuild_stock_levels(warehouse=self.warehouse)
        level = StockLevel.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(level.quantity_on_hand, Decimal('16.000'))
        self.assertEqual(level.average_cost, Decimal('15.0000'))

    def test_api_creates_draft_and_complete_action_applies_it(self):
        response = self.client.post(reverse('stockmove-list'), {
            'product': self.product.id, 'warehouse': self.warehouse.id,
            'move_type': 'adjustment_in', 'quantity': '7', 'unit_cost': '2.5',
            'move_date': '2026-06-01',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'draft')
        move_id = response.data['id']
        response = self.client.post(reverse('stockmove-complete', args=[move_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(services.get_on_hand(self.product, self.warehouse), Decimal('7.000'))
