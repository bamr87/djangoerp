"""
Bootstrapping the sandbox economy: the chart of accounts, a two-family product
structure with BOMs, customers and suppliers, the warehouse, the production
lines (their capex posted through journal.services.post_entry), the agent
roster — each agent its own Django user — and opening balances bought through
the real procure-to-pay services.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import serializers

from core.audit import log_event
from core.models import DocumentSequence
from inventory.models import Warehouse
from manufacturing.models import BillOfMaterials, BOMLine
from partners.models import BusinessPartner
from products.models import Product, ProductCategory, UnitOfMeasure
from purchasing import services as purchasing_services
from purchasing.models import PurchaseOrder, PurchaseOrderLine

from . import finance
from . import parameters as params_module
from .models import Agent, ProductionLine, Scenario, SupplierPayable

FAMILIES = {
    'A': {'category': 'Product line A', 'revenue': 'revenue_a', 'cogs': 'cogs_a'},
    'B': {'category': 'Product line B', 'revenue': 'revenue_b', 'cogs': 'cogs_b'},
}
MATERIALS_CATEGORY = 'Raw materials'

# sku: (name, supplier, lead time days, min order qty, order multiple)
MATERIALS = {
    'RM-STEEL': ('Steel frame blank', 'SUP-METAL', 5, 20, 10),
    'RM-WHEEL': ('Wheel assembly', 'SUP-PARTS', 3, 40, 20),
    'RM-BOARD': ('Control board', 'SUP-ELEC', 7, 20, 10),
    'RM-CASE': ('Moulded case', 'SUP-PARTS', 4, 40, 20),
}
# sku: (name, family, list price, make lead time days, min order qty, order multiple, BOM {component: qty})
FINISHED_GOODS = {
    'A-WIDGET': ('Widget', 'A', 210, 2, 10, 5, {'RM-STEEL': 1, 'RM-WHEEL': 2}),
    'A-WIDGET-XL': ('Widget XL', 'A', 420, 3, 5, 5, {'RM-STEEL': 1, 'RM-WHEEL': 4, 'RM-BOARD': 1}),
    'B-GADGET': ('Gadget', 'B', 260, 2, 10, 5, {'RM-BOARD': 1, 'RM-CASE': 2}),
}
SUPPLIERS = {
    'SUP-METAL': 'Northern Metals',
    'SUP-PARTS': 'Precision Parts Co',
    'SUP-ELEC': 'Circuitry Ltd',
}
# code: (name, share of each product's demand, price sensitivity, payment terms multiplier, lead days offset, discount)
CUSTOMERS = {
    'CUST-RETAIL': ('Retail Chain Ltd', {'A-WIDGET': 0.5, 'A-WIDGET-XL': 0.2, 'B-GADGET': 0.4}, 1.3, 1.5, 0, 0.04),
    'CUST-OEM': ('OEM Assemblies', {'A-WIDGET': 0.3, 'A-WIDGET-XL': 0.6, 'B-GADGET': 0.2}, 0.6, 2.0, 4, 0.06),
    'CUST-WEB': ('Web Shop Direct', {'A-WIDGET': 0.2, 'A-WIDGET-XL': 0.2, 'B-GADGET': 0.4}, 1.0, 0.25, -5, 0.0),
}
# The company's own decision-makers: code, name, role, resource kind, account key, daily cost, review interval.
STAFF = [
    ('SALES', 'Sales manager', 'sales_manager', 'management', 'management', 120, 7),
    ('BUYER', 'Buyer', 'buyer', 'management', 'management', 90, 7),
    ('PLANNER', 'Production planner', 'planner', 'indirect_labor', 'indirect_labor', 90, 7),
    ('MAINT', 'Maintenance technician', 'maintenance', 'maintenance', 'maintenance', 250, 7),
    ('PLANT', 'Plant manager', 'plant_manager', 'management', 'management', 220, 7),
    ('CONTROLLER', 'Controller', 'controller', 'management', 'management', 110, 7),
]


def require_sandbox():
    """Agents may only post in a database flagged as a sandbox (settings.SIMULATION_SANDBOX)."""
    if not getattr(settings, 'SIMULATION_SANDBOX', False):
        raise serializers.ValidationError(
            'Simulation is disabled: this database is not a sandbox. Set SIMULATION_SANDBOX=True only on a '
            'database whose books simulated agents may write to (dev settings default it on).'
        )


def agent_user(scenario, code):
    username = f'sim{scenario.pk}.{code.lower()}'[:150]
    user, created = User.objects.get_or_create(username=username, defaults={'first_name': code[:150]})
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user


def build_master_data(values, user=None):
    """Idempotent master data for the sandbox. Returns {'accounts', 'products', 'categories', 'partners'}."""
    acc = finance.ensure_accounts()
    each, _ = UnitOfMeasure.objects.get_or_create(code='EA', defaults={'name': 'Each'})

    categories = {}
    materials, _ = ProductCategory.objects.get_or_create(
        name=MATERIALS_CATEGORY, defaults={'inventory_account': acc['raw_materials']},
    )
    categories['RM'] = materials
    for key, family in FAMILIES.items():
        category, _ = ProductCategory.objects.get_or_create(
            name=family['category'],
            defaults={
                'inventory_account': acc['finished_goods'], 'revenue_account': acc[family['revenue']],
                'cogs_account': acc[family['cogs']],
            },
        )
        categories[key] = category

    partners = {}
    for code, name in SUPPLIERS.items():
        partners[code], _ = BusinessPartner.objects.get_or_create(
            code=code, defaults={'name': name, 'is_supplier': True, 'payable_account': acc['payable'],
                                 'created_by': user},
        )
    for code, (name, *_rest) in CUSTOMERS.items():
        partners[code], _ = BusinessPartner.objects.get_or_create(
            code=code, defaults={'name': name, 'is_customer': True, 'receivable_account': acc['receivable'],
                                 'created_by': user},
        )

    products = {}
    base_cost = values['material_base_cost']
    for sku, (name, supplier, lead, min_qty, multiple) in MATERIALS.items():
        product, _ = Product.objects.get_or_create(
            sku=sku,
            defaults={
                'name': name, 'category': materials, 'uom': each, 'procurement_type': 'buy',
                'standard_cost': Decimal(str(base_cost.get(sku, 0))), 'lead_time_days': lead,
                'min_order_qty': Decimal(min_qty), 'order_multiple': Decimal(multiple),
                'default_supplier': partners[supplier], 'created_by': user,
            },
        )
        products[sku] = product
    for sku, (name, family, price, lead, min_qty, multiple, bom) in FINISHED_GOODS.items():
        material_cost = sum(Decimal(str(base_cost.get(c, 0))) * q for c, q in bom.items())
        product, _ = Product.objects.get_or_create(
            sku=sku,
            defaults={
                'name': name, 'category': categories[family], 'uom': each, 'procurement_type': 'make',
                'standard_cost': material_cost, 'sales_price': Decimal(price), 'lead_time_days': lead,
                'min_order_qty': Decimal(min_qty), 'order_multiple': Decimal(multiple), 'created_by': user,
            },
        )
        products[sku] = product
        if not BillOfMaterials.objects.filter(product=product, is_active=True).exists():
            recipe = BillOfMaterials.objects.create(product=product, reference=f'{sku}-v1', created_by=user)
            for component, quantity in bom.items():
                BOMLine.objects.create(bom=recipe, component=products[component], quantity=Decimal(quantity))

    return {'accounts': acc, 'products': products, 'categories': categories, 'partners': partners}


def family_of(product):
    """'A' / 'B' for a finished good, from its category; None for materials."""
    for key, family in FAMILIES.items():
        if product.category_id and product.category.name == family['category']:
            return key
    return None


def family_of_line(line):
    """'A' / 'B' for a production line, from the product family it makes."""
    for key, family in FAMILIES.items():
        if line.family.name == family['category']:
            return key
    return None


def daily_line_costs(line, values, maintenance_share=Decimal('0')):
    """Budgeted conversion cost per day of one line, by driver (Decimal)."""
    rate = Decimal(str(values['direct_labor_rate']))
    shift_hours = Decimal(str(values['shift_hours']))
    premium = Decimal(str(values['shift_premium_pct'])) / 100
    shift_factor = sum((Decimal('1') if s == 0 else 1 + premium) for s in range(line.shifts))
    available = Decimal(line.machines) * line.shifts * shift_hours
    normal = available * Decimal(str(values['normal_utilization_pct'])) / 100
    return {
        'direct_labor': Decimal(line.machines) * line.crew_per_machine * shift_hours * rate * shift_factor,
        'indirect_labor': Decimal(str(values['indirect_labor_per_line_shift_day'])) * line.shifts,
        'depreciation': line.capex / line.useful_life_days,
        'energy': normal * Decimal(str(values['energy_cost_per_machine_hour'])),
        'maintenance': maintenance_share + normal * Decimal(str(values['wear_per_hour']))
        / Decimal(str(max(values['pm_wear_threshold'], 0.05))) * Decimal(str(values['pm_cost'])),
        'available_hours': available,
        'normal_hours': normal,
    }


def standard_absorption_rate(line, values, maintenance_share=Decimal('0')):
    """
    The predetermined absorption rate ($ per machine hour) the controller uses:
    budgeted daily conversion cost over normal capacity hours. Using normal
    rather than actual hours keeps idle capacity a visible under-absorption
    variance instead of inflating unit costs (the textbook death spiral).
    """
    costs = daily_line_costs(line, values, maintenance_share)
    if costs['normal_hours'] <= 0:
        return Decimal('0')
    total = (costs['direct_labor'] + costs['indirect_labor'] + costs['depreciation'] + costs['energy']
             + costs['maintenance'])
    return (total / costs['normal_hours']).quantize(Decimal('0.01'))


def maintenance_share(scenario):
    tech = scenario.agents.filter(role='maintenance', is_active=True).first()
    lines = max(scenario.lines.exclude(status='retired').count(), 1)
    return (tech.daily_cost / lines) if tech else Decimal('0')


def refresh_absorption_rates(scenario, values=None):
    """Recompute every active line's standard rate into the controller's policy."""
    values = values or params_module.resolve(scenario.parameters)
    controller = scenario.agents.filter(role='controller').first()
    if controller is None:
        return {}
    share = maintenance_share(scenario)
    rates = {line.code: float(standard_absorption_rate(line, values, share))
             for line in scenario.lines.exclude(status='retired')}
    controller.policy = {**controller.policy, 'oar': rates}
    controller.save(update_fields=['policy', 'updated_at'])
    return rates


def _create_agent(scenario, code, name, role, kind, **extra):
    agent = Agent.objects.create(scenario=scenario, code=code, name=name, role=role, resource_kind=kind, **extra)
    agent.user = agent_user(scenario, code)
    agent.save(update_fields=['user'])
    return agent


def next_line_code(scenario, family):
    taken = set(scenario.lines.values_list('code', flat=True))
    n = 1
    while f'LINE-{family}{n}' in taken:
        n += 1
    return f'LINE-{family}{n}'


def commission_line(scenario, *, family, date, user=None, machines=1, shifts=1, crew_per_machine=None,
                    capex=None, code=None, name=None, values=None):
    """
    Buy and start a production line for product family `family` ('A'/'B'):
    posts the capex (Dr 1500 machinery / Cr 1000 cash), creates its crew and
    machine agents, and refreshes the controller's absorption rates.
    """
    if family not in FAMILIES:
        raise serializers.ValidationError(f'Unknown product family {family!r}; choose one of {sorted(FAMILIES)}')
    values = values or params_module.resolve(scenario.parameters)
    machines = int(machines)
    shifts = int(shifts)
    if machines < 1 or not 1 <= shifts <= int(values['max_shifts']):
        raise serializers.ValidationError('A line needs at least one machine and 1..max_shifts shifts')
    category = ProductCategory.objects.get(name=FAMILIES[family]['category'])
    code = code or next_line_code(scenario, family)
    if scenario.lines.filter(code=code).exists():
        raise serializers.ValidationError(f'{code} already exists')
    capex = finance.q(capex if capex is not None else Decimal(str(values['line_capex'])) * machines)
    life_days = max(int(Decimal(str(values['line_useful_life_years'])) * 365), 1)
    acc = finance.accounts()

    with transaction.atomic():
        line = ProductionLine.objects.create(
            scenario=scenario, code=code, name=name or f'{FAMILIES[family]["category"]} line {code[-1]}',
            family=category, machines=machines, shifts=shifts,
            crew_per_machine=Decimal(str(crew_per_machine if crew_per_machine is not None
                                         else values['crew_per_machine'])),
            capex=capex, useful_life_days=life_days, commissioned_on=date,
        )
        if capex > 0:
            entry = finance.post(
                date=date, description=f'Capex: commission {code} ({category.name})',
                lines=[{'account': acc['machinery'], 'debit': capex, 'reference': code},
                       {'account': acc['cash'], 'credit': capex, 'reference': code}],
                source_reference=f'sim:{scenario.pk}:capex:{code}', user=user,
            )
            line.capex_entry = entry.entry_number
            line.save(update_fields=['capex_entry'])
        _create_agent(scenario, f'CREW-{code}', f'Crew of {code}', 'operator', 'direct_labor', line=line,
                      decision_interval=1, cost_account=acc['direct_labor'])
        _create_agent(scenario, f'MACH-{code}', f'Machine park of {code}', 'machine', 'machine', line=line,
                      decision_interval=1, cost_account=acc['variable_overhead'])
        refresh_absorption_rates(scenario, values)
        log_event(user, 'simulation.line.commission', line, {
            'family': family, 'machines': machines, 'shifts': shifts, 'capex': str(capex),
            'journal_entry': line.capex_entry,
        })
    return line


def decommission_line(scenario, line, *, date, user=None):
    """
    Retire a line: queued jobs move to another active line of the family (or the
    retirement is refused), the asset leaves the books (Dr accumulated
    depreciation, Dr loss on disposal for the net book value / Cr machinery).
    """
    from .models import ProductionJob

    if line.status == 'retired':
        raise serializers.ValidationError(f'{line.code} is already retired')
    if line.jobs.filter(status='running').exists():
        raise serializers.ValidationError(f'{line.code} has running jobs; retire it once they complete')
    others = scenario.lines.filter(family=line.family).exclude(pk=line.pk).exclude(status='retired')
    queued = line.jobs.filter(status='queued')
    if queued.exists() and not others.exists():
        raise serializers.ValidationError(f'{line.code} is the last {line.family.name} line and has queued jobs')
    acc = finance.accounts()
    with transaction.atomic():
        target = others.first()
        moved = list(queued.values_list('work_order__number', flat=True))
        if target is not None:
            ProductionJob.objects.filter(pk__in=queued.values('pk')).update(line=target)
        net_book_value = line.capex - line.accumulated_depreciation
        entry = finance.post(
            date=date, description=f'Retire {line.code}: dispose of machinery',
            lines=[{'account': acc['accumulated_depreciation'], 'debit': line.accumulated_depreciation,
                    'reference': line.code},
                   {'account': acc['disposal_loss'], 'debit': net_book_value, 'reference': line.code},
                   {'account': acc['machinery'], 'credit': line.capex, 'reference': line.code}],
            source_reference=f'sim:{scenario.pk}:retire:{line.code}', user=user,
        )
        line.status = 'retired'
        line.retired_on = date
        line.save(update_fields=['status', 'retired_on', 'updated_at'])
        line.agents.update(is_active=False)
        refresh_absorption_rates(scenario)
        log_event(user, 'simulation.line.retire', line, {
            'journal_entry': entry.entry_number, 'jobs_moved': moved,
            'net_book_value': str(net_book_value),
        })
    return line


def _opening_stock(scenario, master, values, date, user):
    """Seed about a week of materials through purchase orders received on day 0."""
    usage = {}
    for sku, (_n, _f, _p, _l, _m, _o, bom) in FINISHED_GOODS.items():
        for component, quantity in bom.items():
            usage[component] = usage.get(component, 0) + values['demand_base'].get(sku, 0) * quantity
    by_supplier = {}
    for sku, daily in usage.items():
        supplier = MATERIALS[sku][1]
        by_supplier.setdefault(supplier, []).append((master['products'][sku], Decimal(str(round(daily * 7)))))
    buyer = scenario.agents.get(role='buyer')
    for supplier_code, lines in by_supplier.items():
        supplier = master['partners'][supplier_code]
        order = PurchaseOrder.objects.create(
            number=DocumentSequence.next_number('PO'), supplier=supplier, warehouse=scenario.warehouse,
            order_date=date, expected_date=date, notes='Sandbox opening stock', source_reference='sim:opening',
            created_by=buyer.user,
        )
        for product, quantity in lines:
            if quantity > 0:
                PurchaseOrderLine.objects.create(order=order, product=product, quantity=quantity,
                                                 unit_price=product.standard_cost)
        purchasing_services.confirm_purchase_order(order, user=buyer.user)
        receipt = purchasing_services.receive_purchase_order(order, user=buyer.user, receipt_date=date)
        SupplierPayable.objects.create(
            scenario=scenario, supplier=supplier, purchase_order=order, grn=receipt['grn_number'],
            amount=sum((m.total_value for m in receipt['moves']), Decimal('0.00')),
            received_on=date, due_on=date + timedelta(days=int(values['supplier_payment_days'])),
        )


@transaction.atomic
def create_scenario(*, name='Sandbox', seed=42, start_date, tick_days=1, driver='heuristic', agent_model=None,
                    parameters=None, description='', user=None):
    """
    Build the whole sandbox economy at `start_date` and return the Scenario.
    One per database: the books are shared, so a second economy needs its own
    database (what-if variants use experiments instead).
    """
    require_sandbox()
    existing = Scenario.objects.first()
    if existing is not None:
        raise serializers.ValidationError(
            f'This sandbox already runs scenario "{existing.name}". Scenarios share the books of their database, '
            'so start another economy on a fresh database, or compare variants with an experiment.'
        )
    values = params_module.resolve(params_module.validate(parameters or {}))
    master = build_master_data(values, user=user)
    warehouse, _ = Warehouse.objects.get_or_create(code='SIM', defaults={'name': 'Sandbox plant', 'created_by': user})

    scenario = Scenario.objects.create(
        name=name, description=description, seed=seed, start_date=start_date, current_date=start_date,
        tick_days=tick_days, driver=driver, agent_model=agent_model or settings.SIMULATION_AGENT_MODEL,
        parameters=values, warehouse=warehouse, created_by=user,
        state={
            'macro': {'sentiment': 0.0, 'competitor_price_index': 1.0},
            'reference_prices': {sku: float(master['products'][sku].sales_price) for sku in FINISHED_GOODS},
            'demand_ema': {sku: float(values['demand_base'].get(sku, 0)) for sku in FINISHED_GOODS},
        },
    )
    # Reconciliations measure the sandbox's own movement from here on.
    scenario.state['baseline'] = finance.baseline_totals(scenario, start_date)
    scenario.save(update_fields=['state'])
    acc = master['accounts']

    _create_agent(scenario, 'MARKET', 'Market and macro forces', 'market', 'external',
                  policy={'sentiment': 0.0, 'competitor_price_index': 1.0, 'narrative': 'A steady market.'})
    for code, (cname, shares, sensitivity, terms, lead_offset, discount) in CUSTOMERS.items():
        _create_agent(scenario, code, cname, 'customer', 'external', partner=master['partners'][code],
                      policy={'order_multiplier': 1.0, 'extra_payment_delay_days': 0},
                      state={'persona': {'shares': shares, 'price_sensitivity': sensitivity,
                                         'terms_multiplier': terms, 'lead_offset_days': lead_offset,
                                         'discount': discount},
                             'otif': 1.0})
    for code, sname in SUPPLIERS.items():
        _create_agent(scenario, code, sname, 'supplier', 'external', partner=master['partners'][code],
                      policy={'price_index': 1.0, 'reliability': values['supplier_reliability']})
    for code, sname, role, kind, account_key, daily, interval in STAFF:
        policy = {}
        if role == 'buyer':
            policy = {'safety_stock_days': values['safety_stock_days']}
        elif role == 'planner':
            policy = {'priority': 'due_date'}
        elif role == 'maintenance':
            policy = {'pm_wear_threshold': values['pm_wear_threshold']}
        elif role == 'controller':
            policy = {'cash_buffer': values['cash_buffer'], 'oar': {}, 'commentary': ''}
        elif role == 'sales_manager':
            policy = {'last_price_changes_pct': {}}
        elif role == 'plant_manager':
            policy = {'last_actions': []}
        _create_agent(scenario, code, sname, role, kind, daily_cost=Decimal(daily),
                      cost_account=acc[account_key], decision_interval=interval, policy=policy)

    controller = scenario.agents.get(role='controller')
    finance.post(
        date=start_date, description=f'Sandbox opening capital ({name})',
        lines=[{'account': acc['cash'], 'debit': Decimal(str(values['opening_capital']))},
               {'account': acc['capital'], 'credit': Decimal(str(values['opening_capital']))}],
        source_reference=f'sim:{scenario.pk}:opening:capital', user=controller.user,
    )
    plant = scenario.agents.get(role='plant_manager')
    for family in FAMILIES:
        commission_line(scenario, family=family, date=start_date, user=plant.user, values=values)
    _opening_stock(scenario, master, values, start_date, user)
    log_event(user, 'simulation.scenario.create', scenario, {'seed': seed, 'driver': driver})
    return scenario
