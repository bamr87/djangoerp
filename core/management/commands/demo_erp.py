"""
Run the whole ERP loop end to end on the demo company and prove the books
balance afterwards:

    chart of accounts -> products/BOM -> sales order -> MRP run -> planned
    orders -> purchase order + work order -> goods receipt -> production ->
    shipment -> customer invoice -> payment -> supplier payment -> financial
    statements (trial balance, balance sheet, income statement).

Every stage posts through the same services the API uses, so this is a live
integration check, not a fixture load. Exits non-zero if any statement fails
to balance. Safe to re-run: master data is get_or_create'd, documents are
created fresh each run, and the books stay balanced.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import AuditLog, UserRole
from core.demo import build_demo_company
from core.models import DocumentSequence
from inventory.models import StockLevel
from invoices.models import Payment
from invoices.services import post_invoice, post_payment
from journal.services import post_entry
from manufacturing import services as manufacturing_services
from mrp.engine import run_mrp
from mrp.models import MRPRun
from mrp.services import convert_run
from purchasing import services as purchasing_services
from reports.report_generators import generate_balance_sheet, generate_income_statement, generate_trial_balance
from sales import services as sales_services
from sales.models import SalesOrder, SalesOrderLine


class Command(BaseCommand):
    help = 'Demonstrate the full ERP loop end to end and verify the books balance.'

    def handle(self, *args, **options):
        today = timezone.now().date()

        user, created = User.objects.get_or_create(
            username='demo', defaults={'first_name': 'Demo', 'last_name': 'Accountant'}
        )
        if created:
            UserRole.objects.create(user=user, role='accountant')

        self.stdout.write(self.style.MIGRATE_HEADING('1. Master data'))
        demo = build_demo_company(created_by=user)
        accounts = demo['accounts']
        self.stdout.write(
            f"   COA {len(accounts)} accounts | partners {demo['customer'].code}/{demo['supplier'].code} "
            f"| warehouse {demo['warehouse'].code} | BOM {demo['bom']}"
        )

        opening = post_entry(
            date=today, description='Opening capital',
            lines=[
                {'account': accounts['cash'], 'debit': Decimal('10000')},
                {'account': accounts['capital'], 'credit': Decimal('10000')},
            ],
            created_by=user, source_reference='demo:opening',
        )
        self.stdout.write(f'   Opening capital journal entry {opening.entry_number}')

        self.stdout.write(self.style.MIGRATE_HEADING('2. Sales order (demand)'))
        so = SalesOrder.objects.create(
            number=DocumentSequence.next_number('SO'),
            customer=demo['customer'], warehouse=demo['warehouse'],
            order_date=today, requested_date=today + timedelta(days=10),
            created_by=user,
        )
        SalesOrderLine.objects.create(
            order=so, product=demo['products']['widget'],
            quantity=Decimal('5'), unit_price=Decimal('250'),
        )
        sales_services.confirm_sales_order(so, user=user)
        self.stdout.write(f'   {so.number}: 5 x WIDGET @ 250, requested {so.requested_date}')

        self.stdout.write(self.style.MIGRATE_HEADING('3. MRP run'))
        run = MRPRun.objects.create(
            number=DocumentSequence.next_number('MRP'),
            warehouse=demo['warehouse'], as_of_date=today, created_by=user,
        )
        run_mrp(run.id)
        run.refresh_from_db()
        if run.status != 'completed':
            raise CommandError(f'MRP run failed: {run.error_message}')
        for planned in run.planned_orders.all():
            flag = ' [expedite]' if planned.expedited else ''
            self.stdout.write(
                f'   plan {planned.order_type:<4} {planned.quantity:>8} {planned.product.sku:<7}'
                f' release {planned.release_date} due {planned.due_date}{flag}'
                f'  <- {planned.demand_reference}'
            )

        self.stdout.write(self.style.MIGRATE_HEADING('4. Convert plan and execute supply'))
        converted = convert_run(run, user=user)
        purchase_orders, work_orders = converted['purchase_orders'], converted['work_orders']
        for po in purchase_orders:
            purchasing_services.confirm_purchase_order(po, user=user)
            receipt = purchasing_services.receive_purchase_order(po, user=user)
            self.stdout.write(
                f'   {po.number} ({po.supplier.code}) confirmed, received as {receipt["grn_number"]}, '
                f'posted {receipt["journal_entry"].entry_number}'
            )
        for wo in work_orders:
            manufacturing_services.confirm_work_order(wo, user=user)
            completion = manufacturing_services.complete_work_order(wo, user=user)
            self.stdout.write(
                f'   {wo.number}: {wo.quantity} x {wo.product.sku} completed, '
                f'posted {completion["journal_entry"].entry_number}'
            )

        self.stdout.write(self.style.MIGRATE_HEADING('5. Ship, invoice, collect'))
        shipment = sales_services.ship_sales_order(so, user=user)
        self.stdout.write(
            f'   Shipped {shipment["shipment_number"]}, COGS posted {shipment["journal_entry"].entry_number}'
        )
        invoice = sales_services.create_invoice_from_order(so, user=user)
        post_invoice(invoice, user=user, posting_date=today)
        payment = Payment.objects.create(
            payment_reference=DocumentSequence.next_number('PAY'),
            invoice=invoice, amount=invoice.total, payment_method='bank_transfer',
            payment_date=today, deposit_account=accounts['cash'], created_by=user,
        )
        post_payment(payment, user=user)
        invoice.refresh_from_db()
        self.stdout.write(
            f'   {invoice.invoice_number} total {invoice.total} posted and {invoice.status} '
            f'via {payment.payment_reference}'
        )

        self.stdout.write(self.style.MIGRATE_HEADING('6. Pay the supplier accrual'))
        for po in purchase_orders:
            amount = sum((line.line_total for line in po.lines.all()), Decimal('0.00'))
            entry = post_entry(
                date=today, description=f'Supplier payment for {po.number}',
                lines=[
                    {'account': demo['supplier'].payable_account, 'debit': amount},
                    {'account': accounts['cash'], 'credit': amount},
                ],
                created_by=user, source_reference=f'demo:{po.number}:payment',
            )
            self.stdout.write(f'   Paid {amount} against {po.number} ({entry.entry_number})')

        self.stdout.write(self.style.MIGRATE_HEADING('7. Stock position'))
        for level in StockLevel.objects.filter(warehouse=demo['warehouse']).select_related('product'):
            self.stdout.write(
                f'   {level.product.sku:<7} on hand {level.quantity_on_hand:>8} '
                f'@ avg {level.average_cost}'
            )

        self.stdout.write(self.style.MIGRATE_HEADING('8. Financial statements'))
        as_of = str(today)
        trial = generate_trial_balance({'as_of_date': as_of})
        balance_sheet = generate_balance_sheet({'as_of_date': as_of})
        income = generate_income_statement({'to_date': as_of})
        self.stdout.write(
            f'   Trial balance: debits {trial["total_debits"]} credits {trial["total_credits"]} '
            f'balanced={trial["balanced"]}'
        )
        self.stdout.write(
            f'   Balance sheet: assets {balance_sheet["total_assets"]} '
            f'liabilities {balance_sheet["total_liabilities"]} equity {balance_sheet["total_equity"]} '
            f'(incl. current earnings {balance_sheet["current_period_earnings"]}) '
            f'balanced={balance_sheet["balanced"]}'
        )
        self.stdout.write(
            f'   Income statement: revenue {income["total_revenue"]} expenses {income["total_expenses"]} '
            f'net income {income["net_income"]}'
        )

        audit_rows = AuditLog.objects.count()
        self.stdout.write(f'   Audit trail rows: {audit_rows}')

        if not trial['balanced'] or not balance_sheet['balanced']:
            raise CommandError('End-to-end demo FAILED: statements do not balance')
        self.stdout.write(self.style.SUCCESS(
            'End-to-end demo complete: plan -> buy -> make -> ship -> bill -> collect, books balanced.'
        ))
