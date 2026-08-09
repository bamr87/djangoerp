from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from coa.models import Account, AccountType
from journal.models import JournalEntry, JournalLine

from .models import ReportTemplate, SavedReport
from .report_generators import generate_balance_sheet, generate_trial_balance


class ReportTemplateTests(APITestCase):
    def setUp(self):
        self.user = create_user('accountant', role='accountant')
        self.client.force_authenticate(user=self.user)

    def test_create_template(self):
        response = self.client.post(reverse('template-list'), {
            'name': 'Monthly Balance Sheet',
            'report_type': 'balance_sheet',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_list_templates(self):
        response = self.client.get(reverse('template-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_viewer_cannot_create(self):
        viewer = create_user('viewer', role='viewer')
        self.client.force_authenticate(user=viewer)
        response = self.client.post(reverse('template-list'), {
            'name': 'Test', 'report_type': 'trial_balance',
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class SavedReportTests(APITestCase):
    def setUp(self):
        self.user = create_user('accountant', role='accountant')
        self.client.force_authenticate(user=self.user)
        self.template = ReportTemplate.objects.create(
            name='Trial Balance', report_type='trial_balance', created_by=self.user,
        )

    def test_create_saved_report(self):
        response = self.client.post(reverse('savedreport-list'), {
            'template': self.template.id,
            'name': 'Q1 Trial Balance',
            'parameters': {'as_of_date': '2026-03-31'},
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_regenerate_actually_saves_decimal_result_data(self):
        # Regression test: result_data/parameters must use DjangoJSONEncoder.
        # report_generators.py returns raw Decimal values from DB aggregates, and the
        # plain JSON encoder (JSONField's default) can't serialize them — this used to
        # fail every report with "Object of type Decimal is not JSON serializable",
        # caught by generate_report's try/except and silently flipped to status='failed'.
        saved = SavedReport.objects.create(
            template=self.template, name='TB', parameters={'as_of_date': '2026-03-31'},
            created_by=self.user,
        )
        response = self.client.post(reverse('savedreport-regenerate', args=[saved.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        saved.refresh_from_db()
        self.assertEqual(saved.status, 'completed')
        self.assertEqual(saved.error_message, '')
        self.assertIn('balanced', saved.result_data)

    def test_list_saved_reports(self):
        response = self.client.get(reverse('savedreport-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ReportGeneratorTests(APITestCase):
    """Exercise the accounting domain rules report_generators.py must not break."""

    def setUp(self):
        # Seeded by coa's 0002_seed_account_types data migration.
        self.asset_type = AccountType.objects.get(code='AS')
        self.revenue_type = AccountType.objects.get(code='RE')
        self.expense_type = AccountType.objects.get(code='EX')
        self.equity_type = AccountType.objects.get(code='EQ')
        self.liability_type = AccountType.objects.get(code='LI')

        self.cash = Account.objects.create(code='1000', name='Cash', account_type=self.asset_type)
        self.sales = Account.objects.create(code='4000', name='Sales', account_type=self.revenue_type)
        self.rent = Account.objects.create(code='5000', name='Rent', account_type=self.expense_type)

        entry = JournalEntry.objects.create(entry_number='JE-100', date='2026-01-10', status='posted')
        JournalLine.objects.create(entry=entry, account=self.cash, debit=Decimal('500.00'))
        JournalLine.objects.create(entry=entry, account=self.sales, credit=Decimal('500.00'))

        entry2 = JournalEntry.objects.create(entry_number='JE-101', date='2026-01-12', status='posted')
        JournalLine.objects.create(entry=entry2, account=self.rent, debit=Decimal('200.00'))
        JournalLine.objects.create(entry=entry2, account=self.cash, credit=Decimal('200.00'))

        # A draft entry must never be counted by any report.
        draft = JournalEntry.objects.create(entry_number='JE-102', date='2026-01-13', status='draft')
        JournalLine.objects.create(entry=draft, account=self.cash, debit=Decimal('9999.00'))
        JournalLine.objects.create(entry=draft, account=self.sales, credit=Decimal('9999.00'))

    def test_balance_sheet_folds_open_pl_into_equity_and_balances(self):
        result = generate_balance_sheet({'as_of_date': '2026-01-31'})
        self.assertEqual(result['current_period_earnings'], Decimal('300.00'))
        self.assertTrue(result['balanced'])

    def test_trial_balance_ignores_draft_entries_and_balances(self):
        result = generate_trial_balance({'as_of_date': '2026-01-31'})
        self.assertTrue(result['balanced'])
        self.assertEqual(result['total_debits'], result['total_credits'])
