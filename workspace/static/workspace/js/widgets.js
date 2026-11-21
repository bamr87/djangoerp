/*
 * Building blocks every workspace view composes: value rendering by field type, record titles, the lifecycle
 * stepper, tables, the foreign-key combobox and the action dialog. Field metadata comes from the registry, which
 * the server introspects from each serializer, so a field renders the same way on every page.
 */
import {api, debounce, fill, fmt, h, isNum, pill} from './core.js';

export function choiceLabel(meta, value) {
  const found = (meta && meta.choices || []).find(([v]) => v === String(value));
  return found ? found[1] : (value === null || value === undefined ? '' : String(value));
}

/** A record's title from the resource's template, e.g. '{code} {name}'. */
export function titleOf(resource, record) {
  if (!resource || !record) return '';
  const template = resource.title || '{id}';
  return template.replace(/\{(\w+)\}/g, (_, name) => {
    const meta = resource.fields[name];
    const value = record[name];
    if (value === null || value === undefined) return '';
    return meta && meta.type === 'choice' ? choiceLabel(meta, value) : String(value);
  }).replace(/\s+/g, ' ').trim();
}

export function subtitleOf(resource, record) {
  if (!resource.subtitle) return '';
  return titleOf({...resource, title: resource.subtitle}, record);
}

export function isNumeric(meta) {
  return meta && ['decimal', 'number'].includes(meta.type) || (meta && meta.type === 'computed' && meta.money);
}

export function link(href, label, attrs = {}) {
  return h('a', {href, ...attrs}, label);
}

/** Render one field of a record: references become links (when the role can open the target), choices labels,
 *  the status field a pill, numbers formatted by kind. `compact` is for table cells. */
export function renderValue(shell, resource, name, record, {meta, compact = false} = {}) {
  meta = meta || resource.fields[name] || {type: 'text'};
  const value = record[name];
  if (value === null || value === undefined || value === '') {
    if (meta.type === 'bool') return 'No';
    return h('span', {class: 'muted', text: '—'});
  }
  switch (meta.type) {
    case 'ref': {
      const display = record[`${name}_display`];
      if (meta.resource && shell.can(meta.resource)) {
        // No display field from the API: label it later from the target record (resolveLabels).
        const attrs = display ? {} : {'data-label-resource': meta.resource, 'data-label-id': String(value)};
        return link(shell.routeOf(meta.resource, value), display || `#${value}`, attrs);
      }
      return display || `#${value}`;
    }
    case 'choice':
      return resource.status === name ? pill(value, choiceLabel(meta, value)) : choiceLabel(meta, value);
    case 'bool':
      return value ? h('span', {class: 'ok-text', text: '✓ Yes'}) : 'No';
    case 'date':
      return fmt.date(value);
    case 'datetime':
      return fmt.datetime(value);
    case 'decimal':
      return meta.money ? fmt.money(value) : fmt.quantity(value);
    case 'number':
      return meta.money ? fmt.money(value) : fmt.number(value);
    case 'computed':
      return meta.money && isNum(Number(value)) ? fmt.money(value) : String(value);
    case 'json':
      return compact ? h('span', {class: 'muted', text: '{…}'}) : h('pre', {class: 'json', text: JSON.stringify(value, null, 2)});
    case 'lines':
      return `${(value || []).length} line(s)`;
    case 'email':
      return link(`mailto:${value}`, String(value));
    default: {
      const text = String(value);
      return compact && text.length > 90 ? text.slice(0, 88) + '…' : text;
    }
  }
}

/** Replace '#12' reference labels under `root` with the target record's title, one request per record. */
export function resolveLabels(shell, root) {
  const pending = {};
  for (const el of root.querySelectorAll('[data-label-resource]')) {
    const key = `${el.dataset.labelResource}:${el.dataset.labelId}`;
    (pending[key] = pending[key] || []).push(el);
  }
  for (const [key, elements] of Object.entries(pending)) {
    const [resourceKey, id] = key.split(':');
    const resource = shell.resource(resourceKey);
    api(`${resource.endpoint}${id}/`).then((row) => {
      for (const el of elements) el.textContent = titleOf(resource, row) || el.textContent;
    }).catch((err) => console.warn('Reference label unavailable', key, err.message));
  }
}

/** The document lifecycle: past steps done, the current one highlighted, an off-path status (cancelled,
 *  failed...) shown as its own terminal step. */
export function stepper(resource, status) {
  const lifecycle = resource.lifecycle || [];
  if (!lifecycle.length) return null;
  const meta = resource.fields[resource.status];
  const index = lifecycle.indexOf(status);
  const terminal = index < 0 && status;
  const items = lifecycle.map((step, i) => h('li', {
    class: terminal ? '' : (i < index ? 'done' : i === index ? 'current' : ''),
    'aria-current': i === index ? 'step' : null,
  }, choiceLabel(meta, step)));
  if (terminal) items.push(h('li', {class: 'terminal', 'aria-current': 'step'}, choiceLabel(meta, status)));
  return h('ol', {class: 'stepper', 'aria-label': 'Lifecycle'}, items);
}

/** A data table. columns: [{label, numeric, render(row), sort}], rows. */
export function table({columns, rows, onRow, footer, className = '', caption}) {
  return h('div', {class: 'scroll-x'}, h('table', {class: className},
    caption ? h('caption', {class: 'visually-hidden', text: caption}) : null,
    h('thead', {}, h('tr', {}, columns.map((c) => h('th', {class: c.numeric ? 'num' : '', scope: 'col'}, c.header || c.label)))),
    h('tbody', {}, rows.map((row) => h('tr', {onclick: onRow ? (event) => {
      if (event.target.closest('a, button, input, select, textarea')) return;
      onRow(row, event);
    } : null}, columns.map((c) => h('td', {class: c.numeric ? 'num' : (c.wrap ? 'wrap' : '')}, c.render(row)))))),
    footer || null));
}

/**
 * A searchable reference picker for a foreign key, backed by the target resource's own list endpoint (and its
 * search fields). Calls onChange(id, label). Keyboard: arrows move, Enter picks, Escape closes.
 */
export function combobox({shell, target, value = null, label = '', placeholder = 'Search…', onChange, id, required = false,
  ariaLabel, invalid = false}) {
  const resource = shell.resource(target);
  const wrap = h('div', {class: 'combobox'});
  const listId = `${id || 'cb'}-${Math.random().toString(36).slice(2, 8)}-options`;
  const input = h('input', {type: 'text', id, value: label, placeholder, autocomplete: 'off', role: 'combobox',
    'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': listId, required: required || null,
    'aria-label': ariaLabel || null, 'aria-invalid': invalid ? 'true' : null});
  const options = h('div', {class: 'options', role: 'listbox', id: listId, hidden: true});
  let current = value;
  let active = -1;
  let rows = [];
  const pick = (row) => {
    current = row ? row.id : null;
    input.value = row ? titleOf(resource, row) : '';
    close();
    if (onChange) onChange(current, input.value, row);
  };
  const close = () => { options.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
  const highlight = (i) => {
    const items = [...options.querySelectorAll('[role=option]')];
    active = Math.max(0, Math.min(items.length - 1, i));
    items.forEach((el, n) => el.setAttribute('aria-selected', String(n === active)));
    if (items[active]) items[active].scrollIntoView({block: 'nearest'});
  };
  const load = debounce(async () => {
    if (!resource) return;
    try {
      const term = input.value.trim();
      const data = await api(resource.endpoint, {query: resource.search && term ? {search: term} : {}});
      rows = data.results || data;
      fill(options, rows.length ? rows.map((row, i) => h('div', {role: 'option', 'aria-selected': 'false', id: `${listId}-${i}`,
        onmousedown: (event) => { event.preventDefault(); pick(row); }}, titleOf(resource, row) || `#${row.id}`))
        : h('div', {class: 'muted', style: 'padding:6px', text: resource.search ? 'No match' : 'Nothing to pick'}));
      options.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = -1;
    } catch (err) {
      fill(options, h('div', {class: 'error', style: 'padding:6px', text: err.message}));
      options.hidden = false;
    }
  }, 200);
  input.addEventListener('input', () => {
    if (!input.value.trim() && current !== null) { current = null; if (onChange) onChange(null, ''); }
    load();
  });
  input.addEventListener('focus', load);
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); if (options.hidden) load(); else highlight(active + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); highlight(active - 1); }
    else if (event.key === 'Enter' && !options.hidden && rows[active >= 0 ? active : 0]) {
      event.preventDefault(); pick(rows[active >= 0 ? active : 0]);
    } else if (event.key === 'Escape') close();
  });
  wrap.append(input, options);
  wrap.getValue = () => current;
  return wrap;
}

/** A modal dialog. fields: [{name, label, type, required, value}]. onSubmit(values) may throw to keep it open. */
export function dialog({title, text, fields = [], submitLabel = 'Confirm', danger = false, onSubmit}) {
  return new Promise((resolve) => {
    const error = h('p', {class: 'error', role: 'alert'});
    const inputs = fields.map((f) => {
      const input = f.type === 'text'
        ? h('input', {type: 'text', name: f.name, value: f.value || ''})
        : h('input', {type: f.type || 'date', name: f.name, value: f.value || '', required: f.required || null});
      return h('label', {class: 'field'}, h('span', {}, f.label, f.required ? h('span', {class: 'req', text: ' *'}) : null), input,
        f.help ? h('span', {class: 'help', text: f.help}) : null);
    });
    const submit = h('button', {class: 'btn ' + (danger ? 'danger' : 'primary'), type: 'submit', text: submitLabel});
    const el = h('dialog', {class: 'erp-dialog', 'aria-labelledby': 'dlg-title'});
    const form = h('form', {method: 'dialog', onsubmit: async (event) => {
      event.preventDefault();
      const values = {};
      for (const f of fields) {
        const v = form.elements[f.name].value;
        if (v !== '') values[f.name] = v;
      }
      submit.disabled = true;
      error.textContent = '';
      try {
        const result = onSubmit ? await onSubmit(values) : values;
        el.close();
        el.remove();
        resolve(result === undefined ? values : result);
      } catch (err) {
        error.textContent = err.message;
      } finally {
        submit.disabled = false;
      }
    }},
      h('h2', {id: 'dlg-title', text: title}),
      text ? h('p', {class: 'secondary', text}) : null,
      inputs, error,
      h('div', {class: 'form-actions'}, submit,
        h('button', {class: 'btn ghost', type: 'button', onclick: () => { el.close(); el.remove(); resolve(null); }, text: 'Cancel'})));
    el.append(form);
    el.addEventListener('cancel', () => { el.remove(); resolve(null); });
    document.body.append(el);
    el.showModal();
  });
}

export function notice(text, tone = '') {
  return h('p', {class: 'notice ' + tone, role: tone === 'critical' ? 'alert' : null, text});
}
