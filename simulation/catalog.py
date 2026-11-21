"""
The sandbox's design, as data: what each agent role is, decides and does,
which ERP services it calls and which accounts it moves; the posting matrix
with live counts; the invariants with their live status; and the business
flows with document counts. The console's "System map" renders this, so the
design it shows is the one the engine runs.
"""
from django.db.models import Count, Q

from invoices.models import Invoice, Payment
from journal.models import JournalEntry
from manufacturing.models import WorkOrder
from mrp.models import MRPRun
from purchasing.models import PurchaseOrder
from sales.models import SalesOrder

from . import finance
from . import parameters as params_module
from .models import Agent

ROLES = [
    {'role': 'market', 'phase': 1, 'cadence': 'review', 'resource': 'external',
     'decides': ['sentiment', 'competitor_price_index', 'narrative'],
     'acts': [], 'services': [], 'accounts': [],
     'summary': 'The non-deterministic economy: a sentiment random walk and the competitor price level every '
                'customer compares against.'},
    {'role': 'supplier', 'phase': 2, 'cadence': 'tick + review', 'resource': 'external',
     'decides': ['price_change_pct', 'reliability'],
     'acts': ['quote prices (index walk with inflation)', 'deliver due purchase orders or slip a day'],
     'services': ['purchasing.services.receive_purchase_order'], 'accounts': ['1200', '2100'],
     'summary': 'Prices drift with inflation and volatility; deliveries are a Bernoulli draw on reliability, '
                'which falls when the company pays late.'},
    {'role': 'maintenance', 'phase': 3, 'cadence': 'tick + review', 'resource': 'maintenance',
     'decides': ['pm_wear_threshold'],
     'acts': ['repair broken lines', 'service worn lines'], 'services': [], 'accounts': ['5340'],
     'summary': 'Repairs breakdowns (cost + downtime) and services lines past the wear threshold.'},
    {'role': 'customer', 'phase': 4, 'cadence': 'tick + review', 'resource': 'external',
     'decides': ['order_multiplier', 'extra_payment_delay_days'],
     'acts': ['place sales orders (Poisson demand)', 'pay invoices', 'cancel late orders'],
     'services': ['invoices.services.post_payment', 'sales.services.cancel_sales_order'],
     'accounts': ['1000', '1100'],
     'summary': 'Demand = base x season x trend x e^sentiment x (price/competitor)^elasticity x loyalty x '
                'log-normal shock, drawn Poisson. Loyalty follows on-time-in-full delivery.'},
    {'role': 'sales_manager', 'phase': 5, 'cadence': 'tick + review', 'resource': 'management',
     'decides': ['price_change_pct per product'],
     'acts': ['confirm or reject orders on credit', 'ship complete orders', 'invoice and post'],
     'services': ['sales.services.confirm_sales_order', 'sales.services.ship_sales_order',
                  'sales.services.create_invoice_from_order', 'invoices.services.post_invoice'],
     'accounts': ['1100', '1210', '4100', '4200', '5100', '5200'],
     'summary': 'Cost-plus floor at the target margin, rations capacity with price when backlog grows, '
                'discounts excess stock, otherwise drifts toward the market.'},
    {'role': 'buyer', 'phase': 6, 'cadence': 'tick + review', 'resource': 'management',
     'decides': ['safety_stock_days'],
     'acts': ['set safety stock', 'run MRP', 'convert due planned orders', 'price and confirm purchase orders'],
     'services': ['mrp.engine.run_mrp', 'mrp.services.convert_run',
                  'purchasing.services.confirm_purchase_order'],
     'accounts': [],
     'summary': 'Every tick: regenerative MRP at the simulated date, converting only planned orders whose '
                'release date has come.'},
    {'role': 'planner', 'phase': 8, 'cadence': 'tick + review', 'resource': 'indirect_labor',
     'decides': ['priority'],
     'acts': ['confirm work orders', 'queue them on the least-loaded line', 'kit and release jobs'],
     'services': ['manufacturing.services.confirm_work_order', 'manufacturing.services.start_work_order'],
     'accounts': ['5310'],
     'summary': 'Finite capacity on top of infinite-capacity MRP: jobs need line hours and a full kit.'},
    {'role': 'operator', 'phase': 9, 'cadence': 'tick', 'resource': 'direct_labor',
     'decides': [], 'acts': ['work the scheduled hours', 'complete work orders with absorbed conversion cost'],
     'services': ['manufacturing.services.complete_work_order'], 'accounts': ['1200', '1210', '5300', '5390'],
     'summary': 'Capacity = machines x shifts x hours x productivity x (1 - absence) - downtime; a completed '
                'job absorbs hours x the line rate into finished-goods value.'},
    {'role': 'machine', 'phase': 9, 'cadence': 'tick', 'resource': 'machine',
     'decides': [], 'acts': ['wear with use', 'break down (hazard rises with wear)', 'draw energy'],
     'services': [], 'accounts': ['5320', '5330'],
     'summary': 'Breakdown probability 1 - e^(-hazard x hours), hazard = base x (1 + 3 x wear).'},
    {'role': 'plant_manager', 'phase': 11, 'cadence': 'review', 'resource': 'management',
     'decides': ['shift_changes per line', 'commission_family'],
     'acts': ['add or drop shifts', 'buy a line when authorised'], 'services': [],
     'accounts': ['1500', '5300', '5310'],
     'summary': 'Adds a shift above 90% utilization with backlog, drops one below 45%.'},
    {'role': 'controller', 'phase': 12, 'cadence': 'tick + review', 'resource': 'management',
     'decides': ['oar_adjust_pct per line', 'cash_buffer', 'commentary'],
     'acts': ['post payroll, overheads, AI compute and depreciation', 'pay due suppliers',
              'draw and repay the revolver', 'compile statements and check guardrails'],
     'services': ['journal.services.post_entry', 'reports.report_generators'],
     'accounts': ['1000', '1590', '2100', '2400', '5300', '5310', '5320', '5330', '5340', '6100', '6200',
                  '6300', '6400'],
     'summary': 'Sets absorption rates from budgeted line cost over normal capacity and explains the '
                'variance between actual and absorbed conversion cost.'},
]

POSTINGS = [
    {'event': 'Goods receipt', 'debit': 'Inventory 1200', 'credit': 'Accounts payable 2100',
     'prefix': 'grn:', 'by': 'supplier -> purchasing.services.receive_purchase_order'},
    {'event': 'Production completion', 'debit': 'Finished goods 1210',
     'credit': 'Inventory 1200 + absorbed 5390', 'prefix': 'wo:',
     'by': 'operator -> manufacturing.services.complete_work_order(conversion_costs=...)'},
    {'event': 'Shipment', 'debit': 'COGS 5100/5200', 'credit': 'Finished goods 1210', 'prefix': 'shipment:',
     'by': 'sales manager -> sales.services.ship_sales_order'},
    {'event': 'Customer invoice', 'debit': 'Receivables 1100', 'credit': 'Sales 4100/4200', 'prefix': 'invoice:',
     'by': 'sales manager -> invoices.services.post_invoice'},
    {'event': 'Customer payment', 'debit': 'Cash 1000', 'credit': 'Receivables 1100', 'prefix': 'payment:',
     'by': 'customer -> invoices.services.post_payment'},
    {'event': 'Period costs', 'debit': 'Labor, overhead, maintenance, management, facility, AI, interest',
     'credit': 'Cash 1000', 'prefix': 'sim:{id}:t', 'suffix': ':costs', 'by': 'controller -> post_entry'},
    {'event': 'Depreciation', 'debit': 'Depreciation 5330', 'credit': 'Accumulated depreciation 1590',
     'prefix': 'sim:{id}:t', 'suffix': ':depreciation', 'by': 'controller -> post_entry'},
    {'event': 'Supplier payment', 'debit': 'Accounts payable 2100', 'credit': 'Cash 1000',
     'prefix': 'sim:{id}:pay:', 'by': 'controller -> post_entry'},
    {'event': 'Revolver draw / repayment', 'debit': 'Cash 1000 / Revolver 2400',
     'credit': 'Revolver 2400 / Cash 1000', 'prefix': 'sim:{id}:t', 'suffix': 'revolver',
     'by': 'controller -> post_entry'},
    {'event': 'Capex', 'debit': 'Machinery 1500', 'credit': 'Cash 1000', 'prefix': 'sim:{id}:capex:',
     'by': 'plant manager -> simulation.world.commission_line'},
]

INVARIANTS = [
    ('Double entry', 'Every posting goes through journal.services.post_entry / validate_lines; the trial '
                     'balance foots.', 'trial_balance'),
    ('The balance sheet balances', 'Assets = liabilities + equity + current-period earnings '
                                   '(reports.report_generators.generate_balance_sheet).', 'balance_sheet'),
    ('Stock ledger = GL', 'Inventory accounts equal the signed sum of done stock moves.', 'stock'),
    ('Receivables subledger = GL', 'Posted invoices less posted payments equal partner receivables.',
     'receivables'),
    ('Payables subledger = GL', 'Open goods-receipt accruals equal account 2100.', 'payables'),
    ('Asset register = GL', 'Commissioned line capex equals account 1500.', 'fixed_assets'),
    ('Sandbox only', 'Agents act only where settings.SIMULATION_SANDBOX is on; production refuses.', None),
    ('Services only', 'Agents never write models the services own: every status change and posting is a '
                      'service call, audit-logged under the agent\'s own user.', None),
    ('Bounded AI', 'A Claude decision is a set of bounded numbers or choices; out-of-range values are clamped, '
                   'invalid answers fall back to the heuristic, and every call\'s tokens are posted to 6300.', None),
]


def posting_counts(scenario):
    counts = []
    for row in POSTINGS:
        prefix = row['prefix'].replace('{id}', str(scenario.pk))
        query = JournalEntry.objects.filter(status='posted', source_reference__startswith=prefix)
        if row.get('suffix'):
            query = query.filter(source_reference__contains=row['suffix'])
        counts.append({**{k: v for k, v in row.items() if k != 'suffix'}, 'prefix': prefix, 'count': query.count()})
    return counts


def _status_counts(queryset):
    return dict(queryset.values_list('status').annotate(n=Count('id')))


def flows(scenario):
    warehouse = scenario.warehouse
    return [
        {'flow': 'Order to cash', 'steps': [
            {'document': 'Sales orders', 'counts': _status_counts(SalesOrder.objects.filter(warehouse=warehouse))},
            {'document': 'Invoices',
             'counts': _status_counts(Invoice.objects.filter(sales_order__warehouse=warehouse))},
            {'document': 'Payments', 'counts': {'posted': Payment.objects.filter(
                invoice__sales_order__warehouse=warehouse, journal_entry__isnull=False).count()}},
        ]},
        {'flow': 'Procure to pay', 'steps': [
            {'document': 'MRP runs', 'counts': _status_counts(MRPRun.objects.filter(warehouse=warehouse))},
            {'document': 'Purchase orders',
             'counts': _status_counts(PurchaseOrder.objects.filter(warehouse=warehouse))},
            {'document': 'Supplier payables', 'counts': {
                'open': scenario.payables.filter(paid_on__isnull=True).count(),
                'paid': scenario.payables.filter(paid_on__isnull=False).count()}},
        ]},
        {'flow': 'Make to stock', 'steps': [
            {'document': 'Work orders', 'counts': _status_counts(WorkOrder.objects.filter(warehouse=warehouse))},
            {'document': 'Production jobs', 'counts': _status_counts(scenario.jobs.all())},
        ]},
    ]


def design(scenario, guard=None):
    guard = guard or finance.guardrails(scenario, scenario.current_date, full=False)
    status = {'trial_balance': guard['trial_balance']['balanced'], 'balance_sheet': guard['balance_sheet']['balanced'],
              **{c['key']: c['ok'] for c in guard['reconciliations']}}
    agents = Agent.objects.filter(scenario=scenario).values('role').annotate(
        n=Count('id', distinct=True), n_decisions=Count('decisions'),
        n_llm=Count('decisions', filter=Q(decisions__driver='llm')))
    by_role = {row['role']: row for row in agents}
    values = params_module.resolve(scenario.parameters)
    return {
        'roles': [{**role, 'label': dict(Agent.ROLE_CHOICES)[role['role']],
                   'agents': by_role.get(role['role'], {}).get('n', 0),
                   'decisions': by_role.get(role['role'], {}).get('n_decisions', 0),
                   'claude_decisions': by_role.get(role['role'], {}).get('n_llm', 0),
                   'parameters': [p['key'] for p in params_module.PARAMETERS if role['role'] in p['drives']]}
                  for role in ROLES],
        'parameters': [{**p, 'value': values.get(p['key'])} for p in params_module.PARAMETERS],
        'postings': posting_counts(scenario),
        'invariants': [{'name': name, 'rule': rule, 'ok': status.get(key) if key else True}
                       for name, rule, key in INVARIANTS],
        'flows': flows(scenario),
        'accounts': [{'code': code, 'name': name, 'type': type_code, 'key': key}
                     for code, name, type_code, key in finance.ACCOUNTS],
        'cost_drivers': [{'key': k, 'label': label, 'account': finance.CODE[k], 'driven_by': role}
                         for k, label, role in finance.COST_DRIVERS],
    }
