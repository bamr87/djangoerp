"""
The workspace: its registry must describe exactly the API that exists, and its
read models (document flow, search, overview, statements) must show each role
no more than the API itself would.
"""
import datetime
import io

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.test_helpers import create_user
from inventory.models import StockMove
from invoices.models import Invoice
from journal.models import JournalEntry
from manufacturing.models import WorkOrder
from partners.models import BusinessPartner
from purchasing.models import PurchaseOrder
from sales.models import SalesOrder

from . import registry

# Router entries the registry deliberately leaves out.
NOT_RESOURCES = {'workspace'}


class RegistryTests(SimpleTestCase):
    def setUp(self):
        self.reg = registry.build()
        self.resources = self.reg['resources']

    def test_every_api_collection_is_described(self):
        described = {r['basename'] for r in self.resources.values()}
        routed = set(registry.router_index()) - NOT_RESOURCES
        self.assertEqual(routed - described, set(), 'a viewset the workspace does not know about')
        self.assertEqual(described - routed, set(), 'a registry entry with no viewset behind it')

    def test_layout_names_only_fields_and_actions_the_api_has(self):
        for key, resource in self.resources.items():
            fields = resource['fields']
            for name in resource.get('columns', []) + resource.get('form_fields', []):
                self.assertIn(name, fields, f'{key}: column or form field {name}')
            if resource.get('status'):
                self.assertEqual(fields[resource['status']]['type'], 'choice', key)
                choices = {value for value, _label in fields[resource['status']]['choices']}
                self.assertTrue(set(resource.get('lifecycle', [])) | set(resource.get('terminal', [])) <= choices, key)
            if resource.get('lines'):
                lines = fields[resource['lines']['field']]
                self.assertEqual(lines['type'], 'lines', key)
                for name in resource['lines']['columns']:
                    self.assertIn(name, lines['child'], f'{key}: line column {name}')
            for spec in next(r for r in registry.RESOURCES if r['key'] == key).get('actions', []):
                self.assertIn(spec['name'], [a['name'] for a in resource['actions']], f'{key}: action {spec["name"]}')

    def test_every_reference_links_to_a_resource(self):
        for key, resource in self.resources.items():
            for name, meta in resource['fields'].items():
                for field in [meta] + list(meta.get('child', {}).values()):
                    if field['type'] == 'ref':
                        self.assertTrue(field.get('resource'), f'{key}.{name} -> {field.get("model")}')

    def test_endpoints_come_from_the_router(self):
        self.assertEqual(self.resources['sales.orders']['endpoint'], '/api/sales/orders/')
        self.assertEqual(self.resources['admin.audit-log']['endpoint'], '/api/auth/audit-logs/')
        self.assertEqual(self.resources['sales.orders']['route'], '/app/sales/orders/')

    def test_access_is_what_the_permission_classes_say(self):
        access = {key: r['access'] for key, r in self.resources.items()}
        self.assertEqual(access['finance.journal-entries']['read'], ['admin', 'accountant'])
        self.assertEqual(access['finance.invoices']['read'], ['admin', 'accountant', 'auditor'])
        self.assertEqual(access['finance.invoices']['write'], ['admin', 'accountant'])
        self.assertEqual(access['admin.audit-log']['read'], ['admin'])
        self.assertIn('none', access['master.partners']['read'], 'master data is open to any authenticated user')
        self.assertEqual(access['inventory.stock-levels']['write'], [], 'a read-only viewset')
        ship = next(a for a in self.resources['sales.orders']['actions'] if a['name'] == 'ship')
        self.assertEqual((ship['method'], ship['roles'], ship['button']), ('POST', ['admin', 'accountant'], True))

    def test_navigation_routes_resolve_to_a_page(self):
        for group in self.reg['groups']:
            for item in group['items']:
                path = item['route'].split('?')[0]
                with self.subTest(item=item['key']):
                    self.assertIn(resolve(path).url_name, ('shell', 'shell-path', 'console'))


class DemoBooksMixin:
    """The demo_erp loop: plan -> buy -> make -> ship -> bill -> collect, through the real services."""

    @classmethod
    def setUpTestData(cls):
        call_command('demo_erp', stdout=io.StringIO())
        cls.accountant = User.objects.get(username='demo')
        cls.viewer = create_user('ws_viewer', role='viewer')
        cls.auditor = create_user('ws_auditor', role='auditor')
        cls.admin = create_user('ws_admin', role='admin')
        cls.order = SalesOrder.objects.get()
        cls.purchase = PurchaseOrder.objects.first()
        cls.work_order = WorkOrder.objects.get()
        cls.invoice = Invoice.objects.get()

    def flow(self, key, pk, user):
        self.client.force_authenticate(user=user)
        return self.client.get(reverse('workspace-flow', kwargs={'resource': key, 'object_id': pk}))


class DocumentFlowTests(DemoBooksMixin, APITestCase):
    def test_a_sales_order_traces_to_its_shipment_invoice_payment_and_ledger(self):
        # Back-date the shipment's moves so their business date differs from the audit row's timestamp.
        StockMove.objects.filter(move_type='shipment').update(move_date=datetime.date(2020, 1, 2))
        response = self.flow('sales.orders', self.order.pk, self.accountant)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        relations = {item['relation'] for item in response.data['related']}
        self.assertTrue({'Customer', 'Shipment', 'Invoice', 'Payment', 'Planned supply'} <= relations, relations)
        sources = {e['source_reference'].split(':')[0] for e in response.data['journal_entries']}
        self.assertEqual(sources, {'shipment', 'invoice', 'payment'})
        for entry in response.data['journal_entries']:
            self.assertEqual(entry['total_debit'], entry['total_credit'], entry['entry_number'])
        self.assertEqual({m['move_type'] for m in response.data['stock_moves']}, {'shipment'})
        self.assertEqual(response.data['restricted'], ['events'], 'audit history is admin-only, as in the API')
        # A shipment carries its stock moves' business date, not the audit row's wall-clock time; due dates say so.
        shipment = next(i for i in response.data['related'] if i['relation'] == 'Shipment')
        self.assertEqual(shipment['date'], datetime.date(2020, 1, 2))
        invoice = next(i for i in response.data['related'] if i['relation'] == 'Invoice')
        self.assertEqual((invoice['date'], invoice['date_label']), (self.invoice.due_date, 'due'))

    def test_a_purchase_order_shows_its_receipt_and_accrual(self):
        data = self.flow('purchasing.orders', self.purchase.pk, self.accountant).data
        self.assertIn('Goods receipt', {i['relation'] for i in data['related']})
        self.assertIn('Planned by', {i['relation'] for i in data['related']})
        self.assertEqual({e['source_reference'].split(':')[0] for e in data['journal_entries']}, {'grn'})
        self.assertTrue(data['stock_moves'])

    def test_a_work_order_shows_consumption_receipt_and_pegging(self):
        data = self.flow('manufacturing.work-orders', self.work_order.pk, self.accountant).data
        relations = {i['relation'] for i in data['related']}
        self.assertTrue({'Product', 'Bill of materials', 'Component', 'Planned by', 'Pegged demand'} <= relations)
        self.assertEqual({m['move_type'] for m in data['stock_moves']}, {'production_issue', 'production_receipt'})
        self.assertEqual([e['source_reference'] for e in data['journal_entries']],
                         [f'wo:{self.work_order.number}:complete'])

    def test_a_journal_entry_points_back_to_its_source_document(self):
        entry = JournalEntry.objects.get(source_reference=f'invoice:{self.invoice.invoice_number}')
        data = self.flow('finance.journal-entries', entry.pk, self.accountant).data
        source = next(i for i in data['related'] if i['relation'] == 'Source: invoice')
        self.assertEqual((source['resource'], source['id']), ('finance.invoices', self.invoice.pk))

    def test_each_section_follows_the_roles_api_permissions(self):
        self.assertEqual(self.flow('sales.orders', self.order.pk, self.viewer).status_code,
                         status.HTTP_403_FORBIDDEN)
        partner = BusinessPartner.objects.get(code='ACME')
        viewer = self.flow('master.partners', partner.pk, self.viewer).data
        self.assertIsNone(viewer['journal_entries'])
        self.assertIn('journal_entries', viewer['restricted'])
        self.assertNotIn('finance.invoices', {i['resource'] for i in viewer['related']}, 'viewers cannot read invoices')
        auditor = self.flow('finance.invoices', self.invoice.pk, self.auditor).data
        self.assertTrue(auditor['journal_entries'], 'auditors read ledger lines')
        self.assertIsNone(auditor['stock_moves'], 'but not stock moves')
        admin = self.flow('finance.invoices', self.invoice.pk, self.admin).data
        self.assertTrue(any(e['event'] == 'invoice.post' for e in admin['events']))

    def test_unknown_resources_and_objects_are_404(self):
        self.assertEqual(self.flow('nope.things', 1, self.accountant).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.flow('sales.orders', 999999, self.accountant).status_code, status.HTTP_404_NOT_FOUND)


class WorkspaceReadModelTests(DemoBooksMixin, APITestCase):
    def get(self, name, user, **params):
        self.client.force_authenticate(user=user)
        return self.client.get(reverse(name), params)

    def test_endpoints_need_authentication(self):
        for name in ('workspace-registry', 'workspace-overview', 'workspace-search', 'workspace-statements'):
            with self.subTest(name):
                self.client.force_authenticate(user=None)
                self.assertEqual(self.client.get(reverse(name)).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_registry_is_served_to_any_signed_in_user(self):
        response = self.get('workspace-registry', self.viewer)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('sales.orders', response.data['resources'])

    def test_search_only_returns_what_the_role_may_read(self):
        found = self.get('workspace-search', self.accountant, search=self.order.number).data['results']
        self.assertIn(('sales.orders', self.order.pk), {(r['resource'], r['id']) for r in found})
        hidden = self.get('workspace-search', self.viewer, search=self.order.number).data['results']
        self.assertNotIn('sales.orders', {r['resource'] for r in hidden})
        partners = self.get('workspace-search', self.viewer, search='ACME').data['results']
        self.assertIn('master.partners', {r['resource'] for r in partners})
        self.assertEqual(self.get('workspace-search', self.viewer, search='A').data['results'], [])

    def test_overview_counts_and_queues_by_role(self):
        accountant = self.get('workspace-overview', self.accountant).data
        self.assertEqual(accountant['counts']['sales.orders'], 1)
        self.assertIn('sales.orders', {q['resource'] for q in accountant['queues']})
        self.assertTrue(all(q['route'].startswith('/app/') for q in accountant['queues']))
        self.assertIsNone(accountant['activity'], 'recent activity reads the audit log: admin only')
        viewer = self.get('workspace-overview', self.viewer).data
        self.assertNotIn('sales.orders', viewer['counts'])
        self.assertNotIn('sales.orders', {q['resource'] for q in viewer['queues']})
        admin = self.get('workspace-overview', self.admin).data
        self.assertTrue(admin['activity'])

    def test_statements_compile_from_the_report_generators(self):
        trial = self.get('workspace-statements', self.accountant, report='trial_balance').data
        self.assertTrue(trial['data']['balanced'])
        sheet = self.get('workspace-statements', self.auditor, report='balance_sheet').data
        self.assertTrue(sheet['data']['balanced'])
        self.assertEqual(self.get('workspace-statements', self.viewer).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.get('workspace-statements', self.accountant, report='general_ledger').status_code,
                         status.HTTP_400_BAD_REQUEST)
        cash = JournalEntry.objects.filter(lines__account__code='1000').first().lines.get(account__code='1000')
        ledger = self.get('workspace-statements', self.accountant, report='general_ledger', account=cash.account_id)
        self.assertEqual(ledger.status_code, status.HTTP_200_OK)
        self.assertIn('1000', ledger.data['data']['ledger'])
        self.assertEqual(self.get('workspace-statements', self.accountant, report='nope').status_code,
                         status.HTTP_400_BAD_REQUEST)


class ShellPageTests(TestCase):
    def test_every_app_route_serves_the_shell_with_the_module_navigation(self):
        for path in ('/app/', '/app/sales/orders/', '/app/sales/orders/7/', '/app/finance/statements/'):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'workspace/js/shell.js')
                self.assertContains(response, 'aria-label="Modules"')
                self.assertContains(response, 'href="/app/sales/orders/"')
                self.assertContains(response, 'href="/console/"')
