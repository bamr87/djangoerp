from decimal import Decimal

from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from journal.models import JournalLine
from journal.services import post_entry
from reports.report_generators import generate_trial_balance

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

    def test_seeded_types_cannot_be_deleted(self):
        for code in AccountType.SEEDED_CODES:
            with self.subTest(code):
                account_type = AccountType.objects.get(code=code)
                response = self.client.delete(reverse('accounttype-detail', args=[account_type.id]))
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertTrue(AccountType.objects.filter(code=code).exists())

    def test_seeded_types_cannot_be_recoded(self):
        for code in AccountType.SEEDED_CODES:
            with self.subTest(code):
                account_type = AccountType.objects.get(code=code)
                response = self.client.patch(
                    reverse('accounttype-detail', args=[account_type.id]), {'code': 'ZZ'},
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                account_type.refresh_from_db()
                self.assertEqual(account_type.code, code)

    def test_unseeded_type_can_be_recoded_and_deleted(self):
        extra = AccountType.objects.create(code='XX', name='Extraordinary Items')
        recode = self.client.patch(reverse('accounttype-detail', args=[extra.id]), {'code': 'XY'})
        self.assertEqual(recode.status_code, status.HTTP_200_OK)
        response = self.client.delete(reverse('accounttype-detail', args=[extra.id]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_deleting_a_type_still_in_use_is_a_conflict(self):
        # Account.account_type is PROTECT; core.exceptions turns the ProtectedError into a 409, not a 500.
        extra = AccountType.objects.create(code='XX', name='Extraordinary Items')
        Account.objects.create(code='9000', name='Windfall', account_type=extra)
        response = self.client.delete(reverse('accounttype-detail', args=[extra.id]))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('1 account', response.data['detail'])
        self.assertTrue(AccountType.objects.filter(pk=extra.pk).exists())


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


class AccountDeletionTests(APITestCase):
    """Deleting an account must never take ledger lines with it (issue #37)."""

    def setUp(self):
        self.cash = Account.objects.create(code='1000', name='Cash', account_type=AccountType.objects.get(code='AS'))
        self.sales = Account.objects.create(code='4000', name='Sales', account_type=AccountType.objects.get(code='RE'))
        post_entry(
            date='2026-01-15', description='cash sale', source_reference='test:sale',
            lines=[
                {'account': self.cash, 'debit': Decimal('100')},
                {'account': self.sales, 'credit': Decimal('100')},
            ],
        )

    def test_account_with_ledger_lines_cannot_be_deleted_by_any_role(self):
        for role in ('viewer', 'accountant'):
            with self.subTest(role):
                self.client.force_authenticate(user=create_user(role, role=role))
                response = self.client.delete(reverse('account-detail', args=[self.cash.id]))
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('is_active', str(response.data))
                self.assertTrue(Account.objects.filter(pk=self.cash.pk).exists())
                self.assertEqual(JournalLine.objects.count(), 2)
                self.assertTrue(generate_trial_balance({'as_of_date': '2026-01-31'})['balanced'])

    def test_unused_account_can_be_deleted(self):
        self.client.force_authenticate(user=create_user('accountant', role='accountant'))
        unused = Account.objects.create(code='1010', name='Bank', account_type=AccountType.objects.get(code='AS'))
        response = self.client.delete(reverse('account-detail', args=[unused.id]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Account.objects.filter(pk=unused.pk).exists())

    def test_referenced_account_is_a_conflict_not_a_server_error(self):
        # A parent account is held by Account.parent_account (PROTECT): 409 from core.exceptions.
        self.client.force_authenticate(user=create_user('accountant', role='accountant'))
        parent = Account.objects.create(code='1100', name='Banks', account_type=AccountType.objects.get(code='AS'))
        Account.objects.create(
            code='1110', name='Main bank', account_type=AccountType.objects.get(code='AS'), parent_account=parent,
        )
        response = self.client.delete(reverse('account-detail', args=[parent.id]))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Account.objects.filter(pk=parent.pk).exists())

    def test_database_protects_ledger_lines(self):
        with self.assertRaises(ProtectedError):
            self.cash.delete()
        self.assertEqual(JournalLine.objects.count(), 2)

    def test_retiring_an_account_keeps_the_trial_balance(self):
        self.client.force_authenticate(user=create_user('accountant', role='accountant'))
        response = self.client.patch(reverse('account-detail', args=[self.cash.id]), {'is_active': False})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        trial = generate_trial_balance({'as_of_date': '2026-01-31'})
        self.assertTrue(trial['balanced'])
        self.assertEqual(trial['total_debits'], Decimal('100.00'))
        self.assertIn('1000', [row['account_code'] for row in trial['accounts']])
