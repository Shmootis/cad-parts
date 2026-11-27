// CAD Parts app code (the page is index.html, the styles app.css)
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// Every request to the app's own server carries this run's key (the server puts it in the page), so a web page
// in your browser can't use the server. If the server restarted (new key), reload to pick up the new one.
const TOKEN = document.querySelector('meta[name="cad-token"]')?.content || '';
const rawFetch = window.fetch.bind(window);
let appReloads = false; // Restart Server in the app: the app window reloads itself (with the new key), so the page mustn't
window.fetch = async (url, opts = {}) => {
  const api = typeof url === 'string' && url.startsWith('/api/');
  if (api) opts = { ...opts, headers: { ...(opts.headers || {}), 'X-CAD-Token': TOKEN } };
  const r = await rawFetch(url, opts);
  if (api && r.status === 403 && !appReloads && Date.now() - (+sessionStorage.getItem('cad-key-reload') || 0) > 10000) {
    const j = await r.clone().json().catch(() => ({}));
    if (j.error === 'blocked: token') { sessionStorage.setItem('cad-key-reload', Date.now()); location.reload(); }
  }
  return r;
};
const beacon = (url, body) => navigator.sendBeacon(url + (url.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(TOKEN), body);
// Windows' folder picker: the app window shows it itself (fast, in front); in a browser or an older app the server does.
async function pickFolder(start = '') {
  const api = window.pywebview && window.pywebview.api;
  if (api && api.pick_folder) { try { return await api.pick_folder(start) || ''; } catch { /* fall back below */ } }
  toast('Choose the folder in the window that opened (it may be behind this one)');
  const r = await (await fetch('/api/pickfolder?start=' + encodeURIComponent(start), { method: 'POST' })).json().catch(() => ({}));
  return r.path || '';
}
async function pickImportFile() {
  const api = window.pywebview && window.pywebview.api;
  if (api && api.open_file) { try { return await api.open_file() || ''; } catch { /* fall back below */ } }
  toast('Choose the file in the window that opened (it may be behind this one)');
  const r = await (await fetch('/api/pickfile', { method: 'POST' })).json().catch(() => ({}));
  return r.path || '';
}
const $ = id => document.getElementById(id);
const GROUPS = [
  { label: 'Today',      max: 86400 },
  { label: 'This week',  max: 7 * 86400 },
  { label: 'This month', max: 30 * 86400 },
  { label: 'Older',      max: Infinity },
];
let data = null, sel = null, selFile = null;
// one-time cleanup of settings older versions kept in this browser (and thumbnails from before they were per profile)
if (!localStorage.getItem('cad-cleaned-v1')) {
  for (const k of ['cad-tagf', 'cad-draft', 'cad-chat', 'cad-rightw', 'cad-tab', 'cad-chat-hidden', 'cad-view', 'cad-vers']) localStorage.removeItem(k);
  for (const k of Object.keys(localStorage)) if (/^thumb:[^:]*$/.test(k)) localStorage.removeItem(k);
  localStorage.setItem('cad-cleaned-v1', '1');
}

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pretty = n => n.replace(/[-_]+/g, ' ');
const part = name => data.parts.find(p => p.name === name);
const allFiles = p => p.series.flatMap(s => s.files);
const byDate = () => [...data.parts].sort((a, b) => b.latest - a.latest);
function ago(t) {
  const s = data.now - t;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  const d = Math.round(s / 86400);
  return d === 1 ? 'yesterday' : d + ' days ago';
}
// Title Case for labels: every word capitalised, version numbers keep their small "v" (v26)
const tc = t => String(t).replace(/(^|[\s(/-])([a-z])/g, (m, a, c) => a + c.toUpperCase()).replace(/\bV(\d)/g, 'v$1');
const seriesLabel = (p, name) => name === p.name ? 'Main'
  : tc(pretty(name.startsWith(p.name + '_') ? name.slice(p.name.length + 1) : name));
const fileLabel = f => (f.version != null ? 'v' + f.version : tc(pretty(f.series.split('_').pop())))
  + (f.version != null && f.desc ? ' ' + tc(f.desc.length <= 14 ? f.desc : f.desc.slice(0, 13) + '…') : '');
const shortFile = (p, f) => { const s = seriesLabel(p, f.series); return (s === 'Main' ? '' : s + ' ') + fileLabel(f); };
const previewUrl = (p, f) => '/preview/' + encodeURIComponent(p.name) + '/' + encodeURIComponent(f.file);

function toast(msg, opt = {}) {
  const t = $('toast'); t.replaceChildren(Object.assign(document.createElement('span'), { textContent: msg }));
  if (opt.action) {
    const b = Object.assign(document.createElement('button'), { textContent: opt.action });
    b.onclick = () => { t.hidden = true; clearTimeout(toast.h); opt.onAction(); };
    t.append(b);
  }
  t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, opt.ms || 2600);
}
// server stopped: the desktop app can start it again (a browser can't)
$('offRestart').onclick = async () => {
  const api = window.pywebview && window.pywebview.api; if (!api) return;
  $('offRestart').disabled = true; $('offHint').textContent = 'Starting the server…';
  const ok = await api.start_server().catch(() => false);
  $('offRestart').disabled = false;
  // the app window reloads itself with the server's new key: a reload here would race it (and miss the key)
  if (ok) { appReloads = true; $('offHint').textContent = 'Reopening…'; } else $('offHint').textContent = 'Couldn\'t start it. Close and reopen the CAD Parts app.';
};
// "Are you sure?" in the app's own style. Resolves true (confirmed) or false (Cancel, Esc, click outside).
// One at a time: a second question waits until the open one is answered.
let askQueue = Promise.resolve();
function askConfirm(opts) {
  const p = askQueue.then(() => showAsk(opts));
  askQueue = p.catch(() => {});
  return p;
}
function showAsk({ title, text = '', ok = 'Delete', no = 'Cancel', danger = true }) {
  return new Promise(resolve => {
    const box = $('ask');
    $('askTitle').textContent = title; $('askText').textContent = text; $('askText').hidden = !text;
    $('askYes').textContent = ok; $('askNo').textContent = no; $('askNo').hidden = !no; $('askYes').className = danger ? 'dangerbtn' : 'primary';
    box.hidden = false; setTimeout(() => $('askYes').focus(), 0);
    const done = v => { box.hidden = true; document.removeEventListener('keydown', key, true); resolve(v); };
    const key = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
      // Enter presses the focused button (OK unless you tabbed to Cancel)
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); done(document.activeElement !== $('askNo')); }
    };
    document.addEventListener('keydown', key, true);
    $('askYes').onclick = () => done(true);
    $('askNo').onclick = () => done(false);
    box.onclick = e => { if (e.target === box) done(false); };
  });
}
async function postJSON(url, body) {
  try { return await (await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json(); }
  catch { return { ok: false }; }
}

// ---------- data ----------
// Each request is numbered: a slow older answer never replaces newer data. The 4 s poll skips while one is out.
load.seq = 0; load.applied = 0; load.inflight = 0; load.fails = 0;
async function load() {
  const my = ++load.seq;
  let d;
  load.inflight++;
  try { d = await (await fetch('/api/parts', { cache: 'no-store' })).json(); }
  catch { d = null; }
  finally { load.inflight--; }
  if (my < load.applied) return; // a newer answer is already on the page
  if (d && !d.setup && !Array.isArray(d.parts)) d = null; // an error answer (e.g. 403/500 JSON) isn't a parts list
  if (!d) {
    if (++load.fails >= 2) {
      $('offline').hidden = false;
      const api = window.pywebview && window.pywebview.api;
      $('offRestart').hidden = !(api && api.start_server);
      if ($('offRestart').hidden) $('offHint').textContent = 'Start it with Start CAD Parts.cmd in the app folder, or open the CAD Parts app.';
    }
    return;
  }
  load.applied = my; load.fails = 0; $('offline').hidden = true;
  if (d.setup) { location.reload(); return; } // the profile was removed elsewhere: back to setup
  hidePending(d); // don't flash back something that's being deleted right now
  const changed = !data || d.sig !== data.sig;
  const was = data && sel && part(sel), prevNewest = was && was.newest ? was.newest.file : null;
  data = d;
  if (changed) pruneThumbs();
  if (!load.migrated) { load.migrated = true; migrateOldBuild(); }
  const adopted = adoptDraft();
  if (sel === NEW) { /* still describing the new part: nothing saved yet */ }
  else if (!sel || !part(sel)) { if (data.parts.length) selectLatest(); else { sel = null; homeViewer.clear(''); } }
  else if (changed && !adopted) {
    // a new version of the part you have open: show it, if you were looking at its newest version
    const np = part(sel).newest;
    if (np && np.file !== prevNewest && (selFile === prevNewest || !selFile)) { selFile = np.file; loadModel({ animate: true }); } // plays the build-up
  }
  if (changed || adopted) renderAll();
}

// ---------- + New part ----------
// A draft card (no picture) sits at the top of the list while you describe the part in the chat.
// As soon as Claude saves the part's folder/model, the draft turns into that part: its chat moves over
// and the part is selected, so the build plays on it like any other edit.
const NEW = '+new';
let profId = ''; // set once the profile is known (below); keeps per-profile things in this window apart
let draftOpen = false;
const setDraft = on => { draftOpen = on; localStorage.setItem('cad-draft:' + profId, on ? '1' : ''); };
const chatKey = name => name === NEW ? 'new' : name ? 'part:' + name : null;
let curTurn = null; // { k }: the chat the running Claude turn writes to (re-keyed when a draft becomes a part)
function newPart() {
  setDraft(true);
  if ($('search').value) $('search').value = '';
  select(NEW); // the blank "New part" square is in the list, and selected
  setList(false); // straight to the viewer + chat, ready to describe the part
}
async function discardDraft() {
  if (busyKey === 'new') { toast('Claude is still working on it. Stop it first.'); return; }
  if (getChat('new').msgs.length && !await askConfirm({ title: 'Discard this new part?', text: 'Its chat is cleared too.', ok: 'Discard' })) return;
  store.new = { session: null, msgs: [] }; saveChat('new'); setDraft(false);
  if (sel === NEW) { sel = null; if (data.parts.length) selectLatest(); else homeViewer.clear(''); }
  renderAll();
}
function moveChat(c, name) { // give the draft's conversation to part `name`
  const k = 'part:' + name, old = getChat(k);
  for (const f of ['turnStart', 'startedAt', 'target', 'targetAt', 'buildFile']) delete c[f];
  if (old !== c && old.msgs.length) c.msgs = old.msgs.concat(c.msgs);
  store[k] = c; saveChat(k);
  store.new = { session: null, msgs: [] }; saveChat('new');
  return k;
}
function adoptDraft() {
  if (!draftOpen) return false;
  const c = getChat('new');
  if (!c.turnStart || !(busyKey === 'new' || Date.now() - lastTurnEnd < 15000)) return false;
  const p = data.parts.filter(x => x.latest >= c.turnStart - 1).sort((a, b) => b.latest - a.latest)[0];
  if (!p) return false;
  const k = moveChat(c, p.name);
  if (busyKey === 'new') busyKey = k;
  if (curTurn && curTurn.k === 'new') curTurn.k = k;
  setDraft(false);
  if (sel === NEW) { sel = p.name; selFile = p.newest ? p.newest.file : null; loadModel({ animate: true }); }
  return true;
}
function migrateOldBuild() { // a conversation left over from the old Build tab
  const c = getChat('new');
  if (draftOpen || !c.msgs.length) return;
  if (c.target && part(c.target)) moveChat(c, c.target); else setDraft(true);
}
const latestPart = () => data.parts.reduce((a, b) => (b.latest > a.latest ? b : a));
function selectLatest() {
  const p = latestPart(), nf = p.newest ? p.newest.file : null;
  if (p.name !== sel || nf !== selFile) { sel = p.name; selFile = nf; loadModel(); }
}
let selGen = 0; // counts switches to another part (a rename finishing late checks it)
function select(name, file) {
  const p = part(name);
  const f = file || (p && p.newest ? p.newest.file : null);
  const same = name === sel && f === selFile;
  if (name !== sel) { // another part: its menus and unsent pictures belonged to the old one
    selGen++;
    $('pmenu').hidden = true; $('owMenu').hidden = true;
    clearPending();
  }
  sel = name; selFile = f;
  if (!same) loadModel();
  renderAll();
}

// ---------- smart search ----------
// Every word you type must match somewhere: name, printer, version names/descriptions, notes.md, or
// that part's edit chat. Tolerates plurals, one-letter typos and a few workshop synonyms; ranks by
// where it matched (name > printer/versions > notes > chat).
const SYN = {
  pegboard: ['skadis'], ikea: ['skadis'], skadis: ['pegboard'], wall: ['skadis', 'pegboard', 'mount'],
  holder: ['rack', 'stand', 'mount', 'clip', 'hook'], rack: ['holder', 'stand'], stand: ['rack', 'holder'],
  mount: ['holder', 'bracket'], hook: ['clip', 'peg'], clip: ['hook'], bracket: ['mount'],
  paint: ['vallejo', 'tamiya', 'citadel'], bottle: ['paint', 'vallejo', 'tamiya'],
  figure: ['character', 'mini', 'figurine'], mini: ['miniature', 'figure', 'base'], miniature: ['mini', 'figure'],
  resin: ['photon', 'anycubic'], fdm: ['x2d', 'bambu'], bambu: ['x2d'], tool: ['dremel', 'caliper', 'sanding'],
  box: ['container', 'drawer', 'toolbox', 'tray'], drawer: ['box', 'tray'], tray: ['drawer', 'box'],
};
const norm = s => String(s || '').toLowerCase().replace(/[_\-./\\]+/g, ' ').replace(/[^a-z0-9× ]+/g, ' ');
const stem = w => w.length > 4 && /(ches|shes|xes|ses)$/.test(w) ? w.slice(0, -2) : w.length > 3 && w.endsWith('s') && !w.endsWith('ss') ? w.slice(0, -1) : w;
function within1(a, b) { // edit distance <= 1
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length > b.length) i++; else if (b.length > a.length) j++; else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}
const chatText = name => getChat('part:' + name).msgs
  .filter(m => m.role === 'user' || m.role === 'claude').map(m => m.text).join('\n');
function searchIndex(p) {
  const files = allFiles(p).map(f => `${f.series} ${f.desc} ${f.version != null ? 'v' + f.version : ''}`).join(' ');
  const raw = { name: p.name, category: p.category || '', printer: p.printer || '', files, notes: p.notes || '', chat: chatText(p.name) };
  const fields = {};
  for (const [k, v] of Object.entries(raw)) { const t = norm(v); fields[k] = { raw: v, text: t, words: [...new Set(t.split(/\s+/).filter(Boolean))] }; }
  return fields;
}
const WEIGHT = { name: 6, category: 5, printer: 3, files: 3, notes: 2, chat: 1.5 };
function termHit(term, f) {
  if (f.text.includes(term)) return 1;
  const st = stem(term);
  if (st !== term && f.text.includes(st)) return 0.9;
  if (term.length >= 4 && f.words.some(w => within1(term, w) || within1(st, stem(w)))) return 0.7;
  return 0;
}
function snippetFor(raw, term) {
  const flat = String(raw).replace(/\s+/g, ' '), i = flat.toLowerCase().indexOf(term);
  if (i < 0) return '';
  const a = Math.max(0, i - 30), b = Math.min(flat.length, i + term.length + 50);
  return (a ? '…' : '') + flat.slice(a, b).replace(/[#*`]/g, '').trim() + (b < flat.length ? '…' : '');
}
function searchParts(query, pool = data.parts) {
  const terms = norm(query).split(/\s+/).filter(Boolean);
  const out = [];
  for (const p of pool) {
    const fields = searchIndex(p);
    let score = 0, why = null, ok = true;
    for (const term of terms) {
      let best = 0, bestField = null;
      for (const [k, f] of Object.entries(fields)) {
        let h = termHit(term, f) * WEIGHT[k];
        for (const s of SYN[stem(term)] || SYN[term] || []) h = Math.max(h, termHit(s, f) * WEIGHT[k] * 0.6);
        if (h > best) { best = h; bestField = k; }
      }
      if (!best) { ok = false; break; }
      score += best;
      // explain matches that aren't obvious from the card (notes / chat / versions)
      if (!why && (bestField === 'notes' || bestField === 'chat' || bestField === 'files')) {
        const s = snippetFor(fields[bestField].raw, term) || snippetFor(fields[bestField].raw, stem(term));
        if (s) why = (bestField === 'chat' ? 'Chat: ' : bestField === 'files' ? 'Version: ' : '') + s;
      }
    }
    if (ok) out.push({ p, score: score + (p.latest / data.now) * 0.01, why });
  }
  return out.sort((a, b) => b.score - a.score);
}

// ---------- cards ----------
// Date (newest first, in groups), Name (A–Z) or Category (one group per category, newest first inside)
let sortMode = ['name', 'category'].includes(localStorage.getItem('cad-sort')) ? localStorage.getItem('cad-sort') : 'recent';
function paintSort() {
  $('sortLabel').textContent = { name: 'Name', category: 'Category' }[sortMode] || 'Date';
  $('sortMenu').querySelectorAll('button').forEach(b => b.classList.toggle('cur', b.dataset.v === sortMode));
}
paintSort();
$('sortBtn').onclick = e => { e.stopPropagation(); const open = $('sortMenu').hidden; closePanels(); $('sortMenu').hidden = !open; $('sortBtn').classList.toggle('on', open); };
$('sortMenu').onclick = e => {
  const v = e.target.closest('button')?.dataset.v; if (!v) return;
  sortMode = v; localStorage.setItem('cad-sort', v); paintSort();
  $('sortMenu').hidden = true; $('sortBtn').classList.remove('on'); renderCards();
};
document.addEventListener('click', e => { if (!$('sortMenu').hidden && !e.target.closest('.sortdd')) { $('sortMenu').hidden = true; $('sortBtn').classList.remove('on'); } });
function renderCards() {
  const q = $('search').value.trim();
  const pool = data.parts;
  let groups, why = {};
  if (q) {
    const res = searchParts(q, pool);
    res.forEach(r => { if (r.why) why[r.p.name] = r.why; });
    groups = [{ label: res.length ? 'Results' : 'No matches', parts: res.map(r => r.p) }];
  } else {
    const sorted = sortMode === 'name' ? [...pool].sort((a, b) => a.name.localeCompare(b.name)) : [...pool].sort((a, b) => b.latest - a.latest);
    const favs = sorted.filter(p => p.fav), rest = sorted.filter(p => !p.fav);
    if (sortMode === 'name') groups = [{ label: 'All parts', parts: rest }];
    else if (sortMode === 'category') {
      groups = (data.categories || []).map(c => ({ label: c, cat: c, parts: rest.filter(p => p.category === c) }));
      groups.push({ label: 'Uncategorized', parts: rest.filter(p => !p.category || !(data.categories || []).includes(p.category)) });
    } else {
      groups = GROUPS.map(g => ({ ...g, parts: [] }));
      for (const p of rest) groups.find(g => data.now - p.latest < g.max).parts.push(p);
    }
    (sortMode === 'category' ? groups[groups.length - 1] : groups[0]).draft = draftOpen;
    if (favs.length) groups.unshift({ label: 'Favorites', parts: favs });
  }
  const draftCard = `<div class="card draft ${sel === NEW ? 'sel' : ''}" data-n="${NEW}" tabindex="0">
        <div class="thumb"><button class="cdel" data-n="${NEW}" title="Discard New Part" aria-label="Discard new part"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6"/></svg></button></div>
        <div class="cbody"><div class="cname">New Part</div></div></div>`;
  $('cards').innerHTML = groups.filter(g => g.parts.length || q || g.draft).map(g => `
    <div class="group"><h3 ${g.cat ? `data-cat="${esc(g.cat)}"` : ''}><span class="glabel">${esc(g.label)}</span>${g.cat ? `<span class="catact"><button data-act="ren" title="Rename Category"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg></button><button data-act="del" title="Remove Category"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13"/></svg></button></span>` : ''}</h3><div class="grid">
    ${g.draft ? draftCard : ''}${g.parts.map(p => {
      const f = p.newest, th = f && thumbCached(p);
      return `<div class="card ${p.name === sel ? 'sel' : ''}" data-n="${esc(p.name)}" tabindex="0">
        <div class="thumb" data-t="${esc(p.name)}">
          ${th ? `<img src="${th}" alt="">` : f ? '<div class="ph loading"></div>' : '<div class="ph">No Model Yet<br>Notes Only</div>'}
          <button class="cfav ${p.fav ? 'on' : ''}" data-n="${esc(p.name)}" title="${p.fav ? 'Remove from Favorites' : 'Add to Favorites'}">${p.fav ? '★' : '☆'}</button>
          <button class="cdel" data-n="${esc(p.name)}" title="Delete Part" aria-label="Delete ${esc(pretty(p.name))}"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6"/></svg></button>
        </div>
        <div class="cbody">
          <div class="cname">${esc(pretty(p.name))}</div>
          ${why[p.name] ? `<div class="cwhy">${esc(why[p.name])}</div>` : ''}
        </div></div>`;
    }).join('')}</div></div>`).join('');
  $('cards').querySelectorAll('.card').forEach(c => {
    c.onclick = () => select(c.dataset.n);
    c.ondblclick = () => { select(c.dataset.n); setList(false); };
    c.onkeydown = e => { if (e.key === 'Enter') select(c.dataset.n); };
  });
  $('cards').querySelectorAll('.cdel').forEach(b => b.onclick = e => { e.stopPropagation(); b.dataset.n === NEW ? discardDraft() : deleteItem(b.dataset.n); });
  $('cards').querySelectorAll('.cfav').forEach(b => b.onclick = e => { e.stopPropagation(); toggleFav(b.dataset.n); });
  $('cards').querySelectorAll('h3[data-cat] .catact button').forEach(b => b.onclick = e => {
    e.stopPropagation();
    const cat = b.closest('h3').dataset.cat;
    if (b.dataset.act === 'ren') renameCategory(b.closest('h3'), cat); else removeCategory(cat);
  });
  queueThumbs();
}

// ---------- detail ----------
function md(t) {
  const inl = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const out = []; let ul = false;
  for (const raw of t.replace(/^﻿/, '').split(/\r?\n/)) {
    const li = raw.match(/^\s*[-*]\s+(.*)/);
    if (li) { if (!ul) { out.push('<ul>'); ul = true; } out.push('<li>' + inl(li[1]) + '</li>'); continue; }
    if (ul) { out.push('</ul>'); ul = false; }
    const h = raw.match(/^(#{1,4})\s+(.*)/);
    if (h) { const n = h[1].length + 2; out.push(`<h${n}>${inl(h[2])}</h${n}>`); continue; }
    if (raw.trim()) out.push('<p>' + inl(raw) + '</p>');
  }
  if (ul) out.push('</ul>');
  return out.join('');
}
function renderDetail() {
  const draft = sel === NEW, p = draft ? null : part(sel);
  $('tabDetails').hidden = draft;
  $('ein').placeholder = draft ? 'Describe a new part…' : 'What should change?';
  $('dMenu').hidden = $('owWrap').hidden = !p;
  $('dRename').hidden = !p || !!$('dName').querySelector('input');
  if (!p) { // the new part being described, or no part at all (e.g. the last one was just deleted)
    $('bSlicer').hidden = true;
    $('dName').textContent = draft ? 'New Part' : ''; $('dChips').innerHTML = ''; $('versions').innerHTML = '';
    renderDetail.part = draft ? NEW : null; return;
  }
  if (!$('dName').querySelector('input')) $('dName').textContent = pretty(p.name);
  if (!$('dChips').querySelector('input')) {
    $('dChips').innerHTML = (p.category ? `<button class="chip catchip" type="button" title="Change Category">${esc(p.category)}</button>` : '')
      + (p.other.length ? `<span class="chip" title="${esc(p.other.join(', '))}">${p.other.length} Other File${p.other.length > 1 ? 's' : ''}</span>` : '');
    const cc = $('dChips').querySelector('.catchip'); if (cc) cc.onclick = e => { e.stopPropagation(); openPmenu(true); };
  }
  const f = allFiles(p).find(x => x.file === selFile);
  renderOpenButtons(f);
  if (renderDetail.part !== p.name) showAllVers = false; // each part opens showing just its last 5
  // two lines: the pieces this part is made of (only when there's more than one), then the last 5 versions of the
  // piece you're looking at, oldest on the left, newest on the right. Clicking a piece shows its newest version.
  const chip = x => `<span class="vitem"><button class="vchip ${x.file === selFile ? 'cur' : ''}" data-f="${esc(x.file)}" title="${esc(x.file)} · ${ago(x.mtime)}">${esc(fileLabel(x))}</button><button class="vdel" data-f="${esc(x.file)}" title="Delete This Version">×</button></span>`;
  const newestOf = s => s.files[s.files.length - 1];
  const pieces = [...p.series].sort((a, b) => (b.name === p.name) - (a.name === p.name) || newestOf(b).mtime - newestOf(a).mtime);
  const curPiece = pieces.find(s => s.files.some(x => x.file === selFile)) || pieces[0];
  $('versions').innerHTML = pieces.length ? `<div class="vpanel"><div class="vhead">Versions</div>`
    + (pieces.length > 1 ? `<div class="vpieces">${pieces.map(s => `<button class="vpiece ${s === curPiece ? 'cur' : ''}" data-f="${esc(newestOf(s).file)}">${esc(seriesLabel(p, s.name))}</button>`).join('')}</div>` : '')
    + `<div class="vlast">${(() => { const all = curPiece.files, viewing = all.find(x => x.file === selFile);
      const last = all.slice(-5); // the one you're viewing always shows, even if it's older than the last 5
      return (showAllVers ? all : viewing && !last.includes(viewing) ? [viewing, ...last] : last).map(chip).join(''); })()}`
    + (curPiece.files.length > 5 ? `<button type="button" id="vAll" class="vhistbtn">${showAllVers ? 'Show Less' : `Show All (${curPiece.files.length})`}</button>` : '')
    + `</div></div>` : '';
  $('versions').querySelectorAll('.vpiece').forEach(bn => bn.onclick = () => select(p.name, bn.dataset.f));
  if ($('vAll')) $('vAll').onclick = () => { showAllVers = !showAllVers; renderDetail(); };
  $('versions').querySelectorAll('.vchip').forEach(bn => bn.onclick = () => select(p.name, bn.dataset.f));
  $('versions').querySelectorAll('.vdel').forEach(bn => bn.onclick = e => { e.stopPropagation(); deleteItem(p.name, bn.dataset.f); });
  renderDetail.part = p.name;
}

let showAllVers = false;

// Delete a whole part (folder) or one version's model file. Always to the Recycle Bin, so it can be restored.
// The page removes it at once; the server moves it to the Recycle Bin in the background (and the
// polled list keeps it hidden meanwhile). If the server can't, it comes back with a message.
const pendingDelete = new Set(); // "part" or "part/file"
function hidePending(d) {
  if (!pendingDelete.size) return d;
  d.parts = d.parts.filter(p => !pendingDelete.has(p.name));
  for (const p of d.parts) {
    p.series = p.series.map(s => ({ ...s, files: s.files.filter(f => !pendingDelete.has(p.name + '/' + f.file)) })).filter(s => s.files.length);
    if (p.newest && pendingDelete.has(p.name + '/' + p.newest.file)) {
      const tops = p.series.map(s => s.files[s.files.length - 1]);
      p.newest = tops.length ? tops.reduce((a, b) => (b.mtime > a.mtime ? b : a)) : null;
    }
  }
  return d;
}
// Delete = gone from the page at once, with an Undo for 6 s; only then does the server move it to the Recycle Bin.
const deleteTimers = {};
async function deleteItem(name, file) {
  const p = part(name); if (!p) return;
  const f = file && allFiles(p).find(x => x.file === file);
  const what = f ? `${shortFile(p, f)} of ${pretty(name)}` : pretty(name);
  const key = file ? name + '/' + file : name;
  if (deleteTimers[key]) return;
  const nice = pretty(name).replace(/\b\w/g, c => c.toUpperCase()); // as the cards show it
  const sure = await askConfirm({ title: `Delete ${f ? 'version ' + shortFile(p, f) : nice}?`,
    text: f ? `This version of ${nice} goes to the Recycle Bin.` : 'The part and all its versions go to the Recycle Bin.' });
  if (!sure || !part(name)) return;
  const wasSel = sel === name, wasFile = selFile;
  pendingDelete.add(key);
  hidePending(data); // gone from the page right now
  if (!file && name === sel) { sel = null; selFile = null; if (data.parts.length) selectLatest(); else homeViewer.clear(''); }
  if (file && name === sel && selFile === file) { selFile = part(name)?.newest?.file || null; loadModel(); }
  renderAll();
  const commit = async () => {
    delete deleteTimers[key];
    const r = await post(`/api/delete?path=${encodeURIComponent(key)}`);
    pendingDelete.delete(key);
    if (!r.ok) { toast('Couldn\'t delete: ' + (r.error || 'failed')); data.sig = ''; load(); } // bring it back
  };
  deleteTimers[key] = setTimeout(commit, 6000);
  toast(`Deleted ${what}`, { action: 'Undo', ms: 6000, onAction: async () => {
    clearTimeout(deleteTimers[key]); delete deleteTimers[key]; pendingDelete.delete(key);
    data.sig = ''; await load();
    if (wasSel && part(name)) select(name, file ? wasFile : null);
  } });
}
// closing the window during the Undo time still deletes (it was the last thing asked for)
addEventListener('pagehide', () => { for (const key of Object.keys(deleteTimers)) beacon('/api/delete?path=' + encodeURIComponent(key)); });

// ---------- favorites, rename, duplicate ----------
async function toggleFav(name) {
  const p = part(name); if (!p) return;
  p.fav = !p.fav; renderAll(); // at once; the server keeps it in state.json
  const r = await postJSON(`/api/meta?path=${encodeURIComponent(name)}`, { fav: p.fav });
  if (r.ok) data.sig = r.sig; else { toast('Couldn\'t save'); data.sig = ''; load(); }
}
function startRename() {
  const p = part(sel); if (!p || sel === NEW) return;
  const h = $('dName'), inp = Object.assign(document.createElement('input'), { className: 'dname-input', value: pretty(p.name) });
  h.replaceChildren(inp); inp.focus(); inp.select(); $('dRename').hidden = true;
  let done = false;
  const gen = selGen; // clicking another card ends the rename (blur): then stay on the part you clicked
  const finish = async ok => {
    if (done) return; done = true;
    const to = inp.value.trim();
    if (selGen === gen) { h.textContent = pretty(p.name); $('dRename').hidden = false; }
    else renderDetail(); // the title row now belongs to the part you clicked
    if (!ok || !to || to === pretty(p.name)) return;
    const r = await post(`/api/rename?path=${encodeURIComponent(p.name)}&to=${encodeURIComponent(to)}`);
    if (!r.ok) { toast('Couldn\'t rename: ' + (r.error || 'failed')); return; }
    // the chat file moved with the folder; keep the open conversation under the new name
    if (store['part:' + p.name]) { store['part:' + r.name] = store['part:' + p.name]; delete store['part:' + p.name]; }
    if (busyKey === 'part:' + p.name) busyKey = 'part:' + r.name;
    if (curTurn && curTurn.k === 'part:' + p.name) curTurn.k = 'part:' + r.name;
    const still = selGen === gen; // nothing else was selected meanwhile
    const keepFile = still && selFile && selFile.startsWith(p.name) ? r.name + selFile.slice(p.name.length) : selFile;
    if (still) { sel = r.name; selFile = keepFile; }
    data.sig = ''; await load();
    if (still && selGen === gen && part(r.name)) select(r.name, keepFile && allFiles(part(r.name)).some(x => x.file === keepFile) ? keepFile : null);
    toast('Renamed to ' + pretty(r.name));
  };
  inp.onkeydown = e => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') { e.stopPropagation(); finish(false); } };
  inp.onblur = () => finish(true);
}
async function duplicatePart() {
  const p = part(sel); if (!p || sel === NEW) return;
  const r = await post(`/api/duplicate?path=${encodeURIComponent(p.name)}`);
  if (!r.ok) { toast('Couldn\'t duplicate: ' + (r.error || 'failed')); return; }
  data.sig = ''; await load();
  if (part(r.name)) select(r.name);
  toast('Made ' + pretty(r.name));
}
$('dName').ondblclick = () => startRename();
$('dRename').onclick = () => startRename();
const MI = {'star': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
  'trash': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6"/></svg>',
  'gear': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  'folder': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>',
  'pencil': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>',
  'useradd': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="8" r="3.5"/><path d="M3.5 20a6.5 6.5 0 0 1 13 0M19 8v6M16 11h6"/></svg>',
  'userx': '<svg class="mic" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="8" r="3.5"/><path d="M3.5 20a6.5 6.5 0 0 1 13 0M16.5 9l5 5M21.5 9l-5 5"/></svg>'};
const mi = (icon, text) => `<span class="mi">${MI[icon]}${text}</span>`;
const PMENU_HTML = $('pmenu').innerHTML;
function openPmenu(cats) { // cats: straight to the category list
  closePanels();
  const m = $('pmenu');
  if (!cats) {
    m.innerHTML = PMENU_HTML;
    const p = part(sel), fb = m.querySelector('[data-a=fav]');
    if (fb) fb.innerHTML = mi('star', p && p.fav ? 'Remove from Favorites' : 'Add to Favorites');
    m.hidden = false; return;
  }
  const p = part(sel); if (!p) return;
  m.innerHTML = '<div class="ptitle">Category</div>'
    + (data.categories || []).map(c => `<button data-cat="${esc(c)}" class="${p.category === c ? 'cur' : ''}">${esc(c)}</button>`).join('')
    + `<button data-cat="" class="${p.category ? '' : 'cur'}">None</button>`
    + '<hr><div class="catnew"><input id="catNew" placeholder="New Category" maxlength="32" autocomplete="off"><button type="button" data-catnew="1">Add</button></div>';
  m.hidden = false;
  const inp = $('catNew');
  inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); setCategory(p.name, inp.value); m.hidden = true; } if (e.key === 'Escape') { e.stopPropagation(); m.hidden = true; } };
  setTimeout(() => (data.categories || []).length ? null : inp.focus(), 0);
}
$('dMenu').onclick = e => { e.stopPropagation(); if ($('pmenu').hidden) openPmenu(false); else $('pmenu').hidden = true; };
$('pmenu').onclick = e => {
  e.stopPropagation(); // the menu's content changes under the click: don't let it count as "clicked outside"
  const b = e.target.closest('button'); if (!b || b.closest('.catnew') && !b.dataset.catnew) return;
  if (b.dataset.a === 'cat') return openPmenu(true);
  if (b.dataset.a === 'fav') { $('pmenu').hidden = true; if (sel && sel !== NEW) toggleFav(sel); return; }
  if (b.dataset.catnew) { setCategory(sel, $('catNew').value); $('pmenu').hidden = true; return; }
  if (b.dataset.cat !== undefined) { setCategory(sel, b.dataset.cat); $('pmenu').hidden = true; return; }
  $('pmenu').hidden = true;
  if (b.dataset.a === 'dup') duplicatePart();
  else if (b.dataset.a === 'delete' && sel && sel !== NEW) deleteItem(sel);
};
async function setCategory(name, c) {
  const p = part(name); if (!p || name === NEW) return;
  c = (c || '').replace(/\s+/g, ' ').trim().slice(0, 32);
  const same = (data.categories || []).find(x => x.toLowerCase() === c.toLowerCase());
  p.category = same || c;
  if (p.category && !same) data.categories = [...(data.categories || []), p.category].sort((a, b) => a.localeCompare(b));
  renderAll();
  const r = await postJSON(`/api/meta?path=${encodeURIComponent(name)}`, { category: p.category });
  if (r.ok) { data.sig = ''; load(); } else { toast('Couldn\'t save'); data.sig = ''; load(); }
}
function renameCategory(h3, cat) {
  const lab = h3.querySelector('.glabel'), inp = Object.assign(document.createElement('input'), { className: 'cathin', value: cat, maxLength: 32 });
  lab.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const finish = async ok => {
    if (done) return; done = true;
    const to = inp.value.trim();
    if (!ok || !to || to === cat) return renderCards();
    const r = await postJSON('/api/category', { rename: cat, to });
    if (!r.ok) toast('Couldn\'t rename it');
    data.sig = ''; load();
  };
  inp.onkeydown = e => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') { e.stopPropagation(); finish(false); } };
  inp.onblur = () => finish(true);
  inp.onclick = e => e.stopPropagation();
}
async function removeCategory(cat) {
  const was = data.parts.filter(p => p.category === cat).map(p => p.name);
  const r = await postJSON('/api/category', { remove: cat });
  if (!r.ok) return toast('Couldn\'t remove it');
  data.sig = ''; await load();
  toast(`Removed the category ${cat}`, { action: 'Undo', ms: 6000, onAction: async () => {
    for (const n of was) await postJSON(`/api/meta?path=${encodeURIComponent(n)}`, { category: cat });
    data.sig = ''; load();
  } });
}
document.addEventListener('click', e => { if (!$('pmenu').hidden && !$('pmenu').contains(e.target)) $('pmenu').hidden = true; });

async function post(url) {
  try { return await (await fetch(url, { method: 'POST' })).json(); } catch { return { ok: false }; }
}

// ---------- Open with: default slicer, default 3D app, and every app found on this PC ----------
let apps = { apps: [], defaults: {} };
const appById = id => apps.apps.find(a => a.id === id);
async function loadApps() {
  try { apps = await (await fetch('/api/apps', { cache: 'no-store' })).json(); } catch { /* keep what we had */ }
  if (data && sel) renderDetail();
}
async function openWith(id) {
  if (!sel || sel === NEW || !selFile) return;
  const a = appById(id);
  const r = await post(`/api/open?app=${encodeURIComponent(id)}&path=${encodeURIComponent(sel + '/' + selFile)}`);
  toast(r.ok ? `Opening in ${a ? a.name : 'the app'}` : `${a ? a.name : 'Open'}: ${r.error || 'failed'}`);
}
// your default slicer (Settings), or the first slicer found on this PC
const slicerApp = () => appById(apps.defaults.slicer) || apps.apps.find(a => a.kind === 'slicer');
function renderOpenButtons(f) {
  $('bOpenWith').disabled = !f;
  const a = slicerApp(), b = $('bSlicer');
  b.hidden = !a || sel === NEW;
  if (!a) return;
  b.disabled = !f;
  b.innerHTML = `<img src="/api/appicon?id=${encodeURIComponent(a.id)}" alt="" onerror="this.style.display='none'">Open in Slicer`;
  b.title = 'Open in ' + a.name;
}
$('bSlicer').onclick = () => { const a = slicerApp(); if (a) openWith(a.id); };
// each app with its own icon; your default slicer and 3D app (Settings) come first in their group
function renderOwMenu() {
  const f = sel && part(sel) && allFiles(part(sel)).find(x => x.file === selFile);
  const item = a => {
    const off = a.id === 'blender' && f && f.ext !== '.stl';
    return `<button data-id="${esc(a.id)}" ${off ? 'disabled title="Blender opens STL files"' : ''}><span class="owapp">
      <img src="/api/appicon?id=${encodeURIComponent(a.id)}" alt="" onerror="this.style.visibility='hidden'">${esc(a.name)}</span></button>`;
  };
  const group = (title, kind) => {
    const list = apps.apps.filter(a => a.kind === kind).sort((a, b) => (b.id === apps.defaults[kind]) - (a.id === apps.defaults[kind]));
    return list.length ? `<div class="ptitle">${title}</div>` + list.map(item).join('') : '';
  };
  $('owMenu').innerHTML = group('Slicers', 'slicer') + group('3D Apps', 'modeler') + group('Other', 'other')
    + '<hr>' + item({ id: 'explorer', name: 'File Explorer' })
    + '<button data-id="+pick"><span class="owapp"><span class="owplus">+</span>Choose Another App…</span></button>';
}
$('bOpenWith').onclick = e => {
  e.stopPropagation();
  const open = $('owMenu').hidden; closePanels();
  if (open) { renderOwMenu(); $('owMenu').hidden = false; } // opens to the left of the button's right edge (app.css)
};
$('owMenu').onclick = async e => {
  const id = e.target.closest('button')?.dataset.id; if (!id) return;
  $('owMenu').hidden = true;
  if (id !== '+pick') return openWith(id);
  toast('Choose the app in the window that opened (it may be behind this one)');
  const r = await post('/api/apps/pick');
  if (!r.ok) return;
  apps = { apps: r.apps, defaults: r.defaults };
  openWith(r.id);
};
document.addEventListener('click', e => { if (!$('owMenu').hidden && !e.target.closest('.owdd')) $('owMenu').hidden = true; });
function fillDefaultAppSettings() {
  const list = apps.apps.filter(a => a.kind === 'slicer' || a.kind === 'other');
  $('sSlicer').innerHTML = '<option value="none">None</option>' + list.map(a => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
  $('sSlicer').value = apps.defaults.slicer || 'none';
}
async function saveDefaultApps() {
  const r = await postJSON('/api/apps/defaults', { slicer: $('sSlicer').value });
  if (r.ok) { apps = { apps: r.apps, defaults: r.defaults }; toast('Saved'); if (data && sel) renderDetail(); }
  else toast('Couldn\'t save');
}
$('sSlicer').onchange = saveDefaultApps;

// ---------- shared 3D helpers ----------
// Look: studio environment reflections + filmic tone mapping, crisp dark feature edges, soft contact shadow.
const mat = new THREE.MeshStandardMaterial({ color: '#c9c6bf', roughness: 0.5, metalness: 0.04, envMapIntensity: 0.6 });
const EDGE_COLOR = new THREE.Color('#34332f');
const edgeMat = new THREE.LineBasicMaterial({ color: EDGE_COLOR, transparent: true, opacity: 0.55 });
const SHARED = new Set([mat, edgeMat]); // never disposed
function setupRenderer(renderer, scene) {
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 0.95;
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture; pmrem.dispose();
}
function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2a28, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 1.35); key.position.set(1, 2, 1.4); scene.add(key);
  const rim = new THREE.DirectionalLight(0xbfd6ff, 0.45); rim.position.set(-1.4, 0.8, -1.2); scene.add(rim); // cool rim light
}
// feature edges (creases sharper than 28°) as thin dark lines; skipped for very heavy meshes
function edgesFor(geo) {
  if (geo.attributes.position.count > 900000) return null;
  return new THREE.LineSegments(new THREE.EdgesGeometry(geo, 28), edgeMat);
}
// soft radial "contact shadow" texture, shared
const shadowTex = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d');
  const r = g.createRadialGradient(64, 64, 4, 64, 64, 64);
  r.addColorStop(0, 'rgba(0,0,0,0.65)'); r.addColorStop(0.55, 'rgba(0,0,0,0.25)'); r.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = r; g.fillRect(0, 0, 128, 128); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
})();
function makeShadow() {
  const geo = new THREE.PlaneGeometry(1, 1); geo.rotateX(-Math.PI / 2);
  const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, depthWrite: false }));
  m.position.y = 0.05; m.renderOrder = -1; return m;
}
// Wraps a Z-up CAD mesh so it sits centred on the floor of a Y-up scene.
function placeOnFloor(geo) {
  const holder = new THREE.Group(), inner = new THREE.Group();
  inner.rotation.x = -Math.PI / 2;
  inner.add(new THREE.Mesh(geo, mat));
  const e = edgesFor(geo); if (e) inner.add(e);
  holder.add(inner); holder.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(holder), size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  holder.position.set(-c.x, -box.min.y, -c.z);
  return { holder, size, maxD: Math.max(size.x, size.y, size.z) || 10 };
}
// Camera distance that fits the model's bounding sphere in the view (k > 1 adds margin).
function homeView(size, fovDeg, aspect, k = 1.15) {
  const r = size.length() / 2, half = THREE.MathUtils.degToRad(fovDeg / 2);
  const fit = Math.min(half, Math.atan(Math.tan(half) * Math.min(aspect, 1)));
  const dist = r / Math.sin(fit) * k;
  const target = new THREE.Vector3(0, size.y / 2, 0);
  return { pos: new THREE.Vector3(0.62, 0.5, 0.78).normalize().multiplyScalar(dist).add(target), target };
}

// ---------- thumbnails (rendered once per version, kept in this browser) ----------
const TW = 480, TH = 360;
const tRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
tRenderer.setSize(TW, TH, false);
const tScene = new THREE.Scene(); addLights(tScene); setupRenderer(tRenderer, tScene);
const tCam = new THREE.PerspectiveCamera(32, TW / TH, 0.1, 20000);
const thumbKey = p => `4|${p.newest.file}|${p.newest.mtime}|${p.newest.size}`;
const thumbStoreKey = name => `thumb:${profId}:${name}`; // per profile: two profiles can have parts with the same name
const thumbMem = new Map(); // pictures that didn't fit in storage: still shown this session
function thumbCached(p) {
  const m = thumbMem.get(p.name); if (m && m.k === thumbKey(p)) return m.url;
  try { const v = JSON.parse(localStorage.getItem(thumbStoreKey(p.name)) || 'null'); return v && v.k === thumbKey(p) ? v.url : null; }
  catch { return null; }
}
function saveThumb(p, url) {
  const k = thumbStoreKey(p.name), v = JSON.stringify({ k: thumbKey(p), url });
  try { localStorage.setItem(k, v); return; } catch { /* storage full: below */ }
  // make room: drop other profiles' pictures, then try once more
  for (const sk of Object.keys(localStorage)) if (sk.startsWith('thumb:') && !sk.startsWith(`thumb:${profId}:`)) localStorage.removeItem(sk);
  try { localStorage.setItem(k, v); } catch { thumbMem.set(p.name, { k: thumbKey(p), url }); }
}
function pruneThumbs() { // pictures of this profile's parts that no longer exist
  const pre = `thumb:${profId}:`, names = new Set(data.parts.map(p => p.name));
  for (const sk of Object.keys(localStorage)) if (sk.startsWith(pre) && !names.has(sk.slice(pre.length))) localStorage.removeItem(sk);
}
const tQueue = []; let tBusy = false;
function queueThumbs() {
  for (const p of byDate()) if (p.newest && !thumbCached(p) && !tQueue.includes(p.name)) tQueue.push(p.name);
  pumpThumbs();
}
async function pumpThumbs() {
  if (tBusy) return; tBusy = true;
  while (tQueue.length) {
    const p = part(tQueue.shift()); if (!p || !p.newest || thumbCached(p)) continue;
    let url = null;
    try {
      const geo = new STLLoader().parse(await (await fetch(previewUrl(p, p.newest))).arrayBuffer());
      const { holder, size, maxD } = placeOnFloor(geo);
      tScene.add(holder);
      const h = homeView(size, tCam.fov, TW / TH, 1.02);
      tCam.position.copy(h.pos); tCam.near = maxD / 100; tCam.far = maxD * 100;
      tCam.lookAt(h.target); tCam.updateProjectionMatrix();
      tRenderer.render(tScene, tCam);
      url = tRenderer.domElement.toDataURL('image/webp', 0.85);
      tScene.remove(holder); geo.dispose();
      saveThumb(p, url);
    } catch { url = null; }
    const slot = document.querySelector(`.thumb[data-t="${CSS.escape(p.name)}"]`);
    if (slot) {
      const ph = slot.querySelector('.ph');
      if (url) { if (ph) ph.outerHTML = `<img src="${url}" alt="">`; }
      else if (ph) { ph.classList.remove('loading'); ph.textContent = 'No preview'; }
    }
    await new Promise(r => setTimeout(r, 30)); // let the page breathe between renders
  }
  tBusy = false;
}

// ---------- 3D viewers (one on Home, one in Build) ----------
const ACCENT = new THREE.Color('#3987e5');
const easeInOut = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
function makeViewer(el, emptyEl) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.localClippingEnabled = true; // for the grow-from-the-plate effects
  el.prepend(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color('#141413'); addLights(scene); setupRenderer(renderer, scene);
  const shadow = makeShadow(); shadow.visible = false; scene.add(shadow);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 20000);
  // ---- mechanical "rig" while Claude works: gimbal rings, a tick-marked dial, corner brackets locked onto the
  //      part, a laser scan frame and a reticle, all drawn in thin blue lines like the grid and edges ----
  const hud = (() => {
    const g = new THREE.Group(); g.visible = false; scene.add(g);
    const mk = (hex, op) => { const m = new THREE.LineBasicMaterial({ color: new THREE.Color(hex), transparent: true, opacity: op, depthWrite: false }); m.userData.base = op; return m; };
    const mats = { main: mk('#3987e5', .7), dim: mk('#3987e5', .32), faint: mk('#3987e5', .18), hot: mk('#8cc0ff', .75) };
    return { g, mats, P: null, opacity: 0, target: 0, lock: null };
  })();
  const lineGeo = pts => { const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3)); return bg; };
  function ringGeo(r, dashes, fill) { // dashed circle in the XZ plane (dashes=1, fill=1: solid)
    const pts = [], seg = Math.max(4, Math.round(96 / dashes));
    for (let d = 0; d < dashes; d++) {
      const a0 = d / dashes * Math.PI * 2, a1 = a0 + fill * Math.PI * 2 / dashes;
      for (let s = 0; s < seg; s++) {
        const t0 = a0 + (a1 - a0) * s / seg, t1 = a0 + (a1 - a0) * (s + 1) / seg;
        pts.push(Math.cos(t0) * r, 0, Math.sin(t0) * r, Math.cos(t1) * r, 0, Math.sin(t1) * r);
      }
    }
    return lineGeo(pts);
  }
  function ticksGeo(r, n, len, majorEvery) { // dial tick marks, longer every `majorEvery`
    const pts = [];
    for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2, L = i % majorEvery === 0 ? len * 2.4 : len;
      pts.push(Math.cos(a) * r, 0, Math.sin(a) * r, Math.cos(a) * (r + L), 0, Math.sin(a) * (r + L)); }
    return lineGeo(pts);
  }
  function bracketsGeo(min, max, len) { // CAD-style selection corners on the bounding box
    const pts = [];
    for (const x of [min.x, max.x]) for (const y of [min.y, max.y]) for (const z of [min.z, max.z]) {
      const sx = x === min.x ? 1 : -1, sy = y === min.y ? 1 : -1, sz = z === min.z ? 1 : -1;
      pts.push(x, y, z, x + sx * len, y, z, x, y, z, x, y + sy * len, z, x, y, z, x, y, z + sz * len);
    }
    return lineGeo(pts);
  }
  function rectGeo(sx, sz, notch) { // scan frame: a rectangle with small inward notches at the mid-points
    const x = sx / 2, z = sz / 2, n = notch;
    return lineGeo([-x, 0, -z, x, 0, -z, x, 0, -z, x, 0, z, x, 0, z, -x, 0, z, -x, 0, z, -x, 0, -z,
      0, 0, -z, 0, 0, -z + n, 0, 0, z, 0, 0, z - n, -x, 0, 0, -x + n, 0, 0, x, 0, 0, x - n, 0, 0]);
  }
  function reticleGeo(r) { // circle + cross-hair with a gap in the middle
    const pts = [];
    for (let i = 0; i < 48; i++) { const a = i / 48 * Math.PI * 2, b = (i + 1) / 48 * Math.PI * 2; pts.push(Math.cos(a) * r, 0, Math.sin(a) * r, Math.cos(b) * r, 0, Math.sin(b) * r); }
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) pts.push(dx * r * .35, 0, dz * r * .35, dx * r * 1.4, 0, dz * r * 1.4);
    return lineGeo(pts);
  }
  function buildHud() { // (re)build the rig around the part on screen (or the empty plate)
    hud.g.children.slice().forEach(c => { hud.g.remove(c); c.geometry.dispose(); });
    const s = cur ? cur.size : new THREE.Vector3(60, 40, 60), M = hud.mats;
    const R = Math.max(s.x, s.z) * .62 + 8, H = Math.max(s.y, 10), pad = Math.max(2, R * .04);
    const add = (geo, m, y = 0) => { const l = new THREE.LineSegments(geo, m); l.position.y = y; l.renderOrder = 4; hud.g.add(l); return l; };
    const min = new THREE.Vector3(-s.x / 2 - pad, 0, -s.z / 2 - pad), max = new THREE.Vector3(s.x / 2 + pad, s.y + pad, s.z / 2 + pad);
    hud.P = {
      brackets: add(bracketsGeo(min, max, Math.max(3, Math.min(s.x, s.y, s.z) * .2)), M.main),
      scan: add(rectGeo(s.x + pad * 6, s.z + pad * 6, Math.max(2, R * .08)), M.hot),
    };
    hud.top = max.y;
  }
  const WHITE = new THREE.Color('#e6f2ff');
  function tickHud(now) {
    hud.opacity += (hud.target - hud.opacity) * .08;
    hud.g.visible = hud.opacity > .01 && !!hud.P;
    if (!hud.g.visible) return;
    const t = now / 1000, P = hud.P, M = hud.mats;
    // lock-on when a new version lands: rings contract and snap in, brackets and scan flash
    let k = 1, flash = 0;
    if (hud.lock) {
      const l = (now - hud.lock) / 1000;
      k = l < .45 ? 1 - .3 * easeInOut(l / .45) : l < 1.3 ? .7 + .3 * easeInOut((l - .45) / .85) : 1;
      flash = l < 1 ? 1 - l : 0;
      if (l >= 1.3) hud.lock = null;
    }
    P.scan.position.y = (.5 - .5 * Math.cos(t * .9)) * hud.top;  // laser frame sweeps up and down
    const pulse = .9 + .1 * Math.sin(t * 1.5);
    for (const m of Object.values(M)) m.opacity = Math.min(1, m.userData.base * hud.opacity * (m === M.main ? pulse : 1) + flash * .6 * hud.opacity);
    M.main.color.copy(ACCENT).lerp(WHITE, flash * .8); M.hot.color.set('#9ccbff').lerp(WHITE, flash);
  }
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  let holder = null, grid = null, home = null, token = 0, shown = undefined, cur = null; // cur = {size, maxD}
  let curPart = null;  // which part is on screen: new versions of the SAME part never move the camera
  let reveal = null;   // new version rising out of the plate: {t0, dur, h, plane, layer, ghost}
  let scan = null;     // soft layer sweeping while Claude works: {mesh, t0}
  let busyLabel = null;
  let finishedThisTurn = false; // the arrival finish already played during the current build
  const status = document.createElement('div'); status.className = 'vstatus'; status.hidden = true; el.appendChild(status);
  new ResizeObserver(() => {
    const r = el.getBoundingClientRect(); if (!r.width) return;
    renderer.setSize(r.width, r.height, false); camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
  }).observe(el);

  // a translucent accent "layer" plate (fill + glowing outline), used by the reveal and the scan
  function layerMesh(size, opacity) {
    const g = new THREE.Group();
    const geo = new THREE.PlaneGeometry(size.x * 1.12 + 2, size.z * 1.12 + 2); geo.rotateX(-Math.PI / 2);
    g.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending })));
    g.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: Math.min(1, opacity * 4) })));
    return g;
  }
  const disposeTree = o => o.traverse(x => { x.geometry && x.geometry.dispose(); if (x.material && !SHARED.has(x.material)) x.material.dispose(); });
  const setMeshMat = (h, m) => h.traverse(x => x.isMesh && (x.material = m));
  const setEdgeMat = (h, m) => h.traverse(x => x.isLineSegments && (x.material = m));
  function placeShadow(size) { shadow.scale.set(size.x * 1.7 + 8, 1, size.z * 1.7 + 8); shadow.visible = true; }
  function setStatus(html) { status.hidden = !html; if (html) status.innerHTML = html; }
  const plain = s => s ? esc(s) : '';

  function tick(now) {
    tickHud(now); // the mechanical rig (gimbal rings, brackets, scan frame) while Claude works
    // finish: a square layer frame sweeps once from the top down to the plate (old version fading out),
    // then the new part builds up from the base to the top behind it
    if (reveal) {
      const el = now - reveal.t0, A = reveal.down, top = reveal.h * 1.02;
      if (el < A) {
        const d = easeInOut(el / A);
        reveal.layer.position.y = top * (1 - d); reveal.plane.constant = 0;
        reveal.layer.children.forEach(c => c.material.opacity = c.isLineSegments ? .8 : .12);
        if (reveal.ghost) reveal.ghost.traverse(x => x.material && !SHARED.has(x.material) && (x.material.opacity = (x.isLineSegments ? .5 : .9) * (1 - d)));
      } else {
        const t = Math.min(1, (el - A) / reveal.up), e = easeInOut(t);
        if (reveal.ghost) { scene.remove(reveal.ghost); disposeTree(reveal.ghost); reveal.ghost = null; }
        reveal.plane.constant = e * top; reveal.layer.position.y = e * top;
        reveal.layer.children.forEach(c => c.material.opacity = (c.isLineSegments ? .8 : .12) * (t < .88 ? 1 : (1 - t) / .12));
        reveal.edgeMat.color.copy(ACCENT).lerp(EDGE_COLOR, e); reveal.edgeMat.opacity = .9 - .35 * e;
        if (t >= 1) {
          scene.remove(reveal.layer); disposeTree(reveal.layer);
          if (holder) { setMeshMat(holder, mat); setEdgeMat(holder, edgeMat); }
          reveal.mat.dispose(); reveal.edgeMat.dispose(); reveal = null;
        }
      }
    }
    if (scan && scan.mesh) { // the generic sweep
      const h = cur ? cur.size.y : 40, ph = ((now - scan.t0) / 2600) % 2, u = ph < 1 ? ph : 2 - ph; // up and down
      scan.mesh.position.y = easeInOut(u) * h * 1.05;
    }
  }

  renderer.setAnimationLoop(now => { if (!el.offsetParent) return; tick(now); controls.update(); renderer.render(scene, camera); });
  const empty = msg => { emptyEl.textContent = msg; emptyEl.hidden = !msg; };
  function clearModel(keepHolder) {
    if (reveal) {
      if (reveal.ghost) { scene.remove(reveal.ghost); disposeTree(reveal.ghost); }
      if (reveal.layer) { scene.remove(reveal.layer); disposeTree(reveal.layer); }
      reveal.mat.dispose(); reveal.edgeMat && reveal.edgeMat.dispose(); reveal = null;
    }
    if (holder && !keepHolder) { scene.remove(holder); disposeTree(holder); }
    holder = null; shadow.visible = false;
    if (grid) { scene.remove(grid); grid.geometry.dispose(); grid = null; }
  }
  function setStage(size, maxD) { // grid, shadow and depth range for a part of this size (camera untouched)
    const gs = Math.max(50, Math.ceil(maxD * 1.6 / 10) * 10);
    grid = new THREE.GridHelper(gs, gs / 10, 0x3d3d3a, 0x262624); grid.material.transparent = true; grid.material.opacity = .8; scene.add(grid);
    placeShadow(size);
    home = homeView(size, camera.fov, camera.aspect, 1.25);
    camera.near = maxD / 100; camera.far = maxD * 100; camera.updateProjectionMatrix();
  }
  function reset() { if (!home) return; camera.position.copy(home.pos); controls.target.copy(home.target); controls.update(); }
  function restartScan() {
    if (!scan) return;
    if (scan.mesh) { scene.remove(scan.mesh); disposeTree(scan.mesh); }
    const s = cur ? cur.size : new THREE.Vector3(60, 40, 60); // a bit wider than the part so its edge shows
    scan.mesh = layerMesh(new THREE.Vector3(s.x * 1.25 + 6, s.y, s.z * 1.25 + 6), .08); scene.add(scan.mesh);
  }
  return {
    reset,
    // label = what Claude is doing right now (null when idle)
    // label: Claude's current step for the status pill (null when idle).
    // rig: true once Claude is actually modelling (writing/running a part script). Thinking or asking you
    // questions only shows the pill, so the rig never starts and then stops for a clarifying question.
    setBusy(label, rig = false) {
      busyLabel = label;
      setStatus(label ? plain(label) : '');
      if (!label) finishedThisTurn = false;           // the build is over: the next one may play its finish again
      const on = !!label && rig && !finishedThisTurn;  // once the part has arrived, the rig stays off for this build
      if (on && !hud.target) buildHud();
      hud.target = on ? 1 : 0;
    },
    clear(msg = '') {
      if (shown === null && emptyEl.textContent === msg) return;
      shown = null; curPart = null; token++; clearModel(); empty(msg); cur = null; restartScan();
      // an empty build plate, so the stage doesn't look broken before the first version exists
      grid = new THREE.GridHelper(200, 20, 0x3d3d3a, 0x262624); scene.add(grid);
      home = { pos: new THREE.Vector3(150, 120, 190), target: new THREE.Vector3(0, 0, 0) };
      camera.near = 1; camera.far = 5000; camera.updateProjectionMatrix(); reset();
    },
    // animate: true when a NEW version of a part arrives. The camera only moves when a different part is opened.
    async show(p, f, { animate = false } = {}) {
      const key = `${p.name}/${f.file}|${f.mtime}`;
      if (key === shown) return;
      const samePart = !!cur && curPart === p.name;
      // the finish plays once per build: further versions saved in the same build swap in quietly
      const quiet = animate && finishedThisTurn && samePart;
      if (quiet) animate = false;
      shown = key; const my = ++token;
      if (!animate && !quiet) { clearModel(); empty('Loading…'); }
      let geo;
      try { geo = await new STLLoader().loadAsync(previewUrl(p, f)); }
      catch { if (my === token) empty("Couldn't load this file"); return; }
      if (my !== token) { geo.dispose(); return; }
      empty('');
      // the version on screen fades away while the new one materializes in its place
      let ghost = null;
      if (animate && holder) {
        ghost = holder;
        ghost.traverse(x => {
          if (x.isMesh) { const m = mat.clone(); m.transparent = true; m.opacity = .9; m.depthWrite = false; x.material = m; }
          if (x.isLineSegments) x.material = new THREE.LineBasicMaterial({ color: EDGE_COLOR, transparent: true, opacity: .5 });
        });
      }
      clearModel(!!ghost);
      if (ghost) scene.add(ghost);
      const placed = placeOnFloor(geo); holder = placed.holder; scene.add(holder);
      const { size, maxD } = placed; cur = { size, maxD }; curPart = p.name;
      setStage(size, maxD);
      if (!(samePart && (animate || quiet))) reset(); // same part, new version: leave the view exactly as it is
      if (scan) restartScan();
      if (hud.target) buildHud(); // re-fit the rig to this version
      if (animate) {
        // finish: the rig switches off, a square frame sweeps top → plate, then the part builds base → top
        finishedThisTurn = !!busyLabel; // inside a build: don't play it again for later saves in this build
        hud.target = 0; hud.opacity = 0;
        const plane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0); // keeps everything below the layer
        const m = mat.clone(); m.clippingPlanes = [plane]; setMeshMat(holder, m); // outer faces only: a clean cut edge, no noisy insides
        const em = new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: .9, clippingPlanes: [plane] }); setEdgeMat(holder, em);
        const layer = layerMesh(size, .12); layer.position.y = size.y; scene.add(layer);
        const up = Math.max(1000, Math.min(2200, 800 + size.y * 8)); // taller parts take a little longer
        reveal = { t0: performance.now(), down: 650, up, h: size.y, plane, layer, ghost, mat: m, edgeMat: em };
      }
    },
  };
}
const homeViewer = makeViewer($('viewer'), $('vEmpty'));
function loadModel(opts) {
  const p = part(sel), f = p && allFiles(p).find(x => x.file === selFile);
  if (f) homeViewer.show(p, f, opts); else homeViewer.clear(p ? 'No Model Yet' : '');
}
$('reset').onclick = () => homeViewer.reset();

function renderAll() {
  if (!data) return;
  renderDetail(); renderCards(); renderChats();
}


// ---------- chats (Claude Code running in cad-parts, via the local server) ----------
// Each conversation is saved on disk by the server: <part>\\chat.json (hidden) for a part, .cadparts\\chats\\new.json in the parts folder
// for the new part being described. So chats sync with OneDrive and are the same in Firefox and the app.
// The server runs one Claude turn at a time.
// ---------- setup checklist: what this PC needs for the app to work (Claude Code + sign-in, Git) ----------
// On a PC that hasn't passed it yet, this runs before anything else is drawn, so the home screen never flashes
// behind it. The CAD engine (Python + build123d) installs itself; it's only mentioned while it's busy or if it failed.
let health = null, healthPoll = null, checksResolve = null;
const chk = id => (health && health.checks.find(c => c.id === id)) || {};
const coreReady = () => !!(chk('claude').ok && chk('signin').ok && chk('git').ok);
async function fetchHealth(force) {
  try { health = await (await fetch('/api/health' + (force ? '?force=1' : ''), { cache: 'no-store' })).json(); }
  catch { return null; }
  if (coreReady()) localStorage.setItem('cad-setup-ok', '1');
  $('sHealthInfo').textContent = coreReady() ? 'All set' : 'Something needs attention';
  if (data) renderDetail();
  return health;
}
const ICON = {
  ok: '✓',
  install: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>',
  signin: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c1.2-3.4 3.8-5 7-5s5.8 1.6 7 5"/></svg>',
};
const job = id => ((health && health.jobs) || {})[id] || null;
// Two steps, one after the other: 1) install Claude Code and Git (names and bars only), 2) sign in to Claude.
// Only what needs attention gets a line of text (a failed install, the code box while signing in).
const installed = () => !!(chk('claude').ok && chk('git').ok);
let autoClose = false; // first start: once everything's ready, move straight on (no extra click)
function toolCard(id, name) {
  const c = chk(id), j = job(id);
  if (c.ok) return `<div class="citem ok"><span class="ci">${ICON.ok}</span><span class="ct"><b>${name}</b></span></div>`;
  if (j && j.state === 'failed') return `<div class="citem todo"><span class="ci">${ICON.install}</span>
    <span class="ct"><b>${name}</b><small title="${esc(j.error || '')}">Couldn't Install</small></span>
    <button type="button" class="primary" data-what="${id}">Try Again</button></div>`;
  return `<div class="citem busy"><span class="ci">${ICON.install}</span><span class="ct"><b>${name}</b></span>
    <div class="cbar"><i style="width:${Math.round(((j && j.pct) || 0) * 100)}%"></i></div></div>`;
}
function renderChecks() {
  if (!health) return;
  const typed = $('chkCode') ? $('chkCode').value : '', typing = document.activeElement && document.activeElement.id === 'chkCode';
  $('chkNote').hidden = true;
  const stage = !installed() ? 'install' : !chk('signin').ok ? 'signin' : 'done';
  const js = job('signin');
  if (stage === 'install') {
    $('chkTitle').textContent = 'Getting CAD Parts Ready';
    $('chkSub').textContent = 'Installing the tools CAD Parts needs.';
    $('chkList').innerHTML = toolCard('claude', 'Claude Code') + toolCard('git', 'Git for Windows');
  } else if (stage === 'signin') {
    $('chkTitle').textContent = 'Sign In to Claude';
    $('chkSub').textContent = 'CAD Parts uses your own Claude account.';
    $('chkList').innerHTML = js && js.state === 'running'
      ? `<div class="citem wide busy"><span class="ci">${ICON.signin}</span><span class="ct"><small>${esc(js.step && js.step !== 'Finish signing in in your browser' ? js.step : 'Finish signing in in your browser')}</small></span>
          <div class="cpaste"><input id="chkCode" placeholder="Got a code? Paste it here" autocomplete="off" spellcheck="false"><button type="button" class="primary" id="chkCodeGo">Finish</button></div>
          ${js.url ? '<button type="button" class="clink" data-what="signin-page">Open the Sign-In Page Again</button>' : ''}</div>`
      : `<div class="citem wide">${js && js.state === 'failed' ? `<span class="ct"><small>${esc(js.error || 'Sign-in didn\'t finish')}</small></span>` : ''}
          <button type="button" class="primary bigbtn" data-what="signin">Sign In</button></div>`;
  } else {
    $('chkTitle').textContent = 'You\'re All Set';
    $('chkSub').textContent = '';
    $('chkList').innerHTML = toolCard('claude', 'Claude Code') + toolCard('git', 'Git for Windows');
  }
  $('chkSub').hidden = !$('chkSub').textContent;
  $('chkDoneRow').hidden = stage !== 'done' || autoClose;
  $('chkList').querySelectorAll('button[data-what]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const r = await post('/api/setup?what=' + encodeURIComponent(b.dataset.what));
    if (!r.ok) { $('chkNote').hidden = false; $('chkNote').textContent = 'Couldn\'t start that: ' + (r.error || 'failed'); return; }
    await fetchHealth(true); renderChecks();
  });
  if ($('chkCode')) {
    $('chkCode').value = typed; if (typing) $('chkCode').focus();
    const send = async () => {
      const code = $('chkCode').value.trim(); if (!code) return;
      $('chkCodeGo').disabled = true;
      const r = await post('/api/setup?what=signin-code&code=' + encodeURIComponent(code));
      if (!r.ok) { $('chkNote').hidden = false; $('chkNote').textContent = r.error || 'That didn\'t work'; $('chkCodeGo').disabled = false; return; }
      $('chkCode').value = ''; await fetchHealth(true); renderChecks();
    };
    $('chkCodeGo').onclick = send;
    $('chkCode').onkeydown = e => { if (e.key === 'Enter') send(); };
  }
  const e = chk('build123d'); // the CAD engine installs itself; it's only mentioned if it failed
  $('chkEngine').hidden = !(e.id && !e.ok && !e.busy);
  $('chkEngine').innerHTML = $('chkEngine').hidden ? '' : 'The CAD engine didn\'t start.<button type="button" id="chkEngineRetry">Try Again</button>';
  const retry = $('chkEngineRetry'); if (retry) retry.onclick = async () => { await post('/api/setup?what=build123d'); fetchHealth(true).then(renderChecks); };
  if (autoClose && stage === 'done') closeChecks();
}
const autoStarted = {};
async function autoStart() { // Claude Code and Git install as soon as they're found missing: nothing to click
  if (!health) return;
  for (const id of ['claude', 'git']) {
    if (!chk(id).ok && !job(id) && !autoStarted[id]) { autoStarted[id] = true; await post('/api/setup?what=' + id); }
  }
}
function watchHealth() { // while the setup screen is open it keeps checking by itself
  clearInterval(healthPoll);
  healthPoll = setInterval(async () => {
    if ($('checks').hidden) { clearInterval(healthPoll); return; }
    await fetchHealth(true); renderChecks(); autoStart();
  }, 2500);
}
function openChecks() { // resolves when the checklist is closed
  if ($('checks').hidden) closePanels(); // close other panels first
  autoClose = false;
  $('checks').hidden = false; $('chkNote').hidden = true;
  if (health) renderChecks();
  else $('chkList').innerHTML = '<div class="citem wide"><span class="ct"><b>Checking This PC…</b></span></div>';
  fetchHealth(true).then(() => { renderChecks(); autoStart(); watchHealth(); });
  return new Promise(res => { checksResolve = res; });
}
function closeChecks() {
  if (!coreReady()) return false; // required: stays open until Claude Code (signed in) and Git are ready
  $('checks').hidden = true; clearInterval(healthPoll);
  if (checksResolve) { const r = checksResolve; checksResolve = null; r(); }
  return true;
}
$('chkDone').onclick = () => closeChecks();
// first start on this PC (or the last check failed): the checklist comes first, before the profile and the home screen
let checkedOnStart = false;
if (localStorage.getItem('cad-setup-ok') !== '1') {
  checkedOnStart = true;
  autoClose = true;
  $('checks').hidden = false;
  $('chkList').innerHTML = '<div class="citem wide"><span class="ct"><b>Checking This PC…</b></span></div>';
  if (await fetchHealth(true)) {
    if (!coreReady()) { renderChecks(); autoStart(); watchHealth(); await new Promise(res => { checksResolve = res; }); }
  }
  $('checks').hidden = true; clearInterval(healthPoll);
}

// ---------- profiles: who uses this PC and where their parts live ----------
// No profile yet (first run): show the setup screen and stop here; saving it reloads the page.
let profiles = { active: null, profiles: [], detected: {} };
try { profiles = await (await fetch('/api/profiles', { cache: 'no-store' })).json(); }
catch { // server not running: show the banner (with Restart in the app) and reload once it answers
  $('offline').hidden = false;
  const api = window.pywebview && window.pywebview.api;
  $('offRestart').hidden = !(api && api.start_server);
  setInterval(async () => { try { if (!appReloads && (await fetch('/api/ping', { cache: 'no-store' })).ok) location.reload(); } catch { /* still down */ } }, 2000);
  await new Promise(() => {});
}
const RESIN = /photon|saturn|mars|halot|sonic|form ?[0-9]|resin|jupiter|uniformation/i;
function printerRow(pr = { name: '', type: 'FDM' }) {
  const row = document.createElement('div'); row.className = 'prow';
  row.innerHTML = `<input class="pname" list="printerList" placeholder="Printer, e.g. Bambu Lab P1S" value="${esc(pr.name)}">
    <select class="ptype"><option>FDM</option><option>Resin</option></select><button type="button" title="Remove">×</button>`;
  row.querySelector('.ptype').value = pr.type === 'Resin' ? 'Resin' : 'FDM';
  row.querySelector('.pname').oninput = e => { if (RESIN.test(e.target.value)) row.querySelector('.ptype').value = 'Resin'; fillDefault(); };
  row.querySelector('button').onclick = () => { row.remove(); fillDefault(); };
  $('pfPrinters').append(row);
}
const formPrinters = () => [...$('pfPrinters').querySelectorAll('.prow')].map(r => ({ name: r.querySelector('.pname').value.trim(), type: r.querySelector('.ptype').value })).filter(x => x.name);
function fillDefault(want) {
  const cur = want ?? $('pfDefault').value;
  $('pfDefault').innerHTML = '<option value="">Ask Each Time</option>' + formPrinters().map(x => `<option>${esc(x.name)}</option>`).join('');
  $('pfDefault').value = formPrinters().some(x => x.name === cur) ? cur : '';
}
let pfFolder = '';
function folderOptions(mode, prof) {
  const det = profiles.detected || {}, opts = [];
  if (mode === 'edit') opts.push({ id: 'keep', title: 'Keep the Current Folder', path: prof.folder });
  for (const e of det.existing || []) if (!e.used && !(prof && e.folder.toLowerCase() === prof.folder.toLowerCase()))
    opts.push({ id: 'ex:' + e.folder, title: 'Use the Existing Folder', note: `${e.parts} part${e.parts === 1 ? '' : 's'} already in it`, path: e.folder });
  if (det.onedrive && !opts.some(o => o.path.toLowerCase() === det.onedrive.toLowerCase()))
    opts.push({ id: 'od', title: 'OneDrive', note: 'synced to your other PCs and backed up', path: det.onedrive });
  if (det.local && !opts.some(o => o.path.toLowerCase() === det.local.toLowerCase()))
    opts.push({ id: 'local', title: 'This PC Only', note: 'not synced anywhere', path: det.local });
  opts.push({ id: 'pick', title: 'Choose a Folder…', path: '' });
  return opts;
}
function renderFolders(opts, chosenId) {
  $('pfFolders').innerHTML = opts.map(o => `<label class="fopt ${o.id === chosenId ? 'on' : ''}" data-id="${esc(o.id)}">
      <input type="radio" name="pff" ${o.id === chosenId ? 'checked' : ''}>
      <span class="ft"><b>${esc(o.title)}</b><small>${esc(o.path || 'Pick any folder on this PC')}${o.note ? ' · ' + esc(o.note) : ''}</small></span>
      ${o.id === 'pick' ? '<button type="button" class="browse">Browse…</button>' : ''}</label>`).join('');
  const choose = async id => {
    const o = opts.find(x => x.id === id);
    if (id === 'pick') {
      const r = { path: await pickFolder(o.path || '') };
      if (!r.path) return;
      o.path = r.path;
    }
    pfFolder = o.path; renderFolders(opts, id);
    if (id !== 'keep' && !formPrinters().length) { // an existing folder may already know its printers
      const info = await (await fetch('/api/folderinfo?path=' + encodeURIComponent(o.path))).json().catch(() => ({}));
      if (info.printers && info.printers.length && !formPrinters().length) {
        $('pfPrinters').innerHTML = ''; info.printers.forEach(printerRow); fillDefault(info.printer);
      }
    }
  };
  $('pfFolders').querySelectorAll('.fopt').forEach(l => l.onclick = e => { e.preventDefault(); choose(l.dataset.id); });
  return choose;
}
async function openProfileForm(mode, prof) { // mode: 'first' | 'add' | 'edit'
  closePanels();
  $('setup').hidden = false; $('pfErr').textContent = '';
  $('setupTitle').textContent = mode === 'first' ? 'Welcome to CAD Parts' : mode === 'add' ? 'Add a Profile' : 'Edit Profile';
  $('setupSub').textContent = mode === 'first' ? 'Tell it who you are and where your parts should live. You can change this later.'
    : mode === 'add' ? 'A profile has its own parts folder, printers and settings.' : 'Change your name, printers or parts folder. Files are never moved or deleted.';
  $('pfSave').textContent = mode === 'edit' ? 'Save' : mode === 'add' ? 'Add Profile' : 'Get Started';
  $('pfCancel').hidden = mode === 'first';
  let importFile = '';
  const showImport = info => {
    $('pfImportInfo').hidden = $('pfImportClear').hidden = !info; $('pfImportBtn').hidden = !!info;
    $('pfImportInfo').textContent = info || '';
  };
  $('pfImport').hidden = mode === 'edit'; showImport('');
  $('pfImportBtn').onclick = async () => {
    const f = await pickImportFile(); if (!f) return;
    const m = await (await fetch('/api/import/peek?file=' + encodeURIComponent(f))).json().catch(() => ({}));
    if (!m.ok) { $('pfErr').textContent = m.error || 'That isn\'t a CAD Parts file.'; return; }
    importFile = f; $('pfErr').textContent = '';
    showImport(`Importing ${m.parts} part${m.parts === 1 ? '' : 's'}${m.name ? ' from ' + m.name + '\'s file' : ''}`);
    if (m.name && !$('pfName').value.trim()) $('pfName').value = m.name;
    if (m.printers.length) { $('pfPrinters').innerHTML = ''; m.printers.forEach(printerRow); fillDefault(m.printer); }
  };
  $('pfImportClear').onclick = () => { importFile = ''; showImport(''); };
  $('pfName').value = prof ? prof.name : '';
  $('pfPrinters').innerHTML = '';
  const printers = mode === 'edit' ? (settings.printers || []) : [];
  printers.forEach(printerRow); if (!printers.length) printerRow();
  fillDefault(mode === 'edit' ? settings.printer : '');
  const opts = folderOptions(mode, prof);
  const first = opts[0];
  pfFolder = first.path;
  const choose = renderFolders(opts, first.id);
  if (first.id !== 'keep' && first.id !== 'pick') choose(first.id);
  $('pfCancel').onclick = () => { $('setup').hidden = true; };
  $('setupForm').onsubmit = async e => {
    e.preventDefault();
    if (!$('pfName').value.trim()) { $('pfErr').textContent = 'Enter your name.'; $('pfName').focus(); return; }
    if (!pfFolder) { $('pfErr').textContent = 'Choose where your parts should live.'; return; }
    $('pfSave').disabled = true; $('pfErr').textContent = '';
    const label = $('pfSave').textContent; if (importFile) $('pfSave').textContent = 'Importing…';
    const r = await postJSON('/api/profile/save', { id: mode === 'edit' ? prof.id : null, name: $('pfName').value, folder: pfFolder,
      printers: formPrinters(), printer: $('pfDefault').value, ...(importFile ? { import: importFile } : {}) });
    $('pfSave').disabled = false; $('pfSave').textContent = label;
    if (!r.ok) { $('pfErr').textContent = r.error || 'Couldn\'t save.'; return; }
    if (r.imported && r.imported.skipped.length) sessionStorage.setItem('cad-import-skipped', r.imported.skipped.join(', '));
    location.reload(); // everything (parts, chats, settings) comes from the new folder now
  };
  setTimeout(() => $('pfName').focus(), 0);
}
$('pfAddPrinter').onclick = () => { printerRow(); $('pfPrinters').lastElementChild.querySelector('input').focus(); };
const activeProf = profiles.profiles.find(x => x.id === profiles.active);
// the folder moved, OneDrive hasn't synced yet, or a drive is unplugged: point the profile at it again
if (activeProf && !activeProf.exists) {
  $('lost').hidden = false;
  $('lostSub').textContent = activeProf.folder;
  $('lostRetry').onclick = () => location.reload();
  let picked = '';
  const use = async folder => {
    const res = await postJSON('/api/profile/save', { id: activeProf.id, name: activeProf.name, folder });
    if (res.ok) location.reload(); else $('lostSub').textContent = 'Couldn\'t use that folder: ' + (res.error || 'failed');
  };
  $('lostFind').onclick = async () => {
    $('lostFind').disabled = true;
    const r = { path: await pickFolder() };
    $('lostFind').disabled = false;
    if (!r.path) return;
    const info = await (await fetch('/api/folderinfo?path=' + encodeURIComponent(r.path))).json().catch(() => ({}));
    if (info.parts) return use(r.path);
    picked = r.path; // no parts in it: say so, and let them pick again or use it anyway
    $('lostSub').textContent = `No parts in ${r.path}`; $('lostFind').textContent = 'Pick Another'; $('lostUseRow').hidden = false;
  };
  $('lostUse').onclick = () => picked && use(picked);
  await new Promise(() => {}); // the rest of the app starts after the reload
}
// ---------- first start on this PC (after install + Claude sign-in), the same for everyone:
// 1) your parts are here (a sync service) -> open them; 2) parts not tagged with an owner -> "Are these yours?";
// 3) nothing -> Start Fresh (a synced folder if there is one) / Import a CAD Parts File / My Parts Are Somewhere Else.
// Printers aren't asked here: Claude asks per part, and they can be added in Settings.
async function firstStart() {
  $('start').hidden = false;
  const show = (title, sub = '') => { $('stTitle').textContent = title; $('stSub').textContent = sub; $('stSub').hidden = !sub; $('stErr').textContent = ''; };
  const err = t => { $('stErr').textContent = t; };
  const busy = on => $('start').querySelectorAll('button').forEach(b => b.disabled = on);
  const plural = n => `${n} part${n === 1 ? '' : 's'}`;
  let saveErr = ''; // a failed save in steps 1 or 2 stays visible on the next screen
  const save = async (body, after) => {
    busy(true);
    const r = await postJSON('/api/profile/save', { id: null, ...body });
    busy(false);
    if (!r.ok) { saveErr = 'Couldn\'t set that up: ' + (r.error || 'failed'); err(saveErr); return false; }
    if (after) after(r);
    location.reload(); await new Promise(() => {});
  };
  // the server didn't answer: say so, with Look Again, instead of waiting forever
  const look = async force => {
    for (;;) {
      show('Looking for Your Parts…'); $('stAsk').hidden = $('stNew').hidden = $('stFail').hidden = true;
      try { return await (await fetch('/api/profile/find' + (force ? '?force=1' : ''), { cache: 'no-store' })).json(); }
      catch { /* below */ }
      show('Couldn\'t Look for Your Parts'); err('CAD Parts isn\'t answering. Try again in a moment.');
      $('stFail').hidden = false;
      await new Promise(res => { $('stRetry').onclick = res; });
    }
  };
  let f = await look(false);
  if (f.match) { // 1) yours: open straight away
    const m = f.match;
    await save({ name: m.name || f.name || 'Me', folder: m.folder }, () => sessionStorage.setItem('cad-welcome', JSON.stringify(m)));
  }
  if (f.candidate) { // 2) are these yours?
    const c = f.candidate;
    show('Are These Your Parts?', `${plural(c.parts)} in ${c.folder}`);
    $('stAsk').hidden = false;
    const yes = await new Promise(res => { $('stUse').onclick = () => res(true); $('stNotMine').onclick = () => res(false); });
    $('stAsk').hidden = true;
    if (yes) await save({ name: c.name || f.name || 'Me', folder: c.folder }, () => sessionStorage.setItem('cad-welcome', JSON.stringify(c)));
  }
  // 3) let's get your parts
  const sug = f.suggest || {};
  show('Let\'s Get Your Parts', sug.synced ? `New parts go in ${sug.label}, so they follow you to a new PC` : `New parts go in ${sug.label}`);
  if (saveErr) err(saveErr);
  $('stSub').title = sug.folder;
  $('stNew').hidden = false;
  $('stName').value = f.name || '';
  $('stAgain').hidden = false;
  const needName = () => { const n = $('stName').value.trim(); if (!n) { err('Enter your name.'); $('stName').focus(); } return n; };
  $('stFresh').onclick = () => { const n = needName(); if (n) save({ name: n, folder: sug.folder }); };
  $('stImport').onclick = async () => {
    const file = await pickImportFile(); if (!file) return;
    const m = await (await fetch('/api/import/peek?file=' + encodeURIComponent(file))).json().catch(() => ({}));
    if (!m.ok) { err(m.error || 'That isn\'t a CAD Parts file.'); return; }
    const name = $('stName').value.trim() || m.name; if (!name) { needName(); return; }
    $('stImport').textContent = 'Importing…';
    await save({ name, folder: sug.folder, import: file, ...(m.printers.length ? { printers: m.printers, printer: m.printer } : {}) },
      r => { sessionStorage.setItem('cad-welcome', JSON.stringify({ name, parts: m.parts, imported: true }));
             if (r.imported && r.imported.skipped.length) sessionStorage.setItem('cad-import-skipped', r.imported.skipped.join(', ')); });
    $('stImport').textContent = 'Import a CAD Parts File';
  };
  $('stElse').onclick = async () => {
    const folder = await pickFolder(); if (!folder) return;
    const info = await (await fetch('/api/folderinfo?path=' + encodeURIComponent(folder))).json().catch(() => ({}));
    const n = $('stName').value.trim() || info.owner || ''; if (!n) { needName(); return; }
    if (!info.parts && !await askConfirm({ title: 'No parts in that folder', text: 'Keep your new parts there?', ok: 'Use It', danger: false })) return;
    const claim = info.othersAccount; // e.g. you signed in with a different Claude account than before
    if (claim && !await askConfirm({ title: 'Make These Parts Yours?', text: `They belong to another Claude account${info.owner ? ' (' + info.owner + ')' : ''}.`, ok: 'Make Them Mine', danger: false })) return;
    save({ name: n, folder, ...(claim ? { claim: true } : {}) }, () => { if (info.parts) sessionStorage.setItem('cad-welcome', JSON.stringify({ name: n, parts: info.parts })); });
  };
  $('stAgain').onclick = async () => {
    f = await look(true);
    if (f.match || f.candidate) return firstStart(); // found them now: start over with what's there
    show('Still Nothing Here', 'Wait for your sync service to finish, then Look Again');
    $('stNew').hidden = false;
  };
  $('stName').onkeydown = e => { if (e.key === 'Enter') $('stFresh').click(); };
  setTimeout(() => $('stName').focus(), 0);
}
if (!activeProf) {
  await firstStart();
  await new Promise(() => {});
}
if (!profiles.root) { // a profile, but its folder can't be opened (and the missing-folder screen didn't apply)
  openProfileForm('first', null);
  await new Promise(() => {}); // the rest of the app starts after the reload
}
profId = activeProf.id;
document.body.classList.remove('booting');
{ // after "Welcome back" (found your folder) or an import that left some parts out
  const w = sessionStorage.getItem('cad-welcome'); sessionStorage.removeItem('cad-welcome');
  if (w) { const m = JSON.parse(w); setTimeout(() => toast(m.imported ? `Imported ${m.parts} part${m.parts === 1 ? '' : 's'}`
      : `Welcome back${m.name ? ', ' + m.name : ''}: your ${m.parts} part${m.parts === 1 ? ' is' : 's are'} here`), 600); }
  const sk = sessionStorage.getItem('cad-import-skipped'); sessionStorage.removeItem('cad-import-skipped');
  if (sk) setTimeout(() => toast('Already in this folder, so not imported: ' + sk), 600);
}
draftOpen = localStorage.getItem('cad-draft:' + profId) === '1';

const store = {};
let diskChats = {};
try { diskChats = await (await fetch('/api/chats', { cache: 'no-store' })).json(); } catch { /* server down: start empty */ }
const getChat = k => store[k] ??= (diskChats[k] || { session: null, msgs: [] });
const saveTimers = {};
const saveNow = k => { clearTimeout(saveTimers[k]); delete saveTimers[k]; return postJSON('/api/chat/save?key=' + encodeURIComponent(k), getChat(k)); };
const saveChat = k => { clearTimeout(saveTimers[k]); saveTimers[k] = setTimeout(() => saveNow(k), 300); };
addEventListener('pagehide', () => { for (const k of Object.keys(saveTimers)) beacon('/api/chat/save?key=' + encodeURIComponent(k), new Blob([JSON.stringify(getChat(k))], { type: 'application/json' })); });
// one-time move of chats this browser/app kept in localStorage (before chats were saved on disk)
for (const lk of Object.keys(localStorage)) {
  if (!lk.startsWith('cad-chat:')) continue;
  const k = lk.slice(9);
  try {
    const c = JSON.parse(localStorage.getItem(lk)), disk = diskChats[k];
    if (!c || !c.msgs || !c.msgs.length) { localStorage.removeItem(lk); continue; }
    if (!(disk && disk.msgs && disk.msgs.length)) { store[k] = c; saveNow(k); localStorage.removeItem(lk); }
    // else: the other window (Firefox or the app) already moved its chat for this part; keep this copy, never lose it
  } catch { /* ignore */ }
}
let settings = { model: '', keep: 5, printer: '', notify: true };
try { settings = await (await fetch('/api/settings', { cache: 'no-store' })).json(); } catch { /* defaults */ }
let busyKey = null;
let busyStatus = null, busyBuilding = false, lastTurnEnd = 0; // what Claude is doing now (shown on the viewer); when the last turn ended

function makeChat({ msgs, form, input, sendBtn, clearBtn, key, context, onSend, onClear }) {
  const self = {
    render(forceBottom) {
      const k = key(); if (!k) return;
      const c = getChat(k), busy = busyKey === k, box = $(msgs);
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
      box.innerHTML = c.msgs.length ? c.msgs.map(m => {
        if (m.role === 'user') return `<div class="m user">${m.images && m.images.length ? `<div class="mimgs">${m.images.map(u => `<img src="${esc(u)}" alt="attached picture">`).join('')}</div>` : ''}${esc(m.text)}</div>`;
        if (m.role === 'claude') return `<div class="m claude">${md(m.text)}</div>`;
        if (m.role === 'tool') return `<div class="m tool">${esc(m.text)}</div>`;
        if (m.role === 'warn') return `<div class="m warn">${esc(m.text)}</div>`;
        if (m.role === 'err') return `<div class="m err">${esc(m.text)}</div>`;
        return `<div class="m note">${esc(m.text)}</div>`;
      }).join('') + (busy ? '<div class="typing"><i></i><i></i><i></i></div>' : '') : '';
      if (atBottom || busy || forceBottom) box.scrollTop = box.scrollHeight;
      const other = busyKey && !busy;
      $(sendBtn).textContent = busy ? 'Stop' : 'Send';
      $(sendBtn).classList.toggle('primary', !busy);
      $(sendBtn).disabled = !!other;
      $(sendBtn).title = other ? 'Claude is busy with another chat' : '';
    },
    async send(text, pics = []) {
      // turn.k can change mid-turn: a new part's chat moves to the part once Claude saves it
      const turn = curTurn = { k: key() }, c = getChat(turn.k), save = () => saveChat(turn.k);
      c.msgs.push(pics.length ? { role: 'user', text, images: pics.map(x => x.url) } : { role: 'user', text });
      busyKey = turn.k; busyStatus = 'Thinking…'; busyBuilding = false; save(); renderChats();
      const message = (text || 'See the attached picture.') + (pics.length ? '\n\n[Attached pictures: ' + pics.map(x => x.path).join(' | ') + ']' : '');
      onSend && onSend();
      let res;
      try {
        res = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message, session: c.session, context: context(), part: turn.k.startsWith('part:') ? turn.k.slice(5) : '' }) });
        const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
        for (;;) {
          const { value, done } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, i); buf = buf.slice(i + 1);
            let ev; try { ev = JSON.parse(line); } catch { continue; }
            if (ev.t === 'building') { busyBuilding = true; busyStatus = 'Building…'; } // Claude is done asking and starts modelling
            if (ev.t === 'tool' && /^(Wrote|Edited|Ran) .*\.(py|stl)$/.test(ev.text)) busyBuilding = true; // backup: real modelling has started
            if (ev.t === 'tool') busyStatus = /^(Wrote|Edited)/.test(ev.text) ? 'Writing script…' : /^Ran/.test(ev.text) ? 'Building ' + ev.text.slice(4) + '…' : /^(Read|Searched)/.test(ev.text) ? 'Reading…' : 'Working…';
            else if (ev.t === 'text') busyStatus = 'Checking…';
            if (ev.t === 'session' && ev.id) c.session = ev.id;
            else if (ev.t === 'text') c.msgs.push({ role: 'claude', text: ev.text });
            else if (ev.t === 'tool') c.msgs.push({ role: 'tool', text: ev.text });
            else if (ev.t === 'note') c.msgs.push({ role: 'note', text: ev.text });
            else if (ev.t === 'denied') c.msgs.push({ role: 'warn', text: 'Not allowed from the dashboard: ' + ev.items.join(', ') + '' });
            else if (ev.t === 'error') c.msgs.push({ role: 'err', text: ev.text });
            save(); renderChats();
            if (ev.t === 'tool') load(); // pick up new exports while Claude is still working
          }
        }
      } catch { c.msgs.push({ role: 'err', text: "Lost the connection to the CAD Parts server." }); }
      const built = busyBuilding;
      busyKey = null; busyStatus = null; lastTurnEnd = Date.now(); curTurn = null; save(); renderChats(); load();
      if (!document.hasFocus() && settings.notify) { // you're in another window: Windows notification
        const last = [...c.msgs].reverse().find(m => m.role === 'claude' || m.role === 'err');
        const who = turn.k === 'new' ? 'new part' : pretty(turn.k.slice(5));
        postJSON('/api/notify', { title: (built ? 'Build finished: ' : 'Claude replied: ') + who,
          body: last ? last.text.replace(/[#*`]/g, '').replace(/\s+/g, ' ').slice(0, 140) : '' });
      }
    },
  };
  $(form).onsubmit = async e => {
    e.preventDefault();
    if (busyKey && busyKey === key()) { post('/api/chat/stop'); return; }
    if (busyKey) return;
    const text = $(input).value.trim(); if (!text && !pending.length) return;
    const files = pending.splice(0); renderPending();
    $(input).value = '';
    const pics = [];
    for (const it of files) { // upload the pictures first, then send the message that points at them
      let r = null;
      try { r = await (await fetch('/api/upload', { method: 'POST', headers: { 'Content-Type': it.file.type }, body: it.file })).json(); } catch { /* below */ }
      URL.revokeObjectURL(it.url);
      if (r && r.ok) pics.push(r); else toast('Couldn\'t attach a picture: ' + ((r && r.error) || 'upload failed'));
    }
    if (!text && !pics.length) return;
    self.send(text, pics);
  };
  $(input).onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $(form).requestSubmit(); } };
  $(clearBtn).onclick = async () => {
    const k = key(); if (!k || busyKey === k) return;
    if (getChat(k).msgs.length && !await askConfirm({ title: 'Clear this chat?', text: 'Claude starts fresh next time. Your part files stay.', ok: 'Clear' })) return;
    store[k] = { session: null, msgs: [] }; saveChat(k); self.render();
    onClear && onClear();
  };
  return self;
}

// one chat column: the selected part's conversation, or the new part being described
const editChat = makeChat({
  msgs: 'emsgs', form: 'eform', input: 'ein', sendBtn: 'eSend', clearBtn: 'eNew',
  key: () => chatKey(sel),
  context: () => {
    if (sel === NEW) return 'starting a NEW part (the user pressed "+ New Part")'
      + (settings.printer ? `. Default printer (user setting): ${settings.printer}; use it without asking unless they say otherwise` : '');
    const p = part(sel), f = p && allFiles(p).find(x => x.file === selFile);
    return `editing existing part "${p.name}"` + (f ? `, currently viewing ${f.file}` : ' (no model yet)')
      + '. Read its notes.md and latest script first; save changes as the next version number.';
  },
  // a new part: remember when this message started, so the folder Claude saves can be matched to the draft
  onSend: () => { if (sel === NEW) { const c = getChat('new'); c.turnStart = data ? data.now : Date.now() / 1000; saveChat('new'); } },
  onClear: () => $('ein').focus(),
});

// ---------- parts list: open (2 per row) or closed ----------
const PENCIL = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>';
// Two screens: the big parts list with a preview (list open), or the viewer + chat for one part (list closed).
// Into a part: "Edit part" on the preview, double-click a card, or + New part. Back: "‹ Parts" or Esc.
function setList(open) {
  const home = $('home'), was = !home.classList.contains('nolist');
  home.classList.toggle('nolist', !open);
  $('vEdit').hidden = !open; // "Edit part" only on the list screen
  $('vEdit').innerHTML = PENCIL + 'Edit Part'; $('vEdit').title = 'Edit This Part in the Chat';
  $('dBack').hidden = open;  // "‹ Parts" only on the viewer + chat screen
  if (was !== open && setList.ready) { // a short slide when switching (not on start-up)
    home.classList.remove('to-work', 'to-list'); void home.offsetWidth;
    home.classList.add(open ? 'to-list' : 'to-work');
    clearTimeout(setList.t); setList.t = setTimeout(() => home.classList.remove('to-work', 'to-list'), 320);
  }
  if (!open) { editChat.render(true); setTimeout(() => $('ein').focus(), 0); } // chat is there: ready to type
  localStorage.setItem('cad-list', open ? 'open' : 'closed');
  dispatchEvent(new Event('resize')); // the viewer and the chat-width slider fit the new layout
}
$('vEdit').onclick = () => setList(false);
$('dBack').onclick = () => setList(true);

// search: filter as you type; Esc clears; "/" focuses it from anywhere on Home
$('search').oninput = () => { clearTimeout(renderCards.t); renderCards.t = setTimeout(() => { if (data) { renderCards(); $('left').scrollTop = 0; } }, 80); };
$('search').onkeydown = e => {
  if (e.key === 'Escape') { $('search').value = ''; renderCards(); $('search').blur(); }
  if (e.key === 'Enter') { const first = document.querySelector('#cards .card'); if (first) select(first.dataset.n); }
};
addEventListener('keydown', e => {
  if (e.key === '/' && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
    e.preventDefault(); if ($('home').classList.contains('nolist')) setList(true); $('search').focus();
  }
});
setList(localStorage.getItem('cad-list') !== 'closed');
setList.ready = true;

// ---------- slider between the 3D viewer and the edit chat ----------
(() => {
  const home = $('home'), split = $('split');
  // the chat gives up width (down to 300px) before the viewer gets narrower than 360px
  const clamp = w => Math.max(300, Math.min(w, innerWidth - $('left').offsetWidth - 10 - 360));
  const apply = w => home.style.setProperty('--chatw', clamp(w) + 'px');
  const saved = () => +localStorage.getItem('cad-chatw') || 400;
  apply(saved());
  split.onpointerdown = e => {
    e.preventDefault(); split.setPointerCapture(e.pointerId);
    split.classList.add('drag'); document.body.classList.add('resizing');
  };
  split.onpointermove = e => { if (split.hasPointerCapture(e.pointerId)) apply(innerWidth - e.clientX - 5); };
  split.onpointerup = e => {
    split.releasePointerCapture(e.pointerId); split.classList.remove('drag'); document.body.classList.remove('resizing');
    localStorage.setItem('cad-chatw', parseInt(home.style.getPropertyValue('--chatw')) || '');
  };
  split.ondblclick = () => { localStorage.removeItem('cad-chatw'); apply(400); };
  addEventListener('resize', () => apply(saved()));
  // (setList fires a resize event, which re-applies the width above)
})();

// ---------- pictures for the chat: attach button, paste, drag and drop ----------
const pending = []; // { file, url } waiting to be sent
const IMG_OK = /^image\/(png|jpeg|webp|gif)$/;
function addPictures(files) {
  let skipped = 0;
  for (const f of files) { if (IMG_OK.test(f.type)) pending.push({ file: f, url: URL.createObjectURL(f) }); else skipped++; }
  if (skipped) toast('Only PNG, JPEG, WebP or GIF pictures');
  renderPending();
  if ($('home').classList.contains('nolist') === false) setList(false); // pictures belong in the chat: show it
  $('ein').focus();
}
function renderPending() {
  $('eAtts').hidden = !pending.length;
  $('eAtts').innerHTML = pending.map((it, i) => `<div class="att"><img src="${it.url}" alt=""><button type="button" data-i="${i}" title="Remove">×</button></div>`).join('');
  $('eAtts').querySelectorAll('button').forEach(b => b.onclick = () => { const [it] = pending.splice(+b.dataset.i, 1); URL.revokeObjectURL(it.url); renderPending(); });
}
function clearPending() { // switching parts: pictures not sent yet are dropped (they were for the other part)
  if (!pending.length) return;
  for (const it of pending.splice(0)) URL.revokeObjectURL(it.url);
  renderPending();
}
$('eAttach').onclick = () => $('eFile').click();
$('eFile').onchange = () => { addPictures([...$('eFile').files]); $('eFile').value = ''; };
$('ein').addEventListener('paste', e => {
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'));
  if (files.length) { e.preventDefault(); addPictures(files); }
});
for (const t of ['tabEdit', 'detail']) {
  $(t).addEventListener('dragover', e => { if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); $('tabEdit').classList.add('dragging'); } });
  $(t).addEventListener('dragleave', e => { if (!$(t).contains(e.relatedTarget)) $('tabEdit').classList.remove('dragging'); });
  $(t).addEventListener('drop', e => { if (!e.dataTransfer.files.length) return; e.preventDefault(); $('tabEdit').classList.remove('dragging'); addPictures([...e.dataTransfer.files]); });
}
$('emsgs').addEventListener('click', e => { // click a picture in the chat: full size
  const img = e.target.closest('.mimgs img'); if (!img) return;
  const lb = Object.assign(document.createElement('div'), { className: 'lightbox' });
  lb.append(Object.assign(document.createElement('img'), { src: img.src }));
  lb.onclick = () => lb.remove(); document.body.append(lb);
});

// ---------- settings ----------
function openSettings(force) {
  const panel = $('settings');
  if (!force && !panel.hidden) { closePanels(); return; }
  closePanels();
  $('sModel').value = settings.model || ''; $('sKeep').value = settings.keep || 5;
  $('sPrinter').innerHTML = '<option value="">Ask Each Time</option>' + (settings.printers || []).map(x => `<option>${esc(x.name)}</option>`).join('');
  $('sPrinter').value = settings.printer || ''; $('sNotify').checked = settings.notify !== false;
  fillDefaultAppSettings(); loadApps().then(() => { if (!$('settings').hidden) fillDefaultAppSettings(); });
  $('sProfInfo').textContent = (settings.printers || []).map(x => x.name).join(', ') || 'no printers listed';
  panel.hidden = false;
}
async function saveSettings() {
  const body = { model: $('sModel').value, keep: Math.max(1, Math.min(50, +$('sKeep').value || 5)), printer: $('sPrinter').value, notify: $('sNotify').checked };
  const r = await postJSON('/api/settings', body);
  if (r.ok) { settings = r.settings; toast('Saved'); } else toast('Couldn\'t save settings');
}
for (const id of ['sModel', 'sKeep', 'sPrinter', 'sNotify']) $(id).onchange = saveSettings;
$('sEditProf').onclick = () => openProfileForm('edit', activeProf);



// Get Update: the server downloads the new installer, checks it, then closes the app, installs and reopens it.
async function startUpdate() {
  const b = $('sUpdate'); b.disabled = true;
  const r = await post('/api/setup?what=update');
  if (r.ok) toast('Updating… CAD Parts will close and reopen by itself', { ms: 20000 });
  if (!r.ok) { b.disabled = false; toast('Couldn\'t update yet: ' + (r.error || 'failed')); return; }
  for (;;) {
    await new Promise(res => setTimeout(res, 500));
    let v; try { v = await (await fetch('/api/version', { cache: 'no-store' })).json(); } catch { return; } // the installer stopped the server
    const j = v.job || {};
    if (j.state === 'failed') { b.disabled = false; $('sVersion').textContent = 'Update failed: ' + j.error; return; }
    $('sVersion').textContent = (j.step || 'Updating…') + (j.pct != null && j.pct < 1 ? ` ${Math.round(j.pct * 100)}%` : '');
    if (j.state === 'done') return;
  }
}
// Started fresh before your sync service finished? If this folder is still empty and your own parts have turned up
// in another folder on this PC, offer to switch to them (once per folder found, unless you say Not Now).
setTimeout(async () => {
  const r = await (await fetch('/api/profile/elsewhere', { cache: 'no-store' })).json().catch(() => ({}));
  const f = r.found; if (!f || localStorage.getItem('cad-elsewhere-no:' + f.folder)) return;
  if (await askConfirm({ title: `Found Your ${f.parts} Parts`, text: `In ${f.label || f.folder}. Switch to them?`, ok: 'Switch', no: 'Not Now', danger: false })) {
    const res = await postJSON('/api/profile/save', { id: profId, name: activeProf.name, folder: f.folder });
    if (res.ok) { sessionStorage.setItem('cad-welcome', JSON.stringify(f)); location.reload(); } else toast('Couldn\'t switch: ' + (res.error || 'failed'));
  } else localStorage.setItem('cad-elsewhere-no:' + f.folder, '1');
}, 1500);

// Move to Another PC: Export My Parts saves everything in one .zip (the app window asks where; otherwise Downloads)
$('sExport').onclick = async () => {
  const name = `CAD Parts - ${activeProf.name} - ${new Date().toISOString().slice(0, 10)}.zip`;
  const api = window.pywebview && window.pywebview.api;
  let dest = '';
  if (api && api.save_file) { try { dest = await api.save_file(name) || ''; } catch { dest = ''; } if (!dest) return; }
  const r = await post('/api/export?' + (dest ? 'dest=' + encodeURIComponent(dest) : 'name=' + encodeURIComponent(name)));
  if (!r.ok) { toast('Couldn\'t export: ' + (r.error || 'failed')); return; }
  const b = $('sExport'), info = $('sExportInfo'), was = info.textContent; b.disabled = true;
  let misses = 0; // the server stopped or restarted (it forgets the job): give up after a few tries
  for (;;) {
    await new Promise(res => setTimeout(res, 400));
    let j = null;
    try { j = await (await fetch('/api/job?what=export', { cache: 'no-store' })).json(); } catch { /* below */ }
    if (!j || !j.state) { if (++misses >= 5) { info.textContent = 'Export stopped'; break; } continue; }
    misses = 0;
    if (j.state === 'failed') { info.textContent = 'Export failed: ' + (j.error || ''); break; }
    if (j.state === 'done') { info.textContent = was; toast('Saved ' + j.url); break; }
    info.textContent = `Packing your parts… ${Math.round((j.pct || 0) * 100)}%`;
  }
  b.disabled = false;
};

// ---------- updates: checked each time the app opens and every 3 hours while it's open. A new version downloads
// quietly in the background; once it's ready (and checked), the app asks: Update Now, or Later (asked again next time).
$('sUpdate').onclick = startUpdate;
let updateAsked = '', updateRunning = false; // one chain of checks at a time (the 3-hourly one skips if it's busy)
async function checkUpdate() {
  if (updateRunning) return;
  updateRunning = true;
  let fresh = true;
  try { while (await updateStep(fresh)) { fresh = false; await new Promise(res => setTimeout(res, 10000)); } }
  finally { updateRunning = false; }
}
async function updateStep(fresh) { // true: look again in 10 s (the download is still running)
  let v; try { v = await (await fetch('/api/version' + (fresh ? '?fresh=1' : ''), { cache: 'no-store' })).json(); } catch { return false; }
  const failed = !!v.download_failed && !fresh; // a fresh check tries the download again
  $('sVersion').textContent = 'Version ' + v.version + (v.ready ? ` · ${v.latest} is ready to install` : failed ? ` · ${v.latest} couldn't download` : v.newer ? ` · ${v.latest} is available` : v.latest ? ' · up to date' : '');
  $('sUpdate').hidden = !v.newer;
  $('sUpdate').textContent = v.ready ? 'Update Now' : 'Get Update';
  if (!v.newer) return false;
  if (!v.ready) { // download it quietly, then look again; stop if the download failed (tried again at the next check)
    if (failed) return false;
    if (!v.downloading) await post('/api/setup?what=update-download');
    return true;
  }
  if (updateAsked === v.latest) return false; // once per app start (Later asks again next time)
  updateAsked = v.latest;
  if (await askConfirm({ title: 'Update Ready', text: `CAD Parts ${v.latest} is ready. It installs in a few seconds and reopens by itself.`, ok: 'Update Now', no: 'Later', danger: false })) {
    startUpdate();
  }
  return false;
}
checkUpdate();
setInterval(checkUpdate, 3 * 3600 * 1000);
$('sHealth').onclick = () => openChecks();
// a PC that passed the checks before: check again quietly in the background, and only speak up if something broke
if (checkedOnStart === false) fetchHealth(false).then(h => { if (h && !coreReady()) { localStorage.removeItem('cad-setup-ok'); openChecks(); } });

// ---------- profile button (top right) ----------
$('bProf').hidden = false;
$('bProf').textContent = (activeProf.name.trim()[0] || '?').toUpperCase();
$('bProf').title = activeProf.name + ' · ' + activeProf.folder;
// Change Save Location: pick a folder, and this profile uses it from now on (printers come along). Nothing is moved:
// to bring your parts, move the folder in File Explorer and pick it here.
async function changeSaveLocation() {
  const r = { path: await pickFolder(activeProf.folder) };
  if (!r.path || r.path.toLowerCase() === activeProf.folder.toLowerCase()) return;
  if (!await askConfirm({ title: `Save parts in ${r.path}?`, text: 'Nothing is moved. Parts you have now stay where they are.', ok: 'Change', danger: false })) return;
  const fi = await (await fetch('/api/folderinfo?path=' + encodeURIComponent(r.path))).json().catch(() => ({}));
  if (fi.othersAccount && !await askConfirm({ title: 'Make These Parts Yours?', text: `They belong to another Claude account${fi.owner ? ' (' + fi.owner + ')' : ''}.`, ok: 'Make Them Mine', danger: false })) return;
  const st = await (await fetch('/api/settings', { cache: 'no-store' })).json().catch(() => ({}));
  const res = await postJSON('/api/profile/save', { id: profId, name: activeProf.name, folder: r.path, printers: st.printers || [], printer: st.printer || '', ...(fi.othersAccount ? { claim: true } : {}) });
  if (!res.ok) { toast('Couldn\'t change it: ' + (res.error || 'failed')); return; }
  location.reload();
}
function renderProfMenu() {
  $('profMenu').innerHTML = '<div class="ptitle">Profiles</div>' + profiles.profiles.map(x =>
    `<button class="prof ${x.id === profId ? 'cur' : ''} ${x.exists ? '' : 'missing'}" data-id="${esc(x.id)}"><span class="pn">${esc(x.name)}</span>
      ${x.exists ? '' : `<span class="pf" title="${esc(x.folder)}">Folder not found</span>`}</button>`).join('')
    + `<hr><button data-a="folder" title="Now: ${esc(activeProf.folder)}">${mi('folder', 'Change Save Location…')}</button>`
    + `<button data-a="settings">${mi('gear', 'Settings')}</button><button data-a="trash">${mi('trash', 'Recycle Bin')}</button>`
    + `<hr><button data-a="edit">${mi('pencil', 'Edit Profile…')}</button><button data-a="add">${mi('useradd', 'Add Profile…')}</button>`
    + (profiles.profiles.length > 1 ? `<button data-a="remove" class="red">${mi('userx', 'Remove This Profile')}</button>` : '');
}
$('bProf').onclick = e => { e.stopPropagation(); const open = $('profMenu').hidden; closePanels(); if (open) { renderProfMenu(); $('profMenu').hidden = false; $('bProf').classList.add('on'); } };
$('profMenu').onclick = async e => {
  const b = e.target.closest('button'); if (!b) return;
  e.stopPropagation(); // the panel opened below shouldn't see this click as "clicked outside, close"
  $('profMenu').hidden = true; $('bProf').classList.remove('on');
  if (b.dataset.a === 'settings') return openSettings(true);
  if (b.dataset.a === 'trash') { closePanels(); return openTrash(); }
  if (b.dataset.a === 'folder') return changeSaveLocation();
  if (b.dataset.id) {
    if (b.dataset.id === profId) return;
    const r = await post('/api/profile/switch?id=' + encodeURIComponent(b.dataset.id));
    if (r.ok) location.reload(); else toast(r.error || 'That profile\'s folder isn\'t available');
  } else if (b.dataset.a === 'edit') openProfileForm('edit', activeProf);
  else if (b.dataset.a === 'add') openProfileForm('add', null);
  else if (b.dataset.a === 'remove') {
    if (!await askConfirm({ title: `Remove the profile "${activeProf.name}"?`, text: 'Its parts folder and files stay exactly where they are.', ok: 'Remove' })) return;
    const r = await post('/api/profile/remove?id=' + encodeURIComponent(profId));
    if (r.ok) location.reload(); else toast(r.error || 'Couldn\'t remove it');
  }
};
document.addEventListener('click', e => { if (!$('profMenu').hidden && !$('profMenu').contains(e.target) && e.target !== $('bProf')) { $('profMenu').hidden = true; $('bProf').classList.remove('on'); } });
$('setClose').onclick = () => closePanels();
document.addEventListener('click', e => { if (!$('settings').hidden && !$('settings').contains(e.target)) closePanels(); });
function closePanels() { // anything open on top? close it and say so
  let any = false;
  for (const panel of ['settings', 'trash']) if (!$(panel).hidden) { $(panel).hidden = true; any = true; }
  if (!$('pmenu').hidden) { $('pmenu').hidden = true; any = true; }
  if (!$('sortMenu').hidden) { $('sortMenu').hidden = true; $('sortBtn').classList.remove('on'); any = true; }
  if (!$('owMenu').hidden) { $('owMenu').hidden = true; any = true; }
  if (!$('profMenu').hidden) { $('profMenu').hidden = true; $('bProf').classList.remove('on'); any = true; }
  if (!$('setup').hidden && !$('pfCancel').hidden) { $('setup').hidden = true; any = true; }
  if (!$('checks').hidden && closeChecks()) any = true;
  const lb = document.querySelector('.lightbox'); if (lb) { lb.remove(); any = true; }
  return any;
}

// ---------- keyboard shortcuts (listed in Settings) ----------
const typing = () => { const a = document.activeElement; return a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable); };
function stepPart(d) {
  const cards = [...document.querySelectorAll('#cards .card')]; if (!cards.length) return;
  let i = cards.findIndex(c => c.dataset.n === sel);
  i = i < 0 ? 0 : Math.max(0, Math.min(cards.length - 1, i + d));
  select(cards[i].dataset.n);
  document.querySelector(`#cards .card[data-n="${CSS.escape(cards[i].dataset.n)}"]`)?.scrollIntoView({ block: 'nearest' });
}
addEventListener('keydown', e => {
  if (!$('ask').hidden) return; // the "are you sure?" dialog handles its own keys
  if (!$('setup').hidden || !$('checks').hidden) { if (e.key === 'Escape') closePanels(); return; } // a setup card is open: no shortcuts behind it
  if (!data && e.key !== 'Escape') return; // the parts list hasn't arrived yet
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'n') { e.preventDefault(); newPart(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
  if (e.key === 'Escape') {
    if (closePanels()) return;
    if (typing()) { document.activeElement.blur(); return; }
    if ($('home').classList.contains('nolist')) setList(true);
    return;
  }
  if (typing()) return;
  const k = e.key;
  if (k === 'n' || k === 'N') newPart();
  else if (k === 'e' || k === 'E') { // Edit Part on the list screen, Back to Parts on the chat screen
    if ($('home').classList.contains('nolist')) setList(true); else if (sel) setList(false);
  }
  else if (k === 'f' || k === 'F') { if (sel && sel !== NEW) toggleFav(sel); }
  else if (k === 'F2') startRename();
  else if (k === 'd' || k === 'D') duplicatePart();
  else if (k === 'Delete') { if (sel === NEW) discardDraft(); else if (sel) deleteItem(sel); }
  else if (k === 'ArrowDown' || k === 'ArrowRight') stepPart(1);
  else if (k === 'ArrowUp' || k === 'ArrowLeft') stepPart(-1);
  else if (k === '?') openSettings(true);
  else return;
  e.preventDefault();
});

// ---------- Recycle Bin (things deleted from cad-parts, restorable) ----------
const CUBE_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4z"/><path d="M4 7.4 12 12l8-4.6M12 12v9.2"/></svg>';
const agoIso = iso => { const t = Date.parse(iso) / 1000; return isFinite(t) && data ? ago(t) : ''; };
function renderTrash(items) {
  $('trashList').innerHTML = !items.length ? '<div class="tempty">Empty</div>' : items.map((it, i) => {
    const m = !it.folder && it.name.match(/_v(\d+)/);
    const title = it.folder ? pretty(it.name) : (m ? `${pretty(it.part || '')} · v${m[1]}` : it.name);
    const sub = it.part === 'ready-to-print' ? 'Ready-to-Print Copy' : it.folder ? 'Whole Part' : it.name;
    return `<div class="titem"><span class="ic">${it.folder ? CUBE_ICON : 'STL'}</span>
      <span class="tx"><div class="nm" title="${esc(it.from)}">${esc(title)}</div><div class="sb">${esc(sub)} · ${esc(agoIso(it.deleted))}</div></span>
      <button data-i="${i}">Restore</button></div>`;
  }).join('');
  $('trashList').querySelectorAll('button[data-i]').forEach(b => b.onclick = async e => {
    e.stopPropagation(); // the list is redrawn under the click: don't let it count as "clicked outside, close"
    const it = items[+b.dataset.i];
    items.splice(+b.dataset.i, 1); renderTrash(items); // gone from the list right away
    const r = await post(`/api/restore?id=${encodeURIComponent(it.id)}`);
    if (r.ok) { toast(`Restored ${it.folder ? pretty(it.name) : it.name}`); data.sig = ''; load(); }
    else { toast('Couldn\'t restore: ' + (r.error || 'failed')); openTrash(true); }
  });
}
async function openTrash(refresh) {
  const panel = $('trash');
  if (!refresh && !panel.hidden) { panel.hidden = true; return; }
  panel.hidden = false;
  if (!openTrash.items) $('trashList').innerHTML = '<div class="tempty"><div class="ph loading" style="position:static;padding:0"></div></div>';
  else renderTrash(openTrash.items); // show the last list at once, then refresh it
  try { openTrash.items = (await (await fetch('/api/trash', { cache: 'no-store' })).json()).items; } catch { openTrash.items = []; }
  if (!panel.hidden) renderTrash(openTrash.items);
}
$('trashClose').onclick = () => { $('trash').hidden = true; };
document.addEventListener('click', e => { if (!$('trash').hidden && !$('trash').contains(e.target)) $('trash').hidden = true; });

$('bNew').onclick = () => newPart();

function renderChats() {
  editChat.render();
  // working animation on the viewer when it shows the part (or new part) Claude is working on
  homeViewer.setBusy(busyKey && busyKey === chatKey(sel) ? busyStatus : null, busyBuilding);
  $('eHint').textContent = busyKey && busyKey !== chatKey(sel)
    ? 'Working on ' + (busyKey === 'new' ? 'the new part' : pretty(busyKey.slice(5))) + '…' : '';
}

editChat.render(true);

loadApps();
load(); setInterval(() => { if (!load.inflight) load(); }, 4000); // skip a beat while the last one is still out
setInterval(() => data && renderAll(), 60000); // keep "x min ago" fresh
