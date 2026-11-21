"""
The workspace registry: one declarative description of every module and
business object the frontend shows, and how a person interacts with each —
where it sits in the navigation, which columns its list shows, how its detail
page is laid out, its document lines, its status lifecycle, the transitions a
user can trigger and whether it can be created or edited.

Only the layout is written here. Everything the API already knows is read from
the live viewsets so the frontend cannot drift from the backend: endpoints
(reversed from the router), field types, labels, choices and foreign-key
targets (from the serializers), filters, search and ordering (from the
viewsets), transition endpoints (from their @actions) and which roles may read
and write (by running each viewset's own permission classes for every role).
`workspace/tests.py` fails when a router gains a viewset the registry does not
cover, or the registry names a field, filter or action the API does not have.
"""
import importlib
import re
from functools import lru_cache
from types import SimpleNamespace

from django.conf import settings
from django.urls import reverse
from rest_framework import serializers as drf

# None is an authenticated account with no UserRole row: every role check denies it.
ROLES = ('admin', 'accountant', 'auditor', 'viewer', None)

GROUPS = [
    ('finance', 'Finance'),
    ('sales', 'Sales'),
    ('purchasing', 'Purchasing'),
    ('inventory', 'Inventory'),
    ('manufacturing', 'Manufacturing'),
    ('planning', 'Planning'),
    ('master', 'Master data'),
    ('simulation', 'Simulation'),
    ('admin', 'Administration'),
]


def R(key, basename, label, singular, group, **layout):
    """A resource: one API collection and its pages."""
    return {'key': key, 'basename': basename, 'label': label, 'singular': singular, 'group': group, **layout}


def A(name, label, when=None, fields=(), confirm='', style='', requires=None, help=''):
    """A transition: a viewset @action shown as a button on the detail page."""
    return {'name': name, 'label': label, 'when': list(when or []), 'fields': list(fields), 'confirm': confirm,
            'style': style, 'requires': requires or {}, 'help': help}


def I(name, label, type='date', required=False, help=''):  # noqa: E743 — an action input, read as a column header
    return {'name': name, 'label': label, 'type': type, 'required': required, 'help': help}


ORDER_LIFECYCLE_NOTE = 'Status moves only through these actions; the server refuses edits once a document leaves draft.'

RESOURCES = [
    # --- Finance -----------------------------------------------------------------------------------------------
    R('finance.journal-entries', 'journalentry', 'Journal entries', 'Journal entry', 'finance',
      description='The double-entry ledger. System postings come from documents; manual entries are corrected by '
                  'reversal, never edited once posted.',
      title='{entry_number}', subtitle='{description}', status='status',
      columns=['entry_number', 'date', 'description', 'status', 'total_debit', 'source_reference'],
      default_ordering='-date', lifecycle=['draft', 'posted'], terminal=['voided'],
      lines={'field': 'lines', 'columns': ['account', 'debit', 'credit', 'reference'], 'totals': ['debit', 'credit']},
      actions=[A('reverse_entry', 'Reverse', when=['posted'], style='danger',
                 fields=[I('date', 'Reversal date'), I('description', 'Description', 'text')],
                 confirm='Post a mirror entry with every debit and credit swapped? The original stays posted.',
                 requires={'source_reference': None})],
      create=True, editable=['draft'], flow=True,
      form_fields=['date', 'description', 'status']),
    R('finance.journal-lines', 'journalline', 'Ledger lines', 'Ledger line', 'finance', nav=False,
      description='Shown inside their journal entries and in the general ledger.', title='{account_display}',
      columns=['account', 'debit', 'credit', 'reference']),
    R('finance.accounts', 'account', 'Chart of accounts', 'Account', 'finance',
      description='The account tree every posting resolves to. An account with ledger history is retired, '
                  'not deleted.',
      title='{code} {name}', columns=['code', 'name', 'account_type', 'parent_account', 'is_active'],
      default_ordering='code', create=True, editable=True, flow=True),
    R('finance.account-types', 'accounttype', 'Account types', 'Account type', 'finance', nav=False,
      title='{code} {name}', columns=['code', 'name', 'description'], create=True, editable=True),
    R('finance.invoices', 'invoice', 'Invoices', 'Invoice', 'finance',
      description='Customer invoices: posting debits the partner receivable and credits revenue.',
      title='{invoice_number}', subtitle='{customer}', status='status',
      columns=['invoice_number', 'customer', 'sales_order', 'due_date', 'status', 'total', 'journal_entry'],
      default_ordering='-due_date', lifecycle=['draft', 'sent', 'paid'], terminal=['cancelled', 'overdue'],
      lines={'field': 'line_items', 'columns': ['product', 'description', 'quantity', 'unit_price', 'total'],
             'totals': ['total']},
      actions=[A('post_to_ledger', 'Post to ledger', when=['draft', 'sent'], style='primary',
                 fields=[I('posting_date', 'Posting date')], requires={'journal_entry': None},
                 confirm='Post this invoice: Dr receivable / Cr revenue?')],
      create=True, editable=['draft'], flow=True),
    R('finance.payments', 'payment', 'Payments', 'Payment', 'finance',
      description='Customer payments: posting debits the deposit account and credits the receivable.',
      title='{payment_reference}', subtitle='{amount}',
      columns=['payment_reference', 'invoice', 'payment_date', 'amount', 'payment_method', 'journal_entry'],
      default_ordering='-payment_date',
      actions=[A('post_to_ledger', 'Post to ledger', style='primary', requires={'journal_entry': None},
                 confirm='Post this payment: Dr deposit account / Cr receivable?')],
      create=True, editable=True, flow=True),
    R('finance.saved-reports', 'savedreport', 'Saved reports', 'Saved report', 'finance',
      title='{name}', subtitle='{template_name}', status='status',
      columns=['name', 'template', 'status', 'created_by_display', 'created_at'],
      lifecycle=['generating', 'completed'], terminal=['failed'],
      actions=[A('regenerate', 'Regenerate', when=['completed', 'failed'])], create=True),
    R('finance.report-templates', 'template', 'Report templates', 'Report template', 'finance', nav=False,
      title='{name}', columns=['name', 'report_type', 'description', 'is_system'], create=True, editable=True),

    # --- Sales -------------------------------------------------------------------------------------------------
    R('sales.orders', 'salesorder', 'Sales orders', 'Sales order', 'sales',
      description='Order to cash: confirm, ship from stock (Dr COGS / Cr inventory), invoice, collect. '
                  + ORDER_LIFECYCLE_NOTE,
      title='{number}', subtitle='{customer_display}', status='status',
      columns=['number', 'customer', 'order_date', 'requested_date', 'status', 'total_amount'],
      default_ordering='-order_date',
      lifecycle=['draft', 'confirmed', 'partially_shipped', 'shipped', 'invoiced'], terminal=['cancelled'],
      lines={'field': 'lines', 'columns': ['product', 'description', 'quantity', 'unit_price', 'shipped_quantity',
                                           'open_quantity', 'line_total'], 'totals': ['line_total']},
      actions=[A('confirm', 'Confirm', when=['draft'], style='primary'),
               A('ship', 'Ship', when=['confirmed', 'partially_shipped'], style='primary',
                 fields=[I('ship_date', 'Ship date')], confirm='Ship every open line from stock at moving average?'),
               A('invoice', 'Create invoice', when=['shipped'], style='primary', fields=[I('due_date', 'Due date')]),
               A('cancel', 'Cancel', when=['draft', 'confirmed'], style='danger', confirm='Cancel this order?')],
      create=True, editable=['draft'], flow=True),

    # --- Purchasing --------------------------------------------------------------------------------------------
    R('purchasing.orders', 'purchaseorder', 'Purchase orders', 'Purchase order', 'purchasing',
      description='Procure to pay: confirm, receive into stock (Dr inventory / Cr payable). ' + ORDER_LIFECYCLE_NOTE,
      title='{number}', subtitle='{supplier_display}', status='status',
      columns=['number', 'supplier', 'order_date', 'expected_date', 'status', 'total_amount', 'source_reference'],
      default_ordering='-order_date',
      lifecycle=['draft', 'confirmed', 'partially_received', 'received'], terminal=['cancelled'],
      lines={'field': 'lines', 'columns': ['product', 'description', 'quantity', 'unit_price', 'received_quantity',
                                           'open_quantity', 'line_total'], 'totals': ['line_total']},
      actions=[A('confirm', 'Confirm', when=['draft'], style='primary'),
               A('receive', 'Receive', when=['confirmed', 'partially_received'], style='primary',
                 fields=[I('receipt_date', 'Receipt date')], confirm='Receive every open line into stock?'),
               A('cancel', 'Cancel', when=['draft', 'confirmed'], style='danger', confirm='Cancel this order?')],
      create=True, editable=['draft'], flow=True),

    # --- Inventory ---------------------------------------------------------------------------------------------
    R('inventory.stock-levels', 'stocklevel', 'Stock on hand', 'Stock level', 'inventory',
      description='The cached sum of done stock moves per product and warehouse, at moving-average cost.',
      title='{product_display}', subtitle='{warehouse_display}',
      columns=['product', 'warehouse', 'quantity_on_hand', 'average_cost'], money=['average_cost']),
    R('inventory.stock-moves', 'stockmove', 'Stock moves', 'Stock move', 'inventory',
      description='The append-only stock ledger: only done moves count; corrections are opposing moves.',
      title='{reference}', subtitle='{product_display}', status='status',
      columns=['reference', 'move_date', 'product', 'warehouse', 'move_type', 'quantity', 'unit_cost',
               'total_value', 'status', 'source_reference'],
      default_ordering='-move_date', lifecycle=['draft', 'done'], terminal=['cancelled'],
      actions=[A('complete', 'Complete', when=['draft'], style='primary',
                 confirm='Apply this move to the stock ledger? A done move cannot be edited.'),
               A('cancel', 'Cancel', when=['draft'], style='danger')],
      create=True, editable=['draft'], flow=True, money=['unit_cost', 'total_value']),
    R('inventory.warehouses', 'warehouse', 'Warehouses', 'Warehouse', 'inventory',
      title='{code} {name}', columns=['code', 'name', 'is_active'], create=True, editable=True, flow=True),

    # --- Manufacturing -----------------------------------------------------------------------------------------
    R('manufacturing.work-orders', 'workorder', 'Work orders', 'Work order', 'manufacturing',
      description='Make to stock: completion issues components and receives the finished good at rolled-up cost.',
      title='{number}', subtitle='{product_display}', status='status',
      columns=['number', 'product', 'quantity', 'due_date', 'status', 'source_reference'],
      default_ordering='-created_at',
      lifecycle=['draft', 'confirmed', 'in_progress', 'completed'], terminal=['cancelled'],
      actions=[A('confirm', 'Confirm', when=['draft'], style='primary'),
               A('start', 'Start', when=['confirmed']),
               A('complete', 'Complete', when=['confirmed', 'in_progress'], style='primary',
                 fields=[I('completion_date', 'Completion date')],
                 confirm='Issue every component and receive the finished good?'),
               A('cancel', 'Cancel', when=['draft', 'confirmed'], style='danger', confirm='Cancel this work order?')],
      panels=['availability'], create=True, editable=['draft'], flow=True),
    R('manufacturing.boms', 'billofmaterials', 'Bills of materials', 'Bill of materials', 'manufacturing',
      description='Recipes: one active BOM per product; cycles are refused.',
      title='{product_display}', subtitle='{reference}',
      columns=['product', 'reference', 'quantity', 'is_active'],
      lines={'field': 'lines', 'columns': ['component', 'quantity']}, create=True, editable=True, flow=True),

    # --- Planning ----------------------------------------------------------------------------------------------
    R('planning.mrp-runs', 'mrprun', 'MRP runs', 'MRP run', 'planning',
      description='Regenerative MRP: nets confirmed demand against supply and proposes buy and make orders.',
      title='{number}', subtitle='{warehouse_display}', status='status',
      columns=['number', 'as_of_date', 'warehouse', 'status', 'planned_order_count', 'completed_at'],
      default_ordering='-created_at', lifecycle=['pending', 'running', 'completed'], terminal=['failed'],
      actions=[A('convert', 'Convert to orders', when=['completed'], style='primary',
                 confirm='Convert every open planned order into draft purchase and work orders?')],
      create=True, form_fields=['warehouse', 'as_of_date'], flow=True),
    R('planning.planned-orders', 'plannedorder', 'Planned orders', 'Planned order', 'planning',
      description='Supply proposals pegged to the demand that caused them.',
      title='{order_type} {product_display}', subtitle='due {due_date}', status='status',
      columns=['run', 'product', 'order_type', 'quantity', 'release_date', 'due_date', 'expedited', 'status',
               'converted_reference'],
      default_ordering='due_date', lifecycle=['planned', 'converted'], terminal=['cancelled'],
      actions=[A('cancel', 'Cancel', when=['planned'], style='danger')], flow=True),

    # --- Master data -------------------------------------------------------------------------------------------
    R('master.partners', 'businesspartner', 'Business partners', 'Business partner', 'master',
      description='Customers and suppliers, with the receivable and payable accounts their documents post to.',
      title='{code} {name}', columns=['code', 'name', 'is_customer', 'is_supplier', 'email', 'is_active'],
      default_ordering='code', create=True, editable=True, flow=True),
    R('master.products', 'product', 'Products', 'Product', 'master',
      description='The item master: buy or make, costs, lead time, safety stock and lot sizing for MRP.',
      title='{sku} {name}', subtitle='{category_display}',
      columns=['sku', 'name', 'category', 'procurement_type', 'standard_cost', 'sales_price', 'lead_time_days',
               'is_active'],
      default_ordering='sku', create=True, editable=True, flow=True, money=['standard_cost', 'sales_price']),
    R('master.categories', 'productcategory', 'Product categories', 'Product category', 'master',
      description='Each category names the inventory, revenue and COGS accounts its products post to.',
      title='{name}', columns=['name', 'inventory_account', 'revenue_account', 'cogs_account'],
      create=True, editable=True),
    R('master.uoms', 'unitofmeasure', 'Units of measure', 'Unit of measure', 'master',
      title='{code} {name}', columns=['code', 'name'], create=True, editable=True),

    # --- Simulation --------------------------------------------------------------------------------------------
    R('simulation.scenarios', 'scenario', 'Scenarios', 'Scenario', 'simulation',
      title='{name}', subtitle='tick {tick} · {current_date}', status='status',
      columns=['name', 'tick', 'current_date', 'status', 'driver', 'agent_model'],
      lifecycle=['ready', 'running'], terminal=['halted'],
      actions=[A('resume', 'Resume', when=['halted'])]),
    R('simulation.lines', 'productionline', 'Production lines', 'Production line', 'simulation',
      title='{code}', subtitle='{family_name}', status='status',
      columns=['code', 'family_name', 'machines', 'shifts', 'status', 'wear', 'capex', 'commissioned_on'],
      lifecycle=['running'], terminal=['down', 'retired'],
      actions=[A('retire', 'Retire', when=['running', 'down'], style='danger',
                 confirm='Retire this line and dispose of the asset at book value?')],
      money=['capex', 'accumulated_depreciation']),
    R('simulation.agents', 'agent', 'Agents', 'Agent', 'simulation',
      title='{code}', subtitle='{role_label}',
      columns=['code', 'name', 'role', 'resource_kind', 'brain', 'daily_cost', 'decisions_count', 'ai_cost'],
      editable=True, form_fields=['brain', 'model', 'daily_cost', 'decision_interval', 'is_active'],
      money=['daily_cost']),
    R('simulation.decisions', 'agentdecision', 'Agent decisions', 'Agent decision', 'simulation',
      title='T{tick} {agent_code} · {action}', subtitle='{summary}', status='status',
      columns=['tick', 'sim_date', 'agent', 'kind', 'action', 'summary', 'driver', 'status'],
      default_ordering='-id'),
    R('simulation.snapshots', 'ticksnapshot', 'Tick snapshots', 'Tick snapshot', 'simulation', nav=False,
      title='Tick {tick}', columns=['scenario', 'tick', 'sim_date']),
    R('simulation.experiments', 'experiment', 'Experiments', 'Experiment', 'simulation',
      title='{name}', status='status',
      columns=['name', 'scenario', 'horizon_ticks', 'driver', 'status', 'completed_at'],
      lifecycle=['pending', 'running', 'completed'], terminal=['failed']),

    # --- Administration ----------------------------------------------------------------------------------------
    R('admin.companies', 'company', 'Company', 'Company', 'admin',
      title='{name}', subtitle='{legal_name}', columns=['name', 'legal_name', 'currency_code', 'is_active'],
      create=True, editable=True),
    R('admin.users', 'user', 'Users', 'User', 'admin',
      title='{username}', columns=['username', 'first_name', 'last_name', 'email'], create=True, editable=True),
    R('admin.roles', 'userrole', 'Roles', 'Role', 'admin', nav=False, title='{role}', columns=['role']),
    R('admin.audit-log', 'auditlog', 'Audit log', 'Audit event', 'admin',
      description='Every document transition, written by the services (core.audit.log_event).',
      title='{model_name} #{object_id}', subtitle='{object_repr}',
      columns=['timestamp', 'user', 'action', 'model_name', 'object_repr'], default_ordering='-timestamp'),
]

# Navigation entries that are not a resource of their own: a filtered view of one, a page, or a link.
VIEWS = [
    {'key': 'sales.customers', 'group': 'sales', 'label': 'Customers', 'resource': 'master.partners',
     'filters': {'is_customer': 'true'}},
    {'key': 'purchasing.suppliers', 'group': 'purchasing', 'label': 'Suppliers', 'resource': 'master.partners',
     'filters': {'is_supplier': 'true'}},
    {'key': 'sales.invoices', 'group': 'sales', 'label': 'Invoices', 'resource': 'finance.invoices', 'filters': {}},
    {'key': 'manufacturing.components', 'group': 'manufacturing', 'label': 'Make items', 'resource': 'master.products',
     'filters': {'procurement_type': 'make'}},
]
PAGES = [
    {'key': 'finance.statements', 'group': 'finance', 'label': 'Financial statements', 'page': 'statements',
     'access': 'journalline', 'position': 0},
    {'key': 'simulation.console', 'group': 'simulation', 'label': 'Simulation console', 'href': '/console/',
     'access': 'scenario', 'position': 0},
]

# Work queues on the home page: a filtered list that needs someone's attention.
QUEUES = [
    ('sales.orders', {'status': 'draft'}, 'Sales orders to confirm'),
    ('sales.orders', {'status': 'confirmed'}, 'Sales orders to ship'),
    ('sales.orders', {'status': 'shipped'}, 'Shipped, not yet invoiced'),
    ('finance.invoices', {'status': 'draft'}, 'Invoices to post'),
    ('finance.invoices', {'status': 'sent'}, 'Invoices awaiting payment'),
    ('purchasing.orders', {'status': 'draft'}, 'Purchase orders to confirm'),
    ('purchasing.orders', {'status': 'confirmed'}, 'Purchase orders to receive'),
    ('manufacturing.work-orders', {'status': 'confirmed'}, 'Work orders to start'),
    ('manufacturing.work-orders', {'status': 'in_progress'}, 'Work orders in progress'),
    ('planning.planned-orders', {'status': 'planned'}, 'Planned orders to convert'),
    ('inventory.stock-moves', {'status': 'draft'}, 'Stock moves to complete'),
    ('finance.journal-entries', {'status': 'draft'}, 'Journal entries to post'),
]

MONEY = re.compile(r'(price|cost|amount|total|value|debit|credit|balance|capex)', re.I)
LONG_TEXT = {'description', 'notes', 'address', 'summary', 'rationale', 'error_message'}


# --- introspection --------------------------------------------------------------------------------------------

def router_index():
    """{basename: viewset} across every local app's DefaultRouter."""
    index = {}
    for app in settings.INSTALLED_APPS:
        if '.' in app:
            continue
        try:
            module = importlib.import_module(f'{app}.urls')
        except ModuleNotFoundError:
            continue
        router = getattr(module, 'router', None)
        if router is None:
            continue
        for _prefix, viewset, basename in router.registry:
            index[basename] = viewset
    return index


def model_of(serializer, name, field):
    if getattr(field, 'queryset', None) is not None:
        return field.queryset.model
    model = getattr(getattr(serializer, 'Meta', None), 'model', None)
    source = field.source if field.source not in (None, '*') else name
    try:
        return model._meta.get_field(source.split('.')[0]).related_model
    except Exception:  # a non-model source: no reference to link
        return None


def field_meta(serializer, name, field):
    meta = {
        'name': name, 'label': str(field.label or name.replace('_', ' ').capitalize()),
        'read_only': bool(field.read_only), 'required': bool(field.required and not field.read_only),
        'help': str(field.help_text or ''),
    }
    if isinstance(field, drf.ListSerializer):
        child = field.child
        meta.update(type='lines', child={n: field_meta(child, n, f) for n, f in child.fields.items()})
    elif isinstance(field, drf.ManyRelatedField):
        meta.update(type='refs')
    elif isinstance(field, drf.PrimaryKeyRelatedField):
        model = model_of(serializer, name, field)
        meta.update(type='ref', model=model._meta.label if model is not None else None)
    elif isinstance(field, drf.ChoiceField):
        meta.update(type='choice', choices=[[str(k), str(v)] for k, v in field.choices.items()])
    elif isinstance(field, drf.BooleanField):
        meta.update(type='bool')
    elif isinstance(field, drf.DateTimeField):
        meta.update(type='datetime')
    elif isinstance(field, drf.DateField):
        meta.update(type='date')
    elif isinstance(field, drf.DecimalField):
        meta.update(type='decimal', places=field.decimal_places, money=bool(MONEY.search(name)))
    elif isinstance(field, (drf.IntegerField, drf.FloatField)):
        meta.update(type='number', money=bool(MONEY.search(name)))
    elif isinstance(field, drf.JSONField):
        meta.update(type='json')
    elif isinstance(field, drf.EmailField):
        meta.update(type='email')
    elif isinstance(field, drf.SerializerMethodField):
        meta.update(type='computed', money=bool(MONEY.search(name)))
    else:
        meta.update(type='long' if name in LONG_TEXT else 'text')
    if name.endswith('_display'):
        meta['display_for'] = name[:-len('_display')]
    # Keep the payload lean: drop defaults the client assumes.
    return {k: v for k, v in meta.items() if v not in ('', False) or k == 'type'}


def fake_request(role, method):
    user = SimpleNamespace(is_authenticated=True, pk=0, id=0, is_staff=False, is_superuser=False)
    if role is not None:
        user.role = SimpleNamespace(role=role)
    return SimpleNamespace(method=method, user=user, query_params={}, data={})


def permitted(viewset, request, action):
    view = viewset()
    view.action, view.request, view.format_kwarg, view.kwargs, view.args = action, request, None, {}, ()
    return all(permission.has_permission(request, view) for permission in view.get_permissions())


def roles_allowed(viewset, method, action):
    return [role or 'none' for role in ROLES if permitted(viewset, fake_request(role, method), action)]


def filters_of(viewset, model):
    raw = getattr(viewset, 'filterset_fields', None) or []
    names = list(raw.keys()) if isinstance(raw, dict) else list(raw)
    out = []
    for name in names:
        if '__' in name:
            continue
        try:
            field = model._meta.get_field(name)
        except Exception:  # a filter on something other than a model field
            continue
        entry = {'name': name, 'label': str(getattr(field, 'verbose_name', name)).capitalize()}
        if getattr(field, 'choices', None):
            entry.update(type='choice', choices=[[str(k), str(v)] for k, v in field.choices])
        elif field.get_internal_type() == 'BooleanField':
            entry.update(type='bool')
        elif field.is_relation and field.related_model is not None:
            entry.update(type='ref', model=field.related_model._meta.label)
        elif field.get_internal_type() == 'DateField':
            entry.update(type='date')
        else:
            continue
        out.append(entry)
    return out


def route_of(key):
    return '/app/' + key.replace('.', '/') + '/'


@lru_cache(maxsize=1)
def build():
    """The registry as JSON-ready data (cached per process: it only reads code)."""
    index = router_index()
    resources, models = {}, {}
    for spec in RESOURCES:
        viewset = index[spec['basename']]
        serializer = viewset.serializer_class()
        model = viewset.queryset.model
        endpoint = reverse(f'{spec["basename"]}-list')
        fields = {name: field_meta(serializer, name, field) for name, field in serializer.fields.items()}
        for name in spec.get('money', []):
            if name in fields:
                fields[name]['money'] = True
        writable = hasattr(viewset, 'create')
        actions = []
        for extra in viewset.get_extra_actions():
            layout = next((a for a in spec.get('actions', []) if a['name'] == extra.__name__), None)
            method = sorted(extra.mapping.keys())[0].upper()
            entry = {'name': extra.__name__, 'url_path': extra.url_path, 'detail': extra.detail, 'method': method,
                     'roles': roles_allowed(viewset, method, extra.__name__)}
            if layout:
                entry.update({k: v for k, v in layout.items() if k != 'name'})
                entry['button'] = True
            actions.append(entry)
        resource = {
            **{k: v for k, v in spec.items() if k not in ('actions', 'basename', 'money')},
            'basename': spec['basename'], 'endpoint': endpoint, 'route': route_of(spec['key']),
            'model': model._meta.label, 'fields': fields, 'nav': spec.get('nav', True),
            'filters': filters_of(viewset, model),
            'search': bool(getattr(viewset, 'search_fields', None)),
            'ordering': list(getattr(viewset, 'ordering_fields', None) or []),
            'actions': actions,
            'access': {
                'read': roles_allowed(viewset, 'GET', 'list'),
                'write': roles_allowed(viewset, 'POST', 'create') if writable else [],
                'update': roles_allowed(viewset, 'PATCH', 'partial_update') if hasattr(viewset, 'update') else [],
                'delete': roles_allowed(viewset, 'DELETE', 'destroy') if hasattr(viewset, 'destroy') else [],
            },
            'create': bool(spec.get('create')) and writable,
            'editable': spec.get('editable', False) if hasattr(viewset, 'update') else False,
        }
        resources[spec['key']] = resource
        models.setdefault(resource['model'], spec['key'])

    for resource in resources.values():
        children = [c for f in resource['fields'].values() for c in f.get('child', {}).values()]
        for meta in list(resource['fields'].values()) + children:
            if meta.get('model'):
                meta['resource'] = models.get(meta['model'])
        for entry in resource['filters']:
            if entry.get('model'):
                entry['resource'] = models.get(entry['model'])

    groups = []
    for group_key, group_label in GROUPS:
        items = []
        for page in [p for p in PAGES if p['group'] == group_key]:
            items.append({'type': 'page', 'key': page['key'], 'label': page['label'],
                          'route': page.get('href') or route_of(page['key']), 'external': bool(page.get('href')),
                          'page': page.get('page'), 'read': roles_allowed(index[page['access']], 'GET', 'list')})
        for resource in [r for r in resources.values() if r['group'] == group_key and r['nav']]:
            items.append({'type': 'resource', 'key': resource['key'], 'label': resource['label'],
                          'route': resource['route'], 'read': resource['access']['read']})
        for view in [v for v in VIEWS if v['group'] == group_key]:
            target = resources[view['resource']]
            query = '&'.join(f'{k}={v}' for k, v in view['filters'].items())
            items.append({'type': 'view', 'key': view['key'], 'label': view['label'], 'resource': target['key'],
                          'route': target['route'] + (f'?{query}' if query else ''), 'read': target['access']['read']})
        groups.append({'key': group_key, 'label': group_label, 'items': items})

    return {
        'version': 1, 'roles': [r or 'none' for r in ROLES], 'groups': groups, 'resources': resources,
        'models': models, 'queues': [{'resource': r, 'filters': f, 'label': label} for r, f, label in QUEUES],
        'pages': {p['key']: {**p, 'route': p.get('href') or route_of(p['key'])} for p in PAGES},
    }


def resource_for_model(label):
    registry = build()
    key = registry['models'].get(label)
    return registry['resources'].get(key) if key else None


def viewset_for(key):
    return router_index()[build()['resources'][key]['basename']]
