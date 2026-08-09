from datetime import date
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.test_helpers import create_user
from apps.core.demo import build_demo_company
from apps.core.models import DocumentSequence
from apps.inventory import services as inventory_services
from apps.invoices.models import Invoice

from . import services
from .models import SalesOrder, SalesOrderLine


class SalesOrderFlowTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)
        self.widget = self.demo['products']['widget']
        self.warehouse = self.demo['warehouse']

    def stock_widgets(self, quantity='5', unit_cost='70'):
        move = inventory_services.create_move(
            product=self.widget, warehouse=self.warehouse, move_type='adjustment_in',
            quantity=Decimal(quantity), unit_cost=Decimal(unit_cost), created_by=self.user,
        )
        inventory_services.complete_move(move, user=self.user)

    def make_so(self, quantity='5'):
        so = SalesOrder.objects.create(
            number=DocumentSequence.next_number('SO'), customer=self.demo['customer'],
            warehouse=self.warehouse, order_date=date(2026, 6, 1), created_by=self.user,
        )
        SalesOrderLine.objects.create(
            order=so, product=self.widget, quantity=Decimal(quantity), unit_price=Decimal('250'),
        )
        return so

    def test_api_create_assigns_number(self):
        response = self.client.post(reverse('salesorder-list'), {
            'customer': self.demo['customer'].id, 'warehouse': self.warehouse.id,
            'order_date': '2026-06-01',
            'lines': [{'product': self.widget.id, 'quantity': '5', 'unit_price': '250'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['number'].startswith('SO-'))

    def test_supplier_only_partner_rejected(self):
        response = self.client.post(reverse('salesorder-list'), {
            'customer': self.demo['supplier'].id, 'warehouse': self.warehouse.id,
            'order_date': '2026-06-01',
            'lines': [{'product': self.widget.id, 'quantity': '1', 'unit_price': '1'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_ship_without_stock(self):
        so = self.make_so('5')
        services.confirm_sales_order(so, user=self.user)
        with self.assertRaises(Exception):
            services.ship_sales_order(so, user=self.user)
        so.refresh_from_db()
        self.assertEqual(so.status, 'confirmed')

    def test_ship_posts_cogs_at_moving_average(self):
        self.stock_widgets('5', '70')
        so = self.make_so('5')
        services.confirm_sales_order(so, user=self.user)
        result = services.ship_sales_order(so, user=self.user, ship_date=date(2026, 6, 10))
        so.refresh_from_db()
        self.assertEqual(so.status, 'shipped')
        self.assertEqual(inventory_services.get_on_hand(self.widget, self.warehouse), Decimal('0'))
        entry = result['journal_entry']
        cogs_line = entry.lines.get(account=self.demo['accounts']['cogs'])
        self.assertEqual(cogs_line.debit, Decimal('350.00'))

    def test_invoice_from_order_and_duplicate_blocked(self):
        self.stock_widgets('5', '70')
        so = self.make_so('5')
        services.confirm_sales_order(so, user=self.user)
        services.ship_sales_order(so, user=self.user)
        invoice = services.create_invoice_from_order(so, user=self.user)
        self.assertEqual(invoice.total, Decimal('1250.00'))
        self.assertEqual(invoice.partner, self.demo['customer'])
        self.assertEqual(invoice.sales_order, so)
        self.assertEqual(Invoice.objects.filter(sales_order=so).count(), 1)
        so.refresh_from_db()
        self.assertEqual(so.status, 'invoiced')
        with self.assertRaises(Exception):
            services.create_invoice_from_order(so, user=self.user)

    def test_partial_shipment_tracks_open_quantity(self):
        self.stock_widgets('5', '70')
        so = self.make_so('5')
        services.confirm_sales_order(so, user=self.user)
        line = so.lines.first()
        services.ship_sales_order(so, user=self.user, shipments={line.id: Decimal('2')})
        so.refresh_from_db()
        self.assertEqual(so.status, 'partially_shipped')
        line.refresh_from_db()
        self.assertEqual(line.open_quantity, Decimal('3.000'))

    def test_ship_via_api_marks_order_fully_shipped(self):
        """Regression: shipping through the action endpoint must reach 'shipped'.

        SalesOrderViewSet prefetches `lines`, so `get_object()` hands the service an
        order whose related manager is backed by a prefetch cache. A status roll-up
        that re-reads `order.lines.all()` gets the pre-shipment quantities and parks a
        fully shipped order at `partially_shipped`, which then fails to invoice. The
        service-level tests above never see it because they pass a plain instance.
        """
        self.stock_widgets('5', '70')
        so = self.make_so('5')
        services.confirm_sales_order(so, user=self.user)

        response = self.client.post(reverse('salesorder-ship', args=[so.id]), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        so.refresh_from_db()
        self.assertEqual(so.status, 'shipped')
        self.assertEqual(so.lines.first().open_quantity, Decimal('0.000'))

        invoiced = self.client.post(reverse('salesorder-invoice', args=[so.id]), {}, format='json')
        self.assertEqual(invoiced.status_code, status.HTTP_201_CREATED)

    def test_partial_shipment_via_api_stays_partially_shipped(self):
        """The same roll-up must still report a genuine partial shipment honestly."""
        self.stock_widgets('5', '70')
        so = self.make_so('5')
        services.confirm_sales_order(so, user=self.user)
        line = so.lines.first()

        response = self.client.post(
            reverse('salesorder-ship', args=[so.id]), {'shipments': {str(line.id): '2'}}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        so.refresh_from_db()
        self.assertEqual(so.status, 'partially_shipped')
        self.assertEqual(so.lines.first().open_quantity, Decimal('3.000'))
