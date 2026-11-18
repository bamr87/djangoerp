from decimal import Decimal
from unittest import mock

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from coa.models import Account, AccountType
from core.models import DocumentSequence

from .models import JournalEntry, JournalLine
from .serializers import JournalEntrySerializer
from .services import post_entry


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
    """journal.services.post_entry is the one write path for system postings."""

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


def _invalid_line_shapes(cash, revenue):
    """The line shapes both write paths must refuse: (name, lines, what the API's 400 says)."""
    return [
        ('balanced negative lines', [
            {'account': cash, 'debit': '-100.00', 'credit': '0.00'},
            {'account': revenue, 'debit': '0.00', 'credit': '-100.00'},
        ], 'cannot be negative'),
        ('one line carrying both sides', [
            {'account': cash, 'debit': '50.00', 'credit': '50.00'},
        ], 'not both'),
        ('a single zero line', [
            {'account': cash, 'debit': '0.00', 'credit': '0.00'},
        ], 'needs a non-zero debit or credit'),
        ('no non-zero credit', [
            {'account': cash, 'debit': '100.00', 'credit': '0.00'},
        ], 'one debit line and one credit line'),
    ]


class JournalLineRulesTests(APITestCase):
    """The API and post_entry enforce the same line rules (journal.services.validate_lines)."""

    def setUp(self):
        self.user = create_user('accountant', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.cash = Account.objects.create(code='1000', name='Cash', account_type=AccountType.objects.get(code='AS'))
        self.revenue = Account.objects.create(
            code='4000', name='Sales', account_type=AccountType.objects.get(code='RE'),
        )

    def _post(self, lines, status_value='posted', **extra):
        return self.client.post(
            reverse('journalentry-list'),
            data={'date': '2026-01-15', 'status': status_value, 'lines': lines, **extra},
            format='json',
        )

    def test_api_rejects_every_invalid_line_shape(self):
        for name, lines, says in _invalid_line_shapes(self.cash.id, self.revenue.id):
            with self.subTest(name):
                response = self._post(lines)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
                self.assertIn(says, str(response.data))
        self.assertFalse(JournalEntry.objects.exists())

    def test_api_rejects_a_zero_line_beside_valid_ones(self):
        response = self._post([
            {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
            {'account': self.revenue.id, 'debit': '0.00', 'credit': '100.00'},
            {'account': self.cash.id, 'debit': '0.00', 'credit': '0.00'},
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('needs a non-zero debit or credit', str(response.data))

    def test_post_entry_rejects_every_invalid_line_shape(self):
        for name, lines, _ in _invalid_line_shapes(self.cash, self.revenue):
            with self.subTest(name):
                with self.assertRaises(drf_serializers.ValidationError):
                    post_entry(date='2026-01-15', description=name, lines=lines)
        self.assertFalse(JournalEntry.objects.exists())

    def test_post_entry_still_drops_zero_lines_among_valid_ones(self):
        entry = post_entry(
            date='2026-01-15', description='computed posting',
            lines=[
                {'account': self.cash, 'debit': Decimal('100')},
                {'account': self.revenue, 'credit': Decimal('100')},
                {'account': self.cash, 'debit': Decimal('0'), 'credit': Decimal('0')},
            ],
        )
        self.assertEqual(entry.lines.count(), 2)

    def test_database_refuses_negative_and_two_sided_lines(self):
        entry = JournalEntry.objects.create(entry_number='ORM-1', date='2026-01-15')
        for name, amounts in [
            ('negative debit', {'debit': Decimal('-1')}),
            ('negative credit', {'credit': Decimal('-1')}),
            ('both sides', {'debit': Decimal('1'), 'credit': Decimal('1')}),
        ]:
            with self.subTest(name):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    JournalLine.objects.create(entry=entry, account=self.cash, **amounts)
        self.assertFalse(JournalLine.objects.exists())

    def test_failed_line_write_leaves_no_entry(self):
        real_create = JournalLine.objects.create
        calls = []

        def create_then_fail(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise DatabaseError('simulated failure on the second line')
            return real_create(**kwargs)

        with mock.patch.object(JournalLine.objects, 'create', side_effect=create_then_fail):
            with self.assertRaises(DatabaseError):
                self._post([
                    {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
                    {'account': self.revenue.id, 'debit': '0.00', 'credit': '100.00'},
                ])
        self.assertFalse(JournalEntry.objects.exists())
        self.assertFalse(JournalLine.objects.exists())

    def test_patch_posts_a_balanced_draft_without_resending_lines(self):
        draft = self._post([
            {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
            {'account': self.revenue.id, 'debit': '0.00', 'credit': '100.00'},
        ], status_value='draft')
        self.assertEqual(draft.status_code, status.HTTP_201_CREATED)
        response = self.client.patch(
            reverse('journalentry-detail', args=[draft.data['id']]), data={'status': 'posted'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(JournalEntry.objects.get(pk=draft.data['id']).status, 'posted')

    def test_patch_cannot_post_a_draft_whose_kept_lines_do_not_balance(self):
        # A draft written outside the API (raw ORM, admin) is still checked when it is posted.
        draft = JournalEntry.objects.create(entry_number='ORM-2', date='2026-01-15', status='draft')
        JournalLine.objects.create(entry=draft, account=self.cash, debit=Decimal('100'))
        JournalLine.objects.create(entry=draft, account=self.revenue, credit=Decimal('60'))
        response = self.client.patch(
            reverse('journalentry-detail', args=[draft.id]), data={'status': 'posted'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('must balance', str(response.data))
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'draft')


class EntryNumberTests(APITestCase):
    """Manual and system entries draw from the one JE sequence; a client number is ignored."""

    def setUp(self):
        self.user = create_user('accountant', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.cash = Account.objects.create(code='1000', name='Cash', account_type=AccountType.objects.get(code='AS'))
        self.revenue = Account.objects.create(
            code='4000', name='Sales', account_type=AccountType.objects.get(code='RE'),
        )
        self.lines = [
            {'account': self.cash.id, 'debit': '100.00', 'credit': '0.00'},
            {'account': self.revenue.id, 'debit': '0.00', 'credit': '100.00'},
        ]

    def _system_posting(self, reference):
        return post_entry(
            date='2026-01-16', description='system posting', source_reference=reference,
            lines=[
                {'account': self.cash, 'debit': Decimal('10')},
                {'account': self.revenue, 'credit': Decimal('10')},
            ],
        )

    def test_entry_number_is_read_only(self):
        self.assertIn('entry_number', JournalEntrySerializer.Meta.read_only_fields)

    def test_manual_entry_number_cannot_collide_with_the_next_system_posting(self):
        # Issue #39: a manual entry claiming JE-00001 used to make this post_entry raise IntegrityError.
        response = self.client.post(
            reverse('journalentry-list'),
            data={'entry_number': 'JE-00001', 'date': '2026-01-15', 'status': 'posted', 'lines': self.lines},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['entry_number'], 'JE-00001')
        self.assertEqual(DocumentSequence.objects.get(prefix='JE').next_value, 2)
        entry = self._system_posting('test:after-manual')
        self.assertEqual(entry.entry_number, 'JE-00002')

    def test_client_entry_number_is_ignored(self):
        response = self.client.post(
            reverse('journalentry-list'),
            data={'entry_number': 'JE-00002', 'date': '2026-01-15', 'status': 'posted', 'lines': self.lines},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['entry_number'], 'JE-00001')
        numbers = [self._system_posting(f'test:{n}').entry_number for n in (1, 2)]
        self.assertEqual(numbers, ['JE-00002', 'JE-00003'])

    def test_sequence_skips_a_number_an_older_entry_already_holds(self):
        # An entry numbered by hand before entry_number became read-only (or in the admin).
        JournalEntry.objects.create(entry_number='JE-00001', date='2026-01-01', status='draft')
        entry = self._system_posting('test:legacy')
        self.assertEqual(entry.entry_number, 'JE-00002')


class JournalEntryDeletionTests(APITestCase):
    """Posted history is immutable: DELETE removes drafts only (issue #36)."""

    def setUp(self):
        self.user = create_user('accountant', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.cash = Account.objects.create(code='1000', name='Cash', account_type=AccountType.objects.get(code='AS'))
        self.revenue = Account.objects.create(
            code='4000', name='Sales', account_type=AccountType.objects.get(code='RE'),
        )

    def _entry(self, status_value):
        return post_entry(
            date='2026-01-15', description=f'{status_value} entry', status=status_value,
            lines=[
                {'account': self.cash, 'debit': Decimal('100')},
                {'account': self.revenue, 'credit': Decimal('100')},
            ],
        )

    def test_posted_and_voided_entries_cannot_be_deleted(self):
        posted = self._entry('posted')
        voided = self._entry('posted')
        self.client.patch(reverse('journalentry-detail', args=[voided.id]), {'status': 'voided'}, format='json')
        for entry in (posted, voided):
            with self.subTest(JournalEntry.objects.get(pk=entry.pk).status):
                response = self.client.delete(reverse('journalentry-detail', args=[entry.id]))
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('cannot be deleted', str(response.data))
                self.assertTrue(JournalEntry.objects.filter(pk=entry.pk).exists())
                self.assertEqual(JournalLine.objects.filter(entry=entry).count(), 2)

    def test_draft_entry_can_be_deleted(self):
        draft = self._entry('draft')
        response = self.client.delete(reverse('journalentry-detail', args=[draft.id]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(JournalEntry.objects.filter(pk=draft.pk).exists())
        self.assertFalse(JournalLine.objects.filter(entry_id=draft.pk).exists())

    def test_orm_cannot_delete_a_posted_entry_either(self):
        # The guard raises inside Django's delete transaction, so each attempt gets its own savepoint.
        posted = self._entry('posted')
        with self.assertRaises(ProtectedError), transaction.atomic():
            posted.delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            JournalEntry.objects.filter(pk=posted.pk).delete()
        self.assertEqual(JournalLine.objects.filter(entry=posted).count(), 2)
        self._entry('draft').delete()
