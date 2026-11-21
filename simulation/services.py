"""
The sandbox's transitions and read models — the single entry point for the
API, the console and the `simulate` command. Transitions audit-log through
core.audit.log_event; reads compile from the ledger and the report generators.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, TextField
from django.db.models.functions import Cast
from rest_framework import serializers

from accounts.models import AuditLog
from coa.models import Account
from core.audit import log_event
from inventory.models import StockLevel
from journal.models import JournalEntry
from reports.report_generators import (generate_balance_sheet, generate_general_ledger, generate_income_statement,
                                       generate_trial_balance)

from . import catalog, engine, experiments, finance
from . import parameters as params_module
from . import world
from .models import AgentDecision, TickSnapshot


def create_scenario(*, user=None, **kwargs):
    """Bootstrap the sandbox economy and write its opening (tick 0) snapshot."""
    with transaction.atomic():
        scenario = world.create_scenario(user=user, **kwargs)
        TickSnapshot.objects.create(
            scenario=scenario, tick=0, sim_date=scenario.start_date,
            kpis=engine.compile_snapshot(scenario, tick=0, prev_date=scenario.start_date, date=scenario.start_date),
        )
    return scenario


def update_scenario(scenario, data, user=None):
    """Change the controls: driver, agent model, tick length, description, parameters (merged, validated)."""
    changed = {}
    if 'parameters' in data:
        world.require_sandbox()
        cleaned = params_module.validate(data['parameters'])
        merged = dict(scenario.parameters)
        for key, value in cleaned.items():
            old = merged.get(key)
            merged[key] = {**(old or {}), **value} if isinstance(value, dict) else value
            if merged[key] != old:
                changed[key] = {'from': old, 'to': merged[key]}
        scenario.parameters = params_module.resolve(merged)
    for field in ('driver', 'agent_model', 'tick_days', 'description'):
        if field in data and data[field] != getattr(scenario, field):
            changed[field] = {'from': getattr(scenario, field), 'to': data[field]}
            setattr(scenario, field, data[field])
    if not changed:
        return scenario
    with transaction.atomic():
        scenario.save()
        if 'parameters' in data:
            world.refresh_absorption_rates(scenario)
        log_event(user, 'simulation.controls', scenario, {'changes': changed, 'tick': scenario.tick})
    return scenario


def advance(scenario, ticks, user=None):
    if not 1 <= ticks <= 120:
        raise serializers.ValidationError('ticks must be between 1 and 120 per request')
    return engine.advance(scenario, ticks, user=user)


def resume(scenario, user=None):
    if scenario.status != 'halted':
        raise serializers.ValidationError('Only a halted scenario can be resumed')
    scenario.status = 'ready'
    reason, scenario.error_message = scenario.error_message, ''
    scenario.save(update_fields=['status', 'error_message', 'updated_at'])
    log_event(user, 'simulation.resume', scenario, {'halt_reason': reason[:500]})
    return scenario


def commission_line(scenario, data, user=None):
    world.require_sandbox()
    return world.commission_line(scenario, date=scenario.current_date, user=user, **data)


def retire_line(line, user=None):
    world.require_sandbox()
    return world.decommission_line(line.scenario, line, date=line.scenario.current_date, user=user)


def update_line(line, data, user=None):
    """Operational changes to a running line: shifts and crew size; the controller re-rates absorption."""
    if line.status == 'retired':
        raise serializers.ValidationError(f'{line.code} is retired')
    values = params_module.resolve(line.scenario.parameters)
    changed = {}
    if 'shifts' in data:
        shifts = int(data['shifts'])
        if not 1 <= shifts <= int(values['max_shifts']):
            raise serializers.ValidationError({'shifts': f'between 1 and {values["max_shifts"]}'})
        changed['shifts'] = {'from': line.shifts, 'to': shifts}
        line.shifts = shifts
    if 'crew_per_machine' in data:
        crew = Decimal(str(data['crew_per_machine']))
        if crew <= 0:
            raise serializers.ValidationError({'crew_per_machine': 'must be positive'})
        changed['crew_per_machine'] = {'from': str(line.crew_per_machine), 'to': str(crew)}
        line.crew_per_machine = crew
    with transaction.atomic():
        line.save()
        world.refresh_absorption_rates(line.scenario, values)
        log_event(user, 'simulation.line.update', line, {'changes': changed})
    return line


def start_experiment(experiment):
    """Dispatch the what-if (eager in dev: it has run when this returns)."""
    world.require_sandbox()
    experiments.validate_changes(experiment.changes)
    experiments.run_experiment_task.delay(experiment.id)
    experiment.refresh_from_db()
    return experiment


# --- read models ----------------------------------------------------------------------------------

SERIES_PL = ['revenue', 'cogs', 'gross_margin', 'net_income', 'conversion_actual', 'absorbed', 'under_absorbed',
             'direct_labor', 'indirect_labor', 'variable_overhead', 'depreciation', 'maintenance', 'management',
             'facility', 'ai_compute', 'interest']
SERIES_BS = ['cash', 'receivables', 'inventory_materials', 'inventory_finished', 'payables', 'revolver', 'equity']
SERIES_OPS = ['units_ordered', 'units_shipped', 'units_produced', 'units_cancelled', 'backlog_units', 'late_orders',
              'otif_pct', 'demand_potential']


def series_point(k):
    point = {'tick': k['tick'], 'date': k['date'], 'ok': k['guardrails']['ok']}
    point.update({key: k['pl'].get(key, 0) for key in SERIES_PL})
    point.update({key: k['bs'].get(key, 0) for key in SERIES_BS})
    point.update({key: k['ops'].get(key, 0) for key in SERIES_OPS})
    point['sentiment'] = k['market'].get('sentiment', 0)
    point['competitor_price_index'] = k['market'].get('competitor_price_index', 1)
    point['season_factor'] = k['market'].get('season_factor', 1)
    point['utilization'] = {code: line['utilization'] for code, line in k['lines'].items()
                            if line['status'] != 'retired'}
    point['unit_cost'] = {sku: s['unit_cost'] for sku, s in k['skus'].items()}
    point['price'] = {sku: s['price'] for sku, s in k['skus'].items()}
    point['ai_cost'] = k['ai'].get('ai_cost', 0)
    point['llm_calls'] = k['ai'].get('llm_calls', 0)
    return point


def decision_row(d):
    return {
        'id': d.id, 'tick': d.tick, 'date': str(d.sim_date), 'agent': d.agent.code, 'role': d.agent.role,
        'kind': d.kind, 'action': d.action, 'summary': d.summary, 'rationale': d.rationale, 'driver': d.driver,
        'status': d.status, 'error': d.error, 'documents': d.documents, 'model': d.model, 'cost': float(d.cost),
    }


def dashboard(scenario, limit=240):
    snaps = list(scenario.snapshots.order_by('-tick')[:limit])[::-1]
    latest = snaps[-1].kpis if snaps else None
    lines = []
    for line in scenario.lines.select_related('family').annotate(
            queued=Count('jobs', filter=Q(jobs__status='queued')),
            running=Count('jobs', filter=Q(jobs__status='running'))):
        lines.append({
            'id': line.id, 'code': line.code, 'name': line.name, 'family': world.family_of_line(line),
            'status': line.status, 'machines': line.machines, 'shifts': line.shifts,
            'crew_per_machine': float(line.crew_per_machine), 'capex': float(line.capex),
            'accumulated_depreciation': float(line.accumulated_depreciation), 'wear': float(line.wear),
            'run_hours': float(line.run_hours), 'commissioned_on': str(line.commissioned_on),
            'jobs_queued': line.queued, 'jobs_running': line.running,
            'latest': (latest or {}).get('lines', {}).get(line.code),
        })
    agents = [{
        'id': a.id, 'code': a.code, 'name': a.name, 'role': a.role, 'role_label': a.get_role_display(),
        'resource_kind': a.resource_kind, 'brain': a.brain,
        'effective_brain': ('llm' if a.brain == 'llm' or (a.brain == 'auto' and scenario.driver == 'llm')
                            else 'heuristic') if a.role not in ('operator', 'machine') else 'physics',
        'model': a.model or scenario.agent_model, 'daily_cost': float(a.daily_cost),
        'cost_account': a.cost_account.code if a.cost_account_id else None, 'decision_interval': a.decision_interval,
        'decisions': a.decisions_count, 'llm_calls': a.llm_calls, 'ai_cost': float(a.ai_cost),
        'policy': a.policy, 'line': a.line.code if a.line_id else None, 'is_active': a.is_active,
    } for a in scenario.agents.select_related('line', 'cost_account')]
    decisions = [decision_row(d) for d in AgentDecision.objects.filter(scenario=scenario)
                 .select_related('agent').order_by('-id')[:60]]
    stock = [{
        'sku': lvl.product.sku, 'name': lvl.product.name, 'on_hand': float(lvl.quantity_on_hand),
        'average_cost': float(lvl.average_cost), 'value': round(float(lvl.quantity_on_hand * lvl.average_cost), 2),
        'safety_stock': float(lvl.product.safety_stock), 'price': float(lvl.product.sales_price),
        'procurement': lvl.product.procurement_type,
    } for lvl in StockLevel.objects.filter(warehouse=scenario.warehouse).select_related('product')
        .order_by('product__procurement_type', 'product__sku')]
    jobs = [{
        'work_order': job.work_order.number, 'sku': job.work_order.product.sku,
        'quantity': float(job.work_order.quantity),
        'line': job.line.code, 'status': job.status, 'hours_required': float(job.hours_required),
        'hours_done': float(job.hours_done), 'due': str(job.work_order.due_date) if job.work_order.due_date else None,
    } for job in scenario.jobs.filter(status__in=('queued', 'running')).select_related('work_order__product', 'line')]
    controls = [{
        'when': entry.timestamp.isoformat(), 'user': entry.user.username if entry.user_id else None,
        'event': entry.details.get('event'), 'changes': entry.details.get('changes', {}),
        'tick': entry.details.get('tick'),
    } for entry in AuditLog.objects.filter(model_name='simulation.Scenario', object_id=str(scenario.pk),
                                           details__event__in=['simulation.controls', 'simulation.resume'])
        .select_related('user').order_by('-timestamp')[:15]]
    return {
        'scenario': scenario_payload(scenario),
        'latest': latest,
        'series': [series_point(s.kpis) for s in snaps],
        'lines': lines, 'agents': agents, 'decisions': decisions, 'stock': stock, 'jobs': jobs,
        'controls': controls,
        'experiments': list(scenario.experiments.values('id', 'name', 'status', 'horizon_ticks', 'created_at')[:10]),
    }


def scenario_payload(scenario):
    return {
        'id': scenario.id, 'name': scenario.name, 'description': scenario.description, 'seed': scenario.seed,
        'start_date': str(scenario.start_date), 'current_date': str(scenario.current_date), 'tick': scenario.tick,
        'tick_days': scenario.tick_days, 'status': scenario.status, 'driver': scenario.driver,
        'agent_model': scenario.agent_model, 'error_message': scenario.error_message,
        'parameters': scenario.parameters, 'commentary': (
            scenario.agents.filter(role='controller').values_list('policy', flat=True).first() or {}
        ).get('commentary', ''),
        'market': scenario.state.get('macro', {}),
    }


def financials(scenario, as_of=None, from_date=None, period_days=7):
    """
    The statements, compiled by reports.report_generators exactly as the reports
    API would, plus cost drivers, unit economics and the reconciliations.
    """
    as_of = as_of or scenario.current_date
    from_date = from_date or scenario.start_date
    period_from = max(from_date, as_of - timedelta(days=period_days))
    types = finance.account_types()
    since_start = finance.movements(from_date, as_of)
    last_period = finance.movements(period_from, as_of)
    return {
        'as_of': str(as_of), 'from': str(from_date), 'period_from': str(period_from),
        'trial_balance': generate_trial_balance({'as_of_date': str(as_of)}),
        'balance_sheet': generate_balance_sheet({'as_of_date': str(as_of)}),
        'income_statement': generate_income_statement({'from_date': str(from_date + timedelta(days=1)),
                                                       'to_date': str(as_of)}),
        'income_statement_period': generate_income_statement({'from_date': str(period_from + timedelta(days=1)),
                                                              'to_date': str(as_of)}),
        'pl': finance.profit_and_loss(since_start, types),
        'pl_period': finance.profit_and_loss(last_period, types),
        'cost_drivers': finance.cost_breakdown(since_start),
        'cost_drivers_period': finance.cost_breakdown(last_period),
        'by_family': finance.per_family_totals(since_start),
        'unit_economics': finance.unit_economics(from_date, as_of),
        'guardrails': finance.guardrails(scenario, as_of, full=True),
    }


def ledger(scenario, code, from_date=None, to_date=None):
    account = Account.objects.filter(code=code).first()
    if account is None:
        raise serializers.ValidationError({'account': f'No account {code}'})
    report = generate_general_ledger({
        'account_id': account.id, 'from_date': str(from_date or scenario.start_date),
        'to_date': str(to_date or scenario.current_date),
    })
    return report['ledger'].get(account.code, {})


def trace(scenario, reference):
    """
    Follow a document reference (SO-, PO-, WO-, GRN-, SHP-, INV-, PAY-, JE-)
    to the agent decisions that produced or touched it and the journal entries
    it posted, line by line.
    """
    reference = reference.strip()
    if not reference:
        raise serializers.ValidationError({'reference': 'required'})
    decisions = AgentDecision.objects.filter(scenario=scenario).annotate(docs=Cast('documents', TextField())).filter(
        Q(docs__contains=f'"{reference}"') | Q(summary__contains=reference)).select_related('agent').order_by('id')
    entries = JournalEntry.objects.filter(
        Q(entry_number=reference) | Q(source_reference__endswith=reference) | Q(lines__reference=reference)
        | Q(description__contains=reference)
    ).distinct().prefetch_related('lines__account').order_by('date', 'id')[:25]
    return {
        'reference': reference,
        'decisions': [decision_row(d) for d in decisions[:50]],
        'journal_entries': [{
            'entry_number': e.entry_number, 'date': str(e.date), 'description': e.description, 'status': e.status,
            'source_reference': e.source_reference,
            'lines': [{'account': line.account.code, 'name': line.account.name, 'debit': float(line.debit),
                       'credit': float(line.credit), 'reference': line.reference} for line in e.lines.all()],
        } for e in entries],
    }


def design(scenario):
    return catalog.design(scenario)
