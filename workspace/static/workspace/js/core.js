/*
 * DjangoERP workspace — shared toolkit: DOM helpers, formatting, status tones, the JWT session and the API client.
 * Every string from the server is inserted as text (textContent / createTextNode), never as HTML.
 */

export const $ = (id) => document.getElementById(id);

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function fill(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key === 'value' && 'value' in el) el.value = value;
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  return append(el, children);
}

const SVGNS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs, ...children) {
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

export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const toNum = (v) => (v === null || v === undefined || v === '' ? null : Number(v));

export const fmt = {
  number(v, digits = 0) {
    const n = toNum(v);
    return isNum(n) ? n.toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
  },
  money(v) { return fmt.number(v, 2); },
  quantity(v) {
    const n = toNum(v);
    return isNum(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 3}) : '—';
  },
  date(v) { return v ? String(v).slice(0, 10) : '—'; },
  datetime(v) {
    if (!v) return '—';
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
  },
  compact(v) {
    const n = toNum(v);
    if (!isNum(n)) return '—';
    const a = Math.abs(n);
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
    if (a >= 1e4) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1) + 'K';
    return Math.round(n).toLocaleString();
  },
};

// One vocabulary of status tones for every document: progress is info, done is good, partial is warning,
// abandoned is critical. A status always carries its label, so colour is never the only signal.
const TONES = {
  good: ['posted', 'done', 'paid', 'completed', 'received', 'shipped', 'invoiced', 'converted', 'applied', 'running'],
  info: ['confirmed', 'sent', 'in_progress', 'pending', 'planned', 'generating', 'ready'],
  warning: ['partially_received', 'partially_shipped', 'overdue', 'noop', 'down'],
  critical: ['cancelled', 'voided', 'failed', 'halted', 'rejected', 'retired'],
};
export function statusTone(status) {
  for (const [tone, list] of Object.entries(TONES)) if (list.includes(status)) return tone;
  return '';
}
export function pill(status, label) {
  if (!status) return null;
  return h('span', {class: 'pill ' + statusTone(status)}, h('span', {class: 'dot', 'aria-hidden': 'true'}), label || String(status).replace(/_/g, ' '));
}

export const today = () => new Date().toISOString().slice(0, 10);

export function debounce(fn, ms = 250) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// --- session ---------------------------------------------------------------------------------------------------
// Local storage so a document opened in a new tab is still signed in. Sign-out clears it; access tokens are short.
const KEYS = {access: 'erp.access', refresh: 'erp.refresh', user: 'erp.user'};
function read(key) { try { return localStorage.getItem(key); } catch (err) { return null; } }
function write(key, value) {
  try {
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch (err) {
    console.warn('Storage is blocked; the session lasts for this page only.', err);
  }
}
export const auth = {
  access: read(KEYS.access),
  refresh: read(KEYS.refresh),
  user: (() => { try { return JSON.parse(read(KEYS.user) || 'null'); } catch (err) { return null; } })(),
  save({access, refresh, user}) {
    if (access) { this.access = access; write(KEYS.access, access); }
    if (refresh) { this.refresh = refresh; write(KEYS.refresh, refresh); }
    if (user) { this.user = user; write(KEYS.user, JSON.stringify(user)); }
  },
  clear() {
    this.access = this.refresh = this.user = null;
    for (const key of Object.values(KEYS)) write(key, null);
  },
  role() { return (this.user && this.user.role) || 'none'; },
  signedIn() { return Boolean(this.access && this.user); },
};

export class ApiError extends Error {
  constructor(status, data, message) {
    super(message || `${status}`);
    this.status = status;
    this.data = data;
  }
}

export function errorText(data) {
  if (!data) return '';
  if (typeof data === 'string') return data;
  if (Array.isArray(data)) return data.map(errorText).filter(Boolean).join(' ');
  if (data.detail) return errorText(data.detail);
  return Object.entries(data).map(([k, v]) => (k === 'non_field_errors' ? '' : k + ': ') + errorText(v)).join(' · ');
}

async function refreshAccess() {
  try {
    const response = await fetch('/api/auth/refresh/', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({refresh: auth.refresh}),
    });
    if (!response.ok) return false;
    auth.save({access: (await response.json()).access});
    return true;
  } catch (err) {
    return false;
  }
}

export function toPath(url) {
  return String(url).replace(/^https?:\/\/[^/]+/, '');
}

export async function api(path, {method = 'GET', body, query, retry = true} = {}) {
  let url = toPath(path);
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') params.set(k, v);
    const qs = params.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }
  const headers = {'Accept': 'application/json'};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth.access) headers.Authorization = 'Bearer ' + auth.access;
  const response = await fetch(url, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
  if (response.status === 401 && retry && auth.refresh) {
    if (await refreshAccess()) return api(path, {method, body, query, retry: false});
    auth.clear();
    window.dispatchEvent(new CustomEvent('erp:signed-out', {detail: 'Your session expired; sign in again.'}));
    throw new ApiError(401, null, 'Session expired');
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data, errorText(data) || `${response.status} ${response.statusText}`);
  return data;
}

export async function signIn(username, password) {
  const response = await fetch('/api/auth/login/', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password}),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, body, errorText(body) || 'Sign-in failed');
  auth.save(body);
  return body.user;
}

export function toast(message, {tone = 'good', href, linkText} = {}) {
  const host = $('erp-toast');
  if (!host) return;
  const el = h('div', {class: 'toast' + (tone === 'error' ? ' error' : '')}, message,
    href ? [' ', h('a', {href, text: linkText || 'Open'})] : null);
  host.append(el);
  setTimeout(() => el.remove(), tone === 'error' ? 9000 : 5000);
}
