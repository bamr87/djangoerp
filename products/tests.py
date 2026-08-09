from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from partners.models import BusinessPartner

from .models import UnitOfMeasure


class ProductTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.uom = UnitOfMeasure.objects.create(code='EA', name='Each')

    def test_create_product(self):
        response = self.client.post(reverse('product-list'), {
            'sku': 'WIDGET', 'name': 'Widget', 'uom': self.uom.id,
            'procurement_type': 'make', 'standard_cost': '70.0000',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['procurement_type'], 'make')

    def test_negative_cost_rejected(self):
        response = self.client.post(reverse('product-list'), {
            'sku': 'BAD', 'name': 'Bad', 'uom': self.uom.id, 'standard_cost': '-1',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_default_supplier_must_be_supplier(self):
        customer_only = BusinessPartner.objects.create(
            code='CUST', name='Customer Only', is_customer=True,
        )
        response = self.client.post(reverse('product-list'), {
            'sku': 'PART', 'name': 'Part', 'uom': self.uom.id,
            'default_supplier': customer_only.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
