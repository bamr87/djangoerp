"""
Drive the agent simulation sandbox from the command line:

    python manage.py simulate --ticks 30                 # bootstrap if needed, run 30 ticks
    python manage.py simulate --ticks 90 --set market_growth_pct=40 --set demand_base.A-WIDGET=14
    python manage.py simulate --what-if add_line:A --horizon 30
    python manage.py simulate --what-if shifts:LINE-A1=2 --what-if param:direct_labor_rate=30
    python manage.py simulate --driver llm --ticks 7     # Claude agents (needs ANTHROPIC_API_KEY)

Every tick posts through the ERP's services; the command prints a KPI line per
tick and exits non-zero if a tick halts on a guardrail (books out of balance)
or a reconciliation is off. What-ifs run as rolled-back forks and print the
comparison; the live books are untouched.
"""
import json
from datetime import date

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from rest_framework import serializers

from accounts.models import UserRole
from simulation import experiments, services
from simulation.models import Experiment, Scenario


def parse_value(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def parse_sets(pairs):
    """`key=value` and `map_key.SKU=value` pairs into a parameters dict."""
    params = {}
    for pair in pairs or []:
        if '=' not in pair:
            raise CommandError(f'--set expects key=value, got {pair!r}')
        key, raw = pair.split('=', 1)
        value = parse_value(raw)
        if '.' in key:
            name, sku = key.split('.', 1)
            params.setdefault(name, {})[sku] = value
        else:
            params[key] = value
    return params


def parse_what_if(items):
    """add_line:A[:machines] | shifts:LINE=N | retire:LINE | price:SKU=PCT | param:key=value."""
    changes = {}
    for item in items or []:
        kind, _, rest = item.partition(':')
        if kind == 'add_line':
            family, _, machines = rest.partition(':')
            changes.setdefault('add_lines', []).append({'family': family, 'machines': int(machines or 1)})
        elif kind == 'shifts':
            code, _, n = rest.partition('=')
            changes.setdefault('shifts', {})[code] = int(n)
        elif kind == 'retire':
            changes.setdefault('retire_lines', []).append(rest)
        elif kind == 'price':
            sku, _, pct = rest.partition('=')
            changes.setdefault('price_change_pct', {})[sku] = float(pct)
        elif kind == 'param':
            for key, value in parse_sets([rest]).items():
                existing = changes.setdefault('parameters', {}).get(key)
                changes['parameters'][key] = {**existing, **value} if isinstance(existing, dict) else value
        else:
            raise CommandError(f'Unknown what-if {item!r}; use add_line:, shifts:, retire:, price: or param:')
    return changes


class Command(BaseCommand):
    help = 'Run the agent simulation sandbox: bootstrap, advance ticks, or compare a what-if.'

    def add_arguments(self, parser):
        parser.add_argument('--ticks', type=int, default=0,
                            help='Ticks to advance (default 0, or 30 when the scenario is new and no what-if is asked)')
        parser.add_argument('--name', default='Sandbox')
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--start', help='Start date YYYY-MM-DD (new scenario only; default today)')
        parser.add_argument('--tick-days', type=int, default=1)
        parser.add_argument('--driver', choices=['heuristic', 'llm'])
        parser.add_argument('--set', action='append', dest='sets', metavar='KEY=VALUE',
                            help='Parameter change (repeatable); map parameters as key.SKU=value')
        parser.add_argument('--what-if', action='append', dest='what_if', metavar='CHANGE',
                            help='add_line:A | shifts:LINE-A1=2 | retire:LINE-B1 | price:A-WIDGET=-5 | '
                                 'param:key=value (repeatable)')
        parser.add_argument('--horizon', type=int, default=30, help='What-if horizon in ticks')
        parser.add_argument('--quiet', action='store_true', help='Print only the summary')

    def handle(self, *args, **options):
        user = self.operator()
        try:
            scenario, created = self.scenario(options, user)
            if not created and (options['sets'] or options['driver']):
                data = {}
                if options['sets']:
                    data['parameters'] = parse_sets(options['sets'])
                if options['driver']:
                    data['driver'] = options['driver']
                services.update_scenario(scenario, data, user=user)
            ticks = options['ticks'] or (30 if created and not options['what_if'] else 0)
            if ticks:
                self.advance(scenario, ticks, options['quiet'])
            if options['what_if']:
                self.what_if(scenario, parse_what_if(options['what_if']), options['horizon'], user)
        except serializers.ValidationError as exc:
            raise CommandError(exc.detail)

    def operator(self):
        user, created = User.objects.get_or_create(username='simulation-operator',
                                                   defaults={'first_name': 'Simulation operator'})
        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
            UserRole.objects.create(user=user, role='accountant')
        return user

    def scenario(self, options, user):
        scenario = Scenario.objects.first()
        if scenario is not None:
            self.stdout.write(f'Scenario "{scenario.name}" at tick {scenario.tick} ({scenario.current_date})')
            return scenario, False
        start = date.fromisoformat(options['start']) if options['start'] else None
        kwargs = {'name': options['name'], 'seed': options['seed'], 'tick_days': options['tick_days'],
                  'driver': options['driver'] or 'heuristic', 'parameters': parse_sets(options['sets'])}
        if start:
            kwargs['start_date'] = start
        else:
            kwargs['start_date'] = timezone.now().date()
        scenario = services.create_scenario(user=user, **kwargs)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Bootstrapped scenario "{scenario.name}" (seed {scenario.seed}, {scenario.driver} driver) '
            f'on {scenario.start_date}: {scenario.lines.count()} lines, {scenario.agents.count()} agents'))
        return scenario, True

    def advance(self, scenario, ticks, quiet):
        self.stdout.write(self.style.MIGRATE_HEADING(f'Advancing {ticks} tick(s)'))
        header = (f'{"tick":>4} {"date":<10} {"revenue":>9} {"net inc":>9} {"cash":>10} {"AR":>9} {"inventory":>9} '
                  f'{"backlog":>7} {"ship":>5} {"util":<16} books')
        if not quiet:
            self.stdout.write(header)
        last = None
        for _ in range(ticks):
            snapshot = services.advance(scenario, 1)[0]
            last = snapshot
            if not quiet:
                pl, bs, ops = snapshot['pl'], snapshot['bs'], snapshot['ops']
                util = ' '.join(f'{v["utilization"]:.0%}' for v in snapshot['lines'].values()
                                if v['status'] != 'retired')
                books = 'ok' if snapshot['guardrails']['ok'] else 'EXCEPTION'
                self.stdout.write(
                    f'{snapshot["tick"]:>4} {snapshot["date"]:<10} {pl["revenue"]:>9,.0f} {pl["net_income"]:>9,.0f} '
                    f'{bs["cash"]:>10,.0f} {bs["receivables"]:>9,.0f} '
                    f'{bs["inventory_materials"] + bs["inventory_finished"]:>9,.0f} {ops["backlog_units"]:>7,.0f} '
                    f'{ops.get("units_shipped", 0):>5,.0f} {util:<16} {books}')
        scenario.refresh_from_db()
        report = services.financials(scenario)
        tb, bs, is_ = report['trial_balance'], report['balance_sheet'], report['income_statement']
        self.stdout.write(self.style.MIGRATE_HEADING(f'Books at {scenario.current_date} (tick {scenario.tick})'))
        self.stdout.write(f'   Trial balance: debits {tb["total_debits"]:,.2f} credits {tb["total_credits"]:,.2f} '
                          f'balanced={tb["balanced"]}')
        self.stdout.write(f'   Balance sheet: assets {bs["total_assets"]:,.2f} = liabilities '
                          f'{bs["total_liabilities"]:,.2f} + equity {bs["total_equity"]:,.2f} '
                          f'balanced={bs["balanced"]}')
        self.stdout.write(f'   Income since start: revenue {is_["total_revenue"]:,.2f} expenses '
                          f'{is_["total_expenses"]:,.2f} net income {is_["net_income"]:,.2f}')
        for check in report['guardrails']['reconciliations']:
            mark = 'ok' if check['ok'] else f'OFF BY {check["difference"]}'
            self.stdout.write(f'   {check["label"]}: {mark}')
        if not report['guardrails']['ok'] or (last and not last['guardrails']['ok']):
            raise CommandError('Guardrail exception: the books or a subledger do not reconcile')
        self.stdout.write(self.style.SUCCESS(f'{ticks} tick(s) run through the ERP services; books balanced.'))

    def what_if(self, scenario, changes, horizon, user):
        self.stdout.write(self.style.MIGRATE_HEADING(f'What-if over {horizon} tick(s): {json.dumps(changes)}'))
        experiment = Experiment.objects.create(scenario=scenario, name='CLI what-if', horizon_ticks=horizon,
                                               changes=experiments.validate_changes(changes), created_by=user)
        experiments.run_experiment(experiment)
        if experiment.status != 'completed':
            raise CommandError(f'What-if failed: {experiment.error_message}')
        summary = experiment.summary
        self.stdout.write(f'   {summary["headline"]}')
        self.stdout.write(f'   {"metric":<46} {"baseline":>12} {"variant":>12} {"delta":>11}')
        for row in summary['metrics']:
            fmt = (lambda v: '—' if v is None else f'{v:,.2f}')
            self.stdout.write(f'   {row["label"]:<46} {fmt(row["baseline"]):>12} {fmt(row["variant"]):>12} '
                              f'{fmt(row["delta"]):>11}')
        for row in summary['unit_costs']:
            self.stdout.write(f'   unit cost {row["sku"]:<12} {row["baseline"]} -> {row["variant"]}')
        self.stdout.write(f'   utilization baseline {summary["utilization"]["baseline"]}')
        self.stdout.write(f'   utilization variant  {summary["utilization"]["variant"]}')
        self.stdout.write(self.style.SUCCESS('Both arms rolled back: the live books are unchanged.'))
