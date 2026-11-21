/*
 * The detail view every business object shares: header with status and lifecycle, the transitions the role may
 * trigger, the fields, the document lines, and — from /api/workspace/flow/ — the related documents, the journal
 * entries and stock moves the object caused, and its audit trail.
 */
import {api, fmt, h, pill, toast} from './core.js';
import {choiceLabel, dialog, isNumeric, link, notice, renderValue, resolveLabels, stepper, subtitleOf, table, titleOf} from './widgets.js';
import {groupLabel} from './list.js';

const HIDDEN = new Set(['id']);
const RELATION_ORDER = [
  'Source: sales order', 'Source: purchase order', 'Source: work order', 'Source: invoice', 'Source: payment',
  'Customer', 'Supplier', 'Pegged demand', 'Planned by', 'MRP run', 'Planned in', 'Planned supply', 'Planned order', 'Converted to',
  'Shipment', 'Goods receipt', 'Sales order', 'Purchase order', 'Work order', 'Invoice', 'Payment',
];

function actionAllowed(shell, action, record, status) {
  if (!action.button || action.method !== 'POST') return false;
  if (!(action.roles || []).includes(shell.auth.role())) return false;
  if (action.when && action.when.length && !action.when.includes(status)) return false;
  for (const [field, expected] of Object.entries(action.requires || {})) {
    const actual = record[field] === undefined ? null : record[field];
    if (expected === null ? actual !== null && actual !== '' : actual !== expected) return false;
  }
  return true;
}

function editable(resource, status) {
  if (Array.isArray(resource.editable)) return resource.editable.includes(status);
  return Boolean(resource.editable);
}

/** Which resource an action's response is, when it is a document of its own (an invoice, a journal entry). */
function responseResource(response) {
  if (response.invoice_number) return 'finance.invoices';
  if (response.entry_number) return 'finance.journal-entries';
  return null;
}

/** The toast after an action. A response that is the acted-on document itself (posting an invoice returns the
 *  invoice) is reported as done, not as something new. */
function summary(action, response, same) {
  if (!response || typeof response !== 'object' || same) return `${action.label}: done.`;
  if (response.grn_number) return `Received as ${response.grn_number}.`;
  if (response.shipment_number) return `Shipped as ${response.shipment_number}.`;
  if (response.invoice_number) return `Invoice ${response.invoice_number} created.`;
  if (response.entry_number) return `Posted ${response.entry_number}.`;
  if (response.purchase_orders || response.work_orders) {
    return `Created ${(response.purchase_orders || []).length} purchase order(s) and ${(response.work_orders || []).length} work order(s).`;
  }
  return `${action.label}: done.`;
}

async function runAction(ctx, resource, record, action) {
  const url = `${resource.endpoint}${record.id}/${action.url_path}/`;
  const call = (body) => api(url, {method: 'POST', body});
  let response;
  if ((action.fields || []).length || action.confirm) {
    response = await dialog({
      title: `${action.label} ${titleOf(resource, record)}`, text: action.confirm || action.help,
      fields: action.fields || [], submitLabel: action.label, danger: action.style === 'danger',
      onSubmit: (values) => call(values),
    });
    if (response === null) return;
  } else {
    try {
      response = await call({});
    } catch (err) {
      toast(err.message, {tone: 'error'});
      return;
    }
  }
  const target = response && typeof response === 'object' ? responseResource(response) : null;
  const same = target === resource.key && response.id === record.id;
  const href = target && !same ? ctx.shell.routeOf(target, response.id) : null;
  toast(summary(action, response, same), {href, linkText: 'Open'});
  ctx.refresh();
}

function fieldList(shell, resource, record) {
  const lineField = resource.lines && resource.lines.field;
  const names = Object.keys(resource.fields).filter((name) => {
    const meta = resource.fields[name];
    if (HIDDEN.has(name) || name === lineField || meta.display_for) return false;
    if (meta.type === 'ref' && name === 'created_by' && record.created_by_display) return false;
    if (meta.type === 'lines') return false;
    return true;
  });
  return h('dl', {class: 'fields'}, names.map((name) => {
    const meta = resource.fields[name];
    const wide = meta.type === 'json' || meta.type === 'long';
    return h('div', {class: wide ? 'wide' : ''}, h('dt', {text: meta.label}), h('dd', {}, renderValue(shell, resource, name, record)));
  }));
}

function linesTable(shell, resource, record) {
  const spec = resource.lines;
  const child = resource.fields[spec.field].child || {};
  const rows = record[spec.field] || [];
  const childResource = {fields: child, status: null};
  const columns = spec.columns.map((name) => ({
    label: (child[name] || {}).label || name,
    numeric: isNumeric(child[name]),
    render: (row) => renderValue(shell, childResource, name, row, {meta: child[name], compact: true}),
  }));
  let footer = null;
  if ((spec.totals || []).length && rows.length) {
    footer = h('tfoot', {}, h('tr', {class: 'total'}, spec.columns.map((name, i) => {
      if (!spec.totals.includes(name)) return h('td', {text: i === 0 ? 'Total' : ''});
      const total = rows.reduce((sum, row) => sum + (Number(row[name]) || 0), 0);
      return h('td', {class: 'num', text: fmt.money(total)});
    })));
  }
  return h('section', {class: 'card', style: 'margin-top:14px'},
    h('div', {class: 'card-head'}, h('h2', {text: (resource.fields[spec.field].label || 'Lines')}), h('span', {class: 'sub', text: `${rows.length} line(s)`})),
    rows.length ? table({columns, rows, footer}) : h('p', {class: 'muted', text: 'No lines.'}));
}

async function availabilityPanel(resource, record) {
  try {
    const rows = await api(`${resource.endpoint}${record.id}/availability/`);
    return h('section', {class: 'card', style: 'margin-top:14px'},
      h('div', {class: 'card-head'}, h('h2', {text: 'Component availability'}), h('span', {class: 'sub', text: 'BOM requirement against stock on hand'})),
      table({columns: [
        {label: 'Component', render: (r) => r.component},
        {label: 'Required', numeric: true, render: (r) => fmt.quantity(r.required)},
        {label: 'On hand', numeric: true, render: (r) => fmt.quantity(r.on_hand)},
        {label: 'Status', render: (r) => (r.sufficient ? h('span', {class: 'ok-text', text: '✓ Enough'}) : h('span', {class: 'bad-text', text: '✕ Short'}))},
      ], rows}));
  } catch (err) {
    return notice(`Availability unavailable: ${err.message}`);
  }
}

// --- flow tabs ---------------------------------------------------------------------------------------------------
function relatedHref(shell, item) {
  if (item.search) return `${shell.resource(item.resource).route}?search=${encodeURIComponent(item.search)}`;
  return shell.routeOf(item.resource, item.id);
}

function flowView(shell, data, resource, record) {
  if (!data.related.length) return h('p', {class: 'muted', text: 'Nothing linked to this record yet.'});
  const groups = {};
  for (const item of data.related) (groups[item.relation] = groups[item.relation] || []).push(item);
  const order = Object.keys(groups).sort((a, b) => {
    const ia = RELATION_ORDER.indexOf(a), ib = RELATION_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  // The document chain: upstream sources, this record, then what it produced.
  // Upstream: what caused this record. Downstream: what it produced. Other relations are context, not chain.
  const upstream = order.filter((r) => /^(Source: |Pegged demand$|Planned by$|MRP run$|Reverses$|Parent planned order$)/.test(r));
  const downstream = order.filter((r) => /^(Shipment|Goods receipt|Invoice|Payment|Converted to|Reversed by|Planned in)$/.test(r));
  const node = (item) => h('a', {class: 'node', href: relatedHref(shell, item), text: item.label});
  const chain = (upstream.length || downstream.length) ? h('div', {class: 'chain', 'aria-label': 'Document chain'},
    upstream.flatMap((r) => groups[r].map((i) => [node(i), h('span', {class: 'arrow', 'aria-hidden': 'true', text: '→'})])),
    h('span', {class: 'node self', text: titleOf(resource, record)}),
    downstream.flatMap((r) => groups[r].map((i) => [h('span', {class: 'arrow', 'aria-hidden': 'true', text: '→'}), node(i)]))) : null;
  return h('div', {}, chain, h('div', {class: 'flow-groups'}, order.map((relation) => h('section', {class: 'flow-group'},
    h('h3', {text: `${relation} (${groups[relation].length})`}),
    h('ul', {}, groups[relation].map((item) => h('li', {},
      h('a', {href: relatedHref(shell, item), text: item.label}),
      h('span', {class: 'meta'},
        item.status ? pill(item.status) : null,
        item.date ? h('span', {text: (item.date_label ? `${item.date_label} ` : '') + fmt.date(item.date)}) : null,
        item.amount !== undefined ? h('span', {class: 'num', text: fmt.money(item.amount)}) : null,
        item.quantity !== undefined ? h('span', {text: `qty ${fmt.quantity(item.quantity)}`}) : null,
        item.search ? h('span', {text: 'stock moves'}) : null))))))));
}

function accountingView(shell, entries) {
  if (entries === null) return notice('Your role cannot read ledger lines, so the accounting impact is hidden.');
  if (!entries.length) return h('p', {class: 'muted', text: 'No journal entries yet: this record has not posted to the ledger.'});
  return h('div', {}, entries.map((e) => h('section', {class: 'card je-card'},
    h('div', {class: 'card-head'},
      h('h2', {}, shell.can('finance.journal-entries') ? link(shell.routeOf('finance.journal-entries', e.id), e.entry_number) : e.entry_number,
        ' ', h('span', {class: 'muted', style: 'font-weight:400', text: fmt.date(e.date)})),
      h('span', {class: 'inline'}, pill(e.status), h('span', {class: 'sub', text: e.source_reference || 'manual entry'}))),
    h('p', {class: 'secondary', text: e.description}),
    table({columns: [
      {label: 'Account', render: (l) => (shell.can('finance.accounts') ? link(shell.routeOf('finance.accounts', l.account_id), l.account) : l.account)},
      {label: 'Name', render: (l) => l.account_name},
      {label: 'Debit', numeric: true, render: (l) => (l.debit ? fmt.money(l.debit) : '')},
      {label: 'Credit', numeric: true, render: (l) => (l.credit ? fmt.money(l.credit) : '')},
      {label: 'Reference', render: (l) => l.reference || ''},
    ], rows: e.lines, footer: h('tfoot', {}, h('tr', {class: 'total'},
      h('td', {colspan: '2'}, e.total_debit === e.total_credit ? h('span', {class: 'ok-text balanced', text: '✓ Balanced'}) : h('span', {class: 'bad-text', text: '✕ Unbalanced'})),
      h('td', {class: 'num', text: fmt.money(e.total_debit)}), h('td', {class: 'num', text: fmt.money(e.total_credit)}), h('td')))}))));
}

function stockView(shell, moves) {
  if (moves === null) return notice('Your role cannot read stock moves.');
  if (!moves.length) return h('p', {class: 'muted', text: 'No stock moves.'});
  return h('section', {class: 'card'}, table({columns: [
    {label: 'Move', render: (m) => link(shell.routeOf('inventory.stock-moves', m.id), m.reference)},
    {label: 'Date', render: (m) => fmt.date(m.move_date)},
    {label: 'Product', render: (m) => (shell.can('master.products') ? link(shell.routeOf('master.products', m.product_id), m.product) : m.product)},
    {label: 'Warehouse', render: (m) => m.warehouse},
    {label: 'Type', render: (m) => m.move_type_label},
    {label: 'Quantity', numeric: true, render: (m) => fmt.quantity(m.signed_quantity)},
    {label: 'Unit cost', numeric: true, render: (m) => fmt.money(m.unit_cost)},
    {label: 'Value', numeric: true, render: (m) => fmt.money(m.total_value)},
    {label: 'Status', render: (m) => pill(m.status)},
    {label: 'Source', render: (m) => m.source_reference || ''},
  ], rows: moves}));
}

function historyView(events) {
  if (events === null) return notice('The audit trail is visible to the admin role only, as in the API.');
  if (!events.length) return h('p', {class: 'muted', text: 'No recorded transitions.'});
  return h('ul', {class: 'feed'}, events.map((e) => h('li', {},
    h('span', {class: 'meta', text: `${fmt.datetime(e.timestamp)} · ${e.user || 'system'}`}),
    h('span', {class: 'what', text: e.event || e.action}),
    h('details', {class: 'why'}, h('summary', {text: 'Details'}), h('pre', {class: 'json', text: JSON.stringify(e.details, null, 2)})))));
}

// --- the view ----------------------------------------------------------------------------------------------------
export async function detailView(ctx, resource, id, params) {
  const {shell} = ctx;
  const record = await api(`${resource.endpoint}${id}/`);
  const status = resource.status ? record[resource.status] : null;
  const title = titleOf(resource, record) || `${resource.singular} #${id}`;
  const subtitle = subtitleOf(resource, record);

  const buttons = resource.actions.filter((a) => actionAllowed(shell, a, record, status)).map((action) =>
    h('button', {class: 'btn ' + (action.style || ''), type: 'button', onclick: () => runAction(ctx, resource, record, action), text: action.label}));
  if (editable(resource, status) && (resource.access.update || []).includes(shell.auth.role())) {
    buttons.push(h('a', {class: 'btn', href: `${resource.route}${id}/edit/`, text: 'Edit'}));
  }
  const deletable = (resource.access.delete || []).includes(shell.auth.role()) && (!resource.status || status === 'draft')
    && resource.key !== 'simulation.scenarios';
  if (deletable) {
    buttons.push(h('button', {class: 'btn ghost danger', type: 'button', onclick: async () => {
      const done = await dialog({title: `Delete ${title}?`, text: 'Only drafts and unused master data can be deleted; anything with ledger history is refused by the server.',
        submitLabel: 'Delete', danger: true, onSubmit: () => api(`${resource.endpoint}${id}/`, {method: 'DELETE'})});
      if (done !== null) { toast(`${title} deleted.`); ctx.navigate(resource.route); }
    }, text: 'Delete'}));
  }
  if (resource.key === 'simulation.scenarios') buttons.push(h('a', {class: 'btn', href: '/console/', 'data-external': '', text: 'Open the console'}));

  const head = h('div', {class: 'page-head'},
    h('div', {},
      h('div', {class: 'title'}, h('h1', {text: title}), status ? pill(status, choiceLabel(resource.fields[resource.status], status)) : null),
      subtitle && subtitle !== title ? h('p', {class: 'subtitle', text: subtitle}) : null,
      h('p', {class: 'desc', text: resource.singular})),
    h('div', {class: 'page-actions'}, buttons,
      h('a', {class: 'btn ghost', href: `${resource.endpoint}${id}/`, target: '_blank', rel: 'noopener', 'data-external': '', text: 'API'})));

  // Tabs: overview always; flow-backed tabs when the registry says the object has a flow.
  const tabs = [{key: 'overview', label: 'Overview'}];
  if (resource.flow) {
    tabs.push({key: 'flow', label: 'Document flow'}, {key: 'accounting', label: 'Accounting'}, {key: 'stock', label: 'Stock'}, {key: 'history', label: 'History'});
  }
  tabs.push({key: 'data', label: 'Data'});
  let current = tabs.some((t) => t.key === params.get('tab')) ? params.get('tab') : 'overview';
  let flowData = null;
  const panel = h('div', {role: 'tabpanel', id: 'detail-panel', tabindex: '0'});
  const bar = h('div', {class: 'tabs-bar', role: 'tablist', 'aria-label': `${resource.singular} sections`});

  const loadFlow = async () => {
    if (!flowData) flowData = await api(`/api/workspace/flow/${resource.key}/${id}/`);
    return flowData;
  };
  const counts = () => {
    if (!flowData) return;
    const n = {flow: flowData.related.length, accounting: flowData.journal_entries && flowData.journal_entries.length,
      stock: flowData.stock_moves && flowData.stock_moves.length, history: flowData.events && flowData.events.length};
    for (const button of bar.querySelectorAll('button')) {
      const c = n[button.dataset.tab];
      const badge = button.querySelector('.count');
      if (badge) badge.textContent = c === null || c === undefined ? '' : String(c);
    }
  };
  const show = async (key, focus = false) => {
    current = key;
    for (const button of bar.querySelectorAll('button')) {
      const selected = button.dataset.tab === key;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    }
    const url = new URL(window.location.href);
    if (key === 'overview') url.searchParams.delete('tab'); else url.searchParams.set('tab', key);
    window.history.replaceState(null, '', url.pathname + url.search);
    panel.setAttribute('aria-labelledby', `tab-${key}`);
    if (key === 'overview') {
      panel.replaceChildren(stepper(resource, status) || '', h('section', {class: 'card'}, fieldList(shell, resource, record)));
      resolveLabels(shell, panel);
      if (resource.lines) panel.append(linesTable(shell, resource, record));
      if ((resource.panels || []).includes('availability') && ['draft', 'confirmed', 'in_progress'].includes(status)) {
        panel.append(await availabilityPanel(resource, record));
      }
    } else if (key === 'data') {
      panel.replaceChildren(h('section', {class: 'card'}, h('pre', {class: 'json', text: JSON.stringify(record, null, 2)})));
    } else {
      panel.replaceChildren(h('p', {class: 'muted', text: 'Loading…'}));
      try {
        const data = await loadFlow();
        counts();
        const views = {
          flow: () => flowView(shell, data, resource, record),
          accounting: () => accountingView(shell, data.journal_entries),
          stock: () => stockView(shell, data.stock_moves),
          history: () => historyView(data.events),
        };
        panel.replaceChildren(views[key]());
      } catch (err) {
        panel.replaceChildren(notice(err.message, 'critical'));
      }
    }
  };
  tabs.forEach((tab) => bar.append(h('button', {type: 'button', role: 'tab', id: `tab-${tab.key}`, 'data-tab': tab.key,
    'aria-controls': 'detail-panel', 'aria-selected': 'false', tabindex: '-1', onclick: () => show(tab.key)},
    tab.label, ['flow', 'accounting', 'stock', 'history'].includes(tab.key) ? h('span', {class: 'count'}) : null)));
  bar.addEventListener('keydown', (event) => {
    const keys = tabs.map((t) => t.key);
    const i = keys.indexOf(current);
    if (event.key === 'ArrowRight') { event.preventDefault(); show(keys[(i + 1) % keys.length], true); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); show(keys[(i - 1 + keys.length) % keys.length], true); }
  });
  await show(current);
  if (resource.flow && current === 'overview') loadFlow().then(counts).catch((err) => console.warn('Flow counts unavailable', err));

  return {
    title,
    crumbs: [{label: groupLabel(shell, resource.group)}, {label: resource.label, href: resource.route}, {label: title}],
    node: h('div', {}, head, bar, panel),
  };
}
