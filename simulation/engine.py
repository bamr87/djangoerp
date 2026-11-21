"""
The tick engine: one call to ``run_tick`` advances the sandbox economy by
``scenario.tick_days`` days. Agents act in the order a business day runs:

    market -> suppliers (prices) -> maintenance -> customers -> sales manager
    -> buyer (safety stock, MRP, purchase orders) -> suppliers (deliveries)
    -> planner -> line crews and machines -> shipping and billing
    -> plant manager -> controller (period costs, payables, financing)
    -> books compiled, guardrails checked, snapshot written.

Every write goes through the ERP's own services (sales, purchasing,
manufacturing, invoices, mrp, journal.post_entry), each wrapped in a savepoint
so a refused action (insufficient stock, a credit limit) is recorded as a
rejected decision instead of breaking the tick. The whole tick is one
transaction: if the trial balance or balance sheet stops balancing, the tick
rolls back and the scenario halts.

Randomness is common-random-numbers by construction: every draw comes from a
generator seeded with (scenario seed, tick, agent, purpose), so a what-if
variant sees the same demand shocks as its baseline unless its own decisions
change what happens.
"""
import logging
import math
import random
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers

from core.audit import log_event
from core.models import DocumentSequence
from inventory.models import StockLevel
from invoices.models import Invoice, Payment
from invoices.services import post_invoice, post_payment
from manufacturing import services as manufacturing_services
from manufacturing.models import WorkOrder
from mrp.engine import run_mrp
from mrp.models import MRPRun
from mrp.services import convert_run
from products.models import Product
from purchasing import services as purchasing_services
from purchasing.models import PurchaseOrder
from sales import services as sales_services
from sales.models import SalesOrder, SalesOrderLine

from . import brains, finance
from . import parameters as params_module
from . import world
from .models import Agent, AgentDecision, ProductionJob, Scenario, SupplierPayable, TickSnapshot

logger = logging.getLogger(__name__)

ZERO = Decimal('0')
CENT = Decimal('0.01')
QTY = Decimal('0.001')
PRICE = Decimal('0.0001')


class GuardrailBreach(Exception):
    """The books stopped balancing: the tick is rolled back and the scenario halts."""


def season_factor(values, day_index):
    period = max(int(values['seasonality_period_days']), 1)
    return 1 + float(values['seasonality_amplitude']) * math.sin(2 * math.pi * day_index / period)


def trend_factor(values, day_index):
    return (1 + float(values['market_growth_pct']) / 100) ** (day_index / 365)


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam < 30:
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def r2(value):
    return round(float(value), 2)


def qty(value):
    """A quantity for a sentence: 10, 2.5 — never 10.000."""
    return f'{float(value):g}'


class Tick:
    """The working context of one tick: who acts, what they saw, what it cost."""

    def __init__(self, scenario, *, force_heuristic=False, client_factory=None):
        self.scenario = scenario
        self.values = params_module.resolve(scenario.parameters)
        self.tick = scenario.tick + 1
        self.prev_date = scenario.current_date
        self.days = scenario.tick_days
        self.date = scenario.current_date + timedelta(days=self.days)
        self.day_index = (self.date - scenario.start_date).days
        self.state = scenario.state
        self.force_heuristic = force_heuristic
        self.client_factory = client_factory
        self.acc = finance.accounts()
        self.agents = list(
            scenario.agents.filter(is_active=True).select_related('partner', 'line', 'user', 'cost_account')
        )
        self.by_role = defaultdict(list)
        for agent in self.agents:
            self.by_role[agent.role].append(agent)
        skus = list(world.FINISHED_GOODS) + list(world.MATERIALS)
        self.products = {p.sku: p for p in Product.objects.filter(sku__in=skus).select_related(
            'category', 'default_supplier')}
        self.lines = list(scenario.lines.exclude(status='retired').select_related('family'))
        self.warehouse = scenario.warehouse
        self.costs = defaultdict(lambda: ZERO)
        self.ops = defaultdict(float)
        self.sku_ops = defaultdict(lambda: defaultdict(float))
        self.line_stats = {
            line.code: {'available': 0.0, 'used': 0.0, 'downtime': 0.0, 'units': 0.0, 'productivity': 0.0}
            for line in self.lines
        }
        self.pending = []
        self.counters = defaultdict(lambda: {'decisions': 0, 'llm_calls': 0, 'ai_cost': ZERO})
        self.llm_calls = 0
        self.ai_cost = ZERO
        self.commentary = ''

    # --- plumbing ---------------------------------------------------------------------------------

    def one(self, role):
        agents = self.by_role.get(role) or []
        return agents[0] if agents else None

    def rng(self, *keys):
        return random.Random(':'.join(str(k) for k in (self.scenario.seed, self.tick) + keys))

    def due(self, agent):
        return (self.tick - 1) % max(agent.decision_interval, 1) == 0

    def record(self, agent, action, summary, *, kind='action', rationale='', observation=None, payload=None,
               documents=None, driver='heuristic', status='applied', error='', model='', input_tokens=0,
               output_tokens=0, cost=ZERO):
        self.pending.append(AgentDecision(
            scenario=self.scenario, agent=agent, tick=self.tick, sim_date=self.date, kind=kind, action=action,
            summary=summary[:255], rationale=rationale, observation=observation or {}, payload=payload or {},
            documents=[d for d in (documents or []) if d], driver=driver, status=status, error=error[:2000],
            model=model, input_tokens=input_tokens, output_tokens=output_tokens, cost=cost,
        ))
        counter = self.counters[agent.pk]
        counter['decisions'] += 1

    def attempt(self, agent, action, fn, *, summary, documents=None, payload=None):
        """Run one ERP action in a savepoint; record a rejection instead of failing the tick."""
        try:
            with transaction.atomic():
                return fn()
        except serializers.ValidationError as exc:
            detail = exc.detail if hasattr(exc, 'detail') else str(exc)
            self.record(agent, action, f'{summary} — refused', status='rejected', error=str(detail),
                        documents=documents, payload=payload)
            return None

    def review(self, agent, *, observation, proposal, fields, rationale):
        """A policy review through the agent's brain; returns the (clamped) values it settled on."""
        use_llm = not self.force_heuristic and (
            agent.brain == 'llm' or (agent.brain == 'auto' and self.scenario.driver == 'llm'))
        budget_note = ''
        if use_llm and self.llm_calls >= int(self.values['llm_max_calls_per_tick']):
            use_llm, budget_note = False, 'Claude call budget for this tick is spent; heuristic policy used.'
        brain = (brains.ClaudeBrain(agent.model or self.scenario.agent_model, client_factory=self.client_factory)
                 if use_llm else brains.HEURISTIC)
        decision = brain.review(agent=agent, observation=observation, proposal=proposal, fields=fields,
                                rationale=rationale)
        if decision.input_tokens or decision.driver == 'llm':
            self.llm_calls += 1
            self.ai_cost += decision.cost
            self.counters[agent.pk]['llm_calls'] += 1
            self.counters[agent.pk]['ai_cost'] += decision.cost
        error = '; '.join(filter(None, [decision.error, budget_note] + decision.notes))
        self.record(
            agent, f'{agent.role}.review', self.policy_summary(agent, decision.values), kind='policy',
            rationale=decision.rationale, observation=observation,
            payload={'proposal': proposal, 'decision': decision.values}, driver=decision.driver, error=error,
            model=decision.model, input_tokens=decision.input_tokens, output_tokens=decision.output_tokens,
            cost=decision.cost,
        )
        return decision.values

    @staticmethod
    def policy_summary(agent, values):
        parts = []
        for key, value in values.items():
            if isinstance(value, dict):
                inner = ', '.join(f'{k} {v:+.1f}' if isinstance(v, (int, float)) else f'{k} {v}'
                                  for k, v in value.items())
                parts.append(f'{key}: {inner}')
            elif isinstance(value, float):
                parts.append(f'{key} {value:.3g}')
            elif isinstance(value, str) and len(value) > 60:
                continue
            else:
                parts.append(f'{key} {value}')
        return f'{agent.code} policy — ' + '; '.join(parts)

    def on_hand(self):
        return {
            level.product.sku: level.quantity_on_hand
            for level in StockLevel.objects.filter(warehouse=self.warehouse).select_related('product')
        }

    def cash(self):
        return finance.balances(self.date).get(finance.CODE['cash'], ZERO)

    def oar(self, line):
        controller = self.one('controller')
        rates = (controller.policy.get('oar') if controller else None) or {}
        if line.code in rates:
            return Decimal(str(rates[line.code]))
        return world.standard_absorption_rate(line, self.values, world.maintenance_share(self.scenario))

    def demand_ema(self, sku):
        return float(self.state.get('demand_ema', {}).get(sku, self.values['demand_base'].get(sku, 0)) or 0)

    # --- phases -----------------------------------------------------------------------------------

    def run(self):
        self.phase_market()
        self.phase_supplier_prices()
        self.phase_maintenance()
        self.phase_customers()
        self.phase_sales()
        self.phase_procurement()
        self.phase_deliveries()
        self.phase_planning()
        self.phase_production()
        self.phase_shipping()
        self.phase_plant_manager()
        self.phase_controller()

    def phase_market(self):
        market = self.one('market')
        if market is None:
            return
        macro = self.state.setdefault('macro', {'sentiment': 0.0, 'competitor_price_index': 1.0})
        if not self.due(market):
            return
        r = self.rng('market')
        interval = max(market.decision_interval, 1) * self.days
        vol = float(self.values['macro_volatility'])
        sentiment = clamp(0.8 * macro['sentiment'] + r.gauss(0, vol), -0.5, 0.5)
        drift = float(self.values['material_inflation_pct']) / 100 * interval / 365
        cpi = clamp(macro['competitor_price_index'] * math.exp(drift + r.gauss(0, 0.015)), 0.6, 1.6)
        season_now = season_factor(self.values, self.day_index)
        season_ahead = season_factor(self.values, self.day_index + 28)
        proposal = {
            'sentiment': round(sentiment, 3), 'competitor_price_index': round(cpi, 3),
            'narrative': (f'Buyer sentiment {"firms" if sentiment >= macro["sentiment"] else "softens"} to '
                          f'{sentiment:+.2f}; competitors price at {cpi:.2f}x reference; season factor '
                          f'{season_now:.2f} heading to {season_ahead:.2f}.'),
        }
        our_index = {
            sku: r2(self.products[sku].sales_price) / self.state['reference_prices'][sku]
            for sku in world.FINISHED_GOODS if sku in self.products
        }
        values = self.review(market, observation={
            'date': str(self.date), 'previous': macro, 'season_factor_now': round(season_now, 3),
            'season_factor_in_4_weeks': round(season_ahead, 3), 'our_price_index': our_index,
            'material_inflation_pct': self.values['material_inflation_pct'],
        }, proposal=proposal, fields={
            'sentiment': brains.number(-0.5, 0.5, 'Demand sentiment; multiplies demand by e^sentiment.'),
            'competitor_price_index': brains.number(0.6, 1.6, 'Competitor price level as a multiple of the '
                                                              'reference list prices.'),
            'narrative': brains.text('One sentence describing the market this period.'),
        }, rationale=proposal['narrative'])
        macro.update({'sentiment': values['sentiment'], 'competitor_price_index': values['competitor_price_index']})
        market.policy = {**market.policy, **values}
        market.save(update_fields=['policy', 'updated_at'])

    def phase_supplier_prices(self):
        mu = math.log(1 + float(self.values['material_inflation_pct']) / 100) / 365
        sigma = float(self.values['material_price_volatility'])
        for supplier in self.by_role.get('supplier', []):
            r = self.rng('supplier-price', supplier.code)
            index = float(supplier.policy.get('price_index', 1.0))
            index = clamp(index * math.exp(mu * self.days + sigma * math.sqrt(self.days) * r.gauss(0, 1)), 0.3, 5)
            supplier.policy['price_index'] = round(index, 4)
            if self.due(supplier):
                late = SupplierPayable.objects.filter(scenario=self.scenario, supplier=supplier.partner,
                                                      paid_on__isnull=True, due_on__lt=self.date).count()
                base_reliability = float(self.values['supplier_reliability'])
                proposal = {'price_change_pct': 0.0,
                            'reliability': round(clamp(base_reliability - 0.05 * late, 0.3, 1.0), 3)}
                volume = PurchaseOrder.objects.filter(
                    supplier=supplier.partner, order_date__gt=self.date - timedelta(days=30),
                ).count()
                values = self.review(supplier, observation={
                    'price_index': supplier.policy['price_index'], 'orders_last_30_days': volume,
                    'overdue_invoices_from_buyer': late, 'contract_reliability': base_reliability,
                }, proposal=proposal, fields={
                    'price_change_pct': brains.number(-5, 8, 'One-off change to your price index, in percent.'),
                    'reliability': brains.number(0.3, 1.0, 'Probability you deliver a due order on a given day.'),
                }, rationale=(f'Index {supplier.policy["price_index"]:.3f} follows inflation; reliability '
                              f'{proposal["reliability"]:.2f} reflects {late} overdue payment(s) from the buyer.'))
                supplier.policy['price_index'] = round(index * (1 + values['price_change_pct'] / 100), 4)
                supplier.policy['reliability'] = values['reliability']
            supplier.save(update_fields=['policy', 'updated_at'])

    def phase_maintenance(self):
        tech = self.one('maintenance')
        if tech is None:
            return
        history = [e for e in tech.state.get('events', []) if e[0] > self.tick - 30]
        if self.due(tech):
            repairs = sum(1 for e in history if e[1] == 'repair')
            services_done = sum(1 for e in history if e[1] == 'pm')
            threshold = float(tech.policy.get('pm_wear_threshold', self.values['pm_wear_threshold']))
            if repairs > 2:
                proposal, why = threshold - 0.05, f'{repairs} breakdowns in 30 ticks: service earlier.'
            elif repairs == 0 and services_done >= 2:
                proposal, why = threshold + 0.05, 'No breakdowns while servicing often: stretch the interval.'
            else:
                proposal, why = threshold, f'{repairs} breakdown(s), {services_done} service(s): keep the threshold.'
            values = self.review(tech, observation={
                'wear': {line.code: r2(line.wear) for line in self.lines},
                'breakdowns_30_ticks': repairs, 'services_30_ticks': services_done,
                'repair_cost': self.values['repair_cost'], 'pm_cost': self.values['pm_cost'],
                'repair_hours': self.values['repair_hours'], 'pm_hours': self.values['pm_hours'],
            }, proposal={'pm_wear_threshold': round(clamp(proposal, 0.1, 0.95), 3)}, fields={
                'pm_wear_threshold': brains.number(0.1, 0.95, 'Service a line once its wear reaches this.'),
            }, rationale=why)
            tech.policy['pm_wear_threshold'] = values['pm_wear_threshold']
        threshold = Decimal(str(tech.policy.get('pm_wear_threshold', self.values['pm_wear_threshold'])))
        for line in self.lines:
            stats = self.line_stats[line.code]
            if line.status == 'down':
                cost = Decimal(str(self.values['repair_cost']))
                self.costs['maintenance_parts'] += cost
                stats['downtime'] += float(self.values['repair_hours'])
                line.wear = max(Decimal('0'), line.wear - Decimal('0.3'))
                line.status = 'running'
                line.save(update_fields=['wear', 'status', 'updated_at'])
                history.append([self.tick, 'repair'])
                self.ops['repairs'] += 1
                self.record(tech, 'repair', f'Repaired {line.code}: {cost} parts, {self.values["repair_hours"]} h down',
                            payload={'line': line.code, 'cost': str(cost)})
            elif line.wear >= threshold:
                cost = Decimal(str(self.values['pm_cost']))
                self.costs['maintenance_parts'] += cost
                stats['downtime'] += float(self.values['pm_hours'])
                worn = line.wear
                line.wear = Decimal('0.05')
                line.save(update_fields=['wear', 'updated_at'])
                history.append([self.tick, 'pm'])
                self.ops['preventive_services'] += 1
                self.record(tech, 'preventive_maintenance',
                            f'Serviced {line.code} at wear {worn:.2f}: {cost}, {self.values["pm_hours"]} h down',
                            payload={'line': line.code, 'cost': str(cost), 'wear_before': str(worn)})
        tech.state = {**tech.state, 'events': history[-60:]}
        tech.save(update_fields=['policy', 'state', 'updated_at'])

    def open_receivables(self, partner):
        invoiced = Invoice.objects.filter(partner=partner, journal_entry__isnull=False).exclude(
            status__in=('paid', 'cancelled')).aggregate(v=Sum('total'))['v'] or ZERO
        paid = Payment.objects.filter(invoice__partner=partner, journal_entry__isnull=False).exclude(
            invoice__status__in=('paid', 'cancelled')).aggregate(v=Sum('amount'))['v'] or ZERO
        return invoiced - paid

    def phase_customers(self):
        macro = self.state.get('macro', {})
        season = season_factor(self.values, self.day_index)
        trend = trend_factor(self.values, self.day_index)
        sentiment = float(macro.get('sentiment', 0.0))
        cpi = float(macro.get('competitor_price_index', 1.0))
        elasticity = float(self.values['price_elasticity'])
        sigma = float(self.values['demand_volatility'])
        for customer in self.by_role.get('customer', []):
            persona = customer.state.get('persona', {})
            self._customer_payments(customer)
            self._customer_cancellations(customer)
            if self.due(customer):
                otif = float(customer.state.get('otif', 1.0))
                price_gap = sum(
                    r2(self.products[sku].sales_price) / (self.state['reference_prices'][sku] * cpi)
                    for sku in world.FINISHED_GOODS
                ) / len(world.FINISHED_GOODS)
                proposal = {
                    'order_multiplier': round(clamp(0.6 + 0.4 * otif, 0.3, 1.8), 3),
                    'extra_payment_delay_days': round(clamp(-sentiment * 20, 0, 60), 1),
                }
                values = self.review(customer, observation={
                    'persona': persona, 'on_time_in_full_rate': round(otif, 3),
                    'our_price_vs_competitors': round(price_gap, 3), 'market_sentiment': round(sentiment, 3),
                    'open_receivables': r2(self.open_receivables(customer.partner)),
                }, proposal=proposal, fields={
                    'order_multiplier': brains.number(0.3, 1.8, 'How much of your usual demand you place with '
                                                                'this supplier.'),
                    'extra_payment_delay_days': brains.number(0, 60, 'Days you pay after the invoice due date.'),
                }, rationale=(f'OTIF {otif:.0%} sets loyalty at {proposal["order_multiplier"]:.2f}; sentiment '
                              f'{sentiment:+.2f} sets payment delay {proposal["extra_payment_delay_days"]:.0f} days.'))
                customer.policy = {**customer.policy, **values}
            multiplier = float(customer.policy.get('order_multiplier', 1.0))
            lines = []
            potential = 0.0
            for sku, share in persona.get('shares', {}).items():
                product = self.products.get(sku)
                if product is None or not share:
                    continue
                base = float(self.values['demand_base'].get(sku, 0)) * share * self.days
                ref = self.state['reference_prices'][sku] * cpi
                price = float(product.sales_price) * (1 - persona.get('discount', 0))
                price_factor = (price / ref) ** (elasticity * persona.get('price_sensitivity', 1.0)) if ref else 1
                lam = base * season * trend * math.exp(sentiment) * price_factor * multiplier
                r = self.rng('demand', customer.code, sku)
                lam *= math.exp(r.gauss(0, sigma) - sigma * sigma / 2)
                potential += lam
                quantity = poisson(r, lam)
                self.sku_ops[sku]['demand_potential'] += lam
                if quantity > 0:
                    lines.append((product, Decimal(quantity), Decimal(str(round(price, 2)))))
            self.ops['demand_potential'] += potential
            if lines:
                lead = max(int(self.values['customer_lead_time_days']) + int(persona.get('lead_offset_days', 0)), 1)
                order = SalesOrder.objects.create(
                    number=DocumentSequence.next_number('SO'), customer=customer.partner, warehouse=self.warehouse,
                    order_date=self.date, requested_date=self.date + timedelta(days=lead),
                    notes=f'Placed by agent {customer.code}', created_by=customer.user,
                )
                for product, quantity, price in lines:
                    SalesOrderLine.objects.create(order=order, product=product, quantity=quantity, unit_price=price)
                    self.sku_ops[product.sku]['ordered'] += float(quantity)
                    self.ops['units_ordered'] += float(quantity)
                self.ops['orders_placed'] += 1
                detail = ', '.join(f'{qty(q)} x {p.sku} @ {pr}' for p, q, pr in lines)
                self.record(customer, 'place_order', f'{order.number}: {detail}, wanted {order.requested_date}',
                            documents=[order.number], payload={'lines': detail, 'requested': str(order.requested_date)})
            customer.save(update_fields=['policy', 'state', 'updated_at'])

    def _customer_payments(self, customer):
        delay_days = float(customer.policy.get('extra_payment_delay_days', 0))
        spread = float(self.values['payment_delay_volatility_days'])
        invoices = Invoice.objects.filter(partner=customer.partner, status='sent', journal_entry__isnull=False)
        for invoice in invoices.order_by('due_date', 'id'):
            r = random.Random(f'{self.scenario.seed}:pay:{invoice.invoice_number}')
            pay_on = invoice.due_date + timedelta(days=round(r.gauss(delay_days, spread)))
            if pay_on > self.date:
                continue
            paid = invoice.payments.filter(journal_entry__isnull=False).aggregate(v=Sum('amount'))['v'] or ZERO
            amount = (invoice.total - paid).quantize(CENT)
            if amount <= 0:
                continue

            def pay(invoice=invoice, amount=amount):
                payment = Payment.objects.create(
                    payment_reference=DocumentSequence.next_number('PAY'), invoice=invoice, amount=amount,
                    payment_method='bank_transfer', payment_date=self.date, deposit_account=self.acc['cash'],
                    created_by=customer.user,
                )
                post_payment(payment, user=customer.user)
                return payment

            payment = self.attempt(customer, 'pay_invoice', pay, summary=f'Pay {invoice.invoice_number}')
            if payment is not None:
                late = (self.date - invoice.due_date).days
                self.ops['cash_collected'] += float(amount)
                self.ops['payments_received'] += 1
                self.record(customer, 'pay_invoice',
                            f'Paid {invoice.invoice_number} {amount} ({late:+d} days vs due)',
                            documents=[invoice.invoice_number, payment.payment_reference,
                                       payment.journal_entry.entry_number], payload={'days_late': late})

    def _customer_cancellations(self, customer):
        limit = int(self.values['cancel_after_days_late'])
        stale = SalesOrder.objects.filter(
            customer=customer.partner, warehouse=self.warehouse, status='confirmed',
            requested_date__lt=self.date - timedelta(days=limit),
        )
        for order in stale:
            if order.lines.filter(shipped_quantity__gt=0).exists():
                continue
            units = float(order.lines.aggregate(v=Sum('quantity'))['v'] or 0)
            result = self.attempt(customer, 'cancel_order',
                                  lambda order=order: sales_services.cancel_sales_order(order, user=customer.user),
                                  summary=f'Cancel {order.number}')
            if result is not None:
                self.ops['orders_cancelled'] += 1
                self.ops['units_cancelled'] += units
                for line in order.lines.all():
                    self.sku_ops[line.product.sku]['cancelled'] += float(line.quantity)
                customer.state['otif'] = 0.8 * float(customer.state.get('otif', 1.0))
                self.record(customer, 'cancel_order',
                            f'Cancelled {order.number} ({units:g} units): more than {limit} days late — lost sale',
                            documents=[order.number], payload={'units': units})

    def phase_sales(self):
        manager = self.one('sales_manager')
        if manager is None:
            return
        limit = Decimal(str(self.values['credit_limit']))
        for order in SalesOrder.objects.filter(warehouse=self.warehouse, status='draft').select_related('customer'):
            exposure = self.open_receivables(order.customer)
            if exposure > limit:
                result = self.attempt(manager, 'reject_order',
                                      lambda order=order: sales_services.cancel_sales_order(order, user=manager.user),
                                      summary=f'Reject {order.number}')
                if result is not None:
                    self.ops['orders_rejected'] += 1
                    self.record(manager, 'reject_order',
                                f'Rejected {order.number}: {order.customer.code} owes {exposure} over limit {limit}',
                                documents=[order.number])
                continue
            result = self.attempt(manager, 'confirm_order',
                                  lambda order=order: sales_services.confirm_sales_order(order, user=manager.user),
                                  summary=f'Confirm {order.number}')
            if result is not None:
                self.record(manager, 'confirm_order', f'Confirmed {order.number} for {order.customer.code} '
                            f'({order.total_amount})', documents=[order.number])
        if self.due(manager):
            self._pricing_review(manager)

    def unit_cost(self, sku, levels=None):
        level = (levels or {}).get(sku)
        if level is not None and level.average_cost > 0:
            return level.average_cost
        product = self.products[sku]
        bom = world.FINISHED_GOODS[sku][6]
        material = sum((self.products[c].standard_cost * q for c, q in bom.items()), ZERO)
        family_lines = [line for line in self.lines if world.family_of(product) == world.family_of_line(line)]
        rate = self.oar(family_lines[0]) if family_lines else ZERO
        return material + Decimal(str(self.values['std_hours'].get(sku, 0))) * rate

    def backlog(self):
        rows = SalesOrderLine.objects.filter(
            order__warehouse=self.warehouse, order__status__in=SalesOrder.OPEN_STATUSES,
        ).values('product__sku').annotate(q=Sum('quantity'), s=Sum('shipped_quantity'))
        return {row['product__sku']: float((row['q'] or 0) - (row['s'] or 0)) for row in rows}

    def _pricing_review(self, manager):
        levels = {lvl.product.sku: lvl for lvl in StockLevel.objects.filter(
            warehouse=self.warehouse).select_related('product')}
        backlog = self.backlog()
        cpi = float(self.state.get('macro', {}).get('competitor_price_index', 1.0))
        step = float(self.values['max_price_change_pct'])
        target = float(self.values['target_margin_pct']) / 100
        proposal, reasons, observation = {}, [], {}
        for sku in world.FINISHED_GOODS:
            product = self.products[sku]
            price = float(product.sales_price)
            cost = float(self.unit_cost(sku, levels))
            ema = max(self.demand_ema(sku), 0.1)
            on_hand = float(levels[sku].quantity_on_hand) if sku in levels else 0.0
            backlog_days = backlog.get(sku, 0.0) / ema
            stock_days = on_hand / ema
            margin = (price - cost) / price if price else 0
            market_price = self.state['reference_prices'][sku] * cpi
            if margin < target:
                change, why = step, f'margin {margin:.0%} under target'
            elif backlog_days > 6:
                change, why = step / 2, f'backlog {backlog_days:.1f} days'
            elif stock_days > 10:
                change, why = -step / 2, f'{stock_days:.1f} days of stock'
            else:
                change = clamp((market_price - price) / price * 100 * 0.25, -step, step)
                why = f'drift toward market {market_price:.0f}'
            proposal[sku] = round(change, 2)
            reasons.append(f'{sku} {change:+.1f}% ({why})')
            observation[sku] = {'price': round(price, 2), 'unit_cost': round(cost, 2),
                                'margin_pct': round(margin * 100, 1),
                                'competitor_price': round(market_price, 2), 'demand_per_day': round(ema, 2),
                                'backlog_days': round(backlog_days, 1), 'stock_days': round(stock_days, 1)}
        values = self.review(manager, observation={'products': observation, 'target_margin_pct': target * 100},
                             proposal={'price_change_pct': proposal}, fields={
            'price_change_pct': brains.number_map(world.FINISHED_GOODS, -step, step, 'Price change per product, %.'),
        }, rationale='; '.join(reasons))
        changes = values['price_change_pct']
        for sku, pct in changes.items():
            if not pct:
                continue
            product = self.products[sku]
            product.sales_price = (product.sales_price * Decimal(str(1 + pct / 100))).quantize(CENT)
            product.save(update_fields=['sales_price', 'updated_at'])
        manager.policy = {**manager.policy, 'last_price_changes_pct': changes}
        manager.save(update_fields=['policy', 'updated_at'])

    def phase_procurement(self):
        buyer = self.one('buyer')
        planner = self.one('planner') or buyer
        if buyer is None:
            return
        if self.due(buyer):
            late = SalesOrder.objects.filter(warehouse=self.warehouse, status__in=SalesOrder.OPEN_STATUSES,
                                             requested_date__lt=self.date).count()
            days = float(buyer.policy.get('safety_stock_days', self.values['safety_stock_days']))
            cash = float(self.cash())
            if late > 3 and cash > float(self.values['cash_buffer']):
                proposal, why = days + 1, f'{late} late orders and cash {cash:,.0f}: hold more stock.'
            elif late == 0 and cash < float(self.values['cash_buffer']):
                proposal, why = days - 1, f'No late orders but cash {cash:,.0f} is tight: release stock.'
            else:
                proposal, why = days, f'{late} late orders, cash {cash:,.0f}: keep {days:g} days.'
            values = self.review(buyer, observation={
                'late_orders': late, 'cash': round(cash), 'safety_stock_days': days,
                'demand_per_day': {sku: round(self.demand_ema(sku), 2) for sku in world.FINISHED_GOODS},
            }, proposal={'safety_stock_days': round(clamp(proposal, 0, 30), 2)}, fields={
                'safety_stock_days': brains.number(0, 30, 'Safety stock as days of average usage.'),
            }, rationale=why)
            buyer.policy['safety_stock_days'] = values['safety_stock_days']
            buyer.save(update_fields=['policy', 'updated_at'])
        days = Decimal(str(buyer.policy.get('safety_stock_days', self.values['safety_stock_days'])))
        usage = defaultdict(float)
        for sku, (_n, _f, _p, _l, _m, _o, bom) in world.FINISHED_GOODS.items():
            usage[sku] += self.demand_ema(sku)
            for component, quantity in bom.items():
                usage[component] += self.demand_ema(sku) * quantity
        for sku, daily in usage.items():
            product = self.products[sku]
            target = (Decimal(str(round(daily, 3))) * days).quantize(QTY)
            if product.safety_stock != target:
                product.safety_stock = target
                product.save(update_fields=['safety_stock', 'updated_at'])

        run = MRPRun.objects.create(number=DocumentSequence.next_number('MRP'), warehouse=self.warehouse,
                                    as_of_date=self.date, created_by=buyer.user)
        run_mrp(run.id)
        run.refresh_from_db()
        if run.status != 'completed':
            self.record(buyer, 'run_mrp', f'{run.number} failed', status='rejected', error=run.error_message,
                        documents=[run.number])
            return
        due = list(run.planned_orders.filter(status='planned', release_date__lte=self.date)
                   .values_list('id', flat=True))
        if not due:
            return
        converted = self.attempt(buyer, 'convert_plan',
                                 lambda: convert_run(run, user=buyer.user, planned_order_ids=due),
                                 summary=f'Convert {run.number}', documents=[run.number])
        if converted is None:
            return
        for po in converted['purchase_orders']:
            self._place_purchase_order(buyer, run, po)
        for wo in converted['work_orders']:
            self.record(planner, 'plan_work_order',
                        f'{wo.number}: make {qty(wo.quantity)} x {wo.product.sku} by {wo.due_date} (from {run.number})',
                        documents=[run.number, wo.number])

    def _place_purchase_order(self, buyer, run, po):
        supplier = next((s for s in self.by_role.get('supplier', []) if s.partner_id == po.supplier_id), None)
        index = Decimal(str(supplier.policy.get('price_index', 1.0))) if supplier else Decimal('1')
        lead = 0
        details = []
        for line in po.lines.select_related('product'):
            base = Decimal(str(self.values['material_base_cost'].get(line.product.sku, line.product.standard_cost)))
            line.unit_price = (base * index).quantize(PRICE)
            line.save(update_fields=['unit_price'])
            line.product.standard_cost = line.unit_price
            line.product.save(update_fields=['standard_cost', 'updated_at'])
            lead = max(lead, line.product.lead_time_days)
            details.append(f'{qty(line.quantity)} x {line.product.sku} @ {line.unit_price:.2f}')
        po.order_date = self.date
        po.expected_date = self.date + timedelta(days=lead)
        po.save(update_fields=['order_date', 'expected_date', 'updated_at'])
        confirmed = self.attempt(buyer, 'place_purchase_order',
                                 lambda: purchasing_services.confirm_purchase_order(po, user=buyer.user),
                                 summary=f'Confirm {po.number}', documents=[po.number])
        if confirmed is not None:
            self.ops['purchase_orders'] += 1
            self.ops['purchase_value'] += float(po.total_amount)
            self.record(buyer, 'place_purchase_order',
                        f'{po.number} to {po.supplier.code}: {", ".join(details)}; expected {po.expected_date}',
                        documents=[run.number, po.number], payload={'price_index': float(index)})

    def phase_deliveries(self):
        buyer = self.one('buyer')
        terms = int(self.values['supplier_payment_days'])
        for supplier in self.by_role.get('supplier', []):
            reliability = float(supplier.policy.get('reliability', self.values['supplier_reliability']))
            orders = PurchaseOrder.objects.filter(
                supplier=supplier.partner, warehouse=self.warehouse, status__in=PurchaseOrder.OPEN_STATUSES,
                expected_date__lte=self.date,
            )
            for order in orders:
                if self.rng('deliver', supplier.code, order.number).random() >= reliability:
                    self.ops['delivery_delays'] += 1
                    self.record(supplier, 'delay_delivery', f'{order.number} slips a day', kind='event',
                                driver='physics', documents=[order.number])
                    continue
                receiver = buyer.user if buyer else supplier.user
                receipt = self.attempt(
                    supplier, 'deliver',
                    lambda order=order: purchasing_services.receive_purchase_order(
                        order, user=receiver, receipt_date=self.date),
                    summary=f'Deliver {order.number}', documents=[order.number])
                if receipt is None:
                    continue
                amount = sum((m.total_value for m in receipt['moves']), Decimal('0.00'))
                SupplierPayable.objects.create(
                    scenario=self.scenario, supplier=supplier.partner, purchase_order=order,
                    grn=receipt['grn_number'], amount=amount, received_on=self.date,
                    due_on=self.date + timedelta(days=terms),
                )
                self.ops['deliveries'] += 1
                self.record(supplier, 'deliver', f'Delivered {order.number} as {receipt["grn_number"]} ({amount}); '
                            f'AP accrued, due in {terms} days',
                            documents=[order.number, receipt['grn_number'], receipt['journal_entry'].entry_number])

    def line_capacity_per_day(self, line):
        return float(line.machines * line.shifts) * float(self.values['shift_hours'])

    def phase_planning(self):
        planner = self.one('planner')
        if planner is None:
            return
        if self.due(planner):
            busy = [self._recent_utilization(line.code) for line in self.lines]
            peak = max(busy) if busy else 0
            proposal = 'shortest_job' if peak > 0.95 else 'due_date'
            values = self.review(planner, observation={
                'utilization': {line.code: round(u, 3) for line, u in zip(self.lines, busy)},
                'late_orders': SalesOrder.objects.filter(warehouse=self.warehouse, status='confirmed',
                                                         requested_date__lt=self.date).count(),
            }, proposal={'priority': proposal}, fields={
                'priority': brains.choice(['due_date', 'shortest_job'], 'How jobs are sequenced on each line.'),
            }, rationale=(f'Peak utilization {peak:.0%}: '
                          + ('shortest jobs first for throughput.' if proposal == 'shortest_job'
                             else 'earliest due date first.')))
            planner.policy['priority'] = values['priority']
            planner.save(update_fields=['policy', 'updated_at'])

        drafts = WorkOrder.objects.filter(warehouse=self.warehouse, status='draft').select_related('product__category')
        for wo in drafts:
            family = world.family_of(wo.product)
            candidates = [line for line in self.lines if world.family_of_line(line) == family]
            if not candidates:
                self.record(planner, 'schedule_work_order', f'{wo.number}: no line makes {wo.product.sku}',
                            status='rejected', documents=[wo.number])
                continue
            hours = (wo.quantity * Decimal(str(self.values['std_hours'].get(wo.product.sku, 1)))).quantize(CENT)

            def load(line):
                open_hours = ProductionJob.objects.filter(line=line, status__in=('queued', 'running')).aggregate(
                    r=Sum('hours_required'), d=Sum('hours_done'))
                return float((open_hours['r'] or 0) - (open_hours['d'] or 0)) / max(
                    self.line_capacity_per_day(line), 0.1)

            line = min(candidates, key=load)
            confirmed = self.attempt(planner, 'schedule_work_order',
                                     lambda wo=wo: manufacturing_services.confirm_work_order(wo, user=planner.user),
                                     summary=f'Confirm {wo.number}', documents=[wo.number])
            if confirmed is None:
                continue
            ProductionJob.objects.create(scenario=self.scenario, line=line, work_order=wo, hours_required=hours,
                                         queued_on=self.date)
            self.record(planner, 'schedule_work_order',
                        f'{wo.number} ({qty(wo.quantity)} x {wo.product.sku}, {hours} h) queued on {line.code}',
                        documents=[wo.number], payload={'line': line.code, 'hours': str(hours)})
        self._release_jobs(planner)

    def _job_order(self, jobs):
        priority = (self.one('planner').policy.get('priority') if self.one('planner') else 'due_date')
        if priority == 'shortest_job':
            return sorted(jobs, key=lambda j: (j.hours_required - j.hours_done, j.id))
        far = self.date + timedelta(days=3650)
        return sorted(jobs, key=lambda j: (j.work_order.due_date or far, j.id))

    def _release_jobs(self, planner):
        jobs = list(ProductionJob.objects.filter(scenario=self.scenario, status__in=('queued', 'running'))
                    .select_related('work_order__bom', 'work_order__product', 'line'))
        on_hand = self.on_hand()
        available = defaultdict(Decimal, {sku: qty for sku, qty in on_hand.items()})
        for job in jobs:
            if job.status == 'running':
                for component, quantity in manufacturing_services.component_requirements(job.work_order):
                    available[component.sku] -= quantity
        for job in self._job_order([j for j in jobs if j.status == 'queued']):
            needs = manufacturing_services.component_requirements(job.work_order)
            if any(available[c.sku] < q for c, q in needs):
                self.ops['jobs_waiting'] += 1
                continue
            started = self.attempt(planner, 'release_job',
                                   lambda job=job: manufacturing_services.start_work_order(job.work_order,
                                                                                           user=planner.user),
                                   summary=f'Start {job.work_order.number}', documents=[job.work_order.number])
            if started is None:
                continue
            for component, quantity in needs:
                available[component.sku] -= quantity
            job.status = 'running'
            job.started_on = self.date
            job.save(update_fields=['status', 'started_on', 'updated_at'])
            self.ops['jobs_started'] += 1
            self.record(planner, 'release_job', f'Kitted and released {job.work_order.number} on {job.line.code}',
                        documents=[job.work_order.number])

    def _recent_utilization(self, code):
        snaps = TickSnapshot.objects.filter(scenario=self.scenario).order_by('-tick')[:7]
        values = [s.kpis.get('lines', {}).get(code, {}).get('utilization') for s in snaps]
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else 0.0

    def phase_production(self):
        shift_hours = float(self.values['shift_hours'])
        energy_rate = Decimal(str(self.values['energy_cost_per_machine_hour']))
        consumables = Decimal(str(self.values['consumables_per_unit']))
        wear_rate = float(self.values['wear_per_hour'])
        hazard_base = float(self.values['breakdown_rate_per_1000h']) / 1000
        for line in self.lines:
            stats = self.line_stats[line.code]
            crew = next((a for a in self.by_role.get('operator', []) if a.line_id == line.pk), None)
            machine = next((a for a in self.by_role.get('machine', []) if a.line_id == line.pk), None)
            r = self.rng('crew', line.code)
            available = line.machines * line.shifts * shift_hours * self.days
            productivity = float(self.values['labor_productivity']) * (1 + r.gauss(0, 0.05))
            absence = clamp(r.gauss(float(self.values['absenteeism_rate']),
                                    float(self.values['absenteeism_rate']) / 2), 0, 0.6)
            capacity = max(0.0, available - stats['downtime']) * max(productivity, 0) * (1 - absence)
            stats.update({'available': available, 'productivity': productivity})
            remaining = capacity
            completed = []
            jobs = list(ProductionJob.objects.filter(line=line, status='running')
                        .select_related('work_order__product__category', 'work_order__bom'))
            for job in self._job_order(jobs):
                if remaining <= 0:
                    break
                need = float(job.hours_required - job.hours_done)
                use = min(need, remaining)
                if use > 0:
                    job.hours_done += Decimal(str(round(use, 2)))
                    remaining -= use
                    stats['used'] += use
                if job.hours_done >= job.hours_required:
                    job.hours_done = job.hours_required
                    completed.append(job)
                job.save(update_fields=['hours_done', 'updated_at'])
            for job in completed:
                self._complete_job(job, line, crew)
            used = stats['used']
            self.costs['energy'] += (Decimal(str(used)) * energy_rate).quantize(CENT)
            self.costs['consumables'] += Decimal(str(stats['units'])) * consumables
            line.run_hours += Decimal(str(round(used, 2)))
            line.wear = min(Decimal('1'), line.wear + Decimal(str(round(used * wear_rate, 4))))
            hazard = hazard_base * (1 + 3 * float(line.wear))
            broke = self.rng('breakdown', line.code).random() < 1 - math.exp(-hazard * used)
            if broke:
                line.status = 'down'
                self.ops['breakdowns'] += 1
                if machine:
                    self.record(machine, 'breakdown', f'{line.code} broke down at wear {line.wear:.2f} '
                                f'after {used:.1f} h', kind='event', driver='physics',
                                payload={'wear': float(line.wear), 'hours': used})
            line.save(update_fields=['run_hours', 'wear', 'status', 'updated_at'])
            if crew:
                self.record(crew, 'shift_report',
                            f'{line.code}: {available:g} h scheduled, {stats["downtime"]:g} h down, '
                            f'{used:.1f} h used of {capacity:.1f} h capacity (productivity {productivity:.2f}, '
                            f'absence {absence:.0%}), {len(completed)} job(s) done',
                            kind='event', driver='physics',
                            payload={'available': available, 'capacity': round(capacity, 2), 'used': round(used, 2),
                                     'productivity': round(productivity, 3), 'absence': round(absence, 3)})

    def _complete_job(self, job, line, crew):
        wo = job.work_order
        rate = self.oar(line)
        consumables = (wo.quantity * Decimal(str(self.values['consumables_per_unit']))).quantize(CENT)
        absorbed = (job.hours_required * rate).quantize(CENT) + consumables
        actor = crew or self.one('planner')
        result = self.attempt(actor, 'complete_work_order', lambda: manufacturing_services.complete_work_order(
            wo, user=actor.user, completion_date=self.date,
            conversion_costs=[{'account': self.acc['absorbed'], 'amount': absorbed,
                               'label': f'{job.hours_required} h x {rate} + consumables'}],
        ), summary=f'Complete {wo.number}', documents=[wo.number])
        if result is None:
            return
        job.status = 'completed'
        job.completed_on = self.date
        job.absorbed_value = absorbed
        job.save(update_fields=['status', 'completed_on', 'absorbed_value', 'updated_at'])
        receipt = result['receipt_move']
        self.line_stats[line.code]['units'] += float(wo.quantity)
        self.sku_ops[wo.product.sku]['produced'] += float(wo.quantity)
        self.ops['units_produced'] += float(wo.quantity)
        self.ops['jobs_completed'] += 1
        self.record(actor, 'complete_work_order',
                    f'{wo.number}: {qty(wo.quantity)} x {wo.product.sku} at {receipt.unit_cost:.2f}/unit '
                    f'(absorbed {absorbed} = {job.hours_required} h x {rate} + consumables {consumables})',
                    documents=[wo.number, result['journal_entry'].entry_number],
                    payload={'absorbed': str(absorbed), 'unit_cost': str(receipt.unit_cost)})

    def phase_shipping(self):
        manager = self.one('sales_manager')
        if manager is None:
            return
        on_hand = defaultdict(Decimal, self.on_hand())
        terms = int(self.values['customer_payment_days'])
        customers = {a.partner_id: a for a in self.by_role.get('customer', [])}
        orders = SalesOrder.objects.filter(warehouse=self.warehouse, status='confirmed').order_by(
            'requested_date', 'id').select_related('customer')
        for order in orders:
            lines = list(order.lines.select_related('product'))
            if any(on_hand[line.product.sku] < line.open_quantity for line in lines):
                continue
            customer = customers.get(order.customer_id)
            persona = customer.state.get('persona', {}) if customer else {}
            due = self.date + timedelta(days=max(int(round(terms * persona.get('terms_multiplier', 1))), 0))

            def ship_and_bill(order=order, due=due):
                shipment = sales_services.ship_sales_order(order, user=manager.user, ship_date=self.date)
                invoice = sales_services.create_invoice_from_order(order, user=manager.user, due_date=due)
                post_invoice(invoice, user=manager.user, posting_date=self.date)
                return shipment, invoice

            done = self.attempt(manager, 'ship_and_invoice', ship_and_bill, summary=f'Ship {order.number}',
                                documents=[order.number])
            if done is None:
                continue
            shipment, invoice = done
            invoice.refresh_from_db()
            on_time = order.requested_date is None or self.date <= order.requested_date
            units = 0.0
            for line in lines:
                on_hand[line.product.sku] -= line.open_quantity
                units += float(line.quantity)
                self.sku_ops[line.product.sku]['shipped'] += float(line.quantity)
            self.ops['units_shipped'] += units
            self.ops['orders_shipped'] += 1
            self.ops['orders_on_time'] += 1 if on_time else 0
            if customer:
                customer.state['otif'] = round(0.85 * float(customer.state.get('otif', 1.0))
                                               + 0.15 * (1.0 if on_time else 0.0), 4)
                customer.save(update_fields=['state', 'updated_at'])
            self.record(manager, 'ship_and_invoice',
                        f'Shipped {order.number} ({units:g} units, {"on time" if on_time else "LATE"}); '
                        f'invoiced {invoice.invoice_number} {invoice.total}, due {invoice.due_date}',
                        documents=[order.number, shipment['shipment_number'],
                                   shipment['journal_entry'].entry_number, invoice.invoice_number,
                                   invoice.journal_entry.entry_number],
                        payload={'on_time': on_time})

    def phase_plant_manager(self):
        plant = self.one('plant_manager')
        if plant is None or not self.due(plant):
            return
        backlog = self.backlog()
        max_shifts = int(self.values['max_shifts'])
        families = {}
        for sku in world.FINISHED_GOODS:
            family = world.FINISHED_GOODS[sku][1]
            hours = backlog.get(sku, 0.0) * float(self.values['std_hours'].get(sku, 0))
            demand_hours = self.demand_ema(sku) * float(self.values['std_hours'].get(sku, 0))
            entry = families.setdefault(family, {'backlog_hours': 0.0, 'demand_hours_per_day': 0.0})
            entry['backlog_hours'] += hours
            entry['demand_hours_per_day'] += demand_hours
        observation_lines, proposal, reasons = {}, {}, []
        for line in self.lines:
            util = self._recent_utilization(line.code)
            family = world.family_of_line(line)
            fam = families.get(family, {'backlog_hours': 0, 'demand_hours_per_day': 0})
            capacity = sum(self.line_capacity_per_day(other) for other in self.lines
                           if world.family_of_line(other) == family)
            backlog_days = fam['backlog_hours'] / max(capacity, 0.1)
            change = 0
            if util >= 0.9 and backlog_days > 1.5 and line.shifts < max_shifts:
                change = 1
            elif util < 0.45 and line.shifts > 1:
                change = -1
            proposal[line.code] = change
            reasons.append(f'{line.code} util {util:.0%}, backlog {backlog_days:.1f} days -> {change:+d} shift')
            observation_lines[line.code] = {
                'family': family, 'shifts': line.shifts, 'machines': line.machines,
                'utilization_7_ticks': round(util, 3),
                'family_backlog_days_of_capacity': round(backlog_days, 2),
                'family_demand_hours_per_day': round(fam['demand_hours_per_day'], 2),
                'family_capacity_hours_per_day': round(capacity, 2),
            }
        commission = 'none'
        if self.values['autonomous_capex']:
            for family in world.FAMILIES:
                fam_lines = [line for line in self.lines if world.family_of_line(line) == family]
                if fam_lines and all(line.shifts >= max_shifts for line in fam_lines) and all(
                        self._recent_utilization(line.code) >= 0.92 for line in fam_lines):
                    commission = family
                    reasons.append(f'family {family} is out of shifts at >92% utilization: buy a line')
                    break
        fields = {
            'shift_changes': brains.number_map([line.code for line in self.lines], -1, 1,
                                               'Shifts to add (+1) or remove (-1) per line.'),
            'commission_family': brains.choice(['none'] + list(world.FAMILIES),
                                               'Buy a new line for this product family, or none.'),
        }
        values = self.review(plant, observation={
            'lines': observation_lines, 'max_shifts': max_shifts,
            'may_buy_lines': bool(self.values['autonomous_capex']), 'line_capex': self.values['line_capex'],
            'cash': r2(self.cash()),
        }, proposal={'shift_changes': proposal, 'commission_family': commission}, fields=fields,
            rationale='; '.join(reasons) or 'Capacity matches demand.')
        actions = []
        for line in self.lines:
            change = int(round(values['shift_changes'].get(line.code, 0)))
            shifts = int(clamp(line.shifts + change, 1, max_shifts))
            if shifts != line.shifts:
                actions.append(f'{line.code} {line.shifts}->{shifts} shifts')
                line.shifts = shifts
                line.save(update_fields=['shifts', 'updated_at'])
        family = values['commission_family'] if self.values['autonomous_capex'] else 'none'
        if family != 'none':
            line = world.commission_line(self.scenario, family=family, date=self.date, user=plant.user,
                                         values=self.values)
            self.lines.append(line)
            self.line_stats[line.code] = {'available': 0.0, 'used': 0.0, 'downtime': 0.0, 'units': 0.0,
                                          'productivity': 0.0}
            actions.append(f'commissioned {line.code} ({line.capex_entry})')
            self.record(plant, 'commission_line', f'Commissioned {line.code} for family {family}: capex {line.capex}',
                        documents=[line.capex_entry])
        if actions:
            world.refresh_absorption_rates(self.scenario, self.values)
            self.record(plant, 'set_shifts', 'Plant manager: ' + '; '.join(actions))
        plant.policy = {**plant.policy, 'last_actions': actions}
        plant.save(update_fields=['policy', 'updated_at'])

    def phase_controller(self):
        controller = self.one('controller')
        if controller is None:
            return
        acc = self.acc
        user = controller.user
        days = Decimal(self.days)
        share = world.maintenance_share(self.scenario)
        direct = indirect = depreciation = ZERO
        for line in self.lines:
            budget = world.daily_line_costs(line, self.values, share)
            direct += budget['direct_labor'] * days
            indirect += budget['indirect_labor'] * days
            dep = min((line.capex / line.useful_life_days * days).quantize(CENT),
                      line.capex - line.accumulated_depreciation)
            if dep > 0:
                line.accumulated_depreciation += dep
                line.save(update_fields=['accumulated_depreciation', 'updated_at'])
                depreciation += dep
        by_account = defaultdict(lambda: ZERO)
        for agent in self.agents:
            if agent.daily_cost and agent.cost_account_id:
                by_account[agent.cost_account] += agent.daily_cost * days
        revolver = -finance.balances(self.date).get(finance.CODE['revolver'], ZERO)
        interest = (revolver * Decimal(str(self.values['revolver_rate_pct'])) / 100 / 365 * days).quantize(CENT)
        carry = Decimal(str(self.state.get('ai_cost_carry', '0'))) + self.ai_cost
        ai_posted = carry.quantize(CENT, rounding=ROUND_DOWN)
        self.state['ai_cost_carry'] = str(carry - ai_posted)

        by_account[acc['direct_labor']] += direct
        by_account[acc['indirect_labor']] += indirect
        by_account[acc['variable_overhead']] += self.costs['energy'] + self.costs['consumables']
        by_account[acc['maintenance']] += self.costs['maintenance_parts']
        by_account[acc['facility']] += Decimal(str(self.values['facility_cost_per_day'])) * days
        by_account[acc['ai_compute']] += ai_posted
        by_account[acc['interest']] += interest
        cash_lines = [{'account': account, 'debit': amount.quantize(CENT), 'reference': f'T{self.tick}'}
                      for account, amount in by_account.items() if amount.quantize(CENT) > 0]
        total = sum((line['debit'] for line in cash_lines), ZERO)
        documents = []
        if total > 0:
            entry = finance.post(
                date=self.date, description=f'Sandbox period costs, tick {self.tick} (payroll, overheads, AI)',
                lines=cash_lines + [{'account': acc['cash'], 'credit': total, 'reference': f'T{self.tick}'}],
                source_reference=f'sim:{self.scenario.pk}:t{self.tick}:costs', user=user)
            documents.append(entry.entry_number)
        if depreciation > 0:
            entry = finance.post(
                date=self.date, description=f'Sandbox depreciation, tick {self.tick}',
                lines=[{'account': acc['depreciation'], 'debit': depreciation, 'reference': f'T{self.tick}'},
                       {'account': acc['accumulated_depreciation'], 'credit': depreciation,
                        'reference': f'T{self.tick}'}],
                source_reference=f'sim:{self.scenario.pk}:t{self.tick}:depreciation', user=user)
            documents.append(entry.entry_number)
        variable = (self.costs['energy'] + self.costs['consumables']).quantize(CENT)
        self.record(controller, 'post_period_costs',
                    f'Posted period costs {total} (direct labor {direct.quantize(CENT)}, indirect '
                    f'{indirect.quantize(CENT)}, energy+consumables {variable}, '
                    f'maintenance parts {self.costs["maintenance_parts"]}) and depreciation {depreciation}',
                    documents=documents)
        self._financing(controller)
        if self.due(controller):
            self._controller_review(controller)

    def _financing(self, controller):
        acc = self.acc
        user = controller.user
        buffer = Decimal(str(controller.policy.get('cash_buffer', self.values['cash_buffer'])))
        limit = Decimal(str(self.values['revolver_limit']))
        due = list(SupplierPayable.objects.filter(scenario=self.scenario, paid_on__isnull=True,
                                                  due_on__lte=self.date).select_related('supplier'))
        due_total = sum((p.amount for p in due), ZERO)
        cash = self.cash()
        revolver = -finance.balances(self.date).get(finance.CODE['revolver'], ZERO)
        need = buffer + due_total - cash
        if need > 0 and revolver < limit:
            draw = min(need, limit - revolver).quantize(CENT)
            if draw > 0:
                entry = finance.post(
                    date=self.date, description=f'Revolver draw, tick {self.tick}',
                    lines=[{'account': acc['cash'], 'debit': draw}, {'account': acc['revolver'], 'credit': draw}],
                    source_reference=f'sim:{self.scenario.pk}:t{self.tick}:revolver-draw', user=user)
                cash += draw
                revolver += draw
                self.ops['revolver_draw'] += float(draw)
                self.record(controller, 'draw_revolver', f'Drew {draw} on the revolver (balance {revolver})',
                            documents=[entry.entry_number])
        for payable in due:
            if payable.amount > cash:
                self.ops['payables_deferred'] += 1
                continue
            entry = finance.post(
                date=self.date, description=f'Pay {payable.supplier.name} for {payable.grn}',
                lines=[{'account': acc['payable'], 'debit': payable.amount, 'reference': payable.grn},
                       {'account': acc['cash'], 'credit': payable.amount, 'reference': payable.grn}],
                source_reference=f'sim:{self.scenario.pk}:pay:{payable.grn}', user=user)
            payable.paid_on = self.date
            payable.payment_entry = entry.entry_number
            payable.save(update_fields=['paid_on', 'payment_entry', 'updated_at'])
            cash -= payable.amount
            self.ops['supplier_payments'] += float(payable.amount)
            self.record(controller, 'pay_supplier', f'Paid {payable.supplier.code} {payable.amount} for {payable.grn}',
                        documents=[payable.grn, entry.entry_number])
        if self.ops['payables_deferred']:
            self.record(controller, 'defer_payables', f'Deferred {int(self.ops["payables_deferred"])} due payable(s): '
                        f'cash {cash} short and the revolver is at its limit', status='rejected')
        if revolver > 0 and cash > 2 * buffer:
            repay = min(revolver, cash - buffer * Decimal('1.5')).quantize(CENT)
            if repay > 0:
                entry = finance.post(
                    date=self.date, description=f'Revolver repayment, tick {self.tick}',
                    lines=[{'account': acc['revolver'], 'debit': repay}, {'account': acc['cash'], 'credit': repay}],
                    source_reference=f'sim:{self.scenario.pk}:t{self.tick}:revolver-repay', user=user)
                self.ops['revolver_repay'] += float(repay)
                self.record(controller, 'repay_revolver', f'Repaid {repay} of the revolver',
                            documents=[entry.entry_number])

    def _controller_review(self, controller):
        share = world.maintenance_share(self.scenario)
        standard = {line.code: float(world.standard_absorption_rate(line, self.values, share)) for line in self.lines}
        recent = TickSnapshot.objects.filter(scenario=self.scenario).order_by('-tick')
        snaps = list(recent[:controller.decision_interval])
        pl = defaultdict(float)
        for snap in snaps:
            for key, value in snap.kpis.get('pl', {}).items():
                pl[key] += value
        revenue = pl.get('revenue', 0.0)
        direction = 'under' if pl.get('under_absorbed', 0) >= 0 else 'over'
        commentary = (
            f'Last {len(snaps)} ticks: revenue {revenue:,.0f}, gross margin '
            f'{(pl.get("gross_margin", 0) / revenue * 100) if revenue else 0:.1f}%, net income '
            f'{pl.get("net_income", 0):,.0f}. Conversion cost {pl.get("conversion_actual", 0):,.0f} against '
            f'{pl.get("absorbed", 0):,.0f} absorbed: {direction}-absorbed '
            f'by {abs(pl.get("under_absorbed", 0)):,.0f} — idle capacity is a period cost, not product cost. '
            f'Standard absorption rates are reset from budgeted line costs over normal capacity.'
        ) if snaps else 'Opening period: absorption rates set from budgeted line costs over normal capacity.'
        buffer = float(controller.policy.get('cash_buffer', self.values['cash_buffer']))
        values = self.review(controller, observation={
            'standard_rates_per_machine_hour': standard, 'trailing_pl': {k: round(v) for k, v in pl.items()},
            'cash': r2(self.cash()), 'cash_buffer': buffer,
        }, proposal={'oar_adjust_pct': {code: 0.0 for code in standard}, 'cash_buffer': buffer,
                     'commentary': commentary}, fields={
            'oar_adjust_pct': brains.number_map(list(standard), -10, 10, 'Adjustment to each standard absorption '
                                                                         'rate, in percent.'),
            'cash_buffer': brains.number(0, 500_000, 'Cash level below which you draw on the revolver.'),
            'commentary': brains.text('Period commentary explaining what drove the results.', 1200),
        }, rationale=commentary)
        rates = {code: round(rate * (1 + values['oar_adjust_pct'].get(code, 0) / 100), 2)
                 for code, rate in standard.items()}
        controller.policy = {**controller.policy, 'oar': rates, 'cash_buffer': values['cash_buffer'],
                             'commentary': values['commentary']}
        controller.save(update_fields=['policy', 'updated_at'])
        self.commentary = values['commentary']

    # --- close ------------------------------------------------------------------------------------

    def update_demand_averages(self):
        ema = self.state.setdefault('demand_ema', {})
        for sku in world.FINISHED_GOODS:
            daily = self.sku_ops[sku]['ordered'] / self.days
            ema[sku] = round(0.9 * float(ema.get(sku, daily)) + 0.1 * daily, 4)

    def flush(self):
        AgentDecision.objects.bulk_create(self.pending)
        for agent_id, counter in self.counters.items():
            agent = Agent.objects.get(pk=agent_id)
            agent.decisions_count += counter['decisions']
            agent.llm_calls += counter['llm_calls']
            agent.ai_cost += counter['ai_cost']
            agent.save(update_fields=['decisions_count', 'llm_calls', 'ai_cost'])


def compile_snapshot(scenario, *, tick, prev_date, date, ops=None, sku_ops=None, line_stats=None, ai=None,
                     commentary='', full_check=True):
    """Compile one tick's KPIs from the ledger and the documents (analytics, not the book of record)."""
    values = params_module.resolve(scenario.parameters)
    types = finance.account_types()
    moved = finance.movements(prev_date, date)
    pl = finance.profit_and_loss(moved, types)
    bs = finance.balance_position(finance.balances(date), types)
    guard = finance.guardrails(scenario, date, full=full_check)
    levels = {lvl.product.sku: lvl for lvl in StockLevel.objects.filter(
        warehouse=scenario.warehouse).select_related('product')}
    open_lines = SalesOrderLine.objects.filter(order__warehouse=scenario.warehouse,
                                               order__status__in=SalesOrder.OPEN_STATUSES)
    backlog = {row['product__sku']: float((row['q'] or 0) - (row['s'] or 0)) for row in open_lines.values(
        'product__sku').annotate(q=Sum('quantity'), s=Sum('shipped_quantity'))}
    late = open_lines.filter(order__requested_date__lt=date).values('order').distinct().count()
    products = {p.sku: p for p in Product.objects.filter(sku__in=world.FINISHED_GOODS)}
    ema = scenario.state.get('demand_ema', {})
    skus = {}
    for sku in world.FINISHED_GOODS:
        level = levels.get(sku)
        product = products.get(sku)
        skus[sku] = {
            'price': r2(product.sales_price) if product else 0.0,
            'unit_cost': r2(level.average_cost) if level else 0.0,
            'on_hand': float(level.quantity_on_hand) if level else 0.0,
            'backlog': backlog.get(sku, 0.0), 'demand_per_day': float(ema.get(sku, 0.0)),
            **{k: round(v, 2) for k, v in (sku_ops or {}).get(sku, {}).items()},
        }
    controller = scenario.agents.filter(role='controller').first()
    rates = (controller.policy.get('oar') if controller else {}) or {}
    lines = {}
    for line in scenario.lines.select_related('family'):
        stats = (line_stats or {}).get(line.code, {})
        available = stats.get('available', 0.0)
        lines[line.code] = {
            'family': world.family_of_line(line), 'status': line.status, 'shifts': line.shifts,
            'machines': line.machines, 'wear': r2(line.wear), 'oar': rates.get(line.code),
            'available_hours': round(available, 2), 'used_hours': round(stats.get('used', 0.0), 2),
            'downtime_hours': round(stats.get('downtime', 0.0), 2), 'units': stats.get('units', 0.0),
            'utilization': round(stats.get('used', 0.0) / available, 4) if available else 0.0,
        }
    suppliers = {a.code: a.policy.get('price_index', 1.0) for a in scenario.agents.filter(role='supplier')}
    macro = scenario.state.get('macro', {})
    ops = dict(ops or {})
    ops.update({
        'backlog_units': round(sum(backlog.values()), 2), 'late_orders': late,
        'open_purchase_orders': PurchaseOrder.objects.filter(warehouse=scenario.warehouse,
                                                             status__in=PurchaseOrder.OPEN_STATUSES).count(),
        'jobs_queued': ProductionJob.objects.filter(scenario=scenario, status='queued').count(),
        'jobs_running': ProductionJob.objects.filter(scenario=scenario, status='running').count(),
    })
    shipped = ops.get('orders_shipped', 0)
    ops['otif_pct'] = round(ops.get('orders_on_time', 0) / shipped * 100, 1) if shipped else None
    return {
        'tick': tick, 'date': str(date), 'pl': pl, 'bs': bs, 'ops': ops, 'skus': skus, 'lines': lines,
        'market': {'sentiment': macro.get('sentiment', 0.0),
                   'competitor_price_index': macro.get('competitor_price_index', 1.0),
                   'season_factor': round(season_factor(values, (date - scenario.start_date).days), 3),
                   'supplier_price_index': suppliers},
        'ai': ai or {'llm_calls': 0, 'ai_cost': 0.0, 'decisions': 0},
        'guardrails': guard,
        'commentary': commentary,
    }


def run_tick(scenario, *, force_heuristic=False, client_factory=None, full_check=None):
    """
    Advance one tick inside the caller's transaction; returns the snapshot's KPIs.
    `full_check` runs the guardrail through the report generators (default:
    every seventh tick; the others use the one-query ledger identity).
    """
    tick = Tick(scenario, force_heuristic=force_heuristic, client_factory=client_factory)
    tick.run()
    tick.update_demand_averages()
    tick.flush()
    scenario.tick = tick.tick
    scenario.current_date = tick.date
    scenario.state = tick.state
    scenario.status = 'running'
    scenario.save(update_fields=['tick', 'current_date', 'state', 'status', 'updated_at'])
    kpis = compile_snapshot(
        scenario, tick=tick.tick, prev_date=tick.prev_date, date=tick.date, ops=tick.ops, sku_ops=tick.sku_ops,
        line_stats=tick.line_stats, commentary=tick.commentary,
        ai={'llm_calls': tick.llm_calls, 'ai_cost': float(tick.ai_cost), 'decisions': len(tick.pending)},
        full_check=(tick.tick % 7 == 0) if full_check is None else full_check,
    )
    guard = kpis['guardrails']
    if not (guard['trial_balance']['balanced'] and guard['balance_sheet']['balanced']):
        raise GuardrailBreach(
            f'Tick {tick.tick} ({tick.date}) left the books unbalanced: trial balance '
            f'{guard["trial_balance"]}, balance sheet {guard["balance_sheet"]}'
        )
    if not guard['ok']:
        controller = tick.one('controller')
        failed = [c for c in guard['reconciliations'] if not c['ok']]
        if controller:
            AgentDecision.objects.create(
                scenario=scenario, agent=controller, tick=tick.tick, sim_date=tick.date, kind='event',
                action='reconciliation_exception', status='rejected', driver='physics',
                summary='Reconciliation exception: ' + ', '.join(f'{c["label"]} off by {c["difference"]}'
                                                                 for c in failed),
                payload={'reconciliations': failed},
            )
    TickSnapshot.objects.create(scenario=scenario, tick=tick.tick, sim_date=tick.date, kpis=kpis)
    return kpis


def advance(scenario, ticks=1, *, user=None, force_heuristic=False, client_factory=None):
    """
    Advance the scenario `ticks` ticks, each in its own transaction. A guardrail
    breach or an unexpected error rolls that tick back, halts the scenario with
    the reason and raises a ValidationError; ticks already completed stay.
    """
    world.require_sandbox()
    results = []
    for n in range(ticks):
        scenario = Scenario.objects.get(pk=scenario.pk)
        if scenario.status == 'halted':
            raise serializers.ValidationError(f'Scenario is halted: {scenario.error_message}. Resume it first.')
        try:
            with transaction.atomic():
                scenario = Scenario.objects.select_for_update().get(pk=scenario.pk)
                full = True if n == ticks - 1 else None  # the batch's last tick always runs the report generators
                results.append(run_tick(scenario, force_heuristic=force_heuristic, client_factory=client_factory,
                                        full_check=full))
        except Exception as exc:  # a breach or a bug: the tick rolled back; halt with the reason and re-raise
            logger.exception('Simulation tick failed for scenario %s', scenario.pk)
            Scenario.objects.filter(pk=scenario.pk).update(status='halted', error_message=str(exc)[:2000])
            raise serializers.ValidationError(f'Tick halted: {exc}')
    if results:
        log_event(user, 'simulation.advance', scenario,
                  {'ticks': len(results), 'tick': results[-1]['tick'], 'date': results[-1]['date']})
    return results
