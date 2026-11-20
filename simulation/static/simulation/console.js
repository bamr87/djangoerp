/*
 * DjangoERP simulation console: a dependency-free client of /api/simulation/.
 * Signs in with the project's JWT endpoint, then renders the sandbox's cockpit,
 * statements, operations, agents, controls, what-if lab and system map. Every
 * string from the server is inserted with textContent.
 */
(() => {
  'use strict';

  const API = '/api/simulation';
  const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
  const WRITE_ROLES = ['accountant', 'admin'];
  const store = {
    access: sessionStorage.getItem('sim.access'),
    refresh: sessionStorage.getItem('sim.refresh'),
    user: JSON.parse(sessionStorage.getItem('sim.user') || 'null'),
    scenario: null, dash: null, design: null, fin: null, finPeriod: 'start',
    tab: (location.hash || '#cockpit').slice(1), playing: false, speed: 1500, busy: false,
    decisionFilter: {role: '', kind: '', driver: '', search: ''}, decisionPage: null, decisions: [],
    paramDraft: {}, whatif: {changes: {}, horizon: 30, driver: 'heuristic', name: ''}, experiment: null,
  };
  const $ = (id) => document.getElementById(id);

  // --- DOM helpers ------------------------------------------------------------------------------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else if (key === 'style') el.setAttribute('style', value);
      else if (value === true) el.setAttribute(key, '');
      else el.setAttribute(key, value);
    }
    append(el, children);
    return el;
  }
  function fill(el, ...children) {
    el.replaceChildren();
    return append(el, children);
  }
  function append(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const SVGNS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined) continue;
      if (key === 'text') el.textContent = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value);
    }
    for (const child of children.flat()) if (child) el.append(child);
    return el;
  }
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const fmt = {
    money: (v, d = 0) => isNum(v) ? (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}) : '—',
    num: (v, d = 0) => isNum(v) ? v.toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}) : '—',
    pct: (v, d = 1) => isNum(v) ? v.toFixed(d) + '%' : '—',
    ratio: (v, d = 0) => isNum(v) ? (v * 100).toFixed(d) + '%' : '—',
    compact(v) {
      if (!isNum(v)) return '—';
      const a = Math.abs(v);
      if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
      if (a >= 1e4) return (v / 1e3).toFixed(a >= 1e5 ? 0 : 1) + 'K';
      return Math.round(v).toLocaleString();
    },
    signed: (v, f) => (isNum(v) && v > 0 ? '+' : '') + f(v),
  };
  const money2 = (v) => fmt.money(v, 2);
  const sum = (arr) => arr.reduce((a, b) => a + (isNum(b) ? b : 0), 0);
  const mean = (arr) => { const xs = arr.filter(isNum); return xs.length ? sum(xs) / xs.length : null; };
  const rolling = (arr, n, fn = mean) => arr.map((_, i) => fn(arr.slice(Math.max(0, i - n + 1), i + 1)));
  const cumulative = (arr) => { let t = 0; return arr.map((v) => (t += isNum(v) ? v : 0)); };
  const canWrite = () => store.user && WRITE_ROLES.includes(store.user.role);

  // --- API --------------------------------------------------------------------------------------
  async function api(path, {method = 'GET', body, retry = true} = {}) {
    const headers = {'Accept': 'application/json'};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (store.access) headers['Authorization'] = 'Bearer ' + store.access;
    const url = path.startsWith('/api/') ? path : API + path;
    const response = await fetch(url, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
    if (response.status === 401 && retry && store.refresh) {
      if (await refreshToken()) return api(path, {method, body, retry: false});
      logout('Your session expired; sign in again.');
      throw new Error('Session expired');
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorText(data) || `${response.status} ${response.statusText}`);
    return data;
  }
  function errorText(data) {
    if (!data) return '';
    if (typeof data === 'string') return data;
    if (Array.isArray(data)) return data.map(errorText).join(' ');
    if (data.detail) return errorText(data.detail);
    return Object.entries(data).map(([k, v]) => (k === 'non_field_errors' ? '' : k + ': ') + errorText(v)).join(' · ');
  }
  async function refreshToken() {
    try {
      const response = await fetch('/api/auth/refresh/', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({refresh: store.refresh})});
      if (!response.ok) return false;
      const data = await response.json();
      store.access = data.access;
      sessionStorage.setItem('sim.access', data.access);
      return true;
    } catch (err) {
      console.warn('token refresh failed', err);
      return false;
    }
  }
  function say(message, tone) {
    store.message = {text: message || '', tone};  // the run bar re-renders after each refresh; keep the message
    const el = $('runmsg');
    if (!el) return;
    el.textContent = store.message.text;
    el.className = 'secondary ' + (tone === 'error' ? 'error' : '');
  }

  // --- auth -------------------------------------------------------------------------------------
  function renderLogin(message) {
    stop();
    $('tabs').hidden = true;
    $('runbar').hidden = true;
    $('clock').replaceChildren();
    $('session').replaceChildren();
    const error = h('p', {class: 'error', role: 'alert', text: message || ''});
    const form = h('form', {class: 'card', onsubmit: async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      error.textContent = '';
      try {
        const response = await fetch('/api/auth/login/', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({username: data.get('username'), password: data.get('password')})});
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorText(body) || 'Sign-in failed');
        store.access = body.access; store.refresh = body.refresh; store.user = body.user;
        sessionStorage.setItem('sim.access', body.access);
        sessionStorage.setItem('sim.refresh', body.refresh);
        sessionStorage.setItem('sim.user', JSON.stringify(body.user));
        boot();
      } catch (err) {
        error.textContent = err.message;
      }
    }},
      h('h1', {text: 'Sign in to the sandbox'}),
      h('p', {class: 'secondary', text: 'The console uses the same JWT login as the API. Reading needs any role; running the economy and changing controls needs the accountant or admin role.'}),
      h('label', {class: 'field'}, h('span', {text: 'Username'}), h('input', {type: 'text', name: 'username', autocomplete: 'username', required: true})),
      h('label', {class: 'field'}, h('span', {text: 'Password'}), h('input', {type: 'password', name: 'password', autocomplete: 'current-password', required: true})),
      error,
      h('button', {class: 'btn primary', type: 'submit', text: 'Sign in'}),
      h('p', {class: 'muted', text: 'No account with a role yet? Run python manage.py createsuperuser and give it a role in the admin, or use the Docker stack\'s seeded admin.'}));
    $('view').replaceChildren(h('div', {class: 'login'}, form));
  }
  function logout(message) {
    for (const key of ['sim.access', 'sim.refresh', 'sim.user']) sessionStorage.removeItem(key);
    store.access = store.refresh = store.user = null;
    renderLogin(message);
  }
  function renderSession() {
    const role = store.user && store.user.role;
    $('session').replaceChildren(
      h('span', {class: 'pill'}, h('span', {class: 'dot'}), `${store.user ? store.user.username : ''} · ${role || 'no role'}`),
      h('button', {class: 'btn small', type: 'button', onclick: () => logout(), text: 'Sign out'}));
  }

  // --- boot -------------------------------------------------------------------------------------
  async function boot() {
    if (!store.access) return renderLogin();
    renderSession();
    try {
      const list = await api('/scenarios/');
      const scenario = (list.results || list)[0];
      if (!scenario) return renderCreate();
      store.scenario = scenario;
      $('tabs').hidden = false;
      $('runbar').hidden = false;
      await refresh();
    } catch (err) {
      $('view').replaceChildren(h('p', {class: 'notice critical', role: 'alert', text: err.message}));
    }
  }

  function renderCreate() {
    $('tabs').hidden = true;
    $('runbar').hidden = true;
    const error = h('p', {class: 'error', role: 'alert'});
    const today = new Date().toISOString().slice(0, 10);
    const form = h('form', {class: 'card grid', onsubmit: async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      error.textContent = 'Building the economy: chart of accounts, products, BOMs, lines, agents, opening books…';
      try {
        await api('/scenarios/', {method: 'POST', body: {
          name: data.get('name'), seed: Number(data.get('seed')), start_date: data.get('start_date'),
          tick_days: Number(data.get('tick_days')), driver: data.get('driver')}});
        boot();
      } catch (err) {
        error.textContent = err.message;
      }
    }},
      h('h1', {text: 'Start the sandbox economy'}),
      h('p', {class: 'secondary', text: 'Creates a two-family manufacturer — product line A (widgets) and B (gadgets) — with customers, suppliers, two production lines and a roster of agents, then posts its opening capital, line capex and opening stock through the ERP\'s own services.'}),
      h('div', {class: 'grid cols-2'},
        h('label', {class: 'field'}, h('span', {text: 'Name'}), h('input', {type: 'text', name: 'name', value: 'Sandbox', required: true})),
        h('label', {class: 'field'}, h('span', {text: 'Random seed'}), h('input', {type: 'number', name: 'seed', value: '42', min: '0'})),
        h('label', {class: 'field'}, h('span', {text: 'Start date'}), h('input', {type: 'date', name: 'start_date', value: today})),
        h('label', {class: 'field'}, h('span', {text: 'Days per tick'}), h('input', {type: 'number', name: 'tick_days', value: '1', min: '1', max: '31'})),
        h('label', {class: 'field span-2'}, h('span', {text: 'Agent driver'}),
          h('select', {name: 'driver'}, h('option', {value: 'heuristic', text: 'Heuristic policies (seeded, offline, free)'}),
            h('option', {value: 'llm', text: 'Claude agents (claude-haiku-5-5) with heuristic fallback'})))),
      error,
      h('div', {}, h('button', {class: 'btn primary', type: 'submit', disabled: !canWrite(), text: 'Create scenario'})),
      canWrite() ? null : h('p', {class: 'muted', text: 'Creating a scenario needs the accountant or admin role.'}));
    $('view').replaceChildren(h('div', {class: 'login', style: 'max-width:640px'}, form));
  }

  async function refresh({quiet} = {}) {
    if (!store.scenario) return;
    const view = $('view');
    if (!quiet) view.classList.add('loading');
    try {
      store.dash = await api(`/scenarios/${store.scenario.id}/dashboard/`);
      store.scenario = store.dash.scenario;
      if (store.tab === 'financials') store.fin = null;
      renderClock();
      renderRunbar();
      await renderTab();
    } finally {
      view.classList.remove('loading');
    }
  }

  // --- chrome: clock, run bar, tabs --------------------------------------------------------------
  function guardPill(guard) {
    if (!guard) return h('span', {class: 'pill'}, h('span', {class: 'dot'}), 'Books: n/a');
    const tb = guard.trial_balance.balanced, bs = guard.balance_sheet.balanced;
    const tone = guard.ok ? 'good' : (tb && bs ? 'serious' : 'critical');
    const label = guard.ok ? 'Books balanced and reconciled' : (tb && bs ? 'Reconciliation exception' : 'Books out of balance');
    return h('span', {class: 'pill ' + tone, title: 'Checked by ' + guard.source},
      h('span', {class: 'dot'}), h('span', {class: 'status-icon', 'aria-hidden': 'true', text: guard.ok ? '✓' : '!'}), label);
  }
  function renderClock() {
    const sc = store.scenario, latest = store.dash.latest;
    const statusTone = {ready: '', running: 'good', halted: 'critical'}[sc.status];
    $('clock').replaceChildren(
      h('span', {class: 'tick', text: `Tick ${sc.tick}`}),
      h('span', {class: 'secondary', text: sc.current_date}),
      h('span', {class: 'pill ' + (statusTone || '')}, h('span', {class: 'dot'}), sc.status),
      h('span', {class: 'pill'}, h('span', {class: 'dot'}), sc.driver === 'llm' ? `Claude · ${sc.agent_model}` : 'Heuristic agents'),
      guardPill(latest && latest.guardrails));
  }
  function renderRunbar() {
    const write = canWrite() && store.scenario.status !== 'halted';
    const speed = h('select', {'aria-label': 'Autoplay speed', onchange: (e) => { store.speed = Number(e.target.value); }},
      [[3000, 'Slow'], [1500, 'Normal'], [500, 'Fast']].map(([ms, label]) => h('option', {value: ms, selected: store.speed === ms, text: label})));
    fill($('runbar'),
      h('button', {class: 'btn primary', type: 'button', disabled: !write, onclick: togglePlay,
        'aria-pressed': store.playing ? 'true' : 'false', text: store.playing ? 'Pause' : 'Play'}),
      speed,
      h('button', {class: 'btn', type: 'button', disabled: !write || store.playing, onclick: () => step(1), text: `+1 tick (${store.scenario.tick_days} d)`}),
      h('button', {class: 'btn', type: 'button', disabled: !write || store.playing, onclick: () => step(7), text: '+7'}),
      h('button', {class: 'btn', type: 'button', disabled: !write || store.playing, onclick: () => step(30), text: '+30'}),
      store.scenario.status === 'halted' ? h('button', {class: 'btn', type: 'button', disabled: !canWrite(), onclick: resume, text: 'Resume'}) : null,
      h('span', {class: 'spacer'}),
      h('span', {id: 'runmsg', class: 'secondary' + (store.message && store.message.tone === 'error' ? ' error' : ''),
        'aria-live': 'polite', text: store.message ? store.message.text : ''}));
  }
  async function step(ticks) {
    if (store.busy) return;
    store.busy = true;
    say(`Running ${ticks} tick(s)…`);
    try {
      const result = await api(`/scenarios/${store.scenario.id}/advance/`, {method: 'POST', body: {ticks}});
      const last = result.snapshots[result.snapshots.length - 1];
      say(`Tick ${last.tick} (${last.date}): revenue ${fmt.money(last.pl.revenue)}, net income ${fmt.money(last.pl.net_income)}, ${last.ai.decisions} decisions.`);
      await refresh({quiet: true});
    } catch (err) {
      stop();
      say(err.message, 'error');
      await refresh({quiet: true}).catch(() => {});
    } finally {
      store.busy = false;
    }
  }
  function togglePlay() {
    if (store.playing) return stop();
    store.playing = true;
    renderRunbar();
    const loop = async () => {
      if (!store.playing) return;
      await step(1);
      if (store.playing && store.scenario.status !== 'halted') store.timer = setTimeout(loop, store.speed);
      else stop();
    };
    loop();
  }
  function stop() {
    store.playing = false;
    clearTimeout(store.timer);
    if (store.scenario && $('runbar').childElementCount) renderRunbar();
  }
  async function resume() {
    try {
      await api(`/scenarios/${store.scenario.id}/resume/`, {method: 'POST', body: {}});
      await refresh();
    } catch (err) { say(err.message, 'error'); }
  }

  $('tabs').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-tab]');
    if (!button) return;
    store.tab = button.dataset.tab;
    history.replaceState(null, '', '#' + store.tab);
    renderTab();
  });
  async function renderTab() {
    for (const button of $('tabs').querySelectorAll('button')) {
      if (button.dataset.tab === store.tab) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    const views = {cockpit, financials, operations, agents, controls, whatif, design};
    const render = views[store.tab] || cockpit;
    const node = await render();
    $('view').replaceChildren(node);
  }

  // --- charts -----------------------------------------------------------------------------------
  const tooltip = $('tooltip');
  function showTooltip(event, title, rows) {
    fill(tooltip, h('div', {class: 'tt-title', text: title}),
      rows.map((row) => h('div', {class: 'tt-row'}, row.color ? h('span', {class: 'key', style: `background:${row.color}`}) : null,
        h('span', {class: 'secondary', text: row.label}), h('strong', {text: row.value}))));
    tooltip.hidden = false;
    const x = event.clientX ?? 0, y = event.clientY ?? 0;
    const w = tooltip.offsetWidth, ht = tooltip.offsetHeight;
    tooltip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x + 14)) + 'px';
    tooltip.style.top = Math.max(8, Math.min(window.innerHeight - ht - 8, y + 14)) + 'px';
  }
  function hideTooltip() { tooltip.hidden = true; }

  function niceTicks(min, max, count = 4) {
    if (min === max) { max = min + 1; }
    const span = max - min, step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((m) => m >= step0) || step0;
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v / step) * step);
    return ticks;
  }

  // series: [{label, values, color?}], x: labels. Single-series charts carry no legend box (the title names it).
  function lineChart({series, x, format = fmt.compact, tipFormat, height = 200, zero = true, caption}) {
    series = series.filter((sr) => sr.values.some(isNum)).slice(0, 4).map((sr, i) => ({...sr, color: sr.color || SERIES[i]}));
    const tipFmt = tipFormat || format;
    const wrap = h('div', {class: 'chart'});
    if (series.length > 1) {
      wrap.append(h('div', {class: 'legend'}, series.map((sr) => h('span', {}, h('span', {class: 'key', style: `background:${sr.color}`}), sr.label))));
    }
    const holder = h('div');
    const table = h('div', {class: 'chart-table', hidden: true});
    const toggle = h('button', {class: 'btn small ghost', type: 'button', onclick: () => {
      table.hidden = !table.hidden;
      toggle.textContent = table.hidden ? 'Show table' : 'Hide table';
      if (!table.hidden && !table.childElementCount) {
        table.append(h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Date'}), series.map((sr) => h('th', {class: 'num', text: sr.label})))),
          h('tbody', {}, x.map((label, i) => h('tr', {}, h('td', {text: label}), series.map((sr) => h('td', {class: 'num', text: tipFmt(sr.values[i])})))).reverse())));
      }
    }, text: 'Show table'});
    wrap.append(holder, h('div', {class: 'chart-foot'}, caption ? h('span', {class: 'muted', style: 'margin-right:auto;font-size:12px', text: caption}) : null, toggle), table);
    if (!series.length || x.length < 2) {
      holder.append(h('p', {class: 'muted', text: 'Run a few ticks to draw this chart.'}));
      return wrap;
    }

    const draw = () => {
      const width = Math.max(holder.clientWidth || 560, 260);
      const m = {l: 48, r: 58, t: 8, b: 22};
      const iw = width - m.l - m.r, ih = height - m.t - m.b;
      const all = series.flatMap((sr) => sr.values.filter(isNum));
      let min = Math.min(...all), max = Math.max(...all);
      if (zero) { min = Math.min(0, min); max = Math.max(0, max); }
      const ticks = niceTicks(min, max);
      const y0 = ticks[0], y1 = ticks[ticks.length - 1];
      const sx = (i) => m.l + (x.length === 1 ? iw / 2 : (i / (x.length - 1)) * iw);
      const sy = (v) => m.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
      const svgEl = s('svg', {viewBox: `0 0 ${width} ${height}`, height, role: 'img', tabindex: '0',
        'aria-label': series.map((sr) => `${sr.label}: latest ${tipFmt(sr.values[sr.values.length - 1])}`).join('; ')});
      const axis = s('g', {class: 'axis'});
      for (const t of ticks) {
        axis.append(s('line', {class: t === 0 ? 'baseline' : 'gridline', x1: m.l, x2: m.l + iw, y1: sy(t), y2: sy(t)}));
        axis.append(s('text', {x: m.l - 6, y: sy(t) + 4, 'text-anchor': 'end', text: format(t)}));
      }
      const xticks = Math.min(5, x.length);
      for (let k = 0; k < xticks; k++) {
        const i = Math.round((k / Math.max(xticks - 1, 1)) * (x.length - 1));
        axis.append(s('text', {x: sx(i), y: height - 4, 'text-anchor': k === 0 ? 'start' : (k === xticks - 1 ? 'end' : 'middle'), text: x[i]}));
      }
      svgEl.append(axis);
      const labels = [];
      for (const sr of series) {
        let d = '', pen = false;
        sr.values.forEach((v, i) => {
          if (!isNum(v)) { pen = false; return; }
          d += (pen ? 'L' : 'M') + sx(i).toFixed(1) + ' ' + sy(v).toFixed(1) + ' ';
          pen = true;
        });
        svgEl.append(s('path', {class: 'series', d, stroke: sr.color}));
        const last = sr.values.map((v, i) => [v, i]).filter(([v]) => isNum(v)).pop();
        if (last) {
          svgEl.append(s('circle', {class: 'end-dot', cx: sx(last[1]), cy: sy(last[0]), r: 4, fill: sr.color}));
          labels.push({y: sy(last[0]), x: sx(last[1]), text: format(last[0])});
        }
      }
      labels.sort((a, b) => a.y - b.y);
      let lastY = -Infinity;
      for (const label of labels) {
        if (label.y - lastY < 13) continue;  // colliding end labels: legend + tooltip carry the value
        svgEl.append(s('text', {class: 'end-label', x: label.x + 8, y: label.y + 4, text: label.text}));
        lastY = label.y;
      }
      const cross = s('line', {class: 'crosshair', y1: m.t, y2: m.t + ih, visibility: 'hidden'});
      svgEl.append(cross);
      let focusIndex = x.length - 1;
      const point = (i, event) => {
        focusIndex = i;
        cross.setAttribute('x1', sx(i)); cross.setAttribute('x2', sx(i)); cross.setAttribute('visibility', 'visible');
        showTooltip(event, x[i], series.map((sr) => ({label: sr.label, value: tipFmt(sr.values[i]), color: sr.color})));
      };
      const hit = s('rect', {class: 'hit', x: m.l, y: m.t, width: iw, height: ih,
        onpointermove: (event) => {
          const rect = svgEl.getBoundingClientRect();
          const px = ((event.clientX - rect.left) / rect.width) * width;
          point(Math.max(0, Math.min(x.length - 1, Math.round(((px - m.l) / iw) * (x.length - 1)))), event);
        },
        onpointerleave: () => { cross.setAttribute('visibility', 'hidden'); hideTooltip(); }});
      svgEl.append(hit);
      svgEl.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const i = Math.max(0, Math.min(x.length - 1, focusIndex + (event.key === 'ArrowRight' ? 1 : -1)));
        const rect = svgEl.getBoundingClientRect();
        point(i, {clientX: rect.left + (sx(i) / width) * rect.width, clientY: rect.top + 10});
      });
      svgEl.addEventListener('focus', () => {
        const rect = svgEl.getBoundingClientRect();
        point(focusIndex, {clientX: rect.left + (sx(focusIndex) / width) * rect.width, clientY: rect.top + 10});
      });
      svgEl.addEventListener('blur', () => { cross.setAttribute('visibility', 'hidden'); hideTooltip(); });
      holder.replaceChildren(svgEl);
    };
    requestAnimationFrame(draw);
    new ResizeObserver(() => draw()).observe(holder);
    return wrap;
  }

  // Horizontal bars, one hue (a single measure across nominal rows); value at the tip.
  function barChart(items, {format = fmt.money, color = 'var(--series-1)'} = {}) {
    const wrap = h('div', {class: 'chart'});
    const draw = () => {
      const width = Math.max(wrap.clientWidth || 480, 260);
      const labelW = Math.min(170, width * 0.38), valueW = 76, row = 30, thick = 16;
      const max = Math.max(...items.map((i) => Math.abs(i.value)), 1);
      const iw = width - labelW - valueW;
      const svgEl = s('svg', {viewBox: `0 0 ${width} ${items.length * row + 4}`, height: items.length * row + 4, role: 'img',
        'aria-label': items.map((i) => `${i.label} ${format(i.value)}`).join('; ')});
      items.forEach((item, k) => {
        const y = k * row + (row - thick) / 2;
        const w = Math.max((Math.abs(item.value) / max) * iw, 0);
        const r = Math.min(4, w / 2);
        const x0 = labelW;
        const path = w > 0 ? `M${x0} ${y} H${x0 + w - r} A${r} ${r} 0 0 1 ${x0 + w} ${y + r} V${y + thick - r} A${r} ${r} 0 0 1 ${x0 + w - r} ${y + thick} H${x0} Z` : '';
        svgEl.append(s('text', {class: 'bar-label', x: 0, y: y + thick - 3, text: item.label}));
        const bar = s('path', {class: 'bar', d: path, fill: color, tabindex: '0',
          onpointermove: (event) => showTooltip(event, item.label, [{label: item.sub || 'Amount', value: format(item.value)}]),
          onpointerleave: hideTooltip, onfocus: (event) => {
            const rect = event.target.getBoundingClientRect();
            showTooltip({clientX: rect.right, clientY: rect.top}, item.label, [{label: item.sub || 'Amount', value: format(item.value)}]);
          }, onblur: hideTooltip});
        svgEl.append(bar);
        svgEl.append(s('rect', {class: 'hit', x: x0, y: k * row, width: iw, height: row,
          onpointermove: (event) => showTooltip(event, item.label, [{label: item.sub || 'Amount', value: format(item.value)}]),
          onpointerleave: hideTooltip}));
        svgEl.append(s('text', {class: 'bar-label', x: x0 + w + 6, y: y + thick - 3, text: format(item.value)}));
      });
      wrap.replaceChildren(svgEl);
    };
    requestAnimationFrame(draw);
    new ResizeObserver(() => draw()).observe(wrap);
    return wrap;
  }

  function sparkline(values) {
    const vals = values.filter(isNum);
    const svgEl = s('svg', {viewBox: '0 0 120 26', preserveAspectRatio: 'none', 'aria-hidden': 'true'});
    if (vals.length < 2) return svgEl;
    const min = Math.min(...vals), max = Math.max(...vals);
    const sx = (i) => (i / (vals.length - 1)) * 116 + 2, sy = (v) => 22 - ((v - min) / (max - min || 1)) * 18;
    svgEl.append(s('polyline', {points: vals.map((v, i) => `${sx(i)},${sy(v)}`).join(' '), fill: 'none', stroke: 'var(--deemph)', 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke'}));
    svgEl.append(s('circle', {cx: sx(vals.length - 1), cy: sy(vals[vals.length - 1]), r: 2.5, fill: 'var(--series-1)'}));
    return svgEl;
  }

  function tile(label, value, {delta, upGood = true, spark, deltaText} = {}) {
    let deltaEl = null;
    if (isNum(delta) && Math.abs(delta) >= 0.05) {
      const up = delta > 0;
      deltaEl = h('div', {class: 'delta ' + (up ? (upGood ? 'up-good' : 'up-bad') : (upGood ? 'down-bad' : 'down-good'))},
        (up ? '▲ ' : '▼ ') + deltaText);
    } else if (deltaText) {
      deltaEl = h('div', {class: 'delta', text: deltaText});
    }
    return h('div', {class: 'tile'}, h('div', {class: 'label', text: label}), h('div', {class: 'value', text: value}), deltaEl,
      spark ? sparkline(spark) : null);
  }

  // --- shared pieces ----------------------------------------------------------------------------
  function docButtons(documents) {
    return (documents || []).map((ref) => h('button', {class: 'docref', type: 'button', onclick: () => openTrace(ref), text: ref, title: 'Trace ' + ref}));
  }
  function driverPill(driver) {
    const label = {heuristic: 'heuristic', llm: 'Claude', fallback: 'fallback', physics: 'stochastic'}[driver] || driver;
    return h('span', {class: 'chip', text: label});
  }
  function feed(decisions, {expand = false} = {}) {
    if (!decisions.length) return h('p', {class: 'muted', text: 'No decisions yet.'});
    return h('ul', {class: 'feed'}, decisions.map((d) => h('li', {},
      h('span', {class: 'meta'}, `T${d.tick} · ${d.agent}`),
      h('span', {class: 'what'}, d.status === 'rejected' ? h('span', {class: 'bad-text status-icon', text: '✕ '}) : null, d.summary, ' ', driverPill(d.driver)),
      d.kind === 'policy' && d.rationale ? h('span', {class: 'why', text: d.rationale}) : null,
      d.error ? h('span', {class: 'why bad-text', text: d.error}) : null,
      d.documents && d.documents.length ? h('span', {class: 'docs'}, docButtons(d.documents)) : null,
      expand ? h('details', {class: 'why'}, h('summary', {text: 'Observation and decision'}),
        h('pre', {class: 'json', text: JSON.stringify({observation: d.observation, payload: d.payload, model: d.model, cost: d.cost}, null, 2)})) : null)));
  }
  function card(title, sub, ...children) {
    return h('section', {class: 'card'}, h('div', {class: 'card-head'}, h('h2', {text: title}), sub ? h('span', {class: 'sub', text: sub}) : null), ...children);
  }
  function openDrawer(title, body) {
    $('drawer-title').textContent = title;
    $('drawer-body').replaceChildren(body);
    $('drawer').hidden = false;
    $('drawer-close').focus();
  }
  $('drawer-close').addEventListener('click', () => { $('drawer').hidden = true; });
  $('drawer').addEventListener('click', (event) => { if (event.target === $('drawer')) $('drawer').hidden = true; });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') $('drawer').hidden = true; });

  async function openTrace(reference) {
    openDrawer('Trace ' + reference, h('p', {class: 'muted', text: 'Loading…'}));
    try {
      const data = await api(`/scenarios/${store.scenario.id}/trace/?reference=${encodeURIComponent(reference)}`);
      fill($('drawer-body'),
        h('h3', {text: 'Agent decisions'}), feed(data.decisions, {expand: true}),
        h('h3', {style: 'margin-top:16px', text: 'Journal entries'}),
        data.journal_entries.length ? data.journal_entries.map((e) => h('div', {class: 'card', style: 'margin:8px 0'},
          h('div', {class: 'card-head'}, h('h2', {text: `${e.entry_number} · ${e.date}`}), h('span', {class: 'sub', text: e.source_reference || 'manual'})),
          h('p', {class: 'secondary', text: e.description}),
          h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Account'}), h('th', {text: 'Name'}), h('th', {class: 'num', text: 'Debit'}), h('th', {class: 'num', text: 'Credit'}))),
            h('tbody', {}, e.lines.map((l) => h('tr', {}, h('td', {}, h('button', {class: 'docref', type: 'button', onclick: () => openLedger(l.account), text: l.account})),
              h('td', {text: l.name}), h('td', {class: 'num', text: l.debit ? money2(l.debit) : ''}), h('td', {class: 'num', text: l.credit ? money2(l.credit) : ''})))))))) :
          h('p', {class: 'muted', text: 'No journal entry carries this reference.'}));
    } catch (err) {
      $('drawer-body').replaceChildren(h('p', {class: 'error', text: err.message}));
    }
  }
  async function openLedger(code) {
    openDrawer('General ledger ' + code, h('p', {class: 'muted', text: 'Loading…'}));
    try {
      const data = await api(`/scenarios/${store.scenario.id}/ledger/?account=${encodeURIComponent(code)}`);
      const rows = (data.transactions || []).slice(-200).reverse();
      $('drawer-body').replaceChildren(
        h('p', {class: 'secondary', text: `${data.account_code} ${data.account_name} (${data.account_type}) — opening ${money2(data.opening_balance)}, ending ${money2(data.ending_balance)} (debit minus credit), compiled by reports.report_generators.generate_general_ledger. Latest 200 lines first.`}),
        h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, ['Date', 'Entry', 'Description', 'Debit', 'Credit', 'Balance'].map((t, i) => h('th', {class: i > 2 ? 'num' : '', text: t})))),
          h('tbody', {}, rows.map((r) => h('tr', {}, h('td', {text: r.date}), h('td', {}, h('button', {class: 'docref', type: 'button', onclick: () => openTrace(r.entry_number), text: r.entry_number})),
            h('td', {class: 'wrap', text: r.description}), h('td', {class: 'num', text: Number(r.debit) ? money2(Number(r.debit)) : ''}),
            h('td', {class: 'num', text: Number(r.credit) ? money2(Number(r.credit)) : ''}), h('td', {class: 'num', text: money2(Number(r.balance))})))))));
    } catch (err) {
      $('drawer-body').replaceChildren(h('p', {class: 'error', text: err.message}));
    }
  }

  // --- cockpit ----------------------------------------------------------------------------------
  function cockpit() {
    const {series, latest, decisions} = store.dash;
    const sc = store.scenario;
    const x = series.map((p) => p.date);
    const col = (key) => series.map((p) => p[key]);
    const last7 = (key) => sum(series.slice(-7).map((p) => p[key]));
    const prev7 = (key) => sum(series.slice(-14, -7).map((p) => p[key]));
    const at = (key, back = 0) => (series[series.length - 1 - back] || {})[key];
    const root = h('div', {});
    if (sc.status === 'halted') {
      root.append(h('p', {class: 'notice critical', role: 'alert'}, h('strong', {text: 'Halted by a guardrail. '}), sc.error_message, ' The failing tick was rolled back; resume to continue.'));
    }
    const ni = sum(col('net_income'));
    const g = latest && latest.guardrails;
    root.append(h('div', {class: 'hero'},
      h('div', {}, h('div', {class: 'caption', text: 'Net income since start'}), h('div', {class: 'figure', text: fmt.money(ni)})),
      h('div', {class: 'caption'}, `${sc.start_date} → ${sc.current_date} · ${sc.tick} ticks of ${sc.tick_days} day(s)`, h('br'),
        g ? `Trial balance ${fmt.money(g.trial_balance.total_debits)} = ${fmt.money(g.trial_balance.total_credits)} · checked by ${g.source}` : '')));
    const gm7 = last7('revenue') ? (last7('gross_margin') / last7('revenue')) * 100 : null;
    const gmPrev = prev7('revenue') ? (prev7('gross_margin') / prev7('revenue')) * 100 : null;
    const otif = mean(series.slice(-7).map((p) => p.otif_pct));
    const spark = (key) => series.slice(-30).map((p) => p[key]);
    const roll = (key) => rolling(col(key), 7, sum).slice(-30);
    root.append(h('div', {class: 'tiles'},
      tile('Revenue, last 7 days', fmt.money(last7('revenue')), {delta: last7('revenue') - prev7('revenue'), deltaText: fmt.signed(last7('revenue') - prev7('revenue'), fmt.compact) + ' vs prior 7', spark: roll('revenue')}),
      tile('Gross margin, last 7 days', fmt.pct(gm7), {delta: isNum(gmPrev) && isNum(gm7) ? gm7 - gmPrev : null, deltaText: isNum(gmPrev) && isNum(gm7) ? fmt.signed(gm7 - gmPrev, (v) => v.toFixed(1)) + ' pts' : ''}),
      tile('Net income, last 7 days', fmt.money(last7('net_income')), {delta: last7('net_income') - prev7('net_income'), deltaText: fmt.signed(last7('net_income') - prev7('net_income'), fmt.compact) + ' vs prior 7', spark: roll('net_income')}),
      tile('Cash', fmt.money(at('cash')), {delta: at('cash') - at('cash', 7), deltaText: fmt.signed(at('cash') - at('cash', 7), fmt.compact) + ' in 7 ticks', spark: spark('cash')}),
      tile('Receivables', fmt.money(at('receivables')), {spark: spark('receivables')}),
      tile('Inventory', fmt.money((at('inventory_materials') || 0) + (at('inventory_finished') || 0)), {deltaText: `materials ${fmt.compact(at('inventory_materials'))} · finished ${fmt.compact(at('inventory_finished'))}`}),
      tile('Revolver', fmt.money(at('revolver')), {upGood: false, spark: spark('revolver')}),
      tile('Backlog', fmt.num(at('backlog_units')) + ' units', {deltaText: `${at('late_orders') || 0} late order(s)`, spark: spark('backlog_units')}),
      tile('On time in full, last 7 days', fmt.pct(otif, 0), {deltaText: `${fmt.num(last7('units_cancelled'))} units lost to cancellations`}),
      tile('Under-absorbed, last 7 days', fmt.money(last7('under_absorbed')), {deltaText: 'idle capacity cost, expensed as incurred'}),
      tile('AI agent compute', money2(sum(col('ai_cost'))), {deltaText: `${sum(col('llm_calls'))} Claude call(s), posted to 6300`})));

    root.append(h('h2', {class: 'section-title', text: 'Financial and operating trends'}));
    const charts = h('div', {class: 'grid cols-2'});
    const r7 = (key) => rolling(col(key), 7);
    charts.append(card('Revenue, COGS and net income', 'per day, 7-tick rolling average',
      lineChart({x, series: [{label: 'Revenue', values: r7('revenue')}, {label: 'COGS', values: r7('cogs')}, {label: 'Net income', values: r7('net_income')}], tipFormat: fmt.money})));
    charts.append(card('Cash and working capital', 'balance at each tick',
      lineChart({x, series: [{label: 'Cash', values: col('cash')}, {label: 'Receivables', values: col('receivables')}, {label: 'Payables', values: col('payables')}, {label: 'Revolver', values: col('revolver')}], tipFormat: fmt.money})));
    charts.append(card('Conversion cost: actual vs absorbed', 'per day, 7-tick rolling average — the gap is under-absorbed idle capacity',
      lineChart({x, series: [{label: 'Actual (labor, overhead, depreciation, maintenance)', values: r7('conversion_actual')}, {label: 'Absorbed into inventory', values: r7('absorbed')}], tipFormat: fmt.money})));
    charts.append(card('Units ordered, produced and shipped', 'per day, 7-tick rolling average',
      lineChart({x, series: [{label: 'Ordered', values: r7('units_ordered')}, {label: 'Produced', values: r7('units_produced')}, {label: 'Shipped', values: r7('units_shipped')}, {label: 'Cancelled', values: r7('units_cancelled')}], format: (v) => fmt.num(v), tipFormat: (v) => fmt.num(v, 1)})));
    const lineCodes = Object.keys((latest && latest.lines) || {}).filter((c) => latest.lines[c].status !== 'retired');
    charts.append(card('Line utilization', 'standard hours earned / scheduled hours, 7-tick rolling average' + (lineCodes.length > 4 ? ` (first 4 of ${lineCodes.length} lines; all in the table)` : ''),
      lineChart({x, series: lineCodes.map((code) => ({label: code, values: rolling(series.map((p) => (p.utilization || {})[code]), 7)})), format: (v) => fmt.ratio(v), tipFormat: (v) => fmt.ratio(v, 1)})));
    const skus = Object.keys((latest && latest.skus) || {});
    charts.append(card('Unit cost by product', 'moving-average cost of finished goods (materials + absorbed conversion)',
      lineChart({x, zero: false, series: skus.map((sku) => ({label: sku, values: series.map((p) => (p.unit_cost || {})[sku] || null)})), format: (v) => fmt.money(v), tipFormat: money2})));
    charts.append(card('List price by product', 'set by the sales manager agent',
      lineChart({x, zero: false, series: skus.map((sku) => ({label: sku, values: series.map((p) => (p.price || {})[sku] || null)})), format: (v) => fmt.money(v), tipFormat: money2})));
    charts.append(card('Market forces', 'indices around 1.0 — the demand multipliers customers respond to',
      lineChart({x, zero: false, series: [{label: 'Season', values: col('season_factor')}, {label: 'Sentiment (e^s)', values: col('sentiment').map((v) => isNum(v) ? Math.exp(v) : null)}, {label: 'Competitor prices', values: col('competitor_price_index')}], format: (v) => v.toFixed(2), tipFormat: (v) => isNum(v) ? v.toFixed(3) : '—'})));
    root.append(charts);

    root.append(h('h2', {class: 'section-title', text: 'Guardrails and narrative'}));
    root.append(h('div', {class: 'grid cols-2'}, guardCard(latest && latest.guardrails),
      card('Controller commentary', 'the finance agent\'s latest review', h('p', {text: sc.commentary || 'Written at the controller\'s first review.'}),
        h('p', {class: 'muted', text: `Market: sentiment ${isNum(sc.market.sentiment) ? sc.market.sentiment.toFixed(2) : '—'}, competitor prices ${isNum(sc.market.competitor_price_index) ? sc.market.competitor_price_index.toFixed(2) : '—'}x reference.`}))));
    root.append(card('Latest agent activity', 'newest first — open a document to trace it into the ledger', feed(decisions.slice(0, 14))));
    return root;
  }

  function guardCard(guard) {
    if (!guard) return card('Guardrails', '', h('p', {class: 'muted', text: 'No snapshot yet.'}));
    const row = (label, ok, detail) => h('tr', {}, h('td', {}, h('span', {class: 'pill ' + (ok ? 'good' : 'critical')}, h('span', {class: 'dot'}),
      h('span', {class: 'status-icon', 'aria-hidden': 'true', text: ok ? '✓' : '✕'}), ok ? 'OK' : 'Off')), h('td', {text: label}), h('td', {class: 'num', text: detail}));
    return card('Guardrails', 'checked by ' + guard.source, h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {},
      row('Trial balance foots', guard.trial_balance.balanced, `${fmt.money(guard.trial_balance.total_debits)} = ${fmt.money(guard.trial_balance.total_credits)}`),
      row('Balance sheet balances', guard.balance_sheet.balanced, `${fmt.money(guard.balance_sheet.total_assets)} = ${fmt.money(guard.balance_sheet.total_liabilities + guard.balance_sheet.total_equity)}`),
      guard.reconciliations.map((c) => row(c.label, c.ok, `GL ${fmt.money(c.gl)} · sub ${fmt.money(c.subledger)}`))))));
  }

  // --- financials -------------------------------------------------------------------------------
  async function financials() {
    if (!store.fin) store.fin = await api(`/scenarios/${store.scenario.id}/financials/`);
    const fin = store.fin;
    const period = store.finPeriod === 'period';
    const pl = period ? fin.pl_period : fin.pl;
    const root = h('div', {});
    const toggle = h('div', {class: 'inline', role: 'group', 'aria-label': 'Income statement period'},
      h('button', {class: 'btn' + (!period ? ' primary' : ''), type: 'button', 'aria-pressed': String(!period), onclick: () => { store.finPeriod = 'start'; renderTab(); }, text: `Since start (${fin.from} → ${fin.as_of})`}),
      h('button', {class: 'btn' + (period ? ' primary' : ''), type: 'button', 'aria-pressed': String(period), onclick: () => { store.finPeriod = 'period'; renderTab(); }, text: `Last 7 days (${fin.period_from} → ${fin.as_of})`}),
      h('button', {class: 'btn ghost', type: 'button', onclick: () => { store.fin = null; renderTab(); }, text: 'Recompile'}));
    root.append(toggle);
    const generator = period ? fin.income_statement_period : fin.income_statement;
    const r = (label, value, cls) => h('tr', {class: cls || ''}, h('td', {text: label}), h('td', {class: 'num', text: money2(value)}));
    const income = card('Income statement (management view)', 'absorption costing; compiled from posted journal lines',
      h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {},
        r('Sales — product line A (4100)', pl.revenue_a), r('Sales — product line B (4200)', pl.revenue_b), r('Revenue', pl.revenue, 'total'),
        r('Cost of goods sold — line A (5100)', -pl.cogs_a), r('Cost of goods sold — line B (5200)', -pl.cogs_b),
        r(`Gross margin (${fmt.pct(pl.gross_margin_pct)})`, pl.gross_margin, 'total'),
        r('Direct labor (5300)', -pl.direct_labor), r('Indirect labor (5310)', -pl.indirect_labor), r('Variable overhead (5320)', -pl.variable_overhead),
        r('Depreciation (5330)', -pl.depreciation), r('Maintenance (5340)', -pl.maintenance),
        r('Less: absorbed into inventory (5390)', pl.absorbed), r('Under-absorbed conversion cost', -pl.under_absorbed, 'total'),
        r('Management (6100)', -pl.management), r('Facility (6200)', -pl.facility), r('AI agent compute (6300)', -pl.ai_compute), r('Interest (6400)', -pl.interest),
        pl.disposal_loss ? r('Loss on disposal (6500)', -pl.disposal_loss) : null,
        r('Net income', pl.net_income, 'total')))),
      h('p', {class: 'muted', style: 'margin-top:8px', text: `Cross-check from reports.report_generators.generate_income_statement: revenue ${money2(Number(generator.total_revenue))}, expenses ${money2(Number(generator.total_expenses))}, net income ${money2(Number(generator.net_income))}.`}));
    const bs = fin.balance_sheet;
    const accRows = (rows) => rows.filter((a) => Math.abs(Number(a.balance)) >= 0.005).map((a) => h('tr', {}, h('td', {}, h('button', {class: 'docref', type: 'button', onclick: () => openLedger(a.code), text: a.code}), ' ', a.name), h('td', {class: 'num', text: money2(Number(a.balance))})));
    const balance = card('Balance sheet', `as of ${bs.as_of_date} — reports.report_generators.generate_balance_sheet`,
      h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {},
        h('tr', {}, h('th', {colspan: 2, text: 'Assets'})), accRows(bs.assets), r('Total assets', Number(bs.total_assets), 'total'),
        h('tr', {}, h('th', {colspan: 2, text: 'Liabilities'})), accRows(bs.liabilities), r('Total liabilities', Number(bs.total_liabilities), 'total'),
        h('tr', {}, h('th', {colspan: 2, text: 'Equity'})), accRows(bs.equity), r('Current-period earnings (open P&L)', Number(bs.current_period_earnings)),
        r('Total equity', Number(bs.total_equity), 'total'),
        h('tr', {}, h('td', {text: 'Assets = liabilities + equity'}), h('td', {class: 'num ' + (bs.balanced ? 'ok-text' : 'bad-text'), text: bs.balanced ? '✓ balanced' : '✕ out of balance'}))))));
    root.append(h('div', {class: 'grid cols-2', style: 'margin-top:12px'}, income, balance));

    const drivers = (period ? fin.cost_drivers_period : fin.cost_drivers).filter((d) => d.amount > 0);
    root.append(h('div', {class: 'grid cols-2', style: 'margin-top:16px'},
      card('Cost by driver', 'every cost is a resource an agent drives',
        barChart(drivers.map((d) => ({label: d.label, value: d.amount, sub: `${d.account} · driven by ${d.driven_by}`}))),
        h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Driver'}), h('th', {text: 'Account'}), h('th', {text: 'Agent role'}), h('th', {class: 'num', text: 'Amount'}))),
          h('tbody', {}, (period ? fin.cost_drivers_period : fin.cost_drivers).map((d) => h('tr', {}, h('td', {text: d.label}),
            h('td', {}, h('button', {class: 'docref', type: 'button', onclick: () => openLedger(d.account), text: d.account})), h('td', {text: d.driven_by}), h('td', {class: 'num', text: money2(d.amount)}))))))),
      card('Unit economics since start', 'production receipts from the stock ledger: components + absorbed conversion',
        h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Product'}), h('th', {class: 'num', text: 'Units made'}), h('th', {class: 'num', text: 'Value'}), h('th', {class: 'num', text: 'Unit cost'}))),
          h('tbody', {}, Object.entries(fin.unit_economics).map(([sku, u]) => h('tr', {}, h('td', {text: sku}), h('td', {class: 'num', text: fmt.num(u.units)}), h('td', {class: 'num', text: money2(u.value)}), h('td', {class: 'num', text: money2(u.unit_cost)})))))),
        h('h3', {style: 'margin-top:12px', text: 'Margin by product line'}),
        h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Line'}), h('th', {class: 'num', text: 'Revenue'}), h('th', {class: 'num', text: 'COGS'}), h('th', {class: 'num', text: 'Gross margin'}))),
          h('tbody', {}, Object.entries(fin.by_family).map(([fam, v]) => h('tr', {}, h('td', {text: 'Product line ' + fam}), h('td', {class: 'num', text: money2(v.revenue)}), h('td', {class: 'num', text: money2(v.cogs)}),
            h('td', {class: 'num', text: v.revenue ? fmt.pct(((v.revenue - v.cogs) / v.revenue) * 100) : '—'})))))))));

    const tb = fin.trial_balance;
    root.append(h('div', {class: 'grid cols-2', style: 'margin-top:16px'},
      card('Trial balance', `as of ${tb.as_of_date} — raw debit minus credit; click an account for its ledger`,
        h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Account'}), h('th', {class: 'num', text: 'Debit'}), h('th', {class: 'num', text: 'Credit'}))),
          h('tbody', {}, tb.accounts.filter((a) => Number(a.debit) || Number(a.credit)).map((a) => h('tr', {},
            h('td', {}, h('button', {class: 'docref', type: 'button', onclick: () => openLedger(a.account_code), text: a.account_code}), ' ', a.account_name),
            h('td', {class: 'num', text: Number(a.debit) ? money2(Number(a.debit)) : ''}), h('td', {class: 'num', text: Number(a.credit) ? money2(Number(a.credit)) : ''}))),
            h('tr', {class: 'total'}, h('td', {text: tb.balanced ? 'Totals ✓ balanced' : 'Totals ✕ out of balance'}), h('td', {class: 'num', text: money2(Number(tb.total_debits))}), h('td', {class: 'num', text: money2(Number(tb.total_credits))})))))),
      guardCard(fin.guardrails)));
    return root;
  }

  // --- operations -------------------------------------------------------------------------------
  async function ensureDesign(force) {
    if (!store.design || force) store.design = await api(`/scenarios/${store.scenario.id}/design/`);
    return store.design;
  }
  async function operations() {
    const {lines, jobs, stock} = store.dash;
    const design = await ensureDesign(true);
    const root = h('div', {});
    const write = canWrite();
    const lineCards = lines.map((line) => {
      const latest = line.latest || {};
      const wear = line.wear;
      const shiftBtn = (delta) => h('button', {class: 'btn small', type: 'button', disabled: !write || line.status === 'retired' || line.shifts + delta < 1 || line.shifts + delta > 3,
        onclick: async () => { try { await api(`/lines/${line.id}/`, {method: 'PATCH', body: {shifts: line.shifts + delta}}); await refresh({quiet: true}); } catch (err) { say(err.message, 'error'); } },
        text: delta > 0 ? '+ shift' : '− shift'});
      return h('section', {class: 'card line-card'},
        h('div', {class: 'card-head'}, h('h2', {text: line.code}), h('span', {class: 'pill ' + ({running: 'good', down: 'critical', retired: ''}[line.status] || '')}, h('span', {class: 'dot'}), line.status)),
        h('p', {class: 'secondary', text: `${line.name} · makes product line ${line.family}`}),
        h('div', {class: 'row'}, h('span', {text: 'Machines × shifts'}), h('span', {class: 'num', text: `${line.machines} × ${line.shifts}`})),
        h('div', {class: 'row'}, h('span', {text: 'Crew per machine'}), h('span', {class: 'num', text: fmt.num(line.crew_per_machine, 1)})),
        h('div', {class: 'row'}, h('span', {text: 'Utilization (last tick)'}), h('span', {class: 'num', text: fmt.ratio(latest.utilization)})),
        h('div', {class: 'row'}, h('span', {text: 'Absorption rate'}), h('span', {class: 'num', text: isNum(latest.oar) ? money2(latest.oar) + ' / machine-h' : '—'})),
        h('div', {class: 'row'}, h('span', {text: 'Jobs queued / running'}), h('span', {class: 'num', text: `${line.jobs_queued} / ${line.jobs_running}`})),
        h('div', {class: 'row'}, h('span', {text: 'Book value'}), h('span', {class: 'num', text: fmt.money(line.capex - line.accumulated_depreciation)})),
        h('div', {class: 'row'}, h('span', {text: `Wear ${fmt.ratio(wear)}`}), h('span', {class: 'num', text: `${fmt.num(line.run_hours)} run h`})),
        h('div', {class: 'meter ' + (wear > 0.75 ? 'critical' : wear > 0.5 ? 'warning' : ''), role: 'meter', 'aria-valuemin': '0', 'aria-valuemax': '1', 'aria-valuenow': String(wear), 'aria-label': 'Wear'}, h('span', {style: `width:${Math.min(100, wear * 100)}%`})),
        h('div', {class: 'inline', style: 'margin-top:10px'}, shiftBtn(1), shiftBtn(-1),
          h('button', {class: 'btn small ghost', type: 'button', disabled: !write || line.status === 'retired', onclick: async () => {
            if (!confirm(`Retire ${line.code}? Its queued jobs move to a sister line and the asset is disposed of at book value.`)) return;
            try { await api(`/lines/${line.id}/retire/`, {method: 'POST', body: {}}); await refresh({quiet: true}); } catch (err) { say(err.message, 'error'); }
          }, text: 'Retire'})));
    });
    const commission = h('form', {class: 'card grid', onsubmit: async (event) => {
      event.preventDefault();
      const data = new FormData(commission);
      try {
        await api('/lines/', {method: 'POST', body: {scenario: store.scenario.id, family_code: data.get('family'), machines: Number(data.get('machines')), shifts: Number(data.get('shifts'))}});
        say('Line commissioned: capex posted, crew and machine agents hired.');
        await refresh({quiet: true});
      } catch (err) { say(err.message, 'error'); }
    }},
      h('div', {class: 'card-head'}, h('h2', {text: 'Commission a line'}), h('span', {class: 'sub', text: 'posts Dr 1500 / Cr 1000 at capex; try it in the What-if lab first'})),
      h('label', {class: 'field'}, h('span', {text: 'Product family'}), h('select', {name: 'family'}, h('option', {value: 'A', text: 'Product line A (widgets)'}), h('option', {value: 'B', text: 'Product line B (gadgets)'}))),
      h('label', {class: 'field'}, h('span', {text: 'Machines'}), h('input', {type: 'number', name: 'machines', value: '1', min: '1', max: '10'})),
      h('label', {class: 'field'}, h('span', {text: 'Shifts'}), h('input', {type: 'number', name: 'shifts', value: '1', min: '1', max: '3'})),
      h('div', {}, h('button', {class: 'btn primary', type: 'submit', disabled: !write, text: 'Commission'})));
    root.append(h('div', {class: 'grid cols-3'}, lineCards, commission));

    root.append(h('div', {class: 'grid cols-2', style: 'margin-top:16px'},
      card('Open production jobs', 'work orders scheduled on lines (finite capacity)', jobs.length ? h('div', {class: 'scroll-x'}, h('table', {},
        h('thead', {}, h('tr', {}, ['Work order', 'Product', 'Qty', 'Line', 'Status', 'Progress', 'Due'].map((t) => h('th', {text: t})))),
        h('tbody', {}, jobs.map((j) => h('tr', {}, h('td', {}, docButtons([j.work_order])), h('td', {text: j.sku}), h('td', {class: 'num', text: fmt.num(j.quantity)}), h('td', {text: j.line}),
          h('td', {text: j.status}), h('td', {style: 'min-width:120px'}, h('div', {class: 'meter', title: `${fmt.num(j.hours_done, 1)} of ${fmt.num(j.hours_required, 1)} h`}, h('span', {style: `width:${Math.min(100, (j.hours_done / (j.hours_required || 1)) * 100)}%`})),
            h('span', {class: 'muted', style: 'font-size:11px', text: `${fmt.num(j.hours_done, 1)} / ${fmt.num(j.hours_required, 1)} h`})), h('td', {text: j.due || ''})))))) : h('p', {class: 'muted', text: 'No open jobs.'})),
      card('Stock on hand', 'StockLevel cache of the done-move ledger, at moving average', h('div', {class: 'scroll-x'}, h('table', {},
        h('thead', {}, h('tr', {}, ['SKU', 'On hand', 'Safety', 'Avg cost', 'Value', 'Price'].map((t, i) => h('th', {class: i ? 'num' : '', text: t})))),
        h('tbody', {}, stock.map((l) => h('tr', {}, h('td', {text: `${l.sku} · ${l.name}`}), h('td', {class: 'num', text: fmt.num(l.on_hand, 1)}), h('td', {class: 'num', text: fmt.num(l.safety_stock, 1)}),
          h('td', {class: 'num', text: money2(l.average_cost)}), h('td', {class: 'num', text: money2(l.value)}), h('td', {class: 'num', text: l.procurement === 'make' ? money2(l.price) : '—'})))))))));

    root.append(h('h2', {class: 'section-title', text: 'Business flows (documents by status)'}));
    root.append(h('div', {class: 'grid cols-3'}, design.flows.map((flow) => card(flow.flow, '', flow.steps.map((stepDef) => h('div', {style: 'margin-bottom:8px'},
      h('h3', {text: stepDef.document}), h('div', {class: 'chips'}, Object.entries(stepDef.counts).map(([k, v]) => h('span', {class: 'chip', text: `${k}: ${v}`})))))))));
    return root;
  }

  // --- agents -----------------------------------------------------------------------------------
  async function loadDecisions(reset) {
    const f = store.decisionFilter;
    if (reset) { store.decisions = []; store.decisionPage = null; }
    const params = new URLSearchParams({scenario: store.scenario.id, ordering: '-id'});
    if (f.role) params.set('agent__role', f.role);
    if (f.kind) params.set('kind', f.kind);
    if (f.driver) params.set('driver', f.driver);
    if (f.search) params.set('search', f.search);
    const url = store.decisionPage || `/decisions/?${params}`;
    const data = await api(url);
    store.decisions = store.decisions.concat(data.results.map((d) => ({...d, agent: d.agent_code, date: d.sim_date})));
    store.decisionPage = data.next ? data.next.replace(/^https?:\/\/[^/]+/, '') : null;
  }
  async function agents() {
    const write = canWrite();
    await loadDecisions(true);
    const roster = store.dash.agents;
    const brainSelect = (agent) => agent.effective_brain === 'physics' ? h('span', {class: 'chip', text: 'stochastic model'}) :
      h('select', {'aria-label': 'Brain for ' + agent.code, disabled: !write, onchange: async (event) => {
        try { await api(`/agents/${agent.id}/`, {method: 'PATCH', body: {brain: event.target.value}}); await refresh({quiet: true}); } catch (err) { say(err.message, 'error'); }
      }}, [['auto', `auto (${store.scenario.driver === 'llm' ? 'Claude' : 'heuristic'})`], ['heuristic', 'heuristic'], ['llm', 'Claude']].map(([v, l]) => h('option', {value: v, selected: agent.brain === v, text: l})));
    const policyText = (policy) => Object.entries(policy || {}).filter(([k]) => k !== 'commentary' && k !== 'narrative').map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
    const root = h('div', {});
    root.append(card('Agent roster', 'agents are resources: each has a cost driver, a GL account and a brain',
      h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, ['Agent', 'Role', 'Resource', 'Brain', 'Cost/day', 'Account', 'Review', 'Decisions', 'Claude', 'AI cost', 'Policy'].map((t) => h('th', {text: t})))),
        h('tbody', {}, roster.map((a) => h('tr', {}, h('td', {}, h('strong', {text: a.code}), h('br'), h('span', {class: 'muted', text: a.name})), h('td', {text: a.role_label}),
          h('td', {text: a.resource_kind.replace('_', ' ')}), h('td', {}, brainSelect(a)), h('td', {class: 'num', text: a.daily_cost ? fmt.money(a.daily_cost) : (a.role === 'operator' ? 'by hours' : '—')}),
          h('td', {text: a.cost_account || ''}), h('td', {class: 'num', text: a.effective_brain === 'physics' ? 'every tick' : `every ${a.decision_interval}`}), h('td', {class: 'num', text: fmt.num(a.decisions)}),
          h('td', {class: 'num', text: fmt.num(a.llm_calls)}), h('td', {class: 'num', text: '$' + a.ai_cost.toFixed(4)}), h('td', {class: 'wrap muted', style: 'font-size:12px', text: policyText(a.policy)}))))))));

    const f = store.decisionFilter;
    const roles = [...new Set(roster.map((a) => a.role))];
    const listHolder = h('div', {}, feed(store.decisions, {expand: true}));
    const more = h('button', {class: 'btn', type: 'button', hidden: !store.decisionPage, onclick: async () => {
      await loadDecisions(false);
      listHolder.replaceChildren(feed(store.decisions, {expand: true}));
      more.hidden = !store.decisionPage;
    }, text: 'Load more'});
    const apply = async () => { await loadDecisions(true); listHolder.replaceChildren(feed(store.decisions, {expand: true})); more.hidden = !store.decisionPage; };
    const select = (key, label, options) => h('label', {class: 'field'}, h('span', {text: label}), h('select', {onchange: (e) => { f[key] = e.target.value; apply(); }},
      h('option', {value: '', text: 'All'}), options.map(([v, l]) => h('option', {value: v, selected: f[key] === v, text: l}))));
    root.append(h('div', {style: 'margin-top:16px'}, card('Decision log', 'what each agent saw, chose and why; documents trace into the ledger',
      h('div', {class: 'inline', style: 'margin-bottom:8px;align-items:flex-end'},
        select('role', 'Role', roles.map((r) => [r, r.replace('_', ' ')])),
        select('kind', 'Kind', [['policy', 'Policy reviews'], ['action', 'Actions'], ['event', 'Stochastic events']]),
        select('driver', 'Brain', [['heuristic', 'Heuristic'], ['llm', 'Claude'], ['fallback', 'Fallback'], ['physics', 'Stochastic']]),
        h('label', {class: 'field'}, h('span', {text: 'Search'}), h('input', {type: 'text', value: f.search, placeholder: 'e.g. WO-00012, breakdown', onchange: (e) => { f.search = e.target.value; apply(); }}))),
      listHolder, more)));
    return root;
  }

  // --- controls ---------------------------------------------------------------------------------
  async function controls() {
    const design = await ensureDesign(true);
    const write = canWrite();
    const draft = store.paramDraft;
    const root = h('div', {});
    const sc = store.scenario;
    const settings = h('form', {class: 'card grid', onsubmit: async (event) => {
      event.preventDefault();
      const data = new FormData(settings);
      try {
        await api(`/scenarios/${sc.id}/`, {method: 'PATCH', body: {driver: data.get('driver'), agent_model: data.get('agent_model'), tick_days: Number(data.get('tick_days'))}});
        say('Scenario settings saved.');
        await refresh({quiet: true});
      } catch (err) { say(err.message, 'error'); }
    }},
      h('div', {class: 'card-head'}, h('h2', {text: 'Agents and clock'}), h('span', {class: 'sub', text: 'who decides, and how much time one tick is'})),
      h('label', {class: 'field'}, h('span', {text: 'Agent driver'}), h('select', {name: 'driver'},
        h('option', {value: 'heuristic', selected: sc.driver === 'heuristic', text: 'Heuristic policies (seeded, offline, free)'}),
        h('option', {value: 'llm', selected: sc.driver === 'llm', text: 'Claude agents with heuristic fallback'}))),
      h('label', {class: 'field'}, h('span', {text: 'Claude model for agent reviews'}), h('input', {type: 'text', name: 'agent_model', value: sc.agent_model})),
      h('p', {class: 'muted', text: 'Default claude-haiku-5-5 ($0.10 / $0.50 per million tokens). Needs ANTHROPIC_API_KEY on the server; without it every review falls back to the heuristic and the decision log says so. Each call\'s cost posts to account 6300.'}),
      h('label', {class: 'field'}, h('span', {text: 'Days per tick'}), h('input', {type: 'number', name: 'tick_days', value: String(sc.tick_days), min: '1', max: '31'})),
      h('div', {}, h('button', {class: 'btn primary', type: 'submit', disabled: !write, text: 'Save settings'})));

    const dirtyCount = () => Object.keys(draft).length;
    const applyBtn = h('button', {class: 'btn primary', type: 'button', disabled: !write || !dirtyCount(), onclick: async () => {
      try {
        await api(`/scenarios/${sc.id}/`, {method: 'PATCH', body: {parameters: draft}});
        store.paramDraft = {};
        say('Parameters applied: agents respond from the next tick.');
        await refresh({quiet: true});
      } catch (err) { say(err.message, 'error'); }
    }, text: 'Apply changes'});
    const resetBtn = h('button', {class: 'btn', type: 'button', onclick: () => { store.paramDraft = {}; renderTab(); }, text: 'Discard'});
    const status = h('span', {class: 'secondary', text: ''});
    const refreshStatus = () => { status.textContent = dirtyCount() ? `${dirtyCount()} parameter(s) changed — not applied yet` : 'No pending changes'; applyBtn.disabled = !write || !dirtyCount(); };

    const groups = {};
    for (const p of design.parameters) (groups[p.group] = groups[p.group] || []).push(p);
    const paramRow = (p) => {
      const row = h('div', {class: 'param' + (p.key in draft ? ' dirty' : '')});
      const mark = () => { row.classList.toggle('dirty', p.key in draft); refreshStatus(); };
      const setValue = (value) => {
        if (JSON.stringify(value) === JSON.stringify(p.value)) delete draft[p.key]; else draft[p.key] = value;
        mark();
      };
      const current = p.key in draft ? draft[p.key] : p.value;
      const head = h('div', {}, h('strong', {text: p.label}), p.unit ? h('span', {class: 'muted', text: ` (${p.unit})`}) : null);
      let input;
      if (p.kind === 'bool') {
        input = h('label', {class: 'inline'}, h('input', {type: 'checkbox', checked: !!current, disabled: !write, onchange: (e) => setValue(e.target.checked)}), current ? 'on' : 'off');
        row.append(head, input);
      } else if (p.kind === 'map') {
        row.append(head, h('span', {}));
        row.append(h('div', {class: 'map-inputs'}, Object.entries(current).map(([key, value]) => h('label', {class: 'field'}, h('span', {text: key}),
          h('input', {type: 'number', value: String(value), min: p.min, max: p.max, step: p.step, disabled: !write, onchange: (e) => {
            const next = {...(p.key in draft ? draft[p.key] : p.value), [key]: Number(e.target.value)};
            setValue(next);
          }})))));
      } else {
        const number = h('input', {type: 'number', value: String(current), min: p.min, max: p.max, step: p.step, disabled: !write, 'aria-label': p.label});
        const range = h('input', {type: 'range', value: String(current), min: p.min, max: p.max, step: p.step, disabled: !write, 'aria-label': p.label + ' slider'});
        number.addEventListener('change', () => { range.value = number.value; setValue(Number(number.value)); });
        range.addEventListener('input', () => { number.value = range.value; });
        range.addEventListener('change', () => setValue(Number(range.value)));
        row.append(h('div', {}, head, range), number);
      }
      row.append(h('div', {class: 'desc'}, p.description, h('div', {class: 'chips', style: 'margin-top:4px'},
        p.drives.map((role) => h('span', {class: 'chip', text: 'agent: ' + role})), p.affects.map((a) => h('span', {class: 'chip', text: /^\d{4}$/.test(a) ? 'account ' + a : a})))));
      return row;
    };
    root.append(h('div', {class: 'grid cols-3'}, settings,
      card('Recent control changes', 'humans in the loop, from the audit trail', store.dash.controls.length ? h('ul', {class: 'feed'}, store.dash.controls.map((c) => h('li', {},
        h('span', {class: 'meta', text: `T${c.tick ?? '—'} · ${c.user || 'system'}`}), h('span', {class: 'what', text: Object.entries(c.changes || {}).map(([k, v]) => `${k}: ${JSON.stringify(v.from)} → ${JSON.stringify(v.to)}`).join('; ') || c.event})))) :
        h('p', {class: 'muted', text: 'No control changes yet.'})),
      card('How controls work', '', h('p', {text: 'Every parameter names the agents that read it and the accounts it moves. Change one, apply, and step the clock: the agents respond at their next action or review, and the effect lands in the ledger the same way any business event does. Use the What-if lab to see a change\'s effect without committing it.'}))));
    root.append(h('div', {class: 'runbar', style: 'position:sticky;top:52px;margin:12px 0;border:1px solid var(--rule);border-radius:8px'}, applyBtn, resetBtn, status));
    refreshStatus();
    root.append(h('div', {class: 'grid cols-2'}, Object.entries(groups).map(([group, params]) => card(group, `${params.length} parameter(s)`, params.map(paramRow)))));
    return root;
  }

  // --- what-if lab ------------------------------------------------------------------------------
  const PRESETS = [
    ['Add a production line for product line A', {add_lines: [{family: 'A', machines: 1, shifts: 1}]}],
    ['Add a line for A while demand for A grows 60%', {add_lines: [{family: 'A'}], parameters: {demand_base: {'A-WIDGET': 16, 'A-WIDGET-XL': 4.8}}}],
    ['Demand for A grows 60% (no new capacity)', {parameters: {demand_base: {'A-WIDGET': 16, 'A-WIDGET-XL': 4.8}}}],
    ['Second shift on LINE-A1', {shifts: {'LINE-A1': 2}}],
    ['Direct labor rate +20%', {parameters: {direct_labor_rate: 30}}],
    ['Material inflation 40%/year', {parameters: {material_inflation_pct: 40}}],
    ['Cut prices 5% across the range', {price_change_pct: {'A-WIDGET': -5, 'A-WIDGET-XL': -5, 'B-GADGET': -5}}],
    ['Retire LINE-B1', {retire_lines: ['LINE-B1']}],
  ];
  async function whatif() {
    const write = canWrite();
    const w = store.whatif;
    const root = h('div', {});
    const preview = h('pre', {class: 'json', text: JSON.stringify(w.changes, null, 2)});
    const sync = () => { preview.textContent = JSON.stringify(w.changes, null, 2); };
    const design = await ensureDesign();
    const lines = store.dash.lines.filter((l) => l.status !== 'retired');
    const builder = h('div', {class: 'grid'},
      h('div', {class: 'chips'}, PRESETS.map(([label, changes]) => h('button', {class: 'btn small', type: 'button', onclick: () => { w.changes = JSON.parse(JSON.stringify(changes)); w.name = label; renderTab(); }, text: label}))),
      h('div', {class: 'grid cols-2'},
        h('label', {class: 'field'}, h('span', {text: 'Name'}), h('input', {type: 'text', value: w.name, placeholder: 'What are you testing?', onchange: (e) => { w.name = e.target.value; }})),
        h('label', {class: 'field'}, h('span', {text: `Horizon: ${w.horizon} ticks`}), h('input', {type: 'range', min: '7', max: '120', step: '1', value: String(w.horizon), onchange: (e) => { w.horizon = Number(e.target.value); renderTab(); }})),
        h('label', {class: 'field'}, h('span', {text: 'Agents'}), h('select', {onchange: (e) => { w.driver = e.target.value; }},
          h('option', {value: 'heuristic', selected: w.driver === 'heuristic', text: 'Heuristic (reproducible, free)'}),
          h('option', {value: 'scenario', selected: w.driver === 'scenario', text: 'Same driver as the scenario'})))),
      h('div', {class: 'inline'},
        h('select', {id: 'wi-family'}, h('option', {value: 'A', text: 'Product line A'}), h('option', {value: 'B', text: 'Product line B'})),
        h('button', {class: 'btn small', type: 'button', onclick: () => { (w.changes.add_lines = w.changes.add_lines || []).push({family: $('wi-family').value, machines: 1, shifts: 1}); sync(); }, text: '+ add a line'}),
        h('select', {id: 'wi-line'}, lines.map((l) => h('option', {value: l.code, text: l.code}))),
        h('select', {id: 'wi-shifts'}, [1, 2, 3].map((n) => h('option', {value: n, text: `${n} shift(s)`}))),
        h('button', {class: 'btn small', type: 'button', onclick: () => { (w.changes.shifts = w.changes.shifts || {})[$('wi-line').value] = Number($('wi-shifts').value); sync(); }, text: 'set shifts'}),
        h('button', {class: 'btn small', type: 'button', onclick: () => { (w.changes.retire_lines = w.changes.retire_lines || []).push($('wi-line').value); sync(); }, text: 'retire line'})),
      h('div', {class: 'inline'},
        h('select', {id: 'wi-param'}, design.parameters.filter((p) => p.kind === 'number' || p.kind === 'integer').map((p) => h('option', {value: p.key, text: `${p.group}: ${p.label}`}))),
        h('input', {type: 'number', id: 'wi-value', placeholder: 'value', step: 'any', style: 'width:110px'}),
        h('button', {class: 'btn small', type: 'button', onclick: () => {
          const value = Number($('wi-value').value);
          if (Number.isFinite(value) && $('wi-value').value !== '') { (w.changes.parameters = w.changes.parameters || {})[$('wi-param').value] = value; sync(); }
        }, text: 'override parameter'}),
        h('button', {class: 'btn small ghost', type: 'button', onclick: () => { w.changes = {}; sync(); }, text: 'clear'})),
      preview);
    const result = h('div', {});
    const run = h('button', {class: 'btn primary', type: 'button', disabled: !write, onclick: async () => {
      run.disabled = true;
      result.replaceChildren(h('p', {class: 'secondary', role: 'status', text: `Running baseline and variant for ${w.horizon} ticks each, on the same random numbers… (both arms roll back)`}));
      try {
        let exp = await api('/experiments/', {method: 'POST', body: {scenario: store.scenario.id, name: w.name || 'What-if', horizon_ticks: w.horizon, changes: w.changes, driver: w.driver}});
        while (exp.status === 'pending' || exp.status === 'running') {
          await new Promise((r) => setTimeout(r, 2000));
          exp = await api(`/experiments/${exp.id}/`);
        }
        store.experiment = exp;
        result.replaceChildren(experimentView(exp));
        store.dash = await api(`/scenarios/${store.scenario.id}/dashboard/`);
      } catch (err) {
        result.replaceChildren(h('p', {class: 'error', role: 'alert', text: err.message}));
      } finally {
        run.disabled = !write;
      }
    }, text: 'Run what-if'});
    root.append(card('Build a what-if', 'forks the economy from its current state; nothing is posted to the live books', builder, h('div', {class: 'inline', style: 'margin-top:8px'}, run,
      write ? null : h('span', {class: 'muted', text: 'Running experiments needs the accountant or admin role.'}))));
    if (store.experiment) result.append(experimentView(store.experiment));
    root.append(result);
    const past = store.dash.experiments || [];
    if (past.length) {
      root.append(card('Earlier experiments', '', h('ul', {class: 'feed'}, past.map((e) => h('li', {}, h('span', {class: 'meta', text: `#${e.id} · ${e.status}`}),
        h('span', {class: 'what'}, h('button', {class: 'docref', type: 'button', onclick: async () => {
          store.experiment = await api(`/experiments/${e.id}/`);
          result.replaceChildren(experimentView(store.experiment));
        }, text: e.name}), ` · ${e.horizon_ticks} ticks`))))));
    }
    return root;
  }

  function experimentView(exp) {
    if (exp.status === 'failed') return h('p', {class: 'notice critical', role: 'alert', text: 'Experiment failed: ' + exp.error_message});
    const sm = exp.summary;
    const better = (row) => {
      if (!isNum(row.delta) || row.delta === 0 || row.better === 'neutral') return '';
      const good = row.better === 'higher' ? row.delta > 0 : row.delta < 0;
      return good ? 'ok-text' : 'bad-text';
    };
    const isPct = (key) => key.endsWith('_pct');
    const val = (row, v) => isPct(row.key) ? fmt.pct(v) : (['units_shipped', 'units_cancelled', 'backlog_end'].includes(row.key) ? fmt.num(v) : (row.key === 'full_cost_per_unit' ? money2(v) : fmt.money(v)));
    const base = exp.baseline.series, vari = exp.variant.series;
    const x = base.map((p) => p.date);
    const cum = (arr, key) => cumulative(arr.map((p) => p[key]));
    return h('div', {class: 'grid', style: 'margin-top:16px'},
      card(exp.name, `${exp.horizon_ticks} ticks from tick ${exp.base_tick} (${exp.base_date}); both arms rolled back`,
        h('p', {style: 'font-size:15px', text: sm.headline}),
        h('p', {class: 'muted', text: 'Applied in the variant: ' + (exp.variant.applied.join('; ') || 'nothing')}),
        h('div', {class: 'chips'}, h('span', {class: 'chip', text: `baseline guardrails ${exp.baseline.guardrails_ok ? '✓' : '✕'}`}), h('span', {class: 'chip', text: `variant guardrails ${exp.variant.guardrails_ok ? '✓' : '✕'}`}),
          sm.payback_days ? h('span', {class: 'chip', text: `payback ≈ ${sm.payback_days} days`}) : null, sm.capex_delta ? h('span', {class: 'chip', text: `capex ${fmt.money(sm.capex_delta)}`}) : null)),
      h('div', {class: 'grid cols-2'},
        card('Baseline vs variant', 'green is better, red is worse, by each metric\'s own direction',
          h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Metric'}), h('th', {class: 'num', text: 'Baseline'}), h('th', {class: 'num', text: 'Variant'}), h('th', {class: 'num', text: 'Change'}))),
            h('tbody', {}, sm.metrics.map((row) => h('tr', {}, h('td', {text: row.label}), h('td', {class: 'num', text: val(row, row.baseline)}), h('td', {class: 'num', text: val(row, row.variant)}),
              h('td', {class: 'num ' + better(row), text: !isNum(row.delta) ? '—' : isPct(row.key) ? fmt.signed(row.delta, (v) => v.toFixed(1)) + ' pts'
                : fmt.signed(row.delta, (v) => val(row, v)) + (isNum(row.delta_pct) ? ` (${fmt.signed(row.delta_pct, (v) => v.toFixed(1))}%)` : '')}))))))),
        h('div', {class: 'grid'},
          card('Cumulative net income', 'baseline vs variant', lineChart({x, series: [{label: 'Baseline', values: cum(base, 'net_income')}, {label: 'Variant', values: cum(vari, 'net_income')}], tipFormat: fmt.money})),
          card('Cash', 'baseline vs variant', lineChart({x, series: [{label: 'Baseline', values: base.map((p) => p.cash)}, {label: 'Variant', values: vari.map((p) => p.cash)}], tipFormat: fmt.money})),
          card('Unit production cost', 'materials + absorbed conversion, per unit made in the horizon',
            h('div', {class: 'scroll-x'}, h('table', {}, h('thead', {}, h('tr', {}, ['Product', 'Baseline', 'Variant', 'Change', 'Units b / v'].map((t, i) => h('th', {class: i ? 'num' : '', text: t})))),
              h('tbody', {}, sm.unit_costs.map((u) => h('tr', {}, h('td', {text: u.sku}), h('td', {class: 'num', text: money2(u.baseline)}), h('td', {class: 'num', text: money2(u.variant)}),
                h('td', {class: 'num', text: isNum(u.delta) ? fmt.signed(u.delta, money2) : '—'}), h('td', {class: 'num', text: `${fmt.num(u.units_baseline)} / ${fmt.num(u.units_variant)}`})))))),
            h('h3', {style: 'margin-top:10px', text: 'Average utilization'}),
            h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {}, [...new Set([...Object.keys(sm.utilization.baseline), ...Object.keys(sm.utilization.variant)])].map((code) => h('tr', {},
              h('td', {text: code}), h('td', {class: 'num', text: 'baseline ' + fmt.ratio(sm.utilization.baseline[code])}), h('td', {class: 'num', text: 'variant ' + fmt.ratio(sm.utilization.variant[code])}))))))))));
  }

  // --- system map -------------------------------------------------------------------------------
  async function design() {
    const d = await ensureDesign(true);
    const root = h('div', {});
    const node = (title, lines) => h('div', {class: 'node'}, h('h3', {text: title}), lines.map((l) => h('div', {class: 'muted', style: 'font-size:12px', text: l})));
    root.append(card('How the sandbox fits together', 'agents decide; services execute; ledgers record; generators compile; guardrails check',
      h('div', {class: 'flowmap'},
        node('1 · Agents', ['market, customers, suppliers', 'sales, buyer, planner, plant manager', 'crews, machines, maintenance', 'controller', 'brain: heuristic or Claude (bounded, clamped, fallback)']),
        node('2 · Tick engine', ['phases in business-day order', 'seeded common random numbers', 'one transaction per tick', 'savepoint per action']),
        node('3 · ERP services', ['sales, purchasing, manufacturing', 'mrp.engine + convert_run', 'invoices.post_invoice / post_payment', 'journal.post_entry (idempotent)']),
        node('4 · Ledgers', ['journal (double entry)', 'stock moves (moving average)', 'audit log per agent user', 'payables + asset register']),
        node('5 · Compilation', ['report_generators: TB, BS, IS, GL', '4 subledger reconciliations', 'halt and roll back on imbalance', 'snapshots → this console']))));
    root.append(h('div', {style: 'margin-top:16px'}, card('Agent roles', 'the cast, in the order a tick runs them', h('div', {class: 'scroll-x'}, h('table', {},
      h('thead', {}, h('tr', {}, ['Role', 'Cadence', 'Decides (policy)', 'Does (actions)', 'Services called', 'Accounts moved', 'Agents', 'Decisions', 'Claude', 'Driven by parameters'].map((t) => h('th', {text: t})))),
      h('tbody', {}, d.roles.map((r) => h('tr', {}, h('td', {}, h('strong', {text: r.label}), h('br'), h('span', {class: 'muted', style: 'font-size:12px', text: r.summary})),
        h('td', {text: r.cadence}), h('td', {}, h('div', {class: 'chips'}, r.decides.map((x) => h('span', {class: 'chip', text: x})))), h('td', {class: 'wrap', text: r.acts.join('; ')}),
        h('td', {}, r.services.map((x) => h('div', {}, h('code', {text: x})))), h('td', {}, h('div', {class: 'chips'}, r.accounts.map((a) => h('button', {class: 'docref', type: 'button', onclick: () => openLedger(a), text: a})))),
        h('td', {class: 'num', text: r.agents}), h('td', {class: 'num', text: fmt.num(r.decisions)}), h('td', {class: 'num', text: fmt.num(r.claude_decisions)}),
        h('td', {}, h('div', {class: 'chips'}, r.parameters.map((p) => h('span', {class: 'chip', text: p}))))))))))));
    root.append(h('div', {class: 'grid cols-2', style: 'margin-top:16px'},
      card('Posting matrix', 'every business event, its double entry and how many times it has posted', h('div', {class: 'scroll-x'}, h('table', {},
        h('thead', {}, h('tr', {}, ['Event', 'Debit', 'Credit', 'Posted by', 'Count'].map((t, i) => h('th', {class: i === 4 ? 'num' : '', text: t})))),
        h('tbody', {}, d.postings.map((p) => h('tr', {}, h('td', {text: p.event}), h('td', {text: p.debit}), h('td', {text: p.credit}), h('td', {class: 'wrap'}, h('code', {text: p.by})), h('td', {class: 'num', text: fmt.num(p.count)}))))))),
      card('Invariants', 'live status of the rules that must never break', h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {}, d.invariants.map((i) => h('tr', {},
        h('td', {}, h('span', {class: 'pill ' + (i.ok ? 'good' : 'critical')}, h('span', {class: 'dot'}), h('span', {class: 'status-icon', 'aria-hidden': 'true', text: i.ok ? '✓' : '✕'}), i.ok ? 'holds' : 'broken')),
        h('td', {}, h('strong', {text: i.name}), h('br'), h('span', {class: 'muted', text: i.rule}))))))))));
    root.append(h('div', {class: 'grid cols-2', style: 'margin-top:16px'},
      card('Sandbox chart of accounts', 'codes the generators and agents use', h('div', {class: 'scroll-x'}, h('table', {}, h('tbody', {}, d.accounts.map((a) => h('tr', {},
        h('td', {}, h('button', {class: 'docref', type: 'button', onclick: () => openLedger(a.code), text: a.code})), h('td', {text: a.name}), h('td', {text: a.type}), h('td', {class: 'muted', text: a.key}))))))),
      card('API surface', 'everything here is an endpoint', h('ul', {}, [
        ['GET /api/simulation/scenarios/{id}/dashboard/', 'this cockpit'], ['POST …/advance/ {ticks}', 'run the clock'], ['GET …/financials/', 'statements via report generators'],
        ['GET …/ledger/?account=', 'general ledger'], ['GET …/trace/?reference=', 'decision → document → journal'], ['GET …/design/', 'this map'],
        ['PATCH /api/simulation/scenarios/{id}/ {parameters}', 'controls'], ['/api/simulation/lines/', 'commission, shifts, retire'], ['/api/simulation/agents/', 'brains and policies'],
        ['/api/simulation/decisions/', 'the decision log'], ['/api/simulation/experiments/', 'what-ifs']].map(([path, what]) => h('li', {}, h('code', {text: path}), ' — ', what))),
        h('p', {}, h('a', {href: '/swagger/#/simulation', text: 'Open the simulation API in Swagger'}), ' · ', h('a', {href: '/redoc/', text: 'ReDoc'})))));
    return root;
  }

  window.addEventListener('hashchange', () => { const tab = location.hash.slice(1); if (tab && tab !== store.tab) { store.tab = tab; renderTab(); } });
  boot();
})();
