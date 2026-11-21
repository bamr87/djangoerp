/*
 * Create and edit for any writable resource: typed inputs from the registry's field metadata, foreign-key
 * pickers, a nested lines editor for documents, and DRF validation errors mapped back onto the fields and line
 * cells that caused them. The server stays the judge: a confirmed document refuses edits, a cycle in a BOM or
 * an unbalanced journal entry is rejected, and the form shows exactly what it said.
 */
import {api, errorText, fmt, h, toast, today} from './core.js';
import {choiceLabel, combobox, notice, titleOf} from './widgets.js';
import {groupLabel} from './list.js';

const DATE_DEFAULTS = new Set(['date', 'order_date', 'payment_date', 'as_of_date', 'move_date']);
const SKIP = new Set(['id']);

function writableFields(resource) {
  const lineField = resource.lines && resource.lines.field;
  const names = resource.form_fields || Object.keys(resource.fields);
  return names.filter((name) => {
    const meta = resource.fields[name];
    return meta && !meta.read_only && !SKIP.has(name) && name !== lineField && meta.type !== 'lines' && !meta.display_for;
  });
}

function inputFor(shell, name, meta, value, record, onChange) {
  const id = `f-${name}`;
  const common = {id, name, required: meta.required || null};
  if (meta.type === 'ref' && meta.resource) {
    const label = record[`${name}_display`] || (value ? `#${value}` : '');
    const picker = combobox({shell, target: meta.resource, value: value ?? null, label, id, required: meta.required,
      onChange: (v) => onChange(v)});
    if (value && !record[`${name}_display`] && shell.can(meta.resource)) {
      api(`${shell.resource(meta.resource).endpoint}${value}/`).then((row) => {
        picker.querySelector('input').value = titleOf(shell.resource(meta.resource), row);
      }).catch((err) => console.warn('Could not label the reference', err));
    }
    return picker;
  }
  if (meta.type === 'choice') {
    return h('select', {...common, onchange: (e) => onChange(e.target.value)},
      meta.required ? null : h('option', {value: '', text: '—'}),
      meta.choices.map(([v, l]) => h('option', {value: v, selected: String(value ?? '') === v, text: l})));
  }
  if (meta.type === 'bool') {
    return h('input', {...common, type: 'checkbox', checked: Boolean(value), required: null, onchange: (e) => onChange(e.target.checked)});
  }
  if (meta.type === 'long' || meta.type === 'json') {
    const text = meta.type === 'json' ? (value ? JSON.stringify(value, null, 2) : '') : (value ?? '');
    return h('textarea', {...common, rows: meta.type === 'json' ? 6 : 3, oninput: (e) => onChange(e.target.value)}, text);
  }
  const type = {date: 'date', email: 'email', decimal: 'number', number: 'number'}[meta.type] || (/password/.test(name) ? 'password' : 'text');
  const step = meta.type === 'decimal' ? (meta.places ? String(10 ** -meta.places) : 'any') : meta.type === 'number' ? '1' : null;
  return h('input', {...common, type, step, value: value ?? '', autocomplete: /password/.test(name) ? 'new-password' : null,
    oninput: (e) => onChange(e.target.value)});
}

function linesEditor(shell, resource, initial) {
  const spec = resource.lines;
  const child = resource.fields[spec.field].child || {};
  const columns = Object.keys(child).filter((n) => !child[n].read_only && !SKIP.has(n) && !child[n].display_for);
  const rows = (initial || []).map((row) => ({...row}));
  const body = h('tbody');
  const totals = h('p', {class: 'muted', 'aria-live': 'polite'});
  const errors = [];

  const recompute = () => {
    if (columns.includes('debit') && columns.includes('credit')) {
      const d = rows.reduce((s, r) => s + (Number(r.debit) || 0), 0);
      const c = rows.reduce((s, r) => s + (Number(r.credit) || 0), 0);
      totals.replaceChildren(`Debits ${fmt.money(d)} · Credits ${fmt.money(c)} · `,
        Math.abs(d - c) < 0.005 && d > 0 ? h('span', {class: 'ok-text', text: '✓ balanced'}) : h('span', {class: 'bad-text', text: `difference ${fmt.money(d - c)}`}));
    } else if (columns.includes('quantity') && columns.includes('unit_price')) {
      const total = rows.reduce((s, r) => s + (Number(r.quantity) || 0) * (Number(r.unit_price) || 0), 0);
      totals.textContent = `Lines total ${fmt.money(total)}`;
    } else {
      totals.textContent = `${rows.length} line(s)`;
    }
  };
  const render = () => {
    body.replaceChildren(...rows.map((row, i) => h('tr', {}, columns.map((name) => {
      // A line cell is one line high: long text becomes a plain input.
      const meta = child[name].type === 'long' ? {...child[name], type: 'text'} : child[name];
      const invalid = errors[i] && errors[i][name];
      const cell = inputFor(shell, `${name}-${i}`, meta, row[name], {[`${name}-${i}_display`]: row[`${name}_display`]},
        (v) => { row[name] = v; recompute(); });
      const input = cell.querySelector ? (cell.querySelector('input') || cell) : cell;
      input.setAttribute('aria-label', `${meta.label} line ${i + 1}`);
      return h('td', {class: invalid ? 'invalid' : ''}, cell, invalid ? h('div', {class: 'field-error', text: invalid}) : null);
    }), h('td', {}, h('button', {class: 'btn small ghost remove', type: 'button', 'aria-label': `Remove line ${i + 1}`,
      onclick: () => { rows.splice(i, 1); errors.splice(i, 1); render(); }}, '✕')))));
    recompute();
  };
  render();
  const element = h('section', {class: 'card', style: 'margin-top:14px'},
    h('div', {class: 'card-head'}, h('h2', {text: resource.fields[spec.field].label || 'Lines'}), totals),
    h('div', {class: 'scroll-x'}, h('table', {class: 'lines-editor'},
      h('thead', {}, h('tr', {}, columns.map((n) => h('th', {text: child[n].label + (child[n].required ? ' *' : '')})), h('th', {}))),
      body)),
    h('button', {class: 'btn small', type: 'button', style: 'margin-top:8px', onclick: () => { rows.push({}); render(); }}, '+ Add line'));
  return {
    element,
    // Text columns send '' when empty (DRF text fields allow blank, not null); everything else sends null.
    value: () => rows.map((row) => Object.fromEntries(columns.map((n) => {
      const empty = row[n] === '' || row[n] === undefined || row[n] === null;
      return [n, empty ? (['text', 'long', 'email'].includes(child[n].type) ? '' : null) : row[n]];
    }))),
    // DRF reports nested errors as a list or as an object keyed by line index; accept both.
    setErrors: (list) => {
      errors.length = 0;
      for (const [i, e] of Object.entries(list || {})) {
        errors[Number(i)] = Object.fromEntries(Object.entries(e || {}).map(([k, v]) => [k, errorText(v)]));
      }
      render();
    },
  };
}

export async function formView(ctx, resource, id) {
  const {shell} = ctx;
  const record = id ? await api(`${resource.endpoint}${id}/`) : {};
  const title = id ? `Edit ${titleOf(resource, record) || resource.singular}` : `New ${resource.singular.toLowerCase()}`;
  const crumbs = [{label: groupLabel(shell, resource.group)}, {label: resource.label, href: resource.route},
    ...(id ? [{label: titleOf(resource, record) || `#${id}`, href: `${resource.route}${id}/`}] : []), {label: id ? 'Edit' : 'New'}];
  const mode = id ? 'update' : 'write';
  if (!(resource.access[mode] || []).includes(shell.auth.role())) {
    return {title, crumbs, node: notice(`Your role (${shell.auth.role()}) cannot ${id ? 'edit' : 'create'} ${resource.label.toLowerCase()}.`, 'critical')};
  }
  const status = resource.status ? record[resource.status] : null;
  if (id && Array.isArray(resource.editable) && !resource.editable.includes(status)) {
    return {title, crumbs, node: h('div', {}, h('h1', {text: title}),
      notice(`This ${resource.singular.toLowerCase()} is ${choiceLabel(resource.fields[resource.status], status).toLowerCase()}; only ${resource.editable.join(' or ')} documents can be edited. Use its actions instead.`),
      h('p', {}, h('a', {href: `${resource.route}${id}/`, text: `Back to ${titleOf(resource, record)}`})))};
  }

  const values = {};
  const fieldEls = {};
  const names = writableFields(resource);
  for (const name of names) {
    const meta = resource.fields[name];
    let value = record[name];
    if (!id && value === undefined && meta.type === 'date' && DATE_DEFAULTS.has(name)) value = today();
    if (!id && value === undefined && meta.type === 'bool' && name === 'is_active') value = true;
    values[name] = value;
  }
  const grid = h('div', {class: 'form-grid'}, names.map((name) => {
    const meta = resource.fields[name];
    const wide = meta.type === 'long' || meta.type === 'json';
    const error = h('div', {class: 'field-error', id: `err-${name}`});
    const input = inputFor(shell, name, meta, values[name], record, (v) => { values[name] = v; });
    const field = h(meta.type === 'ref' ? 'div' : 'label', {class: 'field' + (wide ? ' wide' : ''), for: meta.type === 'ref' ? null : `f-${name}`},
      h('span', {}, meta.label, meta.required ? h('span', {class: 'req', text: ' *', 'aria-hidden': 'true'}) : null),
      input, meta.help ? h('span', {class: 'help', text: meta.help}) : null, error);
    fieldEls[name] = {field, error};
    return field;
  }));

  const lineField = resource.lines && resource.lines.field;
  const lines = lineField && !resource.fields[lineField].read_only ? linesEditor(shell, resource, record[lineField] || (id ? [] : [{}])) : null;
  const formError = h('div', {role: 'alert'});
  const submit = h('button', {class: 'btn primary', type: 'submit', text: id ? 'Save changes' : `Create ${resource.singular.toLowerCase()}`});

  const form = h('form', {novalidate: true, onsubmit: async (event) => {
    event.preventDefault();
    for (const {field, error} of Object.values(fieldEls)) { field.classList.remove('invalid'); error.textContent = ''; }
    formError.replaceChildren();
    const body = {};
    for (const name of names) {
      const meta = resource.fields[name];
      let v = values[name];
      if (v === '' || v === undefined) v = meta.type === 'text' || meta.type === 'long' || meta.type === 'email' ? '' : null;
      if (meta.type === 'json' && typeof v === 'string' && v.trim()) {
        try { v = JSON.parse(v); } catch (err) { fieldEls[name].error.textContent = 'Not valid JSON'; return; }
      }
      if (v === null && meta.required) continue;
      if (/password/.test(name) && !v) continue;
      body[name] = v;
    }
    if (lines) body[lineField] = lines.value();
    submit.disabled = true;
    try {
      const saved = await api(id ? `${resource.endpoint}${id}/` : resource.endpoint, {method: id ? 'PATCH' : 'POST', body});
      toast(`${resource.singular} ${titleOf(resource, saved) || ''} saved.`);
      ctx.navigate(`${resource.route}${saved.id ?? id}/`);
    } catch (err) {
      const data = err.data && typeof err.data === 'object' ? err.data : {};
      let mapped = false;
      for (const [name, messages] of Object.entries(data)) {
        if (fieldEls[name]) {
          fieldEls[name].field.classList.add('invalid');
          fieldEls[name].error.textContent = errorText(messages);
          mapped = true;
        } else if (lines && name === lineField) {
          const perLine = messages && typeof messages === 'object' && Object.values(messages).some((m) => m && typeof m === 'object' && !Array.isArray(m));
          if (perLine) lines.setErrors(messages);
          else formError.append(notice(`${resource.fields[lineField].label}: ${errorText(messages)}`, 'critical'));
          mapped = true;
        }
      }
      const rest = Object.entries(data).filter(([k]) => !fieldEls[k] && k !== lineField);
      if (rest.length || !mapped) formError.append(notice(rest.length ? errorText(Object.fromEntries(rest)) : err.message, 'critical'));
    } finally {
      submit.disabled = false;
    }
  }},
    h('section', {class: 'card'}, grid),
    lines ? lines.element : null,
    formError,
    h('div', {class: 'form-actions'}, submit, h('a', {class: 'btn ghost', href: id ? `${resource.route}${id}/` : resource.route, text: 'Cancel'}),
      h('span', {class: 'muted', text: 'Saved through the same API endpoint every client uses; its validation is the rule.'})));

  return {title, crumbs, node: h('div', {}, h('div', {class: 'page-head'}, h('div', {}, h('h1', {text: title}),
    resource.description ? h('p', {class: 'desc', text: resource.description}) : null)), form)};
}
