from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from coa.models import Account, AccountType


class JournalEntryTests(APITestCase):
    def setUp(self):
        self.user = create_user('accountant', role='accountant')
        self.client.force_authenticate(user=self.user)

        # Seeded by coa's 0002_seed_account_types data migration.
        at = AccountType.objects.get(code='AS')
        self.cash = Account.objects.create(code='1000', name='Cash', account_type=at)
        at2 = AccountType.objects.get(code='RE')
        self.revenue = Account.objects.create(code='4000', name='Sales', account_type=at2)

    def test_create_balanced_entry(self):
        response = self.client.post(
            reverse('journalentry-list'),
            data={
                'entry_number': 'JE-001',
                'date': '2026-01-15',
                'description': 'Sales receipt',
                'status': 'draft',
                'lines': [
                    {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
                    {'account': self.revenue.id, 'debit': '0.00', 'credit': '100.00'},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['total_debit'], Decimal('100.00'))

    def test_reject_unbalanced_entry(self):
        response = self.client.post(
            reverse('journalentry-list'),
            data={
                'entry_number': 'JE-002',
                'date': '2026-01-15',
                'lines': [
                    {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
                    {'account': self.revenue.id, 'debit': '0.00', 'credit': '50.00'},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_empty_lines(self):
        response = self.client.post(
            reverse('journalentry-list'),
            data={'entry_number': 'JE-003', 'date': '2026-01-15', 'lines': []},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_viewer_denied(self):
        viewer = create_user('viewer', role='viewer')
        self.client.force_authenticate(user=viewer)
        response = self.client.get(reverse('journalentry-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
