/*
 * The common app shell: sign-in gate, role-filtered module navigation, user menu, breadcrumbs and global search.
 * Every shell page (the workspace at /app/, the simulation console at /console/) runs it; pages that need the
 * session wait for the `erp:ready` event and use window.ERPShell.
 */
import {$, api, auth, debounce, errorText, fill, h, pill, signIn, toast} from './core.js';

const PAGE = document.body.dataset.page || 'app';
const state = {registry: null, ready: false, navigate: null};

const shell = {
  api, auth, h, fill, pill, toast,
  get registry() { return state.registry; },
  get ready() { return state.ready; },
  resource(key) { return state.registry && state.registry.resources[key]; },
  can(key, mode = 'read') {
    const resource = shell.resource(key);
    return Boolean(resource && (resource.access[mode] || []).includes(auth.role()));
  },
  routeOf(key, id) {
    const resource = shell.resource(key);
    return resource ? resource.route + (id ? `${id}/` : '') : null;
  },
  go(href) {
    if (state.navigate && href.startsWith('/app/')) state.navigate(href);
    else window.location.href = href;
  },
  setCrumbs(items) { renderCrumbs(items); },
  setActiveNav(path) { markActive(path); },
  setNavigator(fn) { state.navigate = fn; },
  signOut,
};
window.ERPShell = shell;

// --- sign-in ---------------------------------------------------------------------------------------------------
function chromeVisible(visible) {
  $('erp-nav').hidden = !visible;
  $('erp-search').hidden = !visible;
  $('nav-toggle').hidden = !visible;
  $('erp-page').hidden = !visible;
  $('erp-login').hidden = visible;
}

/** The shared sign-in. `focus` moves focus into the form; only for an in-page sign-out or an expired session, so on
 *  page load the skip link stays the first Tab stop and screen readers start at the top. */
function showLogin(message, {focus = false} = {}) {
  state.ready = false;
  chromeVisible(false);
  fill($('erp-user'));
  fill($('erp-crumbs'));
  document.title = 'Sign in · DjangoERP';
  const error = h('p', {class: 'error', role: 'alert', text: message || ''});
  const form = h('form', {class: 'card', onsubmit: async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    error.textContent = '';
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      await signIn(data.get('username'), data.get('password'));
      await start();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }},
    h('h1', {text: 'Sign in to DjangoERP'}),
    h('p', {class: 'secondary', text: 'One sign-in for every module — finance, sales, purchasing, inventory, manufacturing, planning, master data and the simulation sandbox. What you see and can change follows your role, exactly as the API enforces it.'}),
    h('label', {class: 'field'}, h('span', {text: 'Username'}), h('input', {type: 'text', name: 'username', autocomplete: 'username', required: true})),
    h('label', {class: 'field'}, h('span', {text: 'Password'}), h('input', {type: 'password', name: 'password', autocomplete: 'current-password', required: true})),
    error,
    h('button', {class: 'btn primary', type: 'submit', text: 'Sign in'}),
    h('p', {class: 'muted', text: 'Need an account? An administrator creates one and gives it a role (admin, accountant, auditor or viewer); without a role every module stays closed.'}));
  fill($('erp-login'), h('div', {class: 'login'}, form));
  if (focus) form.querySelector('input[name=username]').focus();
}

function signOut(message) {
  auth.clear();
  try { sessionStorage.removeItem('erp.registry'); } catch (err) { console.warn('Could not clear the cached registry', err); }
  if (PAGE === 'app') window.history.replaceState(null, '', '/app/');
  showLogin(message || 'Signed out.', {focus: true});
}

window.addEventListener('erp:signed-out', (event) => showLogin(event.detail, {focus: true}));

// --- navigation ------------------------------------------------------------------------------------------------
function filterNav() {
  const role = auth.role();
  for (const link of $('erp-nav').querySelectorAll('a[data-read]')) {
    const allowed = (link.dataset.read || '').split(',').includes(role);
    link.closest('li').hidden = !allowed;
  }
  for (const group of $('erp-nav').querySelectorAll('.nav-group')) {
    group.hidden = !group.querySelector('li:not([hidden])');
  }
}

function markActive(path) {
  const links = [...$('erp-nav').querySelectorAll('a[href]')];
  const [pathname, query = ''] = path.split('?');
  let best = null;
  let bestScore = -1;
  for (const link of links) {
    const [lp, lq = ''] = link.getAttribute('href').split('?');
    let score = -1;
    if (lp === pathname && lq === query) score = 1000;
    else if (pathname.startsWith(lp) && lp !== '/app/' && (!lq || query.includes(lq))) score = lp.length + (lq ? 1 : 0);
    else if (lp === '/app/' && pathname === '/app/') score = 1;
    if (score > bestScore) { best = link; bestScore = score; }
  }
  for (const link of links) link.removeAttribute('aria-current');
  if (best && bestScore > 0) best.setAttribute('aria-current', 'page');
}

function renderCrumbs(items) {
  const list = h('ol', {}, items.map((item, i) => h('li', {},
    i === items.length - 1 || !item.href ? h('span', {'aria-current': i === items.length - 1 ? 'page' : null, text: item.label})
      : h('a', {href: item.href, text: item.label}))));
  fill($('erp-crumbs'), items.length ? list : null);
}

function renderUser() {
  const user = auth.user || {};
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username;
  fill($('erp-user'),
    h('span', {class: 'pill who'}, h('span', {class: 'dot', 'aria-hidden': 'true'}), `${name} · ${user.role || 'no role'}`),
    h('button', {class: 'btn small', type: 'button', onclick: () => signOut(), text: 'Sign out'}));
}

function wireNavToggle() {
  const toggle = $('nav-toggle');
  const close = () => { document.body.classList.remove('nav-open'); toggle.setAttribute('aria-expanded', 'false'); };
  toggle.onclick = () => {
    const open = document.body.classList.toggle('nav-open');
    toggle.setAttribute('aria-expanded', String(open));
  };
  $('erp-nav').addEventListener('click', (event) => { if (event.target.closest('a')) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
}

// --- global search ---------------------------------------------------------------------------------------------
function wireSearch() {
  const input = $('erp-search-input');
  const panel = $('erp-search-results');
  let active = -1;
  let seq = 0;
  const links = () => [...panel.querySelectorAll('a')];
  const close = () => { panel.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
  const highlight = (index) => {
    const items = links();
    active = Math.max(-1, Math.min(items.length - 1, index));
    items.forEach((a, i) => a.setAttribute('aria-selected', String(i === active)));
    if (items[active]) {
      items[active].scrollIntoView({block: 'nearest'});
      input.setAttribute('aria-activedescendant', items[active].id);
    }
  };
  const run = debounce(async () => {
    const term = input.value.trim();
    if (term.length < 2) return close();
    const mine = ++seq;
    try {
      const data = await api('/api/workspace/search/', {query: {search: term}});
      if (mine !== seq) return;
      const groups = {};
      for (const result of data.results) (groups[result.resource_label] = groups[result.resource_label] || []).push(result);
      let n = 0;
      fill(panel, data.results.length ? Object.entries(groups).map(([label, results]) => [
        h('div', {class: 'group', text: label}),
        results.map((r) => h('a', {id: `search-opt-${n++}`, role: 'option', href: shell.routeOf(r.resource, r.id), 'aria-selected': 'false',
          onclick: (event) => { event.preventDefault(); close(); input.value = ''; shell.go(shell.routeOf(r.resource, r.id)); }},
          h('span', {text: r.label}), r.status ? pill(r.status) : null)),
      ]) : h('div', {class: 'empty', text: `Nothing you can open matches “${term}”.`}));
      panel.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = -1;
    } catch (err) {
      fill(panel, h('div', {class: 'empty error', text: err.message}));
      panel.hidden = false;
    }
  }, 220);
  input.oninput = run;
  input.onkeydown = (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); highlight(active + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); highlight(active - 1); }
    else if (event.key === 'Enter') {
      event.preventDefault();
      const target = links()[active >= 0 ? active : 0];
      if (target) target.click();
    } else if (event.key === 'Escape') { close(); input.blur(); }
  };
  $('erp-search').onsubmit = (event) => event.preventDefault();
  document.addEventListener('click', (event) => { if (!$('erp-search').contains(event.target)) close(); });
  document.addEventListener('keydown', (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) { event.preventDefault(); input.focus(); }
  });
}

// --- boot ------------------------------------------------------------------------------------------------------
async function loadRegistry() {
  try {
    const cached = sessionStorage.getItem('erp.registry');
    if (cached) { state.registry = JSON.parse(cached); return; }
  } catch (err) {
    console.warn('Cached registry unreadable; fetching it', err);
  }
  state.registry = await api('/api/workspace/registry/');
  try { sessionStorage.setItem('erp.registry', JSON.stringify(state.registry)); } catch (err) { console.warn('Registry not cached', err); }
}

let wired = false;
async function start() {
  if (!auth.signedIn()) return showLogin();
  try {
    await loadRegistry();
  } catch (err) {
    if (err.status === 401) return showLogin('Your session expired; sign in again.', {focus: true});
    fill($('erp-login'), h('p', {class: 'notice critical', role: 'alert', text: 'The workspace could not load: ' + (errorText(err.data) || err.message)}));
    $('erp-login').hidden = false;
    return;
  }
  chromeVisible(true);
  renderUser();
  filterNav();
  if (!wired) { wireSearch(); wireNavToggle(); wired = true; }
  markActive(window.location.pathname + window.location.search);
  state.ready = true;
  if (PAGE === 'app') {
    const app = await import('./app.js');
    app.start(shell);
  }
  window.dispatchEvent(new CustomEvent('erp:ready', {detail: shell}));
}

start();
