/*
 * The list view every resource shares: search, filters, sortable columns, paging and the create button, all
 * reflected in the URL so a filtered list is a link someone else can open.
 */
import {api, debounce, fmt, h} from './core.js';
import {choiceLabel, combobox, isNumeric, renderValue, table, titleOf} from './widgets.js';

/** The navigation view (Customers, Suppliers...) this URL matches, if any. */
function matchingView(shell, resource, params) {
  for (const group of shell.registry.groups) {
    for (const item of group.items) {
      if (item.type !== 'view' || item.resource !== resource.key) continue;
      const query = new URLSearchParams(item.route.split('?')[1] || '');
      if ([...query].every(([k, v]) => params.get(k) === v)) return item;
    }
  }
  return null;
}

export async function listView(ctx, resource, params) {
  const {shell} = ctx;
  const query = Object.fromEntries(params);
  if (!query.ordering && resource.default_ordering) query.ordering = resource.default_ordering;
  const data = await api(resource.endpoint, {query});
  const rows = data.results || data;
  const total = data.count ?? rows.length;
  const page = Number(params.get('page') || 1);
  const view = matchingView(shell, resource, params);
  const label = view ? view.label : resource.label;

  const update = (changes) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === undefined || v === '') next.delete(k); else next.set(k, v);
    }
    if (!('page' in changes)) next.delete('page');
    const qs = next.toString();
    ctx.navigate(resource.route + (qs ? `?${qs}` : ''), {replace: true, keepScroll: true});
  };

  // --- toolbar: search + filters ---
  const tools = [];
  const filterTools = [];
  if (resource.search) {
    const input = h('input', {type: 'search', value: params.get('search') || '', placeholder: `Search ${resource.label.toLowerCase()}…`,
      'aria-label': `Search ${resource.label}`});
    input.addEventListener('input', debounce(() => update({search: input.value.trim()}), 400));
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') update({search: input.value.trim()}); });
    tools.push(h('label', {class: 'field grow'}, h('span', {text: 'Search'}), input));
  }
  for (const filter of resource.filters) {
    const current = params.get(filter.name) || '';
    if (filter.type === 'choice' || filter.type === 'bool') {
      const options = filter.type === 'bool' ? [['true', 'Yes'], ['false', 'No']] : filter.choices;
      filterTools.push(h('label', {class: 'field'}, h('span', {text: filter.label}),
        h('select', {onchange: (event) => update({[filter.name]: event.target.value})},
          h('option', {value: '', text: 'All'}), options.map(([v, l]) => h('option', {value: v, selected: current === v, text: l})))));
    } else if (filter.type === 'ref' && filter.resource && shell.can(filter.resource)) {
      const picker = combobox({shell, target: filter.resource, value: current || null,
        label: current ? `#${current}` : '', placeholder: 'Any', ariaLabel: filter.label,
        onChange: (id) => update({[filter.name]: id})});
      if (current) {
        api(`${shell.resource(filter.resource).endpoint}${current}/`).then((row) => {
          picker.querySelector('input').value = titleOf(shell.resource(filter.resource), row);
        }).catch((err) => console.warn('Could not label the filter value', err));
      }
      filterTools.push(h('div', {class: 'field'}, h('span', {text: filter.label}), picker));
    } else if (filter.type === 'date') {
      filterTools.push(h('label', {class: 'field'}, h('span', {text: filter.label}),
        h('input', {type: 'date', value: current, onchange: (event) => update({[filter.name]: event.target.value})})));
    }
  }
  const active = [...params.keys()].some((k) => !['page', 'ordering'].includes(k));
  const activeFilters = [...params.keys()].filter((k) => resource.filters.some((f) => f.name === k)).length;
  if (filterTools.length) {
    // Always open on a wide screen; on a phone the filters fold away behind one control.
    tools.push(h('details', {class: 'filters', open: window.matchMedia('(min-width: 901px)').matches || activeFilters > 0},
      h('summary', {text: `Filters${activeFilters ? ` (${activeFilters} active)` : ''}`}), h('div', {class: 'filter-row'}, filterTools)));
  }
  if (active) tools.push(h('button', {class: 'btn ghost', type: 'button', onclick: () => ctx.navigate(resource.route, {replace: true}), text: 'Clear'}));

  // --- table ---
  const ordering = query.ordering || '';
  const columns = (resource.columns || Object.keys(resource.fields).slice(0, 6)).map((name, i) => {
    const meta = resource.fields[name] || {};
    const sortable = resource.ordering.includes(name);
    const dir = ordering === name ? 'ascending' : ordering === `-${name}` ? 'descending' : 'none';
    return {
      label: meta.label || name,
      numeric: isNumeric(meta),
      header: sortable ? h('button', {type: 'button', 'aria-sort': dir, title: `Sort by ${meta.label || name}`,
        onclick: () => update({ordering: dir === 'ascending' ? `-${name}` : name})}, meta.label || name) : null,
      render: (row) => {
        const value = renderValue(shell, resource, name, row, {compact: true});
        if (i === 0) {
          // The first column opens the record; a nested link or pill keeps only its text.
          return h('a', {href: `${resource.route}${row.id}/`, text: value instanceof Node ? value.textContent : String(value)});
        }
        return value;
      },
    };
  });
  const body = rows.length
    ? table({columns, rows, className: 'data-table', caption: label, onRow: (row) => ctx.navigate(`${resource.route}${row.id}/`)})
    : h('div', {class: 'empty-state'}, active ? `No ${resource.label.toLowerCase()} match these filters.` : `No ${resource.label.toLowerCase()} yet.`);

  const pageSize = rows.length && data.next ? rows.length : 20;
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const pager = h('div', {class: 'pager'},
    h('span', {text: total ? `${fmt.number(from)}–${fmt.number(from + rows.length - 1)} of ${fmt.number(total)}` : ''}),
    h('span', {class: 'inline'},
      h('button', {class: 'btn small', type: 'button', disabled: !data.previous, onclick: () => update({page: page - 1 > 1 ? page - 1 : null})}, '‹ Previous'),
      h('button', {class: 'btn small', type: 'button', disabled: !data.next, onclick: () => update({page: page + 1})}, 'Next ›')));

  const canCreate = resource.create && shell.can(resource.key, 'write');
  const head = h('div', {class: 'page-head'},
    h('div', {},
      h('div', {class: 'title'}, h('h1', {text: label}), h('span', {class: 'muted', text: total ? `${fmt.number(total)} record(s)` : ''})),
      resource.description ? h('p', {class: 'desc', text: resource.description}) : null),
    h('div', {class: 'page-actions'},
      canCreate ? h('a', {class: 'btn primary', href: `${resource.route}new/`, text: `New ${resource.singular.toLowerCase()}`}) : null,
      h('a', {class: 'btn ghost', href: resource.endpoint + (params.toString() ? `?${params}` : ''), target: '_blank', rel: 'noopener',
        'data-external': '', title: 'The same list as JSON from the API', text: 'API'})));

  const filterSummary = [...params].filter(([k]) => !['page', 'ordering', 'search'].includes(k)).map(([k, v]) => {
    const filter = resource.filters.find((f) => f.name === k);
    return filter && filter.type === 'choice' ? `${filter.label}: ${choiceLabel(filter, v)}` : null;
  }).filter(Boolean);

  return {
    title: label,
    crumbs: [{label: groupLabel(shell, resource.group), href: null}, {label}],
    node: h('div', {}, head, tools.length ? h('div', {class: 'toolbar', role: 'search'}, tools) : null,
      filterSummary.length ? h('p', {class: 'muted', text: filterSummary.join(' · ')}) : null,
      h('section', {class: 'card', 'aria-label': `${label} list`}, body, pager)),
  };
}

export function groupLabel(shell, key) {
  const group = shell.registry.groups.find((g) => g.key === key);
  return group ? group.label : key;
}
