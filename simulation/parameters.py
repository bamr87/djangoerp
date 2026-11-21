"""
The parameter catalogue: every knob the agents respond to, with its bounds,
unit, the agent roles that read it (`drives`) and the GL accounts or KPIs it
moves (`affects`). The console renders its control panel from this list and the
API validates every change against it, so a parameter cannot exist without a
declared meaning.
"""
from rest_framework import serializers

P = dict

PARAMETERS = [
    # --- Market: the non-deterministic demand side ------------------------------------------------
    P(key='demand_base', group='Market', kind='map', unit='units/day',
      label='Base demand by product', min=0, max=500, step=0.5,
      default={'A-WIDGET': 10, 'A-WIDGET-XL': 3, 'B-GADGET': 8},
      drives=['customer', 'market'], affects=['4100', '4200', 'units ordered'],
      description='Expected daily units across all customers at reference price, before season, '
                  'trend, sentiment, price response and the daily random shock.'),
    P(key='demand_volatility', group='Market', kind='number', unit='σ', label='Demand volatility',
      min=0, max=1.5, step=0.05, default=0.25, drives=['customer'], affects=['units ordered'],
      description='Log-normal shock applied to each customer\'s daily demand before the Poisson draw.'),
    P(key='seasonality_amplitude', group='Market', kind='number', unit='±share', label='Seasonality amplitude',
      min=0, max=0.8, step=0.05, default=0.15, drives=['customer', 'market'], affects=['units ordered'],
      description='Peak-to-mean swing of the seasonal demand cycle.'),
    P(key='seasonality_period_days', group='Market', kind='integer', unit='days', label='Season length',
      min=7, max=365, step=1, default=91, drives=['customer', 'market'], affects=['units ordered'],
      description='Length of one seasonal demand cycle.'),
    P(key='market_growth_pct', group='Market', kind='number', unit='%/year', label='Market growth',
      min=-50, max=200, step=1, default=6, drives=['customer'], affects=['units ordered'],
      description='Compound annual growth of underlying demand.'),
    P(key='price_elasticity', group='Market', kind='number', unit='ε', label='Price elasticity',
      min=-5, max=0, step=0.1, default=-1.4, drives=['customer', 'sales_manager'],
      affects=['units ordered', '4100', '4200'],
      description='Demand response to our price relative to the competitor price level '
                  '(scaled per customer by its price sensitivity).'),
    P(key='macro_volatility', group='Market', kind='number', unit='σ/review', label='Sentiment volatility',
      min=0, max=0.5, step=0.01, default=0.05, drives=['market'], affects=['units ordered'],
      description='How far the market agent\'s sentiment can wander per review (heuristic driver).'),
    P(key='customer_lead_time_days', group='Market', kind='integer', unit='days', label='Customer lead time',
      min=1, max=60, step=1, default=10, drives=['customer', 'buyer', 'planner'], affects=['OTIF'],
      description='How far ahead customers request delivery when they order.'),
    P(key='cancel_after_days_late', group='Market', kind='integer', unit='days', label='Cancel when late by',
      min=1, max=90, step=1, default=10, drives=['customer'], affects=['lost sales', '4100', '4200'],
      description='A customer cancels an unshipped order once it is this many days late (a lost sale).'),

    # --- Sales and credit -------------------------------------------------------------------------
    P(key='target_margin_pct', group='Sales', kind='number', unit='%', label='Target gross margin',
      min=0, max=90, step=1, default=35, drives=['sales_manager'], affects=['4100', '4200', 'gross margin'],
      description='The sales manager raises prices when a product\'s margin over its unit cost falls below this.'),
    P(key='max_price_change_pct', group='Sales', kind='number', unit='%/review', label='Max price move',
      min=0, max=30, step=0.5, default=5, drives=['sales_manager'], affects=['4100', '4200'],
      description='Guardrail: the most any one price may move in one pricing review.'),
    P(key='customer_payment_days', group='Sales', kind='integer', unit='days', label='Customer payment terms',
      min=0, max=180, step=1, default=30, drives=['customer', 'controller'], affects=['1100', '1000'],
      description='Invoice due date offset; customers pay around it.'),
    P(key='payment_delay_volatility_days', group='Sales', kind='number', unit='days', label='Payment delay spread',
      min=0, max=60, step=1, default=6, drives=['customer'], affects=['1100', '1000'],
      description='Spread of how late (or early) customers pay against terms.'),
    P(key='credit_limit', group='Sales', kind='number', unit='$', label='Credit limit per customer',
      min=0, max=5_000_000, step=10_000, default=250_000, drives=['sales_manager'], affects=['1100'],
      description='The sales manager rejects new orders from a customer whose open receivables exceed this.'),

    # --- Supply -------------------------------------------------------------------------------------
    P(key='material_base_cost', group='Supply', kind='map', unit='$/unit', label='Material base cost',
      min=0, max=10_000, step=0.5,
      default={'RM-STEEL': 40, 'RM-WHEEL': 15, 'RM-BOARD': 55, 'RM-CASE': 12},
      drives=['supplier', 'buyer'], affects=['1200', '5100', '5200'],
      description='Supplier list price at price index 1.0.'),
    P(key='material_inflation_pct', group='Supply', kind='number', unit='%/year', label='Material inflation',
      min=-50, max=200, step=1, default=4, drives=['supplier'], affects=['1200', '5100', '5200'],
      description='Drift of every supplier\'s price index.'),
    P(key='material_price_volatility', group='Supply', kind='number', unit='σ/day', label='Material price volatility',
      min=0, max=0.2, step=0.005, default=0.01, drives=['supplier'], affects=['1200', '5100', '5200'],
      description='Daily random walk of each supplier\'s price index.'),
    P(key='supplier_reliability', group='Supply', kind='number', unit='p', label='Supplier on-time probability',
      min=0.1, max=1, step=0.05, default=0.9, drives=['supplier'], affects=['OTIF', '1200'],
      description='Chance a due purchase order is delivered on a given day (otherwise it slips a day).'),
    P(key='supplier_payment_days', group='Supply', kind='integer', unit='days', label='Supplier payment terms',
      min=0, max=180, step=1, default=30, drives=['controller'], affects=['2100', '1000'],
      description='When the controller settles a goods-receipt accrual.'),
    P(key='safety_stock_days', group='Supply', kind='number', unit='days', label='Safety stock (initial)',
      min=0, max=60, step=0.5, default=4, drives=['buyer'], affects=['1200', '1210'],
      description='Initial buyer policy: safety stock as days of average usage, for materials and finished goods.'),

    # --- Production and labor -----------------------------------------------------------------------
    P(key='std_hours', group='Production', kind='map', unit='machine-h/unit', label='Standard hours per unit',
      min=0.01, max=100, step=0.05, default={'A-WIDGET': 0.4, 'A-WIDGET-XL': 0.7, 'B-GADGET': 0.4},
      drives=['planner', 'operator', 'controller'], affects=['5390', 'utilization'],
      description='Machine hours one unit takes; also the base the controller absorbs conversion cost on.'),
    P(key='shift_hours', group='Production', kind='number', unit='h', label='Hours per shift',
      min=4, max=12, step=0.5, default=8, drives=['operator', 'plant_manager'], affects=['5300', 'capacity'],
      description='Paid hours per shift per crew member.'),
    P(key='max_shifts', group='Production', kind='integer', unit='shifts', label='Max shifts per line',
      min=1, max=3, step=1, default=3, drives=['plant_manager'], affects=['capacity'],
      description='Upper bound on the shifts the plant manager may run on one line.'),
    P(key='direct_labor_rate', group='Production', kind='number', unit='$/h', label='Direct labor rate',
      min=1, max=300, step=0.5, default=25, drives=['operator', 'controller'], affects=['5300', '5390'],
      description='Operator wage; paid for every scheduled hour, busy or idle.'),
    P(key='crew_per_machine', group='Production', kind='number', unit='operators', label='Crew per machine',
      min=0.5, max=10, step=0.5, default=2, drives=['plant_manager'], affects=['5300'],
      description='Operators per machine per shift on newly commissioned lines.'),
    P(key='shift_premium_pct', group='Production', kind='number', unit='%', label='Late-shift premium',
      min=0, max=200, step=1, default=15, drives=['controller'], affects=['5300'],
      description='Wage premium for the second and third shift.'),
    P(key='labor_productivity', group='Production', kind='number', unit='×', label='Labor productivity',
      min=0.3, max=2, step=0.05, default=1.0, drives=['operator'], affects=['capacity', 'utilization'],
      description='Effective output per scheduled hour (the crew agent adds daily noise).'),
    P(key='absenteeism_rate', group='Production', kind='number', unit='share', label='Absenteeism',
      min=0, max=0.5, step=0.01, default=0.04, drives=['operator'], affects=['capacity'],
      description='Average share of scheduled crew hours lost to absence.'),
    P(key='indirect_labor_per_line_shift_day', group='Production', kind='number', unit='$/line-shift-day',
      label='Indirect labor per line shift', min=0, max=5000, step=10, default=200,
      drives=['controller'], affects=['5310'],
      description='Supervision and material handling for each running line shift.'),

    # --- Machines and maintenance -----------------------------------------------------------------
    P(key='breakdown_rate_per_1000h', group='Machines', kind='number', unit='per 1000 h', label='Breakdown rate',
      min=0, max=100, step=0.5, default=2, drives=['machine'], affects=['5340', 'capacity'],
      description='Base breakdown hazard of a new machine; wear multiplies it up to 4×.'),
    P(key='wear_per_hour', group='Machines', kind='number', unit='per h', label='Wear per running hour',
      min=0, max=0.05, step=0.0005, default=0.002, drives=['machine'], affects=['5340'],
      description='How fast a running machine wears out (wear 1.0 = worn out).'),
    P(key='repair_cost', group='Machines', kind='number', unit='$', label='Repair cost',
      min=0, max=100_000, step=50, default=1500, drives=['maintenance'], affects=['5340'],
      description='Parts and contractor cost of one breakdown repair.'),
    P(key='repair_hours', group='Machines', kind='number', unit='h', label='Repair downtime',
      min=0, max=72, step=0.5, default=8, drives=['maintenance'], affects=['capacity'],
      description='Machine hours lost to one repair.'),
    P(key='pm_cost', group='Machines', kind='number', unit='$', label='Preventive maintenance cost',
      min=0, max=50_000, step=50, default=500, drives=['maintenance'], affects=['5340'],
      description='Cost of one preventive maintenance service.'),
    P(key='pm_hours', group='Machines', kind='number', unit='h', label='Preventive maintenance downtime',
      min=0, max=24, step=0.5, default=2, drives=['maintenance'], affects=['capacity'],
      description='Machine hours one preventive service takes.'),
    P(key='pm_wear_threshold', group='Machines', kind='number', unit='wear', label='PM wear threshold (initial)',
      min=0.05, max=0.95, step=0.05, default=0.5, drives=['maintenance'], affects=['5340'],
      description='Initial maintenance policy: service a line once its wear reaches this.'),
    P(key='energy_cost_per_machine_hour', group='Machines', kind='number', unit='$/h', label='Energy per machine hour',
      min=0, max=500, step=0.5, default=9, drives=['machine', 'controller'], affects=['5320'],
      description='Variable overhead: power drawn per running machine hour.'),
    P(key='consumables_per_unit', group='Machines', kind='number', unit='$/unit', label='Consumables per unit',
      min=0, max=500, step=0.1, default=1.5, drives=['operator', 'controller'], affects=['5320'],
      description='Variable overhead: tooling and consumables per unit produced.'),

    # --- Plant and capital ------------------------------------------------------------------------
    P(key='line_capex', group='Plant', kind='number', unit='$', label='Capex per new line',
      min=0, max=50_000_000, step=1000, default=180_000, drives=['plant_manager', 'controller'],
      affects=['1500', '5330', '1000'],
      description='Purchase price of one production line (one machine).'),
    P(key='line_useful_life_years', group='Plant', kind='number', unit='years', label='Line useful life',
      min=1, max=40, step=0.5, default=8, drives=['controller'], affects=['5330'],
      description='Straight-line depreciation period of a new line.'),
    P(key='facility_cost_per_day', group='Plant', kind='number', unit='$/day', label='Facility overhead',
      min=0, max=100_000, step=10, default=350, drives=['controller'], affects=['6200'],
      description='Rent, insurance and utilities not tied to machine hours.'),
    P(key='normal_utilization_pct', group='Plant', kind='number', unit='%', label='Normal capacity',
      min=10, max=100, step=1, default=85, drives=['controller'], affects=['5390'],
      description='The controller sets absorption rates over this share of available hours (normal capacity).'),
    P(key='autonomous_capex', group='Plant', kind='bool', unit='', label='Plant manager may buy lines',
      min=None, max=None, step=None, default=False, drives=['plant_manager'], affects=['1500', '1000'],
      description='Let the plant manager commission a new line on its own when a family is out of shifts.'),

    # --- Finance ----------------------------------------------------------------------------------
    P(key='opening_capital', group='Finance', kind='number', unit='$', label='Opening capital',
      min=0, max=1_000_000_000, step=10_000, default=600_000, drives=['controller'], affects=['3000', '1000'],
      description='Share capital paid in when the scenario starts (only read at bootstrap).'),
    P(key='cash_buffer', group='Finance', kind='number', unit='$', label='Cash buffer (initial)',
      min=0, max=10_000_000, step=5000, default=40_000, drives=['controller'], affects=['1000', '2400'],
      description='Initial controller policy: draw on the revolver below this cash level.'),
    P(key='revolver_limit', group='Finance', kind='number', unit='$', label='Revolving credit limit',
      min=0, max=100_000_000, step=10_000, default=250_000, drives=['controller'], affects=['2400', '6400'],
      description='Most the controller may borrow.'),
    P(key='revolver_rate_pct', group='Finance', kind='number', unit='%/year', label='Revolver interest rate',
      min=0, max=50, step=0.25, default=8, drives=['controller'], affects=['6400'],
      description='Interest charged on the revolver balance.'),

    # --- Agents -----------------------------------------------------------------------------------
    P(key='llm_max_calls_per_tick', group='Agents', kind='integer', unit='calls', label='Claude calls per tick',
      min=0, max=100, step=1, default=8, drives=['market', 'customer', 'supplier', 'sales_manager', 'buyer',
                                                 'planner', 'maintenance', 'plant_manager', 'controller'],
      affects=['6300'],
      description='Budget guardrail: reviews beyond this many Claude calls in one tick fall back to heuristics.'),
]

BY_KEY = {p['key']: p for p in PARAMETERS}


def defaults():
    """A fresh copy of every default value."""
    return {p['key']: (dict(p['default']) if p['kind'] == 'map' else p['default']) for p in PARAMETERS}


def resolve(parameters):
    """Defaults overlaid with the stored values, map parameters merged key by key."""
    values = defaults()
    for key, value in (parameters or {}).items():
        if key in BY_KEY and BY_KEY[key]['kind'] == 'map' and isinstance(value, dict):
            values[key] = {**values[key], **value}
        elif key in BY_KEY:
            values[key] = value
    return values


def _clean_number(spec, key, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError({key: 'must be a number'})
    if spec['min'] is not None and value < spec['min']:
        raise serializers.ValidationError({key: f'must be at least {spec["min"]}'})
    if spec['max'] is not None and value > spec['max']:
        raise serializers.ValidationError({key: f'must be at most {spec["max"]}'})
    return int(value) if spec['kind'] == 'integer' else value


def validate(changes):
    """
    Validate a partial parameter update against the catalogue; returns the
    cleaned values. Unknown keys, wrong types and out-of-range values raise.
    """
    if not isinstance(changes, dict):
        raise serializers.ValidationError('parameters must be an object of {key: value}')
    cleaned = {}
    for key, value in changes.items():
        spec = BY_KEY.get(key)
        if spec is None:
            raise serializers.ValidationError({key: 'unknown parameter'})
        if spec['kind'] == 'bool':
            if not isinstance(value, bool):
                raise serializers.ValidationError({key: 'must be true or false'})
            cleaned[key] = value
        elif spec['kind'] == 'map':
            if not isinstance(value, dict):
                raise serializers.ValidationError({key: 'must be an object of {sku: value}'})
            cleaned[key] = {str(sku): _clean_number(spec, f'{key}.{sku}', v) for sku, v in value.items()}
        else:
            cleaned[key] = _clean_number(spec, key, value)
    return cleaned
