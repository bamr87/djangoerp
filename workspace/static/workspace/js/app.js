/*
 * The workspace single-page app: a history-API router over /app/ that renders the home page, every resource's
 * list, detail and form, and the financial statements — all inside the common shell. Routes:
 *
 *   /app/                              home: work queues, module counts, recent activity
 *   /app/finance/statements/           trial balance, balance sheet, income statement, general ledger
 *   /app/<group>/<resource>/           list            ?search= &<filter>= &ordering= &page=
 *   /app/<group>/<resource>/new/       create
 *   /app/<group>/<resource>/<id>/      detail          ?tab=flow|accounting|stock|history|data
 *   /app/<group>/<resource>/<id>/edit/ edit
 */
import {$, api, fill, fmt, h, pill} from './core.js';
import {detailView} from './detail.js';
import {formView} from './form.js';
import {groupLabel, listView} from './list.js';
import {combobox, link, notice, table, titleOf} from './widgets.js';

let shell = null;
let started = false;
let renderSeq = 0;

export function start(s) {
  shell = s;
  if (!started) {
    started = true;
    shell.setNavigator(navigate);
    document.addEventListener('click', (event) => {
      const a = event.target.closest('a[href]');
      if (!a || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (a.target || a.hasAttribute('download') || a.hasAttribute('data-external')) return;
      const href = a.getAttribute('href');
      if (!href.startsWith('/app/')) return;
      event.preventDefault();
      navigate(href);
    });
    window.addEventListener('popstate', () => render({scroll: false}));
  }
  render({scroll: false});
}

function navigate(href, {replace = false, keepScroll = false} = {}) {
  if (replace) window.history.replaceState(null, '', href); else window.history.pushState(null, '', href);
  render({scroll: !keepScroll});
}

const ctx = {
  get shell() { return shell; },
  navigate,
  refresh: () => render({scroll: false}),
};

async function render({scroll = true} = {}) {
  const mine = ++renderSeq;
  const url = new URL(window.location.href);
  const parts = url.pathname.replace(/^\/app\/?/, '').split('/').filter(Boolean);
  shell.setActiveNav(url.pathname + url.search);
  const view = $('view');
  view.classList.add('loading');
  let result;
  try {
    if (!parts.length) {
      result = await homeView();
    } else if (parts.join('/') === 'finance/statements') {
      result = await statementsView(url.searchParams);
    } else {
      const key = `${parts[0]}.${parts[1]}`;
      const resource = shell.resource(key);
      if (!resource || parts.length > 4) result = notFound();
      else if (!shell.can(key)) result = forbidden(resource);
      else if (parts.length === 2) result = await listView(ctx, resource, url.searchParams);
      else if (parts[2] === 'new' && parts.length === 3) result = await formView(ctx, resource, null);
      else if (/^\d+$/.test(parts[2]) && parts[3] === 'edit') result = await formView(ctx, resource, parts[2]);
      else if (/^\d+$/.test(parts[2]) && parts.length === 3) result = await detailView(ctx, resource, parts[2], url.searchParams);
      else result = notFound();
    }
  } catch (err) {
    result = errorView(err);
  }
  if (mine !== renderSeq) return;
  view.classList.remove('loading');
  fill(view, result.node);
  shell.setCrumbs([{label: 'Home', href: '/app/'}, ...(result.crumbs || [])].filter((c, i) => i === 0 || c.label !== 'Home'));
  document.title = `${result.title || 'Workspace'} · DjangoERP`;
  if (scroll) {
    window.scrollTo(0, 0);
    $('main-content').focus({preventScroll: true});
  }
}

function notFound() {
  return {title: 'Not found', node: h('div', {}, h('h1', {text: 'Nothing here'}),
    notice('No workspace page matches this address. Pick a module from the navigation, or search with /.'))};
}

function forbidden(resource) {
  const roles = (resource.access.read || []).filter((r) => r !== 'none').join(', ') || 'no role';
  return {title: resource.label, crumbs: [{label: resource.label}], node: h('div', {}, h('h1', {text: resource.label}),
    notice(`Your role (${shell.auth.role()}) cannot read ${resource.label.toLowerCase()}. The API allows: ${roles}.`, 'critical'))};
}

function errorView(err) {
  if (err.status === 404) return notFound();
  const text = err.status === 403 ? `Your role (${shell.auth.role()}) is not allowed to see this: ${err.message}` : err.message;
  return {title: 'Error', node: h('div', {}, h('h1', {text: err.status === 403 ? 'Not allowed' : 'Something went wrong'}), notice(text, 'critical'))};
}

// --- home ---------------------------------------------------------------------------------------------------------
const QUICK = ['sales.orders', 'purchasing.orders', 'manufacturing.work-orders', 'finance.journal-entries', 'finance.invoices',
  'master.partners', 'master.products'];

async function homeView() {
  const data = await api('/api/workspace/overview/');
  const user = shell.auth.user || {};
  const name = user.first_name || user.username;
  const queues = data.queues.length ? h('div', {class: 'tiles'}, data.queues.map((q) => h('a', {class: 'tile' + (q.count ? '' : ' zero'), href: q.route},
    h('div', {class: 'label', text: q.label}), h('div', {class: 'value', text: fmt.number(q.count)}),
    h('div', {class: 'delta', text: shell.resource(q.resource).label}))))
    : h('p', {class: 'muted', text: 'No work queues for your role.'});
  const modules = h('div', {class: 'module-grid'}, shell.registry.groups.map((group) => {
    const items = group.items.filter((i) => i.type === 'resource' && data.counts[i.key] !== undefined);
    if (!items.length) return null;
    return h('section', {class: 'card'}, h('div', {class: 'card-head'}, h('h2', {text: group.label})),
      h('ul', {}, items.map((i) => h('li', {}, link(i.route, i.label), h('span', {class: 'num muted', text: fmt.number(data.counts[i.key])})))));
  }));
  const quick = QUICK.filter((k) => shell.resource(k) && shell.resource(k).create && shell.can(k, 'write'))
    .map((k) => h('a', {class: 'btn', href: `${shell.resource(k).route}new/`, text: `New ${shell.resource(k).singular.toLowerCase()}`}));
  const activity = data.activity ? h('section', {class: 'card'}, h('div', {class: 'card-head'}, h('h2', {text: 'Recent activity'}), h('span', {class: 'sub', text: 'from the audit trail'})),
    data.activity.length ? h('ul', {class: 'feed'}, data.activity.map((a) => h('li', {},
      h('span', {class: 'meta', text: `${fmt.datetime(a.timestamp)} · ${a.user || 'system'}`}),
      h('span', {class: 'what'}, a.event, ' · ', a.resource && a.id && shell.can(a.resource) ? link(shell.routeOf(a.resource, a.id), a.label) : a.label))))
      : h('p', {class: 'muted', text: 'Nothing yet.'})) : null;
  return {
    title: 'Home',
    node: h('div', {},
      h('div', {class: 'page-head'}, h('div', {},
        h('h1', {text: `Welcome${name ? ', ' + name : ''}`}),
        h('p', {class: 'desc', text: data.company ? `${data.company.name} · books kept in ${data.company.currency_code}` : 'No company on file yet — an accountant can record it under Administration.'})),
        h('div', {class: 'page-actions'}, quick)),
      h('h2', {class: 'section-title', text: 'Needs attention'}), queues,
      h('h2', {class: 'section-title', text: 'Modules'}), modules,
      activity ? [h('h2', {class: 'section-title', text: 'Activity'}), activity] : null),
  };
}

// --- statements ----------------------------------------------------------------------------------------------------
const REPORTS = [['trial_balance', 'Trial balance'], ['balance_sheet', 'Balance sheet'], ['income_statement', 'Income statement'], ['general_ledger', 'General ledger']];

function accountCell(row) {
  const code = row.code || row.account_code;
  return row.id && shell.can('finance.accounts') ? link(shell.routeOf('finance.accounts', row.id), code)
    : link(`${shell.resource('finance.accounts').route}?search=${encodeURIComponent(code)}`, code);
}

function entryCell(number) {
  return shell.can('finance.journal-entries') ? link(`${shell.resource('finance.journal-entries').route}?search=${encodeURIComponent(number)}`, number) : number;
}

function balancedPill(ok) {
  return ok ? pill('posted', '✓ Balanced') : pill('failed', '✕ Out of balance');
}

function section(title, rows, totalLabel, total) {
  return [h('tr', {}, h('th', {colspan: '3', text: title})),
    rows.filter((a) => Math.abs(Number(a.balance)) >= 0.005).map((a) => h('tr', {}, h('td', {}, accountCell(a)), h('td', {text: a.name}), h('td', {class: 'num', text: fmt.money(a.balance)}))),
    h('tr', {class: 'total'}, h('td', {colspan: '2', text: totalLabel}), h('td', {class: 'num', text: fmt.money(total)}))];
}

function renderStatement(report, data) {
  if (report === 'trial_balance') {
    const rows = data.accounts.filter((a) => Number(a.debit) || Number(a.credit));
    return h('div', {}, h('p', {}, balancedPill(data.balanced), ' ', h('span', {class: 'muted', text: `as of ${data.as_of_date} · raw debit minus credit per account`})),
      table({columns: [
        {label: 'Account', render: accountCell}, {label: 'Name', render: (a) => a.account_name}, {label: 'Type', render: (a) => a.account_type},
        {label: 'Debit', numeric: true, render: (a) => (Number(a.debit) ? fmt.money(a.debit) : '')},
        {label: 'Credit', numeric: true, render: (a) => (Number(a.credit) ? fmt.money(a.credit) : '')},
      ], rows, footer: h('tfoot', {}, h('tr', {class: 'total'}, h('td', {colspan: '3', text: 'Totals'}),
        h('td', {class: 'num', text: fmt.money(data.total_debits)}), h('td', {class: 'num', text: fmt.money(data.total_credits)})))}));
  }
  if (report === 'balance_sheet') {
    return h('div', {}, h('p', {}, balancedPill(data.balanced), ' ', h('span', {class: 'muted', text: `as of ${data.as_of_date} · current-period earnings fold into equity until a closing entry`})),
      h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {},
        section('Assets', data.assets, 'Total assets', data.total_assets),
        section('Liabilities', data.liabilities, 'Total liabilities', data.total_liabilities),
        section('Equity', data.equity, 'Equity accounts', data.equity_accounts_total),
        h('tr', {}, h('td', {colspan: '2', text: 'Current-period earnings (open P&L)'}), h('td', {class: 'num', text: fmt.money(data.current_period_earnings)})),
        h('tr', {class: 'total'}, h('td', {colspan: '2', text: 'Total equity'}), h('td', {class: 'num', text: fmt.money(data.total_equity)})),
        h('tr', {class: 'total'}, h('td', {colspan: '2', text: 'Liabilities + equity'}), h('td', {class: 'num', text: fmt.money(Number(data.total_liabilities) + Number(data.total_equity))}))))));
  }
  if (report === 'income_statement') {
    return h('div', {}, h('p', {class: 'muted', text: `${data.from_date || 'beginning'} → ${data.to_date}`}),
      h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {},
        section('Revenue', data.revenue, 'Total revenue', data.total_revenue),
        section('Expenses', data.expenses, 'Total expenses', data.total_expenses),
        h('tr', {class: 'total'}, h('td', {colspan: '2', text: 'Net income'}), h('td', {class: 'num', text: fmt.money(data.net_income)}))))));
  }
  const ledger = Object.values(data.ledger || {})[0];
  if (!ledger) return notice('No ledger for this account.');
  return h('div', {}, h('p', {}, h('strong', {text: `${ledger.account_code} ${ledger.account_name}`}), ' ',
    h('span', {class: 'muted', text: `${ledger.account_type} · opening ${fmt.money(ledger.opening_balance)} · ending ${fmt.money(ledger.ending_balance)} (debit minus credit)`})),
    table({columns: [
      {label: 'Date', render: (t) => t.date}, {label: 'Entry', render: (t) => entryCell(t.entry_number)},
      {label: 'Description', wrap: true, render: (t) => t.description}, {label: 'Reference', render: (t) => t.reference || ''},
      {label: 'Debit', numeric: true, render: (t) => (Number(t.debit) ? fmt.money(t.debit) : '')},
      {label: 'Credit', numeric: true, render: (t) => (Number(t.credit) ? fmt.money(t.credit) : '')},
      {label: 'Balance', numeric: true, render: (t) => fmt.money(t.balance)},
    ], rows: ledger.transactions.slice().reverse()}));
}

async function statementsView(params) {
  const report = REPORTS.some(([k]) => k === params.get('report')) ? params.get('report') : 'trial_balance';
  const set = (changes) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    navigate(`/app/finance/statements/?${next}`, {replace: true, keepScroll: true});
  };
  const controls = [h('label', {class: 'field'}, h('span', {text: 'Statement'}),
    h('select', {onchange: (e) => set({report: e.target.value})}, REPORTS.map(([k, l]) => h('option', {value: k, selected: k === report, text: l}))))];
  if (report === 'trial_balance' || report === 'balance_sheet') {
    controls.push(h('label', {class: 'field'}, h('span', {text: 'As of'}), h('input', {type: 'date', value: params.get('as_of') || '', onchange: (e) => set({as_of: e.target.value})})));
  } else {
    controls.push(h('label', {class: 'field'}, h('span', {text: 'From'}), h('input', {type: 'date', value: params.get('from') || '', onchange: (e) => set({from: e.target.value})})),
      h('label', {class: 'field'}, h('span', {text: 'To'}), h('input', {type: 'date', value: params.get('to') || '', onchange: (e) => set({to: e.target.value})})));
  }
  if (report === 'general_ledger') {
    const picker = combobox({shell, target: 'finance.accounts', value: params.get('account'), label: params.get('account') ? `#${params.get('account')}` : '',
      placeholder: 'Pick an account', ariaLabel: 'Account', onChange: (id) => set({account: id})});
    if (params.get('account')) {
      api(`${shell.resource('finance.accounts').endpoint}${params.get('account')}/`)
        .then((row) => { picker.querySelector('input').value = titleOf(shell.resource('finance.accounts'), row); })
        .catch((err) => console.warn('Could not label the account', err));
    }
    controls.push(h('div', {class: 'field grow'}, h('span', {text: 'Account'}), picker));
  }
  let body;
  if (!shell.can('finance.journal-lines')) {
    body = notice(`Your role (${shell.auth.role()}) cannot read the ledger: statements need accountant, auditor or admin.`, 'critical');
  } else if (report === 'general_ledger' && !params.get('account')) {
    body = h('p', {class: 'muted', text: 'Pick an account to see its ledger.'});
  } else {
    try {
      const query = {report, as_of: params.get('as_of'), from: params.get('from'), to: params.get('to'), account: params.get('account')};
      const result = await api('/api/workspace/statements/', {query});
      body = renderStatement(report, result.data);
    } catch (err) {
      body = notice(err.status === 403 ? `Your role (${shell.auth.role()}) cannot read the ledger: statements need accountant, auditor or admin.` : err.message, 'critical');
    }
  }
  const label = REPORTS.find(([k]) => k === report)[1];
  return {
    title: label,
    crumbs: [{label: groupLabel(shell, 'finance')}, {label: 'Financial statements', href: '/app/finance/statements/'}, {label}],
    node: h('div', {}, h('div', {class: 'page-head'}, h('div', {}, h('h1', {text: label}),
      h('p', {class: 'desc', text: 'Compiled live from posted journal lines by reports.report_generators — the same code behind the reports API.'}))),
      h('div', {class: 'toolbar'}, controls), h('section', {class: 'card'}, body)),
  };
}
