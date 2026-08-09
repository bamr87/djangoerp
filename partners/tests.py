from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user


class BusinessPartnerTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)

    def test_create_partner(self):
        response = self.client.post(reverse('businesspartner-list'), {
            'code': 'ACME', 'name': 'ACME Industries', 'is_customer': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['created_by'], self.user.id)

    def test_partner_must_have_a_role(self):
        response = self.client.post(reverse('businesspartner-list'), {
            'code': 'NOPE', 'name': 'No Role Co',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_by_role(self):
        self.client.post(reverse('businesspartner-list'), {
            'code': 'C1', 'name': 'Customer', 'is_customer': True,
        }, format='json')
        self.client.post(reverse('businesspartner-list'), {
            'code': 'S1', 'name': 'Supplier', 'is_supplier': True,
        }, format='json')
        response = self.client.get(reverse('businesspartner-list'), {'is_supplier': True})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['code'], 'S1')
