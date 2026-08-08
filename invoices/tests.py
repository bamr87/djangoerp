from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user

from .models import Invoice


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
