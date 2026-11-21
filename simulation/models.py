"""
The agent simulation sandbox: a time-stepped economy whose actors — customers,
suppliers, the market, line crews, machines, maintenance, planners, managers and
the controller — are agents that drive the ERP through the same services the
API uses. The ledger stays the book of record; every model here is either agent
configuration, an explanation of what an agent did, or analytics derived from
the ledger.
"""
from django.conf import settings
from django.db import models

from core.models import TimestampMixin


class Scenario(TimestampMixin):
    """
    One sandboxed economy: its clock, its random seed, the parameters the agents
    respond to (`parameters`, the console's control panel) and the shared market
    state the agents evolve (`state`: sentiment, competitor prices, demand
    averages). One scenario per database — the books are shared, so what-if
    variants run as rolled-back forks (`Experiment`), never as parallel worlds.
    """

    STATUS_CHOICES = [
        ('ready', 'Ready'),
        ('running', 'Running'),
        ('halted', 'Halted by a guardrail'),
    ]
    DRIVER_CHOICES = [
        ('heuristic', 'Heuristic policies (seeded, offline)'),
        ('llm', 'Claude agents with heuristic fallback'),
    ]

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    seed = models.PositiveIntegerField(default=42)
    start_date = models.DateField()
    current_date = models.DateField()
    tick = models.PositiveIntegerField(default=0)
    tick_days = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='ready')
    driver = models.CharField(max_length=12, choices=DRIVER_CHOICES, default='heuristic')
    agent_model = models.CharField(max_length=64, default='claude-haiku-5-5')
    parameters = models.JSONField(default=dict, blank=True)
    state = models.JSONField(default=dict, blank=True)
    warehouse = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT, related_name='scenarios')
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.name


class ProductionLine(TimestampMixin):
    """
    A capacity resource: `machines` parallel stations running `shifts` shifts a
    day, each machine staffed by `crew_per_machine` operators. It makes the
    products of one product family (a ProductCategory). Commissioning a line
    posts its capex (Dr machinery / Cr cash); depreciation runs per tick.
    """

    STATUS_CHOICES = [
        ('running', 'Running'),
        ('down', 'Down (breakdown)'),
        ('retired', 'Retired'),
    ]

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='lines')
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=100)
    family = models.ForeignKey('products.ProductCategory', on_delete=models.PROTECT, related_name='sim_lines')
    machines = models.PositiveSmallIntegerField(default=1)
    shifts = models.PositiveSmallIntegerField(default=1)
    crew_per_machine = models.DecimalField(max_digits=6, decimal_places=2, default=2)
    capex = models.DecimalField(max_digits=14, decimal_places=2)
    useful_life_days = models.PositiveIntegerField()
    commissioned_on = models.DateField()
    retired_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='running')
    # 0 = new, 1 = worn out. Raises the breakdown hazard; maintenance lowers it.
    wear = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    run_hours = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Depreciation the controller has posted for this line (its share of account 1590).
    accumulated_depreciation = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    capex_entry = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(fields=['scenario', 'code'], name='unique_line_code_per_scenario'),
        ]

    def __str__(self):
        return f'{self.code} ({self.family.name})'

    @property
    def is_active(self):
        return self.status != 'retired'


class Agent(TimestampMixin):
    """
    An actor in the sandbox and, for the company's own agents, a resource with a
    cost driver. `policy` holds the knobs the agent decides (prices, safety stock,
    shifts, absorption rates...), revised every `decision_interval` ticks by its
    brain — a heuristic policy or a small Claude model — and `state` its memory.
    Each agent acts in the ERP as its own Django user, so the audit trail names it.
    """

    ROLE_CHOICES = [
        ('market', 'Market (macro forces)'),
        ('customer', 'Customer'),
        ('supplier', 'Supplier'),
        ('sales_manager', 'Sales manager'),
        ('buyer', 'Buyer (procurement)'),
        ('planner', 'Production planner'),
        ('operator', 'Line crew (direct labor)'),
        ('machine', 'Machine'),
        ('maintenance', 'Maintenance technician'),
        ('plant_manager', 'Plant manager'),
        ('controller', 'Controller (finance)'),
    ]
    RESOURCE_KINDS = [
        ('external', 'External party'),
        ('direct_labor', 'Direct labor'),
        ('indirect_labor', 'Indirect labor'),
        ('variable_overhead', 'Variable overhead'),
        ('machine', 'Machine'),
        ('maintenance', 'Maintenance'),
        ('management', 'Management'),
    ]
    BRAIN_CHOICES = [
        ('auto', 'Follow the scenario driver'),
        ('heuristic', 'Heuristic policy'),
        ('llm', 'Claude'),
    ]

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='agents')
    code = models.CharField(max_length=48)
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    resource_kind = models.CharField(max_length=20, choices=RESOURCE_KINDS)
    partner = models.ForeignKey(
        'partners.BusinessPartner', on_delete=models.PROTECT, null=True, blank=True, related_name='sim_agents',
    )
    line = models.ForeignKey(ProductionLine, on_delete=models.CASCADE, null=True, blank=True, related_name='agents')
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='sim_agent',
    )
    brain = models.CharField(max_length=12, choices=BRAIN_CHOICES, default='auto')
    model = models.CharField(max_length=64, blank=True, default='')
    # Fixed cost of this resource per simulated day, posted by the controller to cost_account.
    daily_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_account = models.ForeignKey(
        'coa.Account', on_delete=models.PROTECT, null=True, blank=True, related_name='sim_agents',
    )
    decision_interval = models.PositiveSmallIntegerField(default=7)
    policy = models.JSONField(default=dict, blank=True)
    state = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    decisions_count = models.PositiveIntegerField(default=0)
    llm_calls = models.PositiveIntegerField(default=0)
    ai_cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)

    class Meta:
        ordering = ['role', 'code']
        constraints = [
            models.UniqueConstraint(fields=['scenario', 'code'], name='unique_agent_code_per_scenario'),
        ]

    def __str__(self):
        return f'{self.code} ({self.get_role_display()})'


class AgentDecision(models.Model):
    """
    The semantic trace: one row per thing an agent decided or did, with what it
    saw, what it chose, why, which brain decided, what it cost, and the ERP
    documents and journal entries it produced.
    """

    KIND_CHOICES = [
        ('policy', 'Policy review'),
        ('action', 'Action'),
        ('event', 'Stochastic event'),
    ]
    DRIVER_CHOICES = [
        ('heuristic', 'Heuristic'),
        ('llm', 'Claude'),
        ('fallback', 'Heuristic fallback'),
        ('physics', 'Stochastic model'),
    ]
    STATUS_CHOICES = [
        ('applied', 'Applied'),
        ('rejected', 'Rejected'),
        ('noop', 'No action'),
    ]

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='decisions')
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='decisions')
    tick = models.PositiveIntegerField()
    sim_date = models.DateField()
    kind = models.CharField(max_length=8, choices=KIND_CHOICES, default='action')
    action = models.CharField(max_length=64)
    summary = models.CharField(max_length=255)
    rationale = models.TextField(blank=True)
    observation = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    documents = models.JSONField(default=list, blank=True)
    driver = models.CharField(max_length=10, choices=DRIVER_CHOICES, default='heuristic')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='applied')
    error = models.TextField(blank=True)
    model = models.CharField(max_length=64, blank=True, default='')
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        indexes = [
            models.Index(fields=['scenario', 'tick']),
            models.Index(fields=['kind']),
        ]

    def __str__(self):
        return f'T{self.tick} {self.agent.code}: {self.action}'


class ProductionJob(TimestampMixin):
    """
    A work order scheduled on a production line: the hours it needs, the hours
    done, and the conversion cost it absorbed when it completed. The core work
    order completes all at once; the job is the capacity view of it.
    """

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='jobs')
    line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name='jobs')
    work_order = models.OneToOneField('manufacturing.WorkOrder', on_delete=models.PROTECT, related_name='sim_job')
    hours_required = models.DecimalField(max_digits=10, decimal_places=2)
    hours_done = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='queued')
    queued_on = models.DateField()
    started_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    absorbed_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['work_order__due_date', 'id']
        indexes = [models.Index(fields=['scenario', 'status'])]

    def __str__(self):
        return f'{self.work_order.number} on {self.line.code}'


class SupplierPayable(TimestampMixin):
    """
    The accounts-payable subledger for sandbox receipts: one row per goods
    receipt (GRN) accrual, settled by the controller on its terms. GL account
    2100 must always equal the sum of the open rows (a live guardrail).
    """

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='payables')
    supplier = models.ForeignKey('partners.BusinessPartner', on_delete=models.PROTECT, related_name='sim_payables')
    purchase_order = models.ForeignKey(
        'purchasing.PurchaseOrder', on_delete=models.PROTECT, related_name='sim_payables',
    )
    grn = models.CharField(max_length=32, unique=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    received_on = models.DateField()
    due_on = models.DateField()
    paid_on = models.DateField(null=True, blank=True)
    payment_entry = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        ordering = ['due_on', 'id']

    def __str__(self):
        return f'{self.grn} {self.amount}'


class TickSnapshot(models.Model):
    """
    Analytics only: the KPIs the controller compiled from the ledger at the end
    of one tick (flows for the tick, balances at its date, operations, agents,
    guardrails). Floats are fine here — the journal is the book of record.
    """

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='snapshots')
    tick = models.PositiveIntegerField()
    sim_date = models.DateField()
    kpis = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tick']
        constraints = [
            models.UniqueConstraint(fields=['scenario', 'tick'], name='unique_snapshot_per_tick'),
        ]

    def __str__(self):
        return f'{self.scenario} T{self.tick}'


class Experiment(TimestampMixin):
    """
    A what-if: from the scenario's current state, run `horizon_ticks` ticks as
    a baseline and again with `changes` applied (new lines, shift changes,
    parameter overrides), both on the same random numbers, both rolled back.
    Only the comparison is kept.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    DRIVER_CHOICES = [
        ('heuristic', 'Heuristic agents (reproducible)'),
        ('scenario', 'Same driver as the scenario'),
    ]

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='experiments')
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    horizon_ticks = models.PositiveSmallIntegerField(default=30)
    changes = models.JSONField(default=dict, blank=True)
    driver = models.CharField(max_length=12, choices=DRIVER_CHOICES, default='heuristic')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    base_tick = models.PositiveIntegerField(null=True, blank=True)
    base_date = models.DateField(null=True, blank=True)
    baseline = models.JSONField(default=dict, blank=True)
    variant = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.name
