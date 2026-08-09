from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.test_helpers import create_user

from .models import Account, AccountType


class AccountTypeTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        # Seeded by coa's 0002_seed_account_types data migration.
        self.account_type = AccountType.objects.get(code='AS')

    def test_list_account_types(self):
        response = self.client.get(reverse('accounttype-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_account_type(self):
        response = self.client.post(reverse('accounttype-list'), {
            'code': 'XX', 'name': 'Extraordinary Items',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_update_account_type(self):
        response = self.client.patch(
            reverse('accounttype-detail', args=[self.account_type.id]),
            {'description': 'Things we own'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class AccountTests(APITestCase):
    def setUp(self):
        self.user = create_user('tester', role='accountant')
        self.client.force_authenticate(user=self.user)
        # Seeded by coa's 0002_seed_account_types data migration.
        self.account_type = AccountType.objects.get(code='AS')
        self.account = Account.objects.create(
            code='1000', name='Cash', account_type=self.account_type,
        )

    def test_list_accounts(self):
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_account(self):
        response = self.client.post(reverse('account-list'), {
            'code': '1010', 'name': 'Bank', 'account_type': self.account_type.id,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_hierarchy(self):
        response = self.client.get(reverse('account-hierarchy'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_self_parent_rejected(self):
        response = self.client.patch(
            reverse('account-detail', args=[self.account.id]),
            {'parent_account': self.account.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_circular_hierarchy_rejected(self):
        child = Account.objects.create(
            code='1001', name='Petty Cash', account_type=self.account_type,
            parent_account=self.account,
        )
        response = self.client.patch(
            reverse('account-detail', args=[self.account.id]),
            {'parent_account': child.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
