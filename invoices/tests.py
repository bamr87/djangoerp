from decimal import Decimal

from django.db import transaction
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from core.demo import build_demo_company

from .models import Invoice, InvoiceLineItem, Payment
from .services import post_invoice, post_payment


class InvoiceTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)

    def test_create_invoice_with_line_items(self):
        response = self.client.post(
            reverse('invoice-list'),
            data={
                'invoice_number': 'INV-001',
                'customer': 'Acme Corp',
                'due_date': '2026-02-15',
                'total': '250.00',
                'line_items': [
                    {'description': 'Widget', 'quantity': '5', 'unit_price': '50.00', 'total': '250.00'},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['customer'], 'Acme Corp')
        self.assertEqual(len(response.data['line_items']), 1)
        self.assertEqual(response.data['created_by'], self.user.id)

    def test_list_invoices(self):
        response = self.client.get(reverse('invoice-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class PaymentTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.invoice = Invoice.objects.create(
            invoice_number='INV-001', customer='Acme', due_date='2026-02-15',
            total='100.00', created_by=self.user,
        )

    def test_create_payment(self):
        response = self.client.post(
            reverse('payment-list'),
            data={
                'payment_reference': 'PAY-001',
                'invoice': self.invoice.id,
                'amount': '100.00',
                'payment_method': 'bank_transfer',
                'payment_date': '2026-01-20',
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class InvoicePostingTests(APITestCase):
    """AR posting: Dr partner receivable / Cr revenue, then cash application."""

    def setUp(self):
        self.user = create_user('poster', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)

    def make_invoice(self, partner=None, product=None):
        invoice = Invoice.objects.create(
            invoice_number='INV-100', customer='ACME Industries', partner=partner,
            due_date='2026-02-15', total=Decimal('500.00'), created_by=self.user,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice, description='Widgets', quantity=Decimal('2'),
            unit_price=Decimal('250.00'), total=Decimal('500.00'), product=product,
        )
        return invoice

    def test_posting_requires_partner(self):
        invoice = self.make_invoice(partner=None)
        with self.assertRaises(Exception):
            post_invoice(invoice, user=self.user)

    def test_posting_requires_revenue_account(self):
        invoice = self.make_invoice(partner=self.demo['customer'])  # no product, no fallback
        with self.assertRaises(Exception):
            post_invoice(invoice, user=self.user)

    def test_post_and_pay(self):
        invoice = self.make_invoice(
            partner=self.demo['customer'], product=self.demo['products']['widget'],
        )
        post_invoice(invoice, user=self.user, posting_date='2026-01-20')
        invoice.refresh_from_db()
        self.assertIsNotNone(invoice.journal_entry)
        self.assertEqual(invoice.status, 'sent')
        entry = invoice.journal_entry
        ar_line = entry.lines.get(account=self.demo['accounts']['receivable'])
        self.assertEqual(ar_line.debit, Decimal('500.00'))

        with self.assertRaises(Exception):
            post_invoice(invoice, user=self.user)  # double posting blocked

        payment = Payment.objects.create(
            payment_reference='PAY-100', invoice=invoice, amount=Decimal('500.00'),
            payment_method='bank_transfer', payment_date='2026-01-25',
            deposit_account=self.demo['accounts']['cash'], created_by=self.user,
        )
        post_payment(payment, user=self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')

    def test_posted_invoice_cannot_be_edited(self):
        invoice = self.make_invoice(
            partner=self.demo['customer'], product=self.demo['products']['widget'],
        )
        post_invoice(invoice, user=self.user)
        response = self.client.patch(
            reverse('invoice-detail', args=[invoice.id]),
            data={'total': '999.00'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_payment_on_unposted_invoice_rejected(self):
        invoice = self.make_invoice(partner=self.demo['customer'])
        payment = Payment.objects.create(
            payment_reference='PAY-101', invoice=invoice, amount=Decimal('500.00'),
            payment_method='cash', payment_date='2026-01-25',
            deposit_account=self.demo['accounts']['cash'], created_by=self.user,
        )
        with self.assertRaises(Exception):
            post_payment(payment, user=self.user)


class InvoiceDeletionTests(APITestCase):
    """A posted invoice or payment is ledger history: DELETE refuses it (issue #36)."""

    def setUp(self):
        self.user = create_user('poster', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.demo = build_demo_company(created_by=self.user)

    def make_invoice(self, number):
        invoice = Invoice.objects.create(
            invoice_number=number, customer='ACME Industries', partner=self.demo['customer'],
            due_date='2026-02-15', total=Decimal('500.00'), created_by=self.user,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice, description='Widgets', quantity=Decimal('2'), unit_price=Decimal('250.00'),
            total=Decimal('500.00'), product=self.demo['products']['widget'],
        )
        return invoice

    def make_payment(self, invoice, reference, amount='200.00'):
        return Payment.objects.create(
            payment_reference=reference, invoice=invoice, amount=Decimal(amount), payment_method='cash',
            payment_date='2026-01-25', deposit_account=self.demo['accounts']['cash'], created_by=self.user,
        )

    def test_posted_invoice_and_payment_cannot_be_deleted(self):
        invoice = post_invoice(self.make_invoice('INV-200'), user=self.user)
        payment = post_payment(self.make_payment(invoice, 'PAY-200'), user=self.user)

        response = self.client.delete(reverse('invoice-detail', args=[invoice.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.delete(reverse('payment-detail', args=[payment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertEqual(invoice.line_items.count(), 1)
        self.assertEqual(list(invoice.payments.all()), [payment])

    def test_unposted_invoice_and_payment_can_be_deleted(self):
        invoice = post_invoice(self.make_invoice('INV-201'), user=self.user)
        draft_payment = self.make_payment(invoice, 'PAY-201')
        response = self.client.delete(reverse('payment-detail', args=[draft_payment.id]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Payment.objects.filter(pk=draft_payment.pk).exists())

        draft = self.make_invoice('INV-202')
        response = self.client.delete(reverse('invoice-detail', args=[draft.id]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Invoice.objects.filter(pk=draft.pk).exists())

    def test_orm_cannot_delete_posted_documents_either(self):
        invoice = post_invoice(self.make_invoice('INV-203'), user=self.user)
        payment = post_payment(self.make_payment(invoice, 'PAY-203'), user=self.user)
        for document in (payment, invoice):
            with self.subTest(str(document)):
                with self.assertRaises(ProtectedError), transaction.atomic():
                    document.delete()
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
