from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.test_helpers import create_user
from apps.coa.models import Account, AccountType


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

    def test_posted_entry_is_immutable_except_voiding(self):
        create = self.client.post(
            reverse('journalentry-list'),
            data={
                'entry_number': 'JE-010', 'date': '2026-01-15', 'status': 'posted',
                'lines': [
                    {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
                    {'account': self.revenue.id, 'debit': '0.00', 'credit': '100.00'},
                ],
            },
            format='json',
        )
        entry_id = create.data['id']
        edit = self.client.patch(
            reverse('journalentry-detail', args=[entry_id]),
            data={'description': 'rewrite history'}, format='json',
        )
        self.assertEqual(edit.status_code, status.HTTP_400_BAD_REQUEST)
        void = self.client.patch(
            reverse('journalentry-detail', args=[entry_id]),
            data={'status': 'voided'}, format='json',
        )
        self.assertEqual(void.status_code, status.HTTP_200_OK)
        edit_voided = self.client.patch(
            reverse('journalentry-detail', args=[entry_id]),
            data={'status': 'draft'}, format='json',
        )
        self.assertEqual(edit_voided.status_code, status.HTTP_400_BAD_REQUEST)


class PostEntryServiceTests(APITestCase):
    """apps.journal.services.post_entry is the one write path for system postings."""

    def setUp(self):
        self.user = create_user('svc', role='accountant')
        at = AccountType.objects.get(code='AS')
        self.cash = Account.objects.create(code='1000', name='Cash', account_type=at)
        at2 = AccountType.objects.get(code='RE')
        self.revenue = Account.objects.create(code='4000', name='Sales', account_type=at2)

    def test_posts_balanced_entry_with_sequence_number(self):
        from .services import post_entry

        entry = post_entry(
            date='2026-01-15', description='svc post',
            lines=[
                {'account': self.cash, 'debit': Decimal('100')},
                {'account': self.revenue, 'credit': Decimal('100')},
            ],
            created_by=self.user, source_reference='test:1',
        )
        self.assertTrue(entry.entry_number.startswith('JE-'))
        self.assertEqual(entry.status, 'posted')
        self.assertEqual(entry.lines.count(), 2)

    def test_rejects_unbalanced(self):
        from rest_framework import serializers as drf_serializers

        from .services import post_entry

        with self.assertRaises(drf_serializers.ValidationError):
            post_entry(
                date='2026-01-15', description='bad',
                lines=[
                    {'account': self.cash, 'debit': Decimal('100')},
                    {'account': self.revenue, 'credit': Decimal('99')},
                ],
            )

    def test_idempotent_per_source_reference(self):
        from .models import JournalEntry
        from .services import post_entry

        lines = [
            {'account': self.cash, 'debit': Decimal('50')},
            {'account': self.revenue, 'credit': Decimal('50')},
        ]
        first = post_entry(date='2026-01-15', description='once', lines=lines,
                           source_reference='test:dup')
        second = post_entry(date='2026-01-15', description='once', lines=lines,
                            source_reference='test:dup')
        self.assertEqual(first.id, second.id)
        self.assertEqual(JournalEntry.objects.filter(source_reference='test:dup').count(), 1)
