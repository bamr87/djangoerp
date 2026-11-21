"""
What-if experiments: fork the sandbox, run it forward, compare, roll back.

Each experiment runs two arms from the scenario's current state — the baseline
(no changes) and the variant (the experiment's `changes` applied first) — for
`horizon_ticks` ticks each, inside transactions that are always rolled back.
Both arms draw the same random numbers (common random numbers: every draw is
seeded by scenario seed, tick, agent and purpose), so the difference between
them is the effect of the change, not noise. Only the comparison is stored.

Supported changes::

    {"parameters": {"direct_labor_rate": 30, "demand_base": {"A-WIDGET": 14}},
     "add_lines": [{"family": "A", "machines": 1, "shifts": 1}],
     "shifts": {"LINE-A1": 2},
     "retire_lines": ["LINE-B1"],
     "price_change_pct": {"A-WIDGET": -5}}
"""
from collections import defaultdict
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from products.models import Product

from . import engine, finance
from . import parameters as params_module
from . import world
from .models import Experiment, Scenario

CHANGE_KEYS = {'parameters', 'add_lines', 'shifts', 'retire_lines', 'price_change_pct'}


class _Rollback(Exception):
    def __init__(self, result):
        super().__init__('experiment arm rolled back')
        self.result = result


def validate_changes(changes):
    """Structural validation of an experiment's changes (values are checked again when applied)."""
    if not isinstance(changes, dict):
        raise serializers.ValidationError('changes must be an object')
    unknown = set(changes) - CHANGE_KEYS
    if unknown:
        raise serializers.ValidationError(f'Unknown change(s) {sorted(unknown)}; use {sorted(CHANGE_KEYS)}')
    if 'parameters' in changes:
        params_module.validate(changes['parameters'])
    for spec in changes.get('add_lines', []):
        if not isinstance(spec, dict) or spec.get('family') not in world.FAMILIES:
            raise serializers.ValidationError(f'add_lines entries need a family in {sorted(world.FAMILIES)}')
    if not isinstance(changes.get('shifts', {}), dict) or not isinstance(changes.get('price_change_pct', {}), dict):
        raise serializers.ValidationError('shifts and price_change_pct are objects keyed by line code / SKU')
    for pct in changes.get('price_change_pct', {}).values():
        if not isinstance(pct, (int, float)) or not -50 <= pct <= 100:
            raise serializers.ValidationError('price_change_pct values are percentages between -50 and 100')
    return changes


def apply_changes(scenario, changes, *, user=None):
    """Apply a what-if's changes to the scenario (inside the caller's transaction). Returns what was done."""
    applied = []
    date = scenario.current_date
    if changes.get('parameters'):
        cleaned = params_module.validate(changes['parameters'])
        merged = dict(scenario.parameters)
        for key, value in cleaned.items():
            merged[key] = {**merged.get(key, {}), **value} if isinstance(value, dict) else value
        scenario.parameters = params_module.resolve(merged)
        scenario.save(update_fields=['parameters', 'updated_at'])
        applied.append('parameters: ' + ', '.join(f'{k}={v}' for k, v in cleaned.items()))
    for spec in changes.get('add_lines', []):
        line = world.commission_line(
            scenario, family=spec['family'], date=date, user=user, machines=spec.get('machines', 1),
            shifts=spec.get('shifts', 1), crew_per_machine=spec.get('crew_per_machine'), capex=spec.get('capex'),
        )
        applied.append(f'commissioned {line.code} (capex {line.capex})')
    max_shifts = int(params_module.resolve(scenario.parameters)['max_shifts'])
    for code, shifts in changes.get('shifts', {}).items():
        line = scenario.lines.filter(code=code).exclude(status='retired').first()
        if line is None:
            raise serializers.ValidationError(f'No active line {code}')
        line.shifts = max(1, min(int(shifts), max_shifts))
        line.save(update_fields=['shifts', 'updated_at'])
        applied.append(f'{code} runs {line.shifts} shift(s)')
    for code in changes.get('retire_lines', []):
        line = scenario.lines.filter(code=code).first()
        if line is None:
            raise serializers.ValidationError(f'No line {code}')
        world.decommission_line(scenario, line, date=date, user=user)
        applied.append(f'retired {code}')
    for sku, pct in changes.get('price_change_pct', {}).items():
        product = Product.objects.filter(sku=sku).first()
        if product is None:
            raise serializers.ValidationError(f'No product {sku}')
        product.sales_price = (product.sales_price * Decimal(str(1 + pct / 100))).quantize(Decimal('0.01'))
        product.save(update_fields=['sales_price', 'updated_at'])
        applied.append(f'{sku} price {pct:+g}%')
    world.refresh_absorption_rates(scenario)
    return applied


SERIES_KEYS = ['revenue', 'cogs', 'gross_margin', 'net_income', 'conversion_actual', 'absorbed', 'under_absorbed']


def summarize(scenario, series, base_date, applied, lines_before=()):
    """Totals, end position, unit costs and utilization of one arm (computed before the rollback)."""
    totals = defaultdict(float)
    ops = defaultdict(float)
    util = defaultdict(list)
    for k in series:
        for key, value in k['pl'].items():
            if key != 'gross_margin_pct':
                totals[key] += value
        for key in ('units_ordered', 'units_shipped', 'units_produced', 'units_cancelled', 'orders_cancelled',
                    'orders_shipped', 'orders_on_time', 'breakdowns', 'demand_potential', 'revolver_draw'):
            ops[key] += k['ops'].get(key, 0) or 0
        for code, line in k['lines'].items():
            if line['status'] != 'retired':
                util[code].append(line['utilization'])
    end = series[-1]
    revenue = totals.get('revenue', 0.0)
    totals['gross_margin_pct'] = totals['gross_margin'] / revenue * 100 if revenue else 0.0
    # Every expense of the period — product cost of what shipped, idle capacity and period costs — per unit
    # shipped: the economic cost of a unit, which normal-capacity absorption keeps out of inventory value.
    totals['full_cost_per_unit'] = (totals['total_expenses'] / ops['units_shipped']) if ops['units_shipped'] else 0.0
    units = finance.unit_economics(base_date, scenario.current_date)
    capex = float(sum((line.capex for line in scenario.lines.exclude(pk__in=lines_before)), Decimal('0')))
    return {
        'applied': applied,
        'ticks': len(series), 'from': str(base_date), 'to': str(scenario.current_date),
        'totals': {key: round(value, 2) for key, value in totals.items()},
        'operations': {
            **{key: round(value, 2) for key, value in ops.items()},
            'otif_pct': (round(ops['orders_on_time'] / ops['orders_shipped'] * 100, 1)
                         if ops['orders_shipped'] else None),
            'fill_rate_pct': (round(ops['units_shipped'] / ops['units_ordered'] * 100, 1)
                              if ops['units_ordered'] else None),
            'backlog_units_end': end['ops']['backlog_units'],
        },
        'position_end': end['bs'],
        'unit_costs': units,
        'utilization': {code: round(sum(v) / len(v), 4) for code, v in util.items() if v},
        'lines_end': {code: {'shifts': line['shifts'], 'status': line['status'], 'family': line['family']}
                      for code, line in end['lines'].items()},
        'capex': round(capex, 2),
        'guardrails_ok': all(k['guardrails']['ok'] for k in series),
        'agents': {'decisions': sum(k['ai']['decisions'] for k in series),
                   'llm_calls': sum(k['ai']['llm_calls'] for k in series),
                   'ai_cost': round(sum(k['ai']['ai_cost'] for k in series), 6)},
        'series': [
            {'tick': k['tick'], 'date': k['date'], **{key: k['pl'][key] for key in SERIES_KEYS},
             'cash': k['bs']['cash'], 'backlog_units': k['ops']['backlog_units'],
             'units_shipped': k['ops'].get('units_shipped', 0),
             'unit_cost': {sku: v['unit_cost'] for sku, v in k['skus'].items()},
             'utilization': {code: line['utilization'] for code, line in k['lines'].items()}}
            for k in series
        ],
    }


def run_arm(scenario_id, changes, horizon, *, force_heuristic, user=None, client_factory=None):
    """Run one arm in a transaction that is always rolled back; return its summary."""
    try:
        with transaction.atomic():
            scenario = Scenario.objects.select_for_update().get(pk=scenario_id)
            base_date = scenario.current_date
            lines_before = list(scenario.lines.values_list('pk', flat=True))
            applied = apply_changes(scenario, changes, user=user) if changes else []
            series = []
            for n in range(horizon):
                series.append(engine.run_tick(scenario, force_heuristic=force_heuristic,
                                              client_factory=client_factory, full_check=(n == horizon - 1)))
            raise _Rollback(summarize(scenario, series, base_date, applied, lines_before))
    except _Rollback as rollback:
        return rollback.result


METRICS = [
    # key, label, path, which direction is better
    ('revenue', 'Revenue', ('totals', 'revenue'), 'higher'),
    ('gross_margin', 'Gross margin', ('totals', 'gross_margin'), 'higher'),
    ('gross_margin_pct', 'Gross margin %', ('totals', 'gross_margin_pct'), 'higher'),
    ('net_income', 'Net income', ('totals', 'net_income'), 'higher'),
    ('conversion_actual', 'Conversion cost (actual)', ('totals', 'conversion_actual'), 'lower'),
    ('direct_labor', 'Direct labor', ('totals', 'direct_labor'), 'lower'),
    ('indirect_labor', 'Indirect labor', ('totals', 'indirect_labor'), 'lower'),
    ('variable_overhead', 'Variable overhead', ('totals', 'variable_overhead'), 'lower'),
    ('depreciation', 'Depreciation', ('totals', 'depreciation'), 'lower'),
    ('maintenance', 'Maintenance', ('totals', 'maintenance'), 'lower'),
    ('absorbed', 'Absorbed into inventory', ('totals', 'absorbed'), 'neutral'),
    ('under_absorbed', 'Under-absorbed (idle capacity)', ('totals', 'under_absorbed'), 'lower'),
    ('period_costs', 'Period costs (mgmt, facility, AI, interest)', ('totals', 'period_costs'), 'lower'),
    ('full_cost_per_unit', 'Fully loaded cost per unit shipped', ('totals', 'full_cost_per_unit'), 'lower'),
    ('cash_end', 'Cash at end', ('position_end', 'cash'), 'higher'),
    ('units_shipped', 'Units shipped', ('operations', 'units_shipped'), 'higher'),
    ('units_cancelled', 'Units lost to cancellations', ('operations', 'units_cancelled'), 'lower'),
    ('otif_pct', 'On time in full %', ('operations', 'otif_pct'), 'higher'),
    ('backlog_end', 'Backlog at end (units)', ('operations', 'backlog_units_end'), 'lower'),
]


def _get(arm, path):
    value = arm
    for part in path:
        value = (value or {}).get(part)
    return value


def compare(baseline, variant, horizon_days):
    rows = []
    for key, label, path, better in METRICS:
        b, v = _get(baseline, path), _get(variant, path)
        delta = None if b is None or v is None else round(v - b, 2)
        rows.append({'key': key, 'label': label, 'baseline': b, 'variant': v, 'delta': delta,
                     'delta_pct': round(delta / abs(b) * 100, 1) if delta is not None and b else None,
                     'better': better})
    unit_rows = []
    for sku in sorted(set(baseline['unit_costs']) | set(variant['unit_costs'])):
        b = baseline['unit_costs'].get(sku, {}).get('unit_cost')
        v = variant['unit_costs'].get(sku, {}).get('unit_cost')
        unit_rows.append({'sku': sku, 'baseline': b, 'variant': v,
                          'delta': round(v - b, 2) if b is not None and v is not None else None,
                          'units_baseline': baseline['unit_costs'].get(sku, {}).get('units'),
                          'units_variant': variant['unit_costs'].get(sku, {}).get('units')})
    by_key = {row['key']: row for row in rows}
    capex = variant['capex'] - baseline['capex']
    gain = (by_key['net_income']['delta'] or 0) + (by_key['depreciation']['delta'] or 0)
    payback_days = round(capex / (gain / horizon_days)) if capex > 0 and gain > 0 else None
    ni = by_key['net_income']
    conv = by_key['conversion_actual']
    headline = (
        f'Over {horizon_days} days the change moves net income by {ni["delta"]:+,.0f} '
        f'({ni["baseline"]:,.0f} -> {ni["variant"]:,.0f}) and conversion cost by {conv["delta"]:+,.0f}; '
        f'units shipped {by_key["units_shipped"]["delta"]:+,.0f}, lost to cancellations '
        f'{by_key["units_cancelled"]["delta"]:+,.0f}.'
    )
    if capex > 0:
        headline += (f' Capex {capex:,.0f}: ' + (f'pays back in about {payback_days} days at this run rate.'
                                                 if payback_days else 'no payback at this demand — the extra '
                                                 'capacity mostly adds fixed cost.'))
    return {'headline': headline, 'metrics': rows, 'unit_costs': unit_rows, 'capex_delta': round(capex, 2),
            'operating_gain': round(gain, 2), 'payback_days': payback_days,
            'utilization': {'baseline': baseline['utilization'], 'variant': variant['utilization']}}


def run_experiment(experiment, *, client_factory=None):
    """Run both arms and store the comparison; the scenario's books are left exactly as they were."""
    world.require_sandbox()
    experiment.status = 'running'
    experiment.save(update_fields=['status', 'updated_at'])
    scenario = experiment.scenario
    force = experiment.driver == 'heuristic'
    try:
        validate_changes(experiment.changes)
        baseline = run_arm(scenario.pk, {}, experiment.horizon_ticks, force_heuristic=force,
                           client_factory=client_factory)
        variant = run_arm(scenario.pk, experiment.changes, experiment.horizon_ticks, force_heuristic=force,
                          user=experiment.created_by, client_factory=client_factory)
    except (serializers.ValidationError, engine.GuardrailBreach) as exc:
        experiment.status = 'failed'
        experiment.error_message = str(getattr(exc, 'detail', exc))[:2000]
        experiment.save(update_fields=['status', 'error_message', 'updated_at'])
        return experiment
    scenario.refresh_from_db()
    experiment.base_tick = scenario.tick
    experiment.base_date = scenario.current_date
    experiment.baseline = baseline
    experiment.variant = variant
    experiment.summary = compare(baseline, variant, experiment.horizon_ticks * scenario.tick_days)
    experiment.status = 'completed'
    experiment.completed_at = timezone.now()
    experiment.save()
    return experiment


@shared_task
def run_experiment_task(experiment_id):
    """Celery wrapper, the reports/MRP pattern: eager in dev, a worker in the compose `celery` profile."""
    run_experiment(Experiment.objects.get(pk=experiment_id))
