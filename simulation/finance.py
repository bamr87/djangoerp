"""
The financial backbone of the sandbox: ledger aggregates, the sandbox chart of
accounts, KPI compilation from the journal, and the guardrails — the trial
balance and balance sheet from reports.report_generators, plus four
subledger-to-GL reconciliations (stock ledger, receivables, payables, fixed
asset register). Agents may decide anything; these numbers are only ever
compiled from posted journal lines.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from coa.models import Account, AccountType
from inventory.models import StockMove
from invoices.models import Invoice, Payment
from journal.models import JournalLine
from journal.services import post_entry
from partners.models import BusinessPartner
from products.models import ProductCategory
from reports.report_generators import generate_balance_sheet, generate_trial_balance

CENT = Decimal('0.01')
ZERO = Decimal('0.00')

# (code, name, account type, key). get_or_create by code: codes the demo company already
# holds (1000, 1100, 1200, 2100, 3000) are shared, so the sandbox continues those books.
ACCOUNTS = [
    ('1000', 'Cash', 'AS', 'cash'),
    ('1100', 'Accounts Receivable', 'AS', 'receivable'),
    ('1200', 'Inventory', 'AS', 'raw_materials'),
    ('1210', 'Finished goods inventory', 'AS', 'finished_goods'),
    ('1500', 'Machinery and equipment', 'AS', 'machinery'),
    ('1590', 'Accumulated depreciation', 'AS', 'accumulated_depreciation'),
    ('2100', 'Accounts Payable', 'LI', 'payable'),
    ('2400', 'Revolving credit facility', 'LI', 'revolver'),
    ('3000', 'Share Capital', 'EQ', 'capital'),
    ('4100', 'Sales - product line A', 'RE', 'revenue_a'),
    ('4200', 'Sales - product line B', 'RE', 'revenue_b'),
    ('5100', 'Cost of goods sold - line A', 'EX', 'cogs_a'),
    ('5200', 'Cost of goods sold - line B', 'EX', 'cogs_b'),
    ('5300', 'Direct labor', 'EX', 'direct_labor'),
    ('5310', 'Indirect labor', 'EX', 'indirect_labor'),
    ('5320', 'Variable overhead (energy and consumables)', 'EX', 'variable_overhead'),
    ('5330', 'Depreciation - machinery', 'EX', 'depreciation'),
    ('5340', 'Maintenance and repairs', 'EX', 'maintenance'),
    ('5390', 'Production costs absorbed into inventory', 'EX', 'absorbed'),
    ('6100', 'Management salaries', 'EX', 'management'),
    ('6200', 'Facility overhead', 'EX', 'facility'),
    ('6300', 'AI agent compute', 'EX', 'ai_compute'),
    ('6400', 'Interest expense', 'EX', 'interest'),
    ('6500', 'Loss on disposal of equipment', 'EX', 'disposal_loss'),
]
CODE = {key: code for code, _name, _type, key in ACCOUNTS}

# The cost drivers an agent resource maps to, in the order the console lists them.
COST_DRIVERS = [
    ('direct_labor', 'Direct labor', 'operator'),
    ('indirect_labor', 'Indirect labor', 'planner'),
    ('variable_overhead', 'Variable overhead', 'machine'),
    ('depreciation', 'Machine depreciation', 'machine'),
    ('maintenance', 'Maintenance', 'maintenance'),
    ('management', 'Management', 'plant_manager'),
    ('facility', 'Facility overhead', 'controller'),
    ('ai_compute', 'AI agent compute', 'controller'),
    ('interest', 'Interest', 'controller'),
]


def q(value):
    return Decimal(value).quantize(CENT)


def ensure_accounts():
    """Create the sandbox chart of accounts (idempotent). Returns {key: Account}."""
    types = {t.code: t for t in AccountType.objects.all()}
    result = {}
    for code, name, type_code, key in ACCOUNTS:
        account, _ = Account.objects.get_or_create(code=code, defaults={'name': name, 'account_type': types[type_code]})
        result[key] = account
    return result


def accounts():
    """{key: Account} for the sandbox chart (assumes ensure_accounts ran)."""
    by_code = {a.code: a for a in Account.objects.filter(code__in=CODE.values())}
    return {key: by_code[code] for key, code in CODE.items() if code in by_code}


def post(*, date, description, lines, source_reference, user=None):
    """System posting through journal.services.post_entry (idempotent per source_reference)."""
    return post_entry(
        date=date, description=description, lines=lines, created_by=user, source_reference=source_reference,
    )


# --- Ledger aggregates ----------------------------------------------------------------------------

def _posted(**filters):
    return JournalLine.objects.filter(entry__status='posted', **filters)


def balances(as_of):
    """{account code: debit - credit} over posted lines dated on or before as_of."""
    rows = _posted(entry__date__lte=as_of).values('account__code').annotate(d=Sum('debit'), c=Sum('credit'))
    return {row['account__code']: (row['d'] or ZERO) - (row['c'] or ZERO) for row in rows}


def movements(after, through):
    """{account code: debit - credit} over posted lines dated in (after, through]."""
    rows = _posted(entry__date__gt=after, entry__date__lte=through).values('account__code').annotate(
        d=Sum('debit'), c=Sum('credit'))
    return {row['account__code']: (row['d'] or ZERO) - (row['c'] or ZERO) for row in rows}


def account_types():
    return dict(Account.objects.values_list('code', 'account_type__code'))


def inventory_account_codes():
    return set(ProductCategory.objects.exclude(inventory_account=None)
               .values_list('inventory_account__code', flat=True))


def receivable_account_codes():
    return set(BusinessPartner.objects.exclude(receivable_account=None)
               .values_list('receivable_account__code', flat=True))


def stock_ledger_value(as_of):
    """Signed sum of done stock-move values: the stock ledger's own valuation."""
    moves = StockMove.objects.filter(status='done', move_date__lte=as_of)
    inbound = moves.filter(move_type__in=StockMove.INBOUND_TYPES).aggregate(v=Sum('total_value'))['v'] or ZERO
    outbound = moves.filter(move_type__in=StockMove.OUTBOUND_TYPES).aggregate(v=Sum('total_value'))['v'] or ZERO
    return inbound - outbound


def receivables_subledger(as_of):
    """Posted invoices less posted payments: what customers owe according to the documents."""
    invoiced = Invoice.objects.filter(journal_entry__isnull=False, journal_entry__date__lte=as_of).aggregate(
        v=Sum('total'))['v'] or ZERO
    paid = Payment.objects.filter(journal_entry__isnull=False, journal_entry__date__lte=as_of).aggregate(
        v=Sum('amount'))['v'] or ZERO
    return invoiced - paid


def reconciliation_totals(scenario, as_of):
    """
    The four subledger/GL pairs. Each guardrail compares movement since the
    scenario's bootstrap (`state['baseline']`), so books that held other activity
    before the sandbox started do not trip them.
    """
    from .models import ProductionLine, SupplierPayable

    bal = balances(as_of)
    inv_codes = inventory_account_codes()
    ar_codes = receivable_account_codes()
    payables = SupplierPayable.objects.filter(scenario=scenario, received_on__lte=as_of).filter(
        Q(paid_on__isnull=True) | Q(paid_on__gt=as_of))
    lines = ProductionLine.objects.filter(scenario=scenario, commissioned_on__lte=as_of).filter(
        Q(retired_on__isnull=True) | Q(retired_on__gt=as_of))
    return {
        'gl_inventory': sum((bal.get(code, ZERO) for code in inv_codes), ZERO),
        'stock_ledger': stock_ledger_value(as_of),
        'gl_receivables': sum((bal.get(code, ZERO) for code in ar_codes), ZERO),
        'receivables_subledger': receivables_subledger(as_of),
        'gl_payables': -bal.get(CODE['payable'], ZERO),
        'payables_subledger': payables.aggregate(v=Sum('amount'))['v'] or ZERO,
        'gl_machinery': bal.get(CODE['machinery'], ZERO),
        'asset_register': lines.aggregate(v=Sum('capex'))['v'] or ZERO,
    }


def baseline_totals(scenario, as_of):
    """Snapshot of reconciliation_totals before the sandbox posts anything (as strings, for JSON)."""
    return {key: str(value) for key, value in reconciliation_totals(scenario, as_of).items()}


RECONCILIATIONS = [
    ('stock', 'Stock ledger = GL inventory', 'gl_inventory', 'stock_ledger',
     'Sum of done stock-move values (inventory.StockMove) against inventory accounts 1200/1210.'),
    ('receivables', 'Receivables subledger = GL AR', 'gl_receivables', 'receivables_subledger',
     'Posted invoices less posted payments against partner receivable accounts.'),
    ('payables', 'Payables subledger = GL AP', 'gl_payables', 'payables_subledger',
     'Open goods-receipt accruals (SupplierPayable) against account 2100.'),
    ('fixed_assets', 'Asset register = GL machinery', 'gl_machinery', 'asset_register',
     'Capex of the commissioned production lines against account 1500.'),
]


def quick_statements(as_of):
    """
    The trial balance and balance sheet identities from one aggregate query:
    total debits = total credits, and assets = liabilities + equity + open P&L.
    Same arithmetic as the report generators, a fraction of the queries; the
    engine cross-checks it against them on review ticks.
    """
    rows = _posted(entry__date__lte=as_of).values('account__account_type__code').annotate(
        d=Sum('debit'), c=Sum('credit'))
    by_type = {row['account__account_type__code']: (row['d'] or ZERO, row['c'] or ZERO) for row in rows}
    debits = sum((d for d, _c in by_type.values()), ZERO)
    credits = sum((c for _d, c in by_type.values()), ZERO)

    def net(code, debit_normal):
        d, c = by_type.get(code, (ZERO, ZERO))
        return d - c if debit_normal else c - d

    assets, liabilities, equity = net('AS', True), net('LI', False), net('EQ', False)
    earnings = net('RE', False) - net('EX', True)
    return (
        {'total_debits': debits, 'total_credits': credits, 'balanced': debits == credits},
        {'total_assets': assets, 'total_liabilities': liabilities, 'total_equity': equity + earnings,
         'current_period_earnings': earnings, 'balanced': assets == liabilities + equity + earnings},
    )


def guardrails(scenario, as_of, *, full=True):
    """
    Compile the statements and reconcile every subledger. With `full` the
    trial balance and balance sheet come from reports.report_generators — the
    same code the reports API runs; without it, from quick_statements. Returns
    {'ok', 'source', 'trial_balance', 'balance_sheet', 'reconciliations'}; `ok`
    is False when anything is off by a cent.
    """
    if full:
        trial = generate_trial_balance({'as_of_date': str(as_of)})
        sheet = generate_balance_sheet({'as_of_date': str(as_of)})
    else:
        trial, sheet = quick_statements(as_of)
    totals = reconciliation_totals(scenario, as_of)
    base = {key: Decimal(value) for key, value in (scenario.state.get('baseline') or {}).items()}
    checks = []
    for key, label, gl_key, sub_key, explanation in RECONCILIATIONS:
        gl = totals[gl_key] - base.get(gl_key, ZERO)
        sub = totals[sub_key] - base.get(sub_key, ZERO)
        checks.append({
            'key': key, 'label': label, 'explanation': explanation,
            'gl': float(gl), 'subledger': float(sub), 'difference': float(q(gl - sub)), 'ok': q(gl - sub) == ZERO,
        })
    ok = bool(trial['balanced'] and sheet['balanced'] and all(c['ok'] for c in checks))
    return {
        'ok': ok,
        'source': 'reports.report_generators' if full else 'ledger aggregate',
        'trial_balance': {
            'balanced': bool(trial['balanced']),
            'total_debits': float(trial['total_debits']), 'total_credits': float(trial['total_credits']),
        },
        'balance_sheet': {
            'balanced': bool(sheet['balanced']),
            'total_assets': float(sheet['total_assets']), 'total_liabilities': float(sheet['total_liabilities']),
            'total_equity': float(sheet['total_equity']),
            'current_period_earnings': float(sheet['current_period_earnings']),
        },
        'reconciliations': checks,
    }


# --- KPI compilation ------------------------------------------------------------------------------

def _f(value):
    return round(float(value), 2)


def profit_and_loss(move_by_code, types):
    """
    Income-statement view of a {code: debit - credit} map. Revenue and expense
    totals cover every RE/EX account (the demo company's included); the named
    drivers read the sandbox accounts.
    """
    def credit(key):
        return -move_by_code.get(CODE[key], ZERO)

    def debit(key):
        return move_by_code.get(CODE[key], ZERO)

    revenue = sum((-v for code, v in move_by_code.items() if types.get(code) == 'RE'), ZERO)
    expenses = sum((v for code, v in move_by_code.items() if types.get(code) == 'EX'), ZERO)
    cogs = debit('cogs_a') + debit('cogs_b')
    conversion = (debit('direct_labor') + debit('indirect_labor') + debit('variable_overhead')
                  + debit('depreciation') + debit('maintenance'))
    absorbed = credit('absorbed')
    period_costs = (debit('management') + debit('facility') + debit('ai_compute') + debit('interest')
                    + debit('disposal_loss'))
    gross_margin = revenue - cogs
    return {
        'revenue': _f(revenue), 'revenue_a': _f(credit('revenue_a')), 'revenue_b': _f(credit('revenue_b')),
        'cogs': _f(cogs), 'cogs_a': _f(debit('cogs_a')), 'cogs_b': _f(debit('cogs_b')),
        'gross_margin': _f(gross_margin),
        'gross_margin_pct': _f(gross_margin / revenue * 100) if revenue else 0.0,
        'direct_labor': _f(debit('direct_labor')), 'indirect_labor': _f(debit('indirect_labor')),
        'variable_overhead': _f(debit('variable_overhead')), 'depreciation': _f(debit('depreciation')),
        'maintenance': _f(debit('maintenance')),
        'conversion_actual': _f(conversion), 'absorbed': _f(absorbed),
        'under_absorbed': _f(conversion - absorbed),
        'management': _f(debit('management')), 'facility': _f(debit('facility')),
        'ai_compute': _f(debit('ai_compute')), 'interest': _f(debit('interest')),
        'disposal_loss': _f(debit('disposal_loss')),
        'period_costs': _f(period_costs),
        'total_expenses': _f(expenses),
        'net_income': _f(revenue - expenses),
    }


def balance_position(bal_by_code, types):
    def sign(code_type):
        return sum((v for code, v in bal_by_code.items() if types.get(code) == code_type), ZERO)

    assets = sign('AS')
    liabilities = -sign('LI')
    ar = sum((bal_by_code.get(code, ZERO) for code in receivable_account_codes()), ZERO)
    return {
        'cash': _f(bal_by_code.get(CODE['cash'], ZERO)),
        'receivables': _f(ar),
        'inventory_materials': _f(bal_by_code.get(CODE['raw_materials'], ZERO)),
        'inventory_finished': _f(bal_by_code.get(CODE['finished_goods'], ZERO)),
        'fixed_assets_net': _f(bal_by_code.get(CODE['machinery'], ZERO)
                               + bal_by_code.get(CODE['accumulated_depreciation'], ZERO)),
        'payables': _f(-bal_by_code.get(CODE['payable'], ZERO)),
        'revolver': _f(-bal_by_code.get(CODE['revolver'], ZERO)),
        'total_assets': _f(assets),
        'total_liabilities': _f(liabilities),
        'equity': _f(assets - liabilities),
    }


def cost_breakdown(move_by_code):
    """Cost by driver for a period, with the agent role that drives it and its GL account."""
    rows = []
    for key, label, role in COST_DRIVERS:
        rows.append({
            'key': key, 'label': label, 'account': CODE[key], 'driven_by': role,
            'amount': _f(move_by_code.get(CODE[key], ZERO)),
        })
    return rows


def unit_economics(after, through):
    """
    Production cost per unit by finished good over (after, through]: the receipt
    value of completed production (components plus absorbed conversion) over
    the units made. Reads the stock ledger, whose values the GL posted.
    """
    rows = StockMove.objects.filter(
        status='done', move_type='production_receipt', move_date__gt=after, move_date__lte=through,
    ).values('product__sku').annotate(qty=Sum('quantity'), value=Sum('total_value'))
    return {
        row['product__sku']: {
            'units': float(row['qty']), 'value': _f(row['value']),
            'unit_cost': _f(row['value'] / row['qty']) if row['qty'] else 0.0,
        }
        for row in rows
    }


def per_family_totals(move_by_code):
    """Revenue/COGS by product family, for margin by line."""
    return {
        'A': {'revenue': _f(-move_by_code.get(CODE['revenue_a'], ZERO)),
              'cogs': _f(move_by_code.get(CODE['cogs_a'], ZERO))},
        'B': {'revenue': _f(-move_by_code.get(CODE['revenue_b'], ZERO)),
              'cogs': _f(move_by_code.get(CODE['cogs_b'], ZERO))},
    }


def sum_maps(maps):
    total = defaultdict(lambda: ZERO)
    for m in maps:
        for key, value in m.items():
            total[key] += value
    return dict(total)
