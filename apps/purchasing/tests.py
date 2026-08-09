from datetime import date
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.test_helpers import create_user
from apps.core.demo import build_demo_company
from apps.core.models import DocumentSequence
from apps.inventory.services import get_on_hand
from apps.journal.models import JournalEntry

from . import services
from .models import PurchaseOrder, PurchaseOrderLine


class PurchaseOrderFlowTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)
        self.frame = self.demo['products']['frame']
        self.warehouse = self.demo['warehouse']

    def make_po(self, quantity='10'):
        po = PurchaseOrder.objects.create(
            number=DocumentSequence.next_number('PO'), supplier=self.demo['supplier'],
            warehouse=self.warehouse, order_date=date(2026, 6, 1), created_by=self.user,
        )
        PurchaseOrderLine.objects.create(
            order=po, product=self.frame, quantity=Decimal(quantity), unit_price=Decimal('40'),
        )
        return po

    def test_api_create_assigns_number_and_owner(self):
        response = self.client.post(reverse('purchaseorder-list'), {
            'supplier': self.demo['supplier'].id, 'warehouse': self.warehouse.id,
            'order_date': '2026-06-01',
            'lines': [{'product': self.frame.id, 'quantity': '10', 'unit_price': '40'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['number'].startswith('PO-'))
        self.assertEqual(response.data['total_amount'], '400.00')

    def test_customer_only_partner_rejected(self):
        response = self.client.post(reverse('purchaseorder-list'), {
            'supplier': self.demo['customer'].id, 'warehouse': self.warehouse.id,
            'order_date': '2026-06-01',
            'lines': [{'product': self.frame.id, 'quantity': '1', 'unit_price': '1'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_requires_lines(self):
        po = PurchaseOrder.objects.create(
            number=DocumentSequence.next_number('PO'), supplier=self.demo['supplier'],
            warehouse=self.warehouse, order_date=date(2026, 6, 1), created_by=self.user,
        )
        with self.assertRaises(Exception):
            services.confirm_purchase_order(po, user=self.user)

    def test_receive_moves_stock_and_posts_accrual(self):
        po = self.make_po('10')
        services.confirm_purchase_order(po, user=self.user)
        result = services.receive_purchase_order(po, user=self.user, receipt_date=date(2026, 6, 5))
        po.refresh_from_db()
        self.assertEqual(po.status, 'received')
        self.assertEqual(get_on_hand(self.frame, self.warehouse), Decimal('10.000'))
        entry = result['journal_entry']
        self.assertEqual(entry.status, 'posted')
        self.assertEqual(sum(line.debit for line in entry.lines.all()), Decimal('400.00'))
        self.assertEqual(sum(line.credit for line in entry.lines.all()), Decimal('400.00'))
        # Idempotency: the GRN posting is keyed, so the ledger holds exactly one entry.
        self.assertEqual(
            JournalEntry.objects.filter(source_reference=f'grn:{result["grn_number"]}').count(), 1,
        )

    def test_partial_receipt_keeps_order_open(self):
        po = self.make_po('10')
        services.confirm_purchase_order(po, user=self.user)
        line = po.lines.first()
        services.receive_purchase_order(
            po, user=self.user, receipts={line.id: Decimal('4')}, receipt_date=date(2026, 6, 5),
        )
        po.refresh_from_db()
        self.assertEqual(po.status, 'partially_received')
        line.refresh_from_db()
        self.assertEqual(line.open_quantity, Decimal('6.000'))

    def test_over_receipt_rejected(self):
        po = self.make_po('10')
        services.confirm_purchase_order(po, user=self.user)
        line = po.lines.first()
        with self.assertRaises(Exception):
            services.receive_purchase_order(po, user=self.user, receipts={line.id: Decimal('11')})

    def test_cannot_cancel_after_receipt(self):
        po = self.make_po('10')
        services.confirm_purchase_order(po, user=self.user)
        services.receive_purchase_order(po, user=self.user)
        with self.assertRaises(Exception):
            services.cancel_purchase_order(po, user=self.user)
