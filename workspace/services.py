"""
Read models for the workspace shell: document flow, global search, the home
overview and financial statements.

Every read goes through the owning viewset — its permission classes decide
whether the user may see a section, its get_queryset() applies any per-user
scoping and its filter backends run the search — so the workspace never shows
a role more than the API would.
"""
from types import SimpleNamespace

from django.apps import apps
from django.http import Http404
from django.utils.dateparse import parse_date
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from accounts.models import AuditLog
from inventory.models import StockMove
from journal.models import JournalEntry
from reports.report_generators import (generate_balance_sheet, generate_general_ledger, generate_income_statement,
                                       generate_trial_balance)

from . import registry

STATEMENTS = {
    'trial_balance': 'Trial balance',
    'balance_sheet': 'Balance sheet',
    'income_statement': 'Income statement',
    'general_ledger': 'General ledger',
}


# --- access ---------------------------------------------------------------------------------------------------

def make_view(viewset, request, action):
    view = viewset()
    view.request, view.action, view.format_kwarg, view.kwargs, view.args = request, action, None, {}, ()
    return view


class Access:
    """Per-request cache of 'may this user list resource X', answered by the resource's own permission classes."""

    def __init__(self, request):
        self.request = request
        self.read_request = SimpleNamespace(method='GET', user=request.user, query_params={}, data={})
        self.cache = {}

    def can_read(self, key):
        if key not in self.cache:
            self.cache[key] = registry.permitted(registry.viewset_for(key), self.read_request, 'list')
        return self.cache[key]

    def can_read_basename(self, basename):
        key = next((k for k, r in registry.build()['resources'].items() if r['basename'] == basename), None)
        return bool(key) and self.can_read(key)


# --- document flow --------------------------------------------------------------------------------------------

def money(value):
    return float(value) if value is not None else None


class Flow:
    """Collects what one business object is linked to: documents, ledger entries, stock moves."""

    def __init__(self, access):
        self.access = access
        self.related = []
        self.seen = set()
        self.entry_ids = set()
        self.move_ids = set()

    def add(self, relation, obj, **extra):
        if obj is None:
            return
        resource = registry.resource_for_model(obj._meta.label)
        key = (relation, obj._meta.label, obj.pk)
        if key in self.seen:
            return
        self.seen.add(key)
        if resource is None or not self.access.can_read(resource['key']):
            return
        item = {'relation': relation, 'resource': resource['key'], 'id': obj.pk, 'label': str(obj)}
        status = getattr(obj, 'status', None)
        if status:
            item['status'] = status
        item.update({k: v for k, v in extra.items() if v is not None})
        self.related.append(item)

    def pseudo(self, relation, reference, search_resource, **extra):
        """A business event with no model of its own (a shipment, a goods receipt): link to its stock moves."""
        if not reference or ('pseudo', reference) in self.seen:
            return
        self.seen.add(('pseudo', reference))
        if not self.access.can_read(search_resource):
            return
        self.related.append({'relation': relation, 'label': reference, 'resource': search_resource,
                             'search': reference, **{k: v for k, v in extra.items() if v is not None}})

    def entries(self, *ids):
        self.entry_ids.update(i for i in ids if i)

    def entry_numbers(self, *numbers):
        numbers = [n for n in numbers if n]
        if numbers:
            self.entries(*JournalEntry.objects.filter(entry_number__in=numbers).values_list('id', flat=True))

    def entries_for_reference(self, reference):
        if reference:
            self.entries(*JournalEntry.objects.filter(lines__reference=reference).values_list('id', flat=True))

    def moves(self, queryset):
        self.move_ids.update(queryset.values_list('id', flat=True)[:200])


def audit_events(obj, event):
    return AuditLog.objects.filter(model_name=obj._meta.label, object_id=str(obj.pk), details__event=event)


def document_by_event(model_label, event, key, value):
    """The document whose transition logged `details[key] == value` (a GRN's order, a shipment's order)."""
    log = AuditLog.objects.filter(model_name=model_label, details__event=event, **{f'details__{key}': value}).first()
    if log is None:
        return None
    return apps.get_model(model_label).objects.filter(pk=log.object_id).first()


def model(label):
    return apps.get_model(label) if apps.is_installed(label.split('.')[0]) else None


def event_moves(event):
    """The stock moves a shipment or goods-receipt audit event names."""
    return StockMove.objects.filter(reference__in=event.details.get('moves') or [])


def business_date(moves, event):
    """A shipment's or receipt's own date: its stock moves' move_date, not the audit row's wall-clock timestamp."""
    return moves.order_by('move_date').values_list('move_date', flat=True).first() or event.timestamp.date()


def flow_sales_order(flow, order):
    flow.add('Customer', order.customer)
    flow.add('Warehouse', order.warehouse)
    for line in order.lines.select_related('product'):
        flow.add('Product', line.product, quantity=float(line.quantity))
    for event in audit_events(order, 'sales_order.ship'):
        moves = event_moves(event)
        flow.pseudo('Shipment', event.details.get('shipment'), 'inventory.stock-moves',
                    date=business_date(moves, event))
        flow.entry_numbers(event.details.get('journal_entry'))
        flow.moves(moves)
    for invoice in order.invoices.all():
        flow.add('Invoice', invoice, date=invoice.due_date, date_label='due', amount=money(invoice.total))
        flow.entries(invoice.journal_entry_id)
        for payment in invoice.payments.all():
            flow.add('Payment', payment, date=payment.payment_date, amount=money(payment.amount))
            flow.entries(payment.journal_entry_id)
    planned = model('mrp.PlannedOrder')
    for plan in planned.objects.filter(demand_reference__startswith=f'{order.number} ').select_related('run'):
        flow.add('Planned supply', plan, date=plan.due_date, date_label='due')
        flow.add('Planned in', plan.run)


def flow_purchase_order(flow, order):
    flow.add('Supplier', order.supplier)
    flow.add('Warehouse', order.warehouse)
    for line in order.lines.select_related('product'):
        flow.add('Product', line.product, quantity=float(line.quantity))
    for event in audit_events(order, 'purchase_order.receive'):
        moves = event_moves(event)
        flow.pseudo('Goods receipt', event.details.get('grn'), 'inventory.stock-moves',
                    date=business_date(moves, event))
        flow.entry_numbers(event.details.get('journal_entry'))
        flow.moves(moves)
    if order.source_reference:
        flow.add('Planned by', model('mrp.MRPRun').objects.filter(number=order.source_reference).first())
    for plan in model('mrp.PlannedOrder').objects.filter(converted_reference=order.number):
        flow.add('Planned order', plan, date=plan.due_date, date_label='due')


def flow_work_order(flow, work_order):
    flow.add('Product', work_order.product)
    flow.add('Bill of materials', work_order.bom)
    flow.add('Warehouse', work_order.warehouse)
    for line in work_order.bom.lines.select_related('component'):
        required = line.quantity * work_order.quantity / work_order.bom.quantity
        flow.add('Component', line.component, quantity=float(required))
    if work_order.source_reference:
        flow.add('Planned by', model('mrp.MRPRun').objects.filter(number=work_order.source_reference).first())
    for plan in model('mrp.PlannedOrder').objects.filter(converted_reference=work_order.number):
        flow.add('Planned order', plan, date=plan.due_date, date_label='due')
        sales_number = plan.demand_reference.split(' ')[0] if plan.demand_reference else ''
        flow.add('Pegged demand', model('sales.SalesOrder').objects.filter(number=sales_number).first())
    flow.moves(StockMove.objects.filter(source_reference=work_order.number))
    flow.entries(*JournalEntry.objects.filter(source_reference=f'wo:{work_order.number}:complete')
                 .values_list('id', flat=True))


def flow_invoice(flow, invoice):
    flow.add('Customer', invoice.partner)
    flow.add('Sales order', invoice.sales_order)
    flow.add('Revenue account', invoice.revenue_account)
    flow.entries(invoice.journal_entry_id)
    for payment in invoice.payments.all():
        flow.add('Payment', payment, date=payment.payment_date, amount=money(payment.amount))
        flow.entries(payment.journal_entry_id)
    for item in invoice.line_items.select_related('product'):
        flow.add('Product', item.product)


def flow_payment(flow, payment):
    flow.add('Invoice', payment.invoice, amount=money(payment.invoice.total))
    flow.add('Customer', payment.invoice.partner)
    flow.add('Sales order', payment.invoice.sales_order)
    flow.add('Deposit account', payment.deposit_account)
    flow.entries(payment.journal_entry_id, payment.invoice.journal_entry_id)


def source_document(flow, reference):
    """The business document behind a system posting's source_reference."""
    kind, _, rest = (reference or '').partition(':')
    if kind == 'grn':
        flow.add('Source: purchase order', document_by_event('purchasing.PurchaseOrder', 'purchase_order.receive',
                                                             'grn', rest))
        flow.pseudo('Goods receipt', rest, 'inventory.stock-moves')
        flow.moves(StockMove.objects.filter(source_reference=rest))
    elif kind == 'shipment':
        flow.add('Source: sales order', document_by_event('sales.SalesOrder', 'sales_order.ship', 'shipment', rest))
        flow.pseudo('Shipment', rest, 'inventory.stock-moves')
        flow.moves(StockMove.objects.filter(source_reference=rest))
    elif kind == 'wo':
        number = rest.split(':')[0]
        flow.add('Source: work order', model('manufacturing.WorkOrder').objects.filter(number=number).first())
        flow.moves(StockMove.objects.filter(source_reference=number))
    elif kind == 'invoice':
        flow.add('Source: invoice', model('invoices.Invoice').objects.filter(invoice_number=rest).first())
    elif kind == 'payment':
        flow.add('Source: payment', model('invoices.Payment').objects.filter(payment_reference=rest).first())
    elif kind == 'reversal':
        flow.add('Reverses', JournalEntry.objects.filter(pk=rest).first())
    elif kind == 'sim' and model('simulation.Scenario') is not None:
        flow.add('Posted by the simulation', model('simulation.Scenario').objects.filter(pk=rest.split(':')[0]).first())


def flow_journal_entry(flow, entry):
    flow.entries(entry.pk)
    source_document(flow, entry.source_reference)
    flow.add('Reverses', entry.reverses)
    flow.add('Reversed by', getattr(entry, 'reversed_by', None))  # the reverse one-to-one, if any
    for line in entry.lines.select_related('account'):
        flow.add('Account', line.account)


def flow_stock_move(flow, move):
    flow.add('Product', move.product)
    flow.add('Warehouse', move.warehouse)
    reference = move.source_reference
    if reference.startswith('GRN-'):
        flow.add('Source: purchase order', document_by_event('purchasing.PurchaseOrder', 'purchase_order.receive',
                                                             'grn', reference))
    elif reference.startswith('SHP-'):
        flow.add('Source: sales order', document_by_event('sales.SalesOrder', 'sales_order.ship', 'shipment',
                                                          reference))
    elif reference.startswith('WO-'):
        flow.add('Source: work order', model('manufacturing.WorkOrder').objects.filter(number=reference).first())
    if reference:
        flow.moves(StockMove.objects.filter(source_reference=reference))
        flow.entries_for_reference(reference)
    else:
        flow.moves(StockMove.objects.filter(pk=move.pk))


def flow_mrp_run(flow, run):
    flow.add('Warehouse', run.warehouse)
    for plan in run.planned_orders.select_related('product')[:60]:
        flow.add('Planned order', plan, date=plan.due_date, date_label='due', quantity=float(plan.quantity))
    for order in model('purchasing.PurchaseOrder').objects.filter(source_reference=run.number):
        flow.add('Converted to', order, date=order.order_date)
    for order in model('manufacturing.WorkOrder').objects.filter(source_reference=run.number):
        flow.add('Converted to', order, date=order.due_date, date_label='due')


def flow_planned_order(flow, plan):
    flow.add('MRP run', plan.run)
    flow.add('Product', plan.product)
    flow.add('Parent planned order', plan.parent)
    for child in plan.component_requirements.select_related('product'):
        flow.add('Component requirement', child, date=child.due_date, date_label='due')
    if plan.converted_reference:
        flow.add('Converted to', model('purchasing.PurchaseOrder').objects.filter(number=plan.converted_reference)
                 .first())
        flow.add('Converted to', model('manufacturing.WorkOrder').objects.filter(number=plan.converted_reference)
                 .first())
    sales_number = plan.demand_reference.split(' ')[0] if plan.demand_reference else ''
    flow.add('Pegged demand', model('sales.SalesOrder').objects.filter(number=sales_number).first())


def flow_partner(flow, partner):
    flow.add('Receivable account', partner.receivable_account)
    flow.add('Payable account', partner.payable_account)
    for order in partner.sales_orders.order_by('-order_date', '-id')[:10]:
        flow.add('Sales order', order, date=order.order_date, amount=money(order.total_amount))
    for order in partner.purchase_orders.order_by('-order_date', '-id')[:10]:
        flow.add('Purchase order', order, date=order.order_date, amount=money(order.total_amount))
    for invoice in partner.invoices.order_by('-due_date', '-id')[:10]:
        flow.add('Invoice', invoice, date=invoice.due_date, date_label='due', amount=money(invoice.total))
    for product in partner.supplied_products.all()[:20]:
        flow.add('Supplies', product)


def flow_product(flow, product):
    flow.add('Category', product.category)
    flow.add('Unit of measure', product.uom)
    flow.add('Default supplier', product.default_supplier)
    for bom in product.boms.all():
        flow.add('Bill of materials', bom)
    for line in product.used_in_bom_lines.select_related('bom'):
        flow.add('Used in', line.bom, quantity=float(line.quantity))
    for level in product.stock_levels.select_related('warehouse'):
        flow.add('Stock', level, quantity=float(level.quantity_on_hand), amount=money(level.average_cost))
    for line in product.sales_lines.select_related('order').order_by('-order__order_date')[:10]:
        flow.add('Sales order', line.order, date=line.order.order_date, quantity=float(line.quantity))
    for line in product.purchase_lines.select_related('order').order_by('-order__order_date')[:10]:
        flow.add('Purchase order', line.order, date=line.order.order_date, quantity=float(line.quantity))
    for order in product.work_orders.order_by('-id')[:10]:
        flow.add('Work order', order, date=order.due_date, date_label='due', quantity=float(order.quantity))
    flow.moves(product.stock_moves.order_by('-move_date', '-id'))


def flow_account(flow, account):
    flow.add('Parent account', account.parent_account)
    for child in account.child_accounts.all():
        flow.add('Sub-account', child)
    for partner in account.receivable_partners.all()[:20]:
        flow.add('Receivable of', partner)
    for partner in account.payable_partners.all()[:20]:
        flow.add('Payable of', partner)
    for relation in ('inventory_categories', 'revenue_categories', 'cogs_categories'):
        for category in getattr(account, relation).all():
            flow.add('Used by category', category)
    flow.entries(*JournalEntry.objects.filter(lines__account=account, status='posted')
                 .order_by('-date', '-id').values_list('id', flat=True)[:25])


def flow_warehouse(flow, warehouse):
    for level in warehouse.stock_levels.select_related('product'):
        flow.add('Stock', level, quantity=float(level.quantity_on_hand))
    flow.moves(warehouse.stock_moves.order_by('-move_date', '-id'))


def flow_bom(flow, bom):
    flow.add('Product', bom.product)
    for line in bom.lines.select_related('component'):
        flow.add('Component', line.component, quantity=float(line.quantity))
    for order in bom.work_orders.order_by('-id')[:15]:
        flow.add('Work order', order, date=order.due_date, date_label='due')


FLOWS = {
    'sales.SalesOrder': flow_sales_order,
    'purchasing.PurchaseOrder': flow_purchase_order,
    'manufacturing.WorkOrder': flow_work_order,
    'invoices.Invoice': flow_invoice,
    'invoices.Payment': flow_payment,
    'journal.JournalEntry': flow_journal_entry,
    'inventory.StockMove': flow_stock_move,
    'mrp.MRPRun': flow_mrp_run,
    'mrp.PlannedOrder': flow_planned_order,
    'partners.BusinessPartner': flow_partner,
    'products.Product': flow_product,
    'coa.Account': flow_account,
    'inventory.Warehouse': flow_warehouse,
    'manufacturing.BillOfMaterials': flow_bom,
}


def entry_payload(entry):
    lines = [{
        'account_id': line.account_id, 'account': line.account.code, 'account_name': line.account.name,
        'debit': float(line.debit), 'credit': float(line.credit), 'reference': line.reference,
    } for line in entry.lines.all()]
    return {
        'id': entry.id, 'entry_number': entry.entry_number, 'date': str(entry.date), 'description': entry.description,
        'status': entry.status, 'source_reference': entry.source_reference, 'lines': lines,
        'total_debit': round(sum(line['debit'] for line in lines), 2),
        'total_credit': round(sum(line['credit'] for line in lines), 2),
    }


def move_payload(move):
    return {
        'id': move.id, 'reference': move.reference, 'move_date': str(move.move_date), 'product_id': move.product_id,
        'product': move.product.sku, 'warehouse': move.warehouse.code, 'move_type': move.move_type,
        'move_type_label': move.get_move_type_display(), 'quantity': float(move.quantity),
        'signed_quantity': float(move.signed_quantity), 'unit_cost': float(move.unit_cost),
        'total_value': float(move.total_value), 'status': move.status, 'source_reference': move.source_reference,
    }


def flow(request, key, object_id):
    """Everything one business object links to, each section filtered by what the user may read."""
    resources = registry.build()['resources']
    if key not in resources:
        raise Http404(f'Unknown resource {key}')
    access = Access(request)
    if not access.can_read(key):
        raise PermissionDenied(f'Your role cannot read {resources[key]["label"].lower()}')
    view = make_view(registry.viewset_for(key), request, 'retrieve')
    obj = view.get_queryset().filter(pk=object_id).first()
    if obj is None:
        raise Http404(f'No {resources[key]["singular"].lower()} {object_id}')

    collected = Flow(access)
    builder = FLOWS.get(resources[key]['model'])
    if builder:
        builder(collected, obj)

    restricted = []
    if access.can_read('finance.journal-lines'):
        entries = JournalEntry.objects.filter(pk__in=collected.entry_ids).prefetch_related('lines__account') \
            .order_by('date', 'id')
        journal = [entry_payload(e) for e in entries]
    else:
        journal = None
        restricted.append('journal_entries')
    if access.can_read('inventory.stock-moves'):
        moves = StockMove.objects.filter(pk__in=collected.move_ids).select_related('product', 'warehouse') \
            .order_by('move_date', 'id')
        stock = [move_payload(m) for m in moves]
    else:
        stock = None
        restricted.append('stock_moves')
    if access.can_read('admin.audit-log'):
        logs = AuditLog.objects.filter(model_name=obj._meta.label, object_id=str(obj.pk)).select_related('user')[:50]
        events = [{'timestamp': log.timestamp.isoformat(), 'user': log.user.username if log.user_id else None,
                   'action': log.action, 'event': (log.details or {}).get('event'), 'details': log.details}
                  for log in logs]
    else:
        events = None
        restricted.append('events')
    return {
        'object': {'resource': key, 'id': obj.pk, 'label': str(obj)},
        'related': collected.related, 'journal_entries': journal, 'stock_moves': stock, 'events': events,
        'restricted': restricted,
    }


# --- search, overview, statements -----------------------------------------------------------------------------

def search(request, per_resource=5, limit=40):
    """Search every resource the user may list, through each viewset's own search fields."""
    term = (request.query_params.get('search') or '').strip()
    if len(term) < 2:
        return {'search': term, 'results': []}
    access = Access(request)
    lowered = term.lower()
    results = []
    for order, (key, resource) in enumerate(registry.build()['resources'].items()):
        if not resource['search'] or not access.can_read(key):
            continue
        view = make_view(registry.viewset_for(key), request, 'list')
        for obj in view.filter_queryset(view.get_queryset())[:per_resource]:
            label = str(obj)
            # Rank: the term starts the record's name, then appears in it, then only in another searched field.
            rank = 0 if label.lower().startswith(lowered) else 1 if lowered in label.lower() else 2
            item = {'resource': key, 'resource_label': resource['singular'], 'id': obj.pk, 'label': label,
                    'rank': rank, '_order': order}
            if getattr(obj, 'status', None):
                item['status'] = obj.status
            results.append(item)
    results.sort(key=lambda r: (r['rank'], r.pop('_order')))
    return {'search': term, 'results': results[:limit]}


def overview(request):
    """Work queues and module counts for the home page, for what this user may read."""
    reg = registry.build()
    access = Access(request)
    queues = []
    for queue in reg['queues']:
        key = queue['resource']
        if not access.can_read(key):
            continue
        view = make_view(registry.viewset_for(key), request, 'list')
        count = view.get_queryset().filter(**queue['filters']).count()
        query = '&'.join(f'{k}={v}' for k, v in queue['filters'].items())
        queues.append({**queue, 'count': count, 'route': f'{reg["resources"][key]["route"]}?{query}'})
    counts = {}
    for key, resource in reg['resources'].items():
        if resource['nav'] and access.can_read(key):
            counts[key] = make_view(registry.viewset_for(key), request, 'list').get_queryset().count()
    activity = None
    if access.can_read('admin.audit-log'):
        activity = []
        for log in AuditLog.objects.select_related('user').order_by('-timestamp')[:15]:
            target = registry.resource_for_model(log.model_name)
            activity.append({
                'timestamp': log.timestamp.isoformat(), 'user': log.user.username if log.user_id else None,
                'event': (log.details or {}).get('event') or log.action, 'label': log.object_repr,
                'resource': target['key'] if target else None,
                'id': int(log.object_id) if target and str(log.object_id).isdigit() else None,
            })
    company = None
    company_model = model('company.Company')
    if company_model is not None and access.can_read('admin.companies'):
        found = company_model.objects.filter(is_active=True).first()
        if found:
            company = {'id': found.id, 'name': found.name, 'currency_code': found.currency_code}
    return {'queues': queues, 'counts': counts, 'activity': activity, 'company': company}


def statements(request):
    """A financial statement compiled by reports.report_generators, live, for ledger readers."""
    access = Access(request)
    if not access.can_read('finance.journal-lines'):
        raise PermissionDenied('Financial statements need the accountant, auditor or admin role')
    params = request.query_params
    report = params.get('report', 'trial_balance')
    if report not in STATEMENTS:
        raise serializers.ValidationError({'report': f'one of {sorted(STATEMENTS)}'})
    dates = {}
    for name in ('as_of', 'from', 'to'):
        value = params.get(name)
        if value:
            parsed = parse_date(value)
            if parsed is None:
                raise serializers.ValidationError({name: 'YYYY-MM-DD'})
            dates[name] = str(parsed)
    if report == 'trial_balance':
        data = generate_trial_balance({'as_of_date': dates['as_of']} if 'as_of' in dates else {})
    elif report == 'balance_sheet':
        data = generate_balance_sheet({'as_of_date': dates['as_of']} if 'as_of' in dates else {})
    elif report == 'income_statement':
        data = generate_income_statement({k: v for k, v in (('from_date', dates.get('from')),
                                                            ('to_date', dates.get('to'))) if v})
    else:
        account = params.get('account')
        if not account or not str(account).isdigit():
            raise serializers.ValidationError({'account': 'choose an account for the general ledger'})
        bounds = (('from_date', dates.get('from')), ('to_date', dates.get('to')), ('account_id', int(account)))
        data = generate_general_ledger({k: v for k, v in bounds if v})
    return {'report': report, 'label': STATEMENTS[report], 'data': data}
