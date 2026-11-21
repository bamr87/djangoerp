"""
The agent simulation sandbox: the economy must stay a set of balanced,
reconciled books however its agents behave, what-ifs must leave the live books
untouched, Claude decisions must stay inside their bounds, and the API must
follow the project's role rules.
"""
import io
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.db.models import Sum
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import serializers, status
from rest_framework.test import APITestCase

from accounts.models import AuditLog
from accounts.test_helpers import create_user
from inventory.models import StockLevel
from inventory.services import rebuild_stock_levels
from journal.models import JournalEntry, JournalLine
from reports.report_generators import generate_balance_sheet, generate_trial_balance
from sales.models import SalesOrder

from . import brains, engine, experiments, finance
from . import parameters as params_module
from . import services, world
from .management.commands.simulate import parse_sets, parse_what_if
from .models import Agent, AgentDecision, Experiment, ProductionLine, Scenario, TickSnapshot

START = date(2027, 1, 1)


def bootstrap(**kwargs):
    kwargs.setdefault('start_date', START)
    return services.create_scenario(name='Test economy', seed=11, **kwargs)


class FakeClaude:
    """Stands in for anthropic.Anthropic(): answers every review at the edge of (or past) its bounds."""

    def __init__(self, input_tokens=40_000, output_tokens=12_000, stop_reason='end_turn', text=None, error=None):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.stop_reason, self.text, self.error = stop_reason, text, error

    def __call__(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        schema = kwargs['output_config']['format']['schema']
        answer = {}
        for name, prop in schema['properties'].items():
            if prop['type'] == 'number':
                answer[name] = 1e9
            elif prop['type'] == 'object':
                answer[name] = {key: 0 for key in prop['properties']}
            elif 'enum' in prop:
                answer[name] = prop['enum'][-1]
            else:
                answer[name] = f'{name} from Claude'
        text = self.text if self.text is not None else json.dumps(answer)
        return SimpleNamespace(
            stop_reason=self.stop_reason, content=[SimpleNamespace(type='text', text=text)],
            usage=SimpleNamespace(input_tokens=self.input_tokens, output_tokens=self.output_tokens),
        )


class ParameterCatalogTests(SimpleTestCase):
    def test_every_parameter_declares_its_meaning(self):
        for spec in params_module.PARAMETERS:
            with self.subTest(key=spec['key']):
                self.assertTrue(spec['description'] and spec['label'] and spec['group'])
                self.assertTrue(spec['drives'], 'a parameter no agent reads is dead weight')
                self.assertTrue(spec['affects'])
                roles = dict(Agent.ROLE_CHOICES)
                self.assertTrue(set(spec['drives']) <= set(roles))

    def test_validate_rejects_unknown_keys_types_and_ranges(self):
        for bad in ({'nope': 1}, {'direct_labor_rate': 'x'}, {'direct_labor_rate': -3},
                    {'autonomous_capex': 'yes'}, {'demand_base': 5}, {'demand_base': {'A-WIDGET': 10_000}}):
            with self.subTest(bad=bad), self.assertRaises(serializers.ValidationError):
                params_module.validate(bad)
        self.assertEqual(params_module.validate({'max_shifts': 2.0}), {'max_shifts': 2})

    def test_resolve_merges_map_parameters_key_by_key(self):
        values = params_module.resolve({'demand_base': {'A-WIDGET': 20}})
        self.assertEqual(values['demand_base']['A-WIDGET'], 20)
        self.assertEqual(values['demand_base']['B-GADGET'], params_module.defaults()['demand_base']['B-GADGET'])


class BrainTests(SimpleTestCase):
    fields = {
        'level': brains.number(0, 10, 'A level.'),
        'per_sku': brains.number_map(['A', 'B'], -5, 5, 'Changes.'),
        'mode': brains.choice(['x', 'y'], 'A mode.'),
        'note': brains.text('A note.'),
    }
    proposal = {'level': 3.0, 'per_sku': {'A': 1.0, 'B': -1.0}, 'mode': 'x', 'note': 'baseline'}

    def setUp(self):
        brains._UNAVAILABLE.clear()
        self.agent = Agent(code='SALES', name='Sales manager', role='sales_manager', resource_kind='management')

    def review(self, client):
        return brains.ClaudeBrain('claude-haiku-5-5', client_factory=client).review(
            agent=self.agent, observation={'x': 1}, proposal=self.proposal, fields=self.fields, rationale='heuristic')

    def test_schema_is_strict_and_complete(self):
        schema = brains.json_schema(self.fields)
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), {'level', 'per_sku', 'mode', 'note', 'rationale'})
        self.assertEqual(schema['properties']['mode']['enum'], ['x', 'y'])
        self.assertIn('Between 0 and 10', schema['properties']['level']['description'])

    def test_claude_answers_are_clamped_and_costed(self):
        client = FakeClaude()
        decision = self.review(client)
        self.assertEqual(decision.driver, 'llm')
        self.assertEqual(decision.values['level'], 10)  # 1e9 clamped to the bound
        self.assertEqual(decision.values['per_sku'], {'A': 0.0, 'B': 0.0})
        self.assertEqual(decision.values['mode'], 'y')
        self.assertTrue(any('clamped' in note for note in decision.notes))
        # 40k input x $0.10/M + 12k output x $0.50/M on Claude Haiku 5.5.
        self.assertEqual(decision.cost, Decimal('0.01'))
        request = client.calls[0]
        self.assertEqual(request['model'], 'claude-haiku-5-5')
        self.assertEqual(request['output_config']['effort'], 'low')
        self.assertEqual(request['output_config']['format']['type'], 'json_schema')
        for removed in ('temperature', 'top_p', 'top_k', 'tool_choice'):
            self.assertNotIn(removed, request)

    def test_invalid_answers_fall_back_to_the_proposal(self):
        cases = {
            'not json': FakeClaude(text='I think prices should rise'),
            'refusal': FakeClaude(stop_reason='refusal'),
            'truncated': FakeClaude(stop_reason='max_tokens'),
        }
        for label, client in cases.items():
            with self.subTest(label):
                decision = self.review(client)
                self.assertEqual(decision.driver, 'fallback')
                self.assertEqual(decision.values, self.proposal)
                self.assertTrue(decision.error)
                self.assertGreater(decision.cost, 0, 'a call that ran is billed even when its answer is unusable')

    def test_api_errors_and_missing_credentials_fall_back(self):
        import anthropic

        decision = self.review(FakeClaude(error=anthropic.APIConnectionError(request=mock.Mock())))
        self.assertEqual((decision.driver, decision.values), ('fallback', self.proposal))
        self.assertNotIn('claude-haiku-5-5', brains._UNAVAILABLE, 'a network blip is retried next review')

        decision = self.review(FakeClaude(error=TypeError('Could not resolve authentication method.')))
        self.assertEqual(decision.driver, 'fallback')
        self.assertIn('claude-haiku-5-5', brains._UNAVAILABLE)
        client = FakeClaude()
        self.assertEqual(self.review(client).driver, 'fallback')
        self.assertEqual(client.calls, [], 'an unusable model is not called again')

    def test_other_type_errors_are_bugs_and_propagate(self):
        with self.assertRaises(TypeError):
            self.review(FakeClaude(error=TypeError('unsupported operand')))


@override_settings(SIMULATION_SANDBOX=False)
class SandboxGuardTests(TestCase):
    def test_simulation_refuses_a_database_that_is_not_a_sandbox(self):
        with self.assertRaises(serializers.ValidationError):
            bootstrap()
        self.assertFalse(Scenario.objects.exists())
        self.assertFalse(JournalEntry.objects.exists())


class EconomyLifecycleTests(TestCase):
    """One economy, bootstrapped and run 14 ticks; every test reads (or forks) it and is rolled back."""

    TICKS = 14

    @classmethod
    def setUpTestData(cls):
        cls.scenario = bootstrap()
        cls.entries_at_start = JournalEntry.objects.count()
        cls.snapshots = engine.advance(cls.scenario, cls.TICKS)
        cls.scenario.refresh_from_db()

    def test_bootstrap_builds_the_economy_and_its_opening_books(self):
        scenario = Scenario.objects.get()
        self.assertEqual(scenario.lines.count(), 2)
        roles = set(scenario.agents.values_list('role', flat=True))
        self.assertEqual(roles, set(dict(Agent.ROLE_CHOICES)))
        self.assertTrue(all(a.user_id for a in scenario.agents.all()), 'every agent acts as its own user')
        opening = TickSnapshot.objects.get(scenario=scenario, tick=0).kpis
        # Capital paid in; opening stock bought on 30-day terms, so assets exceed it by exactly the payable.
        self.assertEqual(opening['bs']['equity'], 600_000.0)
        self.assertGreater(opening['bs']['payables'], 0)
        self.assertEqual(opening['bs']['total_assets'], 600_000.0 + opening['bs']['payables'])
        self.assertEqual(opening['pl']['revenue'], 0.0)
        self.assertTrue(opening['guardrails']['ok'])
        with self.assertRaises(serializers.ValidationError):
            bootstrap()

    def test_the_clock_advanced_and_every_tick_reconciled(self):
        self.assertEqual((self.scenario.tick, self.scenario.current_date), (self.TICKS, date(2027, 1, 15)))
        self.assertEqual(len(self.snapshots), self.TICKS)
        for snap in self.snapshots:
            with self.subTest(tick=snap['tick']):
                self.assertTrue(snap['guardrails']['ok'], snap['guardrails'])
        sources = {s['guardrails']['source'] for s in self.snapshots}
        self.assertEqual(sources, {'reports.report_generators', 'ledger aggregate'})
        self.assertEqual(self.snapshots[-1]['guardrails']['source'], 'reports.report_generators')

    def test_report_generators_and_independent_recomputation_agree(self):
        as_of = str(self.scenario.current_date)
        self.assertTrue(generate_trial_balance({'as_of_date': as_of})['balanced'])
        self.assertTrue(generate_balance_sheet({'as_of_date': as_of})['balanced'])
        # Stock ledger value = GL inventory, recomputed here from first principles.
        codes = finance.inventory_account_codes()
        lines = JournalLine.objects.filter(entry__status='posted', account__code__in=codes)
        gl = (lines.aggregate(d=Sum('debit'))['d'] or 0) - (lines.aggregate(c=Sum('credit'))['c'] or 0)
        self.assertEqual(gl, finance.stock_ledger_value(self.scenario.current_date))
        # The StockLevel cache is what the move ledger says it is.
        before = {(lvl.product_id, lvl.warehouse_id): lvl.quantity_on_hand for lvl in StockLevel.objects.all()}
        rebuild_stock_levels()
        after = {(lvl.product_id, lvl.warehouse_id): lvl.quantity_on_hand for lvl in StockLevel.objects.all()}
        self.assertEqual(before, after)

    def test_the_business_actually_ran(self):
        totals = {key: sum(s['pl'][key] for s in self.snapshots) for key in ('revenue', 'cogs', 'absorbed')}
        self.assertGreater(totals['revenue'], 0)
        self.assertGreater(totals['cogs'], 0)
        self.assertGreater(totals['absorbed'], 0, 'completed jobs absorb conversion cost into inventory')
        self.assertGreater(sum(s['ops'].get('units_shipped', 0) for s in self.snapshots), 0)
        self.assertTrue(JournalEntry.objects.filter(source_reference__startswith='invoice:').exists())
        self.assertTrue(JournalEntry.objects.filter(source_reference=f'sim:{self.scenario.pk}:t1:costs').exists())
        self.assertTrue(JournalEntry.objects.filter(
            source_reference=f'sim:{self.scenario.pk}:t1:depreciation').exists())
        absorbed = JournalLine.objects.filter(account__code='5390').aggregate(c=Sum('credit'))['c']
        self.assertGreater(absorbed, 0)

    def test_every_journal_entry_balances_and_is_idempotent_per_event(self):
        new = JournalEntry.objects.filter(status='posted').order_by('id')[self.entries_at_start:]
        self.assertTrue(new)
        for entry in new:
            debit = sum(line.debit for line in entry.lines.all())
            credit = sum(line.credit for line in entry.lines.all())
            self.assertEqual(debit, credit, entry.entry_number)
            self.assertTrue(entry.source_reference, f'{entry.entry_number} is a system posting')

    def test_agents_left_a_semantic_and_audit_trail(self):
        acted = set(AgentDecision.objects.filter(scenario=self.scenario).values_list('agent__role', flat=True))
        self.assertTrue({'market', 'customer', 'supplier', 'sales_manager', 'buyer', 'planner', 'operator',
                         'controller', 'plant_manager', 'maintenance'} <= acted, acted)
        reviews = AgentDecision.objects.filter(kind='policy')
        self.assertTrue(reviews.exists() and all(d.rationale for d in reviews))
        sales = Agent.objects.get(scenario=self.scenario, role='sales_manager')
        self.assertTrue(AuditLog.objects.filter(user=sales.user, details__event='sales_order.ship').exists())
        ordered = AgentDecision.objects.filter(action='place_order').first()
        self.assertTrue(SalesOrder.objects.filter(number=ordered.documents[0]).exists())
        self.assertEqual(sum(a.decisions_count for a in Agent.objects.all()),
                         AgentDecision.objects.filter(scenario=self.scenario).count())

    def test_trace_follows_a_document_into_the_ledger(self):
        grn = AgentDecision.objects.filter(action='deliver').first().documents[1]
        result = services.trace(self.scenario, grn)
        self.assertTrue(result['decisions'])
        entry = next(e for e in result['journal_entries'] if e['source_reference'] == f'grn:{grn}')
        self.assertEqual(sum(line['debit'] for line in entry['lines']), sum(line['credit'] for line in entry['lines']))

    def test_dashboard_financials_and_design_compile(self):
        dash = services.dashboard(self.scenario)
        self.assertEqual(len(dash['series']), self.TICKS + 1)
        self.assertEqual(len(dash['lines']), 2)
        fin = services.financials(self.scenario)
        self.assertTrue(fin['trial_balance']['balanced'])
        self.assertAlmostEqual(float(fin['income_statement']['net_income']), fin['pl']['net_income'], places=2)
        design = services.design(self.scenario)
        self.assertTrue(all(i['ok'] for i in design['invariants']))
        self.assertGreater(next(p for p in design['postings'] if p['event'] == 'Shipment')['count'], 0)

    def test_an_empty_what_if_reproduces_the_baseline_exactly(self):
        experiment = Experiment.objects.create(scenario=self.scenario, name='No change', horizon_ticks=4, changes={})
        experiments.run_experiment(experiment)
        self.assertEqual(experiment.status, 'completed', experiment.error_message)
        for row in experiment.summary['metrics']:
            self.assertIn(row['delta'], (0, 0.0, None), row)
        self.assertEqual(experiment.baseline['series'], experiment.variant['series'])

    def test_adding_a_line_for_product_line_a_shows_its_cost_and_leaves_the_books_untouched(self):
        entries, decisions = JournalEntry.objects.count(), AgentDecision.objects.count()
        experiment = Experiment.objects.create(scenario=self.scenario, name='Line A2', horizon_ticks=6,
                                               changes={'add_lines': [{'family': 'A'}]})
        experiments.run_experiment(experiment)
        self.assertEqual(experiment.status, 'completed', experiment.error_message)
        metrics = {row['key']: row for row in experiment.summary['metrics']}
        self.assertEqual(experiment.summary['capex_delta'], 180_000.0)
        self.assertGreater(metrics['depreciation']['delta'], 0)
        self.assertGreater(metrics['direct_labor']['delta'], 0)
        self.assertGreater(metrics['indirect_labor']['delta'], 0)
        self.assertIn('LINE-A2', experiment.summary['utilization']['variant'])
        self.assertTrue(experiment.baseline['guardrails_ok'] and experiment.variant['guardrails_ok'])
        # Both arms rolled back.
        self.assertEqual((JournalEntry.objects.count(), AgentDecision.objects.count()), (entries, decisions))
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.tick, self.TICKS)
        self.assertFalse(ProductionLine.objects.filter(code='LINE-A2').exists())

    def test_an_unbalanced_tick_rolls_back_and_halts(self):
        entries = JournalEntry.objects.count()
        broken = {'trial_balance': {'balanced': False}, 'balance_sheet': {'balanced': True}, 'ok': False,
                  'reconciliations': [], 'source': 'test'}
        real = engine.compile_snapshot

        def unbalanced(*args, **kwargs):
            return {**real(*args, **kwargs), 'guardrails': broken}

        with mock.patch.object(engine, 'compile_snapshot', side_effect=unbalanced):
            with self.assertRaises(serializers.ValidationError):
                engine.advance(self.scenario, 1)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.status, 'halted')
        self.assertIn('unbalanced', self.scenario.error_message)
        self.assertEqual(self.scenario.tick, self.TICKS)
        self.assertEqual(JournalEntry.objects.count(), entries)
        with self.assertRaises(serializers.ValidationError):
            engine.advance(self.scenario, 1)
        services.resume(self.scenario)
        self.assertEqual(engine.advance(self.scenario, 1)[0]['tick'], self.TICKS + 1)

    def test_controls_change_what_agents_respond_to(self):
        controller = Agent.objects.get(scenario=self.scenario, role='controller')
        old_rate = controller.policy['oar']['LINE-A1']
        services.update_scenario(self.scenario, {'parameters': {'direct_labor_rate': 50}})
        controller.refresh_from_db()
        self.assertGreater(controller.policy['oar']['LINE-A1'], old_rate)
        self.assertTrue(AuditLog.objects.filter(details__event='simulation.controls').exists())
        before = JournalLine.objects.filter(account__code='5300').aggregate(d=Sum('debit'))['d']
        engine.advance(self.scenario, 1)
        paid = JournalLine.objects.filter(account__code='5300').aggregate(d=Sum('debit'))['d'] - before
        line_hours = sum(line.machines * float(line.crew_per_machine) * 8 * (1 + 0.15 * (line.shifts - 1))
                         for line in ProductionLine.objects.filter(scenario=self.scenario))
        self.assertEqual(float(paid), round(line_hours * 50, 2))

    def test_commissioning_and_retiring_a_line_keep_the_asset_register_reconciled(self):
        line = services.commission_line(self.scenario, {'family': 'B'})
        self.assertTrue(JournalEntry.objects.filter(source_reference=f'sim:{self.scenario.pk}:capex:{line.code}')
                        .exists())
        engine.advance(self.scenario, 2)
        line.refresh_from_db()
        self.assertGreater(line.accumulated_depreciation, 0)
        if line.jobs.filter(status='running').exists():
            with self.assertRaises(serializers.ValidationError):
                services.retire_line(line)
            line.jobs.filter(status='running').update(status='completed')
        services.retire_line(line)
        guard = finance.guardrails(self.scenario, self.scenario.current_date)
        self.assertTrue(guard['ok'], guard)
        self.assertTrue(JournalLine.objects.filter(account__code='6500').exists())


class ClaudeDriverTests(TestCase):
    """The `llm` driver end to end, with a fake client in place of the API."""

    def setUp(self):
        brains._UNAVAILABLE.clear()

    def test_reviews_go_to_claude_within_the_call_budget_and_their_cost_is_posted(self):
        scenario = bootstrap(driver='llm')
        client = FakeClaude()
        engine.advance(scenario, 1, client_factory=client)
        reviews = AgentDecision.objects.filter(scenario=scenario, kind='policy')
        by_claude = reviews.filter(driver='llm')
        self.assertEqual(by_claude.count(), 8, 'llm_max_calls_per_tick caps the calls')
        self.assertEqual(len(client.calls), 8)
        self.assertTrue(reviews.filter(driver='heuristic', error__contains='budget').exists())
        self.assertTrue(all(d.model == 'claude-haiku-5-5' and d.cost == Decimal('0.01') for d in by_claude))
        self.assertTrue(by_claude.filter(error__contains='clamped').exists())
        market = Agent.objects.get(scenario=scenario, role='market')
        self.assertEqual(market.policy['sentiment'], 0.5)  # Claude asked for 1e9; the bound is 0.5
        posted = JournalLine.objects.filter(account__code='6300').aggregate(d=Sum('debit'))['d']
        self.assertEqual(posted, Decimal('0.08'))
        snapshot = TickSnapshot.objects.get(scenario=scenario, tick=1).kpis
        self.assertTrue(snapshot['guardrails']['ok'])
        self.assertEqual(snapshot['ai']['llm_calls'], 8)


class SimulationAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {role: create_user(f'sim_{role}', role=role)
                     for role in ('admin', 'accountant', 'auditor', 'viewer')}
        cls.scenario = bootstrap()
        engine.advance(cls.scenario, 3)

    def as_role(self, role):
        self.client.force_authenticate(user=self.users[role])

    def url(self, name, *args):
        return reverse(name, args=args)

    def test_reads_are_open_to_every_role_and_closed_to_anonymous(self):
        routes = ['scenario-list', 'productionline-list', 'agent-list', 'agentdecision-list', 'ticksnapshot-list',
                  'experiment-list']
        detail = ['scenario-dashboard', 'scenario-financials', 'scenario-design']
        for name in routes + detail:
            args = (self.scenario.pk,) if name in detail else ()
            with self.subTest(route=name):
                self.client.force_authenticate(user=None)
                self.assertEqual(self.client.get(self.url(name, *args)).status_code, status.HTTP_401_UNAUTHORIZED)
                for role in self.users:
                    self.as_role(role)
                    self.assertEqual(self.client.get(self.url(name, *args)).status_code, status.HTTP_200_OK)

    def test_running_the_economy_needs_the_accountant_role(self):
        url = self.url('scenario-advance', self.scenario.pk)
        for role in ('viewer', 'auditor'):
            self.as_role(role)
            self.assertEqual(self.client.post(url, {'ticks': 1}, format='json').status_code,
                             status.HTTP_403_FORBIDDEN)
        self.as_role('accountant')
        response = self.client.post(url, {'ticks': 2}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['ticks'], 2)
        self.assertEqual(response.data['snapshots'][-1]['tick'], 5)
        self.assertEqual(self.client.post(url, {'ticks': 500}, format='json').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_parameters_are_validated_merged_and_audited(self):
        self.as_role('accountant')
        url = self.url('scenario-detail', self.scenario.pk)
        bad = self.client.patch(url, {'parameters': {'direct_labor_rate': -1}}, format='json')
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        good = self.client.patch(url, {'parameters': {'demand_base': {'B-GADGET': 12}}}, format='json')
        self.assertEqual(good.status_code, status.HTTP_200_OK, good.data)
        self.assertEqual(good.data['parameters']['demand_base']['B-GADGET'], 12)
        self.assertEqual(good.data['parameters']['demand_base']['A-WIDGET'], 10)
        self.assertEqual(self.client.patch(url, {'seed': 99}, format='json').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_a_scenario_is_never_deleted_and_a_second_is_refused(self):
        self.as_role('admin')
        self.assertEqual(self.client.delete(self.url('scenario-detail', self.scenario.pk)).status_code,
                         status.HTTP_400_BAD_REQUEST)
        response = self.client.post(self.url('scenario-list'), {'name': 'Second'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already runs', str(response.data))

    def test_lines_are_commissioned_rescheduled_and_retired_never_deleted(self):
        self.as_role('accountant')
        response = self.client.post(self.url('productionline-list'),
                                    {'scenario': self.scenario.pk, 'family_code': 'A', 'shifts': 2}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data['capex_entry'])
        line_url = self.url('productionline-detail', response.data['id'])
        self.assertEqual(self.client.patch(line_url, {'shifts': 3}, format='json').status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.patch(line_url, {'machines': 4}, format='json').status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.delete(line_url).status_code, status.HTTP_400_BAD_REQUEST)
        retire = self.client.post(self.url('productionline-retire', response.data['id']), {}, format='json')
        self.assertEqual(retire.status_code, status.HTTP_200_OK, retire.data)
        self.assertEqual(retire.data['status'], 'retired')

    def test_agents_can_be_steered_and_their_decisions_filtered(self):
        self.as_role('accountant')
        buyer = Agent.objects.get(scenario=self.scenario, role='buyer')
        response = self.client.patch(self.url('agent-detail', buyer.pk),
                                     {'brain': 'llm', 'policy': {'safety_stock_days': 9}}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        buyer.refresh_from_db()
        self.assertEqual((buyer.brain, buyer.policy['safety_stock_days']), ('llm', 9))
        listing = self.client.get(self.url('agentdecision-list'), {'agent__role': 'buyer'})
        self.assertTrue(listing.data['results'])
        self.assertTrue(all(d['role'] == 'buyer' for d in listing.data['results']))

    def test_experiments_run_on_create_and_report_a_comparison(self):
        self.as_role('accountant')
        response = self.client.post(self.url('experiment-list'), {
            'scenario': self.scenario.pk, 'name': 'Second shift', 'horizon_ticks': 3,
            'changes': {'shifts': {'LINE-A1': 2}}}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['status'], 'completed', response.data)
        self.assertIn('headline', response.data['summary'])
        bad = self.client.post(self.url('experiment-list'), {
            'scenario': self.scenario.pk, 'name': 'Bad', 'changes': {'teleport': True}}, format='json')
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ledger_and_trace_endpoints(self):
        self.as_role('viewer')
        ledger = self.client.get(self.url('scenario-ledger', self.scenario.pk), {'account': '1000'})
        self.assertEqual(ledger.status_code, status.HTTP_200_OK)
        self.assertEqual(ledger.data['account_code'], '1000')
        trace = self.client.get(self.url('scenario-trace', self.scenario.pk), {'reference': 'PO-00001'})
        self.assertEqual(trace.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.url('scenario-trace', self.scenario.pk)).status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_the_console_page_loads_its_client(self):
        response = self.client.get(reverse('simulation:console'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, 'simulation/console.js')
        self.assertContains(response, 'id="main-content"')
        # It is a page of the common shell: the shared sign-in, module navigation and search come with it.
        self.assertContains(response, 'workspace/js/shell.js')
        self.assertContains(response, 'data-page="console"')
        self.assertContains(response, 'aria-label="Modules"')


class SimulateCommandTests(TestCase):
    def test_parsers(self):
        self.assertEqual(parse_sets(['direct_labor_rate=30', 'demand_base.A-WIDGET=14']),
                         {'direct_labor_rate': 30, 'demand_base': {'A-WIDGET': 14}})
        self.assertEqual(parse_what_if(['add_line:A:2', 'shifts:LINE-A1=2', 'retire:LINE-B1', 'price:B-GADGET=-5',
                                        'param:demand_base.A-WIDGET=20']), {
            'add_lines': [{'family': 'A', 'machines': 2}], 'shifts': {'LINE-A1': 2}, 'retire_lines': ['LINE-B1'],
            'price_change_pct': {'B-GADGET': -5.0}, 'parameters': {'demand_base': {'A-WIDGET': 20}}})

    def test_the_command_bootstraps_runs_and_compares(self):
        out = io.StringIO()
        call_command('simulate', ticks=3, start='2027-01-01', quiet=True, stdout=out)
        self.assertIn('books balanced', out.getvalue())
        self.assertEqual(Scenario.objects.get().tick, 3)
        out = io.StringIO()
        call_command('simulate', what_if=['add_line:A'], horizon=2, stdout=out)
        self.assertIn('live books are unchanged', out.getvalue())
        self.assertEqual(Scenario.objects.get().tick, 3)


class WorldTests(TestCase):
    def test_absorption_rate_is_budgeted_cost_over_normal_capacity(self):
        scenario = bootstrap()
        line = scenario.lines.get(code='LINE-A1')
        values = params_module.resolve(scenario.parameters)
        costs = world.daily_line_costs(line, values, world.maintenance_share(scenario))
        # 1 machine x 2 crew x 8 h x $25 = $400 of direct labor a day on one shift.
        self.assertEqual(costs['direct_labor'], Decimal('400'))
        self.assertEqual(costs['normal_hours'], Decimal('6.8'))
        rate = world.standard_absorption_rate(line, values, world.maintenance_share(scenario))
        total = sum(costs[k] for k in ('direct_labor', 'indirect_labor', 'depreciation', 'energy', 'maintenance'))
        self.assertEqual(rate, (total / costs['normal_hours']).quantize(Decimal('0.01')))
        line.shifts = 2
        self.assertGreater(world.daily_line_costs(line, values)['direct_labor'], Decimal('800'),
                           'the late shift is paid a premium')
