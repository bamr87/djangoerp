from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import AuditLog
from accounts.test_helpers import create_user
from core.demo import build_demo_company
from core.models import DocumentSequence
from inventory.services import get_on_hand
from invoices.models import Payment
from invoices.services import post_invoice, post_payment
from journal.services import post_entry
from manufacturing import services as manufacturing_services
from mrp.engine import run_mrp
from mrp.models import MRPRun
from mrp.services import convert_run
from purchasing import services as purchasing_services
from reports.report_generators import (calculate_account_balances, generate_balance_sheet, generate_income_statement,
                                       generate_trial_balance)
from sales import services as sales_services
from sales.models import SalesOrder, SalesOrderLine


class DocumentSequenceTests(TestCase):
    def test_sequences_increment_per_prefix(self):
        self.assertEqual(DocumentSequence.next_number('SO'), 'SO-00001')
        self.assertEqual(DocumentSequence.next_number('SO'), 'SO-00002')
        self.assertEqual(DocumentSequence.next_number('PO'), 'PO-00001')

    def test_padding(self):
        DocumentSequence.objects.create(prefix='X', next_value=123, padding=3)
        self.assertEqual(DocumentSequence.next_number('X'), 'X-123')


class EndToEndERPFlowTests(TestCase):
    """
    The whole loop on one database: plan -> buy -> make -> ship -> bill ->
    collect, asserting stock, costing and GL effects at every stage, ending
    with statements that foot to the cent. This is the executable definition
    of "core functionality demonstrated end to end".
    """

    def setUp(self):
        self.user = create_user('flow', role='accountant')
        self.demo = build_demo_company(created_by=self.user)
        self.accounts = self.demo['accounts']
        self.warehouse = self.demo['warehouse']
        self.day = date(2026, 6, 1)

    def balance(self, account, as_of='2026-06-30'):
        return calculate_account_balances([account], as_of)[0]['balance']

    def test_full_loop(self):
        widget = self.demo['products']['widget']
        frame = self.demo['products']['frame']
        wheel = self.demo['products']['wheel']

        # Opening capital.
        post_entry(
            date=self.day, description='Opening capital',
            lines=[
                {'account': self.accounts['cash'], 'debit': Decimal('10000')},
                {'account': self.accounts['capital'], 'credit': Decimal('10000')},
            ],
            created_by=self.user, source_reference='e2e:opening',
        )

        # Demand: 5 widgets wanted ten days out.
        so = SalesOrder.objects.create(
            number=DocumentSequence.next_number('SO'), customer=self.demo['customer'],
            warehouse=self.warehouse, order_date=self.day,
            requested_date=date(2026, 6, 11), created_by=self.user,
        )
        SalesOrderLine.objects.create(order=so, product=widget, quantity=Decimal('5'),
                                      unit_price=Decimal('250'))
        sales_services.confirm_sales_order(so, user=self.user)

        # Plan.
        run = MRPRun.objects.create(
            number=DocumentSequence.next_number('MRP'), warehouse=self.warehouse,
            as_of_date=self.day, created_by=self.user,
        )
        run_mrp(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')

        planned = list(run.planned_orders.order_by('id'))
        self.assertEqual(len(planned), 5)

        widget_plan = run.planned_orders.get(product=widget)
        self.assertEqual(widget_plan.order_type, 'make')
        self.assertEqual(widget_plan.quantity, Decimal('5.000'))
        self.assertEqual(widget_plan.due_date, date(2026, 6, 11))
        # Lead-time offset: widget lead time is 2 days.
        self.assertEqual(widget_plan.release_date, date(2026, 6, 9))
        self.assertFalse(widget_plan.expedited)

        # Safety-stock top-ups are due immediately and flagged expedited.
        frame_safety = run.planned_orders.get(product=frame, demand_reference='safety stock')
        self.assertEqual(frame_safety.quantity, Decimal('10.000'))
        self.assertTrue(frame_safety.expedited)
        wheel_safety = run.planned_orders.get(product=wheel, demand_reference='safety stock')
        self.assertEqual(wheel_safety.quantity, Decimal('20.000'))

        # Exploded component demand pegs back to the widget make order and is
        # due at its release date, offset again by each component's lead time.
        frame_so = run.planned_orders.get(product=frame, parent=widget_plan)
        self.assertEqual(frame_so.quantity, Decimal('5.000'))
        self.assertEqual(frame_so.due_date, date(2026, 6, 9))
        self.assertEqual(frame_so.release_date, date(2026, 6, 4))
        wheel_so = run.planned_orders.get(product=wheel, parent=widget_plan)
        self.assertEqual(wheel_so.quantity, Decimal('10.000'))
        self.assertEqual(wheel_so.release_date, date(2026, 6, 6))

        # Convert: one PO grouped at the shared supplier, one WO.
        converted = convert_run(run, user=self.user)
        self.assertEqual(len(converted['purchase_orders']), 1)
        self.assertEqual(len(converted['work_orders']), 1)
        po = converted['purchase_orders'][0]
        wo = converted['work_orders'][0]
        self.assertEqual(po.supplier, self.demo['supplier'])
        quantities = {line.product.sku: line.quantity for line in po.lines.all()}
        self.assertEqual(quantities, {'FRAME': Decimal('15.000'), 'WHEEL': Decimal('30.000')})
        self.assertTrue(all(o.status == 'converted' for o in run.planned_orders.all()))

        # Receive: stock in at PO price, Dr inventory / Cr supplier AP accrual.
        purchasing_services.confirm_purchase_order(po, user=self.user)
        receipt = purchasing_services.receive_purchase_order(po, user=self.user,
                                                             receipt_date=date(2026, 6, 5))
        po.refresh_from_db()
        self.assertEqual(po.status, 'received')
        self.assertEqual(get_on_hand(frame, self.warehouse), Decimal('15.000'))
        self.assertEqual(get_on_hand(wheel, self.warehouse), Decimal('30.000'))
        entry = receipt['journal_entry']
        self.assertEqual(sum(line.debit for line in entry.lines.all()), Decimal('1050.00'))
        self.assertEqual(self.balance(self.accounts['payable']), Decimal('1050.00'))

        # Produce: consume 5 frames + 10 wheels (350), receive 5 widgets at 70.
        manufacturing_services.confirm_work_order(wo, user=self.user)
        completion = manufacturing_services.complete_work_order(wo, user=self.user,
                                                                completion_date=date(2026, 6, 9))
        self.assertEqual(get_on_hand(frame, self.warehouse), Decimal('10.000'))
        self.assertEqual(get_on_hand(wheel, self.warehouse), Decimal('20.000'))
        self.assertEqual(get_on_hand(widget, self.warehouse), Decimal('5.000'))
        self.assertEqual(completion['receipt_move'].total_value, Decimal('350.00'))
        self.assertEqual(completion['receipt_move'].unit_cost, Decimal('70.0000'))

        # Ship: COGS at moving average, inventory relieved.
        shipment = sales_services.ship_sales_order(so, user=self.user, ship_date=date(2026, 6, 10))
        so.refresh_from_db()
        self.assertEqual(so.status, 'shipped')
        self.assertEqual(get_on_hand(widget, self.warehouse), Decimal('0.000'))
        self.assertEqual(self.balance(self.accounts['cogs']), Decimal('350.00'))
        self.assertEqual(shipment['journal_entry'].lines.count(), 2)

        # Bill and collect.
        invoice = sales_services.create_invoice_from_order(so, user=self.user)
        self.assertEqual(invoice.total, Decimal('1250.00'))
        post_invoice(invoice, user=self.user, posting_date=date(2026, 6, 10))
        self.assertEqual(self.balance(self.accounts['receivable']), Decimal('1250.00'))
        payment = Payment.objects.create(
            payment_reference=DocumentSequence.next_number('PAY'), invoice=invoice,
            amount=invoice.total, payment_method='bank_transfer',
            payment_date=date(2026, 6, 12), deposit_account=self.accounts['cash'],
            created_by=self.user,
        )
        post_payment(payment, user=self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        self.assertEqual(self.balance(self.accounts['receivable']), Decimal('0.00'))

        # Settle the supplier accrual.
        post_entry(
            date=date(2026, 6, 15), description=f'Supplier payment for {po.number}',
            lines=[
                {'account': self.demo['supplier'].payable_account, 'debit': Decimal('1050.00')},
                {'account': self.accounts['cash'], 'credit': Decimal('1050.00')},
            ],
            created_by=self.user, source_reference=f'e2e:{po.number}:payment',
        )

        # Final position: every subledger agrees with the GL and it all foots.
        self.assertEqual(self.balance(self.accounts['cash']), Decimal('10200.00'))
        self.assertEqual(self.balance(self.accounts['inventory']), Decimal('700.00'))
        self.assertEqual(self.balance(self.accounts['payable']), Decimal('0.00'))

        trial = generate_trial_balance({'as_of_date': '2026-06-30'})
        self.assertTrue(trial['balanced'])
        self.assertEqual(trial['total_debits'], Decimal('11250.00'))

        balance_sheet = generate_balance_sheet({'as_of_date': '2026-06-30'})
        self.assertTrue(balance_sheet['balanced'])
        self.assertEqual(balance_sheet['total_assets'], Decimal('10900.00'))
        self.assertEqual(balance_sheet['current_period_earnings'], Decimal('900.00'))

        income = generate_income_statement({'from_date': '2026-06-01', 'to_date': '2026-06-30'})
        self.assertEqual(income['net_income'], Decimal('900.00'))

        # The services wrote a real audit trail along the way.
        events = {row.details['event'] for row in AuditLog.objects.all() if row.details}
        self.assertLessEqual(
            {'sales_order.confirm', 'mrp_run.convert', 'purchase_order.receive',
             'work_order.complete', 'sales_order.ship', 'invoice.post', 'payment.post'},
            events,
        )

        # A second regenerative run finds a fully satisfied plan: safety stock
        # is back on hand and no open demand remains.
        rerun = MRPRun.objects.create(
            number=DocumentSequence.next_number('MRP'), warehouse=self.warehouse,
            as_of_date=date(2026, 6, 16), created_by=self.user,
        )
        run_mrp(rerun.id)
        rerun.refresh_from_db()
        self.assertEqual(rerun.status, 'completed')
        self.assertEqual(rerun.planned_orders.count(), 0)
