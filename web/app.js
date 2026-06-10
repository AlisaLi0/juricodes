// leagle-chat frontend — conversational retrieval UI over real US case law.
'use strict';

// Backend API base. When served by the FastAPI backend itself, leave empty
// (same-origin). When hosted statically (e.g. GitHub Pages), set this to our
// backend URL. Resolution order:
//   1. window.LEAGLE_API_BASE (set inline in index.html)
//   2. <meta name="leagle-api-base" content="https://...">
//   3. "" (same origin)
const API_BASE = (
  (typeof window !== 'undefined' && window.LEAGLE_API_BASE) ||
  document.querySelector('meta[name="leagle-api-base"]')?.content ||
  ''
).replace(/\/$/, '');

const chat = document.getElementById('chat');
// Snapshot the intro/landing markup at load so "New research" can restore it.
const INTRO_HTML = chat.innerHTML;
const form = document.getElementById('composer');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const DEFAULT_PLACEHOLDER = 'Ask in plain English…  (e.g. wrongful termination after reporting safety violations)';

const messages = []; // conversation history: {role, content}
let turnSeq = 0;
let busy = false;
// Active search mode: 'chat' = full leagleLM reasoning; the toolkit entries set
// 'concept' | 'keyword' | 'case' | 'citation' for a direct precise search.
let currentMode = 'chat';

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Citation linking: [1],[2] → case refs; [R1],[R2] → statute/regulation refs.
// Each becomes a clickable chip scoped to this turn's authority lists.
function linkifyCites(s, turnId) {
  return s
    .replace(/\[(\d{1,2})\]/g, (m, n) =>
      `<span class="cite-ref" data-kind="case" data-turn="${turnId}" data-n="${n}">[${n}]</span>`)
    .replace(/\[R(\d{1,2})\]/g, (m, n) =>
      `<span class="cite-ref" data-kind="statute" data-turn="${turnId}" data-n="${n}">[R${n}]</span>`);
}

// Streaming render: escape + link citations; shown pre-wrap while tokens arrive.
function renderWithCites(text, turnId) {
  return linkifyCites(escapeHtml(text), turnId);
}

// Inline markdown on already-escaped text: bold, italic, inline code, + citations.
function renderInline(s, turnId) {
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
       .replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>')
       .replace(/`([^`]+)`/g, '<code>$1</code>');
  return linkifyCites(s, turnId);
}

// Minimal, safe Markdown → HTML for the final answer. Escapes first (no raw HTML
// from the model or case text ever reaches the DOM), then builds paragraphs,
// bullet/numbered lists and headings with inline formatting + clickable cites.
function renderMarkdown(text, turnId) {
  const lines = escapeHtml(text).split('\n');
  let html = '', listType = null, para = [];
  const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
  const flushPara = () => { if (para.length) { html += `<p>${renderInline(para.join(' '), turnId)}</p>`; para = []; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); closeList(); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); closeList();
      const lvl = Math.min(m[1].length + 2, 4);
      html += `<h${lvl}>${renderInline(m[2], turnId)}</h${lvl}>`;
    } else if ((m = line.match(/^[-*•]\s+(.*)$/))) {
      flushPara();
      if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
      html += `<li>${renderInline(m[1], turnId)}</li>`;
    } else if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
      html += `<li>${renderInline(m[1], turnId)}</li>`;
    } else {
      closeList(); para.push(line);
    }
  }
  flushPara(); closeList();
  return html;
}

function scrollDown() { chat.scrollTop = chat.scrollHeight; }

// The visible research workflow (mirrors the backend SSE phases).
const STEPS = [['analyze', 'Analyze'], ['search', 'Search'], ['authorities', 'Authorities'], ['answer', 'Reasoning']];

function buildStepper() {
  return STEPS.map(([k, lbl], i) =>
    `${i ? '<div class="step-sep"></div>' : ''}` +
    `<div class="step" data-step="${k}"><span class="dot"><span class="n">${i + 1}</span></span><span class="lbl">${lbl}</span></div>`
  ).join('');
}

// Set a step to 'active' | 'done' | '' (pending). Optionally mark all earlier as done.
function setStep(turnEl, key, state, completeEarlier) {
  const idx = STEPS.findIndex(([k]) => k === key);
  STEPS.forEach(([k], i) => {
    const el = turnEl.querySelector(`.step[data-step="${k}"]`);
    if (!el) return;
    if (completeEarlier && i < idx) el.className = 'step done';
    else if (k === key) el.className = 'step ' + state;
  });
}

function addUser(text) {
  const el = document.createElement('div');
  el.className = 'turn user';
  el.innerHTML = `<div class="qcard"><span class="ql">Question</span><span class="qt">${escapeHtml(text)}</span></div>`;
  chat.appendChild(el);
  scrollDown();
}

function newBotTurn() {
  const turnId = ++turnSeq;
  const el = document.createElement('div');
  el.className = 'turn bot';
  el.dataset.turn = turnId;
  el.innerHTML = `
    <div class="stepper">${buildStepper()}</div>
    <div class="status"><span class="spinner"></span><span class="status-text">…</span></div>
    <div class="authorities" style="display:none">
      <div class="auth-head">Table of Authorities <span class="cnt"></span></div>
      <div class="cases"></div>
    </div>
    <div class="statutes" style="display:none">
      <div class="auth-head">Federal Statutes &amp; Regulations <span class="cnt"></span></div>
      <div class="statlist"></div>
    </div>
    <div class="answer" style="display:none"></div>
    <div class="answer-actions" style="display:none"><button type="button" class="copy-btn">⧉ Copy</button></div>`;
  chat.appendChild(el);
  setStep(el, 'analyze', 'active');
  scrollDown();
  return {
    turnId, el,
    statusEl: el.querySelector('.status'),
    statusText: el.querySelector('.status-text'),
    authEl: el.querySelector('.authorities'),
    authCnt: el.querySelector('.auth-head .cnt'),
    casesEl: el.querySelector('.cases'),
    statEl: el.querySelector('.statutes'),
    statCnt: el.querySelector('.statutes .cnt'),
    statListEl: el.querySelector('.statlist'),
    answerEl: el.querySelector('.answer'),
    actionsEl: el.querySelector('.answer-actions'),
  };
}

function renderCases(casesEl, turnId, cases) {
  casesEl.innerHTML = '';
  cases.forEach((c, i) => {
    const n = i + 1;
    const card = document.createElement('div');
    card.className = 'case';
    card.id = `case-${turnId}-${n}`;
    const cites = (c.citations || []).slice(0, 3).join(' · ');
    // Cytator (treatment) badge: good-law signal from how often/recently the
    // case is cited by later opinions.
    let cyt = '';
    if (c.cited_by != null) {
      const label = {
        'landmark': 'Landmark',
        'frequently-cited': 'Frequently cited',
        'cited': 'Cited',
        'rarely-cited': 'Rarely cited',
      }[c.treatment] || 'Cited';
      const recent = c.last_cited ? `, latest ${escapeHtml(c.last_cited)}` : '';
      cyt = `<span class="cytator cyt-${escapeHtml(c.treatment || 'cited')}" title="Cited by ${c.cited_by} later opinions${recent}. Citation-frequency signal, not a negative-history (overruled) check.">▣ ${label} · cited by ${c.cited_by}${recent}</span>`;
    }
    card.innerHTML = `
      <div class="row1"><span class="num">${n}</span><span class="title">${escapeHtml(c.title)}</span><span class="verified">Verified</span></div>
      <div class="meta">${escapeHtml(c.court || '')}${c.date ? ' · ' + escapeHtml(c.date) : ''}${c.cite_count ? ' · cited by ' + c.cite_count : ''}</div>
      ${cyt ? `<div class="treatment">${cyt}</div>` : ''}
      ${cites ? `<div class="cites">${escapeHtml(cites)}</div>` : ''}
      ${c.snippet ? `<div class="snip">${escapeHtml(c.snippet.slice(0, 280))}…</div>` : ''}
      ${c.url ? `<a class="open" href="${escapeHtml(c.url)}" target="_blank" rel="noopener">Open full opinion ↗</a>` : ''}`;
    casesEl.appendChild(card);
  });
}

function renderStatutes(listEl, turnId, statutes) {
  listEl.innerHTML = '';
  statutes.forEach((s, i) => {
    const card = document.createElement('div');
    card.className = 'statute';
    card.id = `statute-${turnId}-${i + 1}`;
    card.innerHTML = `
      <div class="row1"><span class="rnum">R${i + 1}</span><span class="title">${escapeHtml(s.citation)}</span><span class="verified">Verified</span></div>
      ${s.heading ? `<div class="meta">${escapeHtml(s.heading)}</div>` : ''}
      ${s.excerpt ? `<div class="snip">${escapeHtml(s.excerpt.slice(0, 260))}…</div>` : ''}
      ${s.url ? `<a class="open" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">Open regulation ↗</a>` : ''}`;
    listEl.appendChild(card);
  });
}

// Click a [n] / [Rn] reference -> highlight the matching authority card.
chat.addEventListener('click', (e) => {
  const ref = e.target.closest('.cite-ref');
  if (!ref) return;
  const prefix = ref.dataset.kind === 'statute' ? 'statute' : 'case';
  const card = document.getElementById(`${prefix}-${ref.dataset.turn}-${ref.dataset.n}`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.style.borderColor = 'var(--accent)';
    setTimeout(() => { card.style.borderColor = ''; }, 1200);
  }
});

// Copy a turn's reasoning to the clipboard.
chat.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-btn');
  if (!btn) return;
  const ans = btn.closest('.turn')?.querySelector('.answer');
  const text = ans ? ans.innerText.trim() : '';
  if (!text || !navigator.clipboard) return;
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied'); btn.textContent = '✓ Copied';
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = '⧉ Copy'; }, 1500);
  }).catch(() => {});
});

async function send(text) {
  if (busy || !text.trim()) return;
  // Require sign-in before asking. The whole research flow (LLM + retrieval) is
  // gated behind an account, so a signed-out visitor who tries to ask is shown
  // the sign-in dialog instead. Wait for the initial auth check if it's still
  // in flight so we don't prompt a user who actually has a valid session.
  if (!authReady && authPromise) { try { await authPromise; } catch { /* ignore */ } }
  if (!me) { openLoginModal(text); return; }
  busy = true; sendBtn.disabled = true;
  document.querySelector('.intro')?.remove();

  addUser(text);
  pushHistory(text);
  messages.push({ role: 'user', content: text });

  const t = newBotTurn();
  let answerRaw = '';
  let clarified = '';

  try {
    const resp = await fetch(API_BASE + '/api/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, mode: currentMode }),
    });
    // Session expired (or never signed in): drop back to the sign-in gate and
    // re-queue this question so it's ready after they authenticate.
    if (resp.status === 401) {
      me = null; renderAccount();
      t.el.remove();
      messages.pop();
      busy = false; sendBtn.disabled = false;
      openLoginModal(text);
      return;
    }
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
        let ev = 'message', data = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) ev = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        }
        let obj = {};
        try { obj = JSON.parse(data); } catch { continue; }

        if (ev === 'status') {
          t.statusText.textContent = obj.message || '…';
          if (/^search/i.test(obj.message || '')) setStep(t.el, 'search', 'active', true);
          else setStep(t.el, 'analyze', 'active');
        } else if (ev === 'clarify') {
          clarified = obj.question || '';
          setStep(t.el, 'analyze', 'done');
          t.statusEl.style.display = 'none';
          t.answerEl.style.display = '';
          t.answerEl.className = 'answer clarify';
          t.answerEl.textContent = obj.question || '';
        } else if (ev === 'cases') {
          t.statusText.textContent = obj.count
            ? `Found ${obj.count} authorit${obj.count > 1 ? 'ies' : 'y'} for: ${obj.query}`
            : `No authorities for: ${obj.query}`;
          setStep(t.el, 'authorities', 'done', true);
          setStep(t.el, 'answer', 'active');
          if (obj.count) {
            t.authEl.style.display = '';
            t.authCnt.textContent = '· ' + obj.count;
            renderCases(t.casesEl, t.turnId, obj.cases || []);
          }
          scrollDown();
        } else if (ev === 'statutes') {
          if (obj.count) {
            t.statEl.style.display = '';
            t.statCnt.textContent = '· ' + obj.count;
            renderStatutes(t.statListEl, t.turnId, obj.statutes || []);
          }
          scrollDown();
        } else if (ev === 'token') {
          answerRaw += obj.text || '';
          setStep(t.el, 'answer', 'active', true);
          t.answerEl.style.display = '';
          t.answerEl.innerHTML = renderWithCites(answerRaw, t.turnId);
          scrollDown();
        } else if (ev === 'error') {
          t.answerEl.style.display = '';
          t.answerEl.className = 'answer note';
          t.answerEl.textContent = obj.message || 'Something went wrong.';
        } else if (ev === 'done') {
          if (!clarified) setStep(t.el, 'answer', 'done', true);
          t.statusEl.style.display = 'none';
          // Final pass: render the streamed answer as Markdown and expose Copy.
          if (answerRaw.trim()) {
            t.answerEl.className = 'answer rendered';
            t.answerEl.innerHTML = renderMarkdown(answerRaw, t.turnId);
            t.actionsEl.style.display = '';
          }
        }
      }
    }
  } catch (err) {
    t.statusEl.style.display = 'none';
    t.answerEl.style.display = '';
    t.answerEl.className = 'answer note';
    t.answerEl.textContent = 'Connection error: ' + err.message;
  }

  messages.push({ role: 'assistant', content: clarified || answerRaw || '(cases shown)' });
  busy = false; sendBtn.disabled = false;
  // Persist the thread to the account when signed in (durable, cross-device).
  autosaveSession();
  input.focus();
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = ''; input.style.height = 'auto';
  send(text);
});

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
});
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});

// ── Examples + New research ──────────────────────────────────────
function bindExamples() {
  document.querySelectorAll('.ex').forEach((b) =>
    b.addEventListener('click', () => { currentMode = 'chat'; send(b.textContent); }));
}
bindExamples();

// Start a fresh research session: clear the conversation and restore the intro.
function newResearch() {
  if (busy) return;
  messages.length = 0;
  turnSeq = 0;
  currentMode = 'chat';
  currentSessionId = null;
  chat.innerHTML = INTRO_HTML;
  bindExamples();
  input.placeholder = DEFAULT_PLACEHOLDER;
  input.value = ''; input.style.height = 'auto';
  input.focus();
}

// ── Sidebar: mobile drawer ───────────────────────────────────────
const app = document.querySelector('.app');
function closeNav() { app.classList.remove('nav-open'); }
document.getElementById('hamburger')?.addEventListener('click', () => app.classList.toggle('nav-open'));
app.addEventListener('click', (e) => {
  if (app.classList.contains('nav-open') && !e.target.closest('.sidebar') && !e.target.closest('.hamburger')) closeNav();
});

// ── Account + research history (backend when signed in, else localStorage) ──
// Signed-in users get durable, cross-device research history stored on their
// account; signed-out users get a local-only recent list (localStorage). The
// account block offers OAuth sign-in (only the providers the server has
// configured) and sign-out.
const HKEY = 'leagle-history';
const historyEl = document.getElementById('history');
const accountEl = document.getElementById('account');
let me = null;                 // current signed-in user, or null
let providers = [];            // configured OAuth providers, e.g. ['github']
let currentSessionId = null;   // backend id of the active thread (when signed in)
let authReady = false;         // true once /api/auth/me has resolved
let authPromise = null;        // the in-flight initial auth load

function api(path, opts = {}) {
  return fetch(API_BASE + path, { credentials: 'include', ...opts });
}

const PROVIDER_LABEL = { github: 'GitHub', google: 'Google' };

function renderAccount() {
  if (!accountEl) return;
  if (me) {
    const initial = (me.name || me.email || '?').trim().charAt(0).toUpperCase();
    const avatar = me.avatar_url
      ? `<img class="acc-avatar" src="${escapeHtml(me.avatar_url)}" alt="" />`
      : `<span class="acc-avatar acc-initial">${escapeHtml(initial)}</span>`;
    accountEl.innerHTML = `
      <div class="acc-user">
        ${avatar}
        <div class="acc-meta">
          <div class="acc-name">${escapeHtml(me.name || me.email || 'Signed in')}</div>
          <div class="acc-plan">${escapeHtml(me.plan || 'free')} plan</div>
        </div>
        <button class="acc-logout" id="logoutBtn" title="Sign out">⎋</button>
      </div>`;
    document.getElementById('logoutBtn').addEventListener('click', logout);
  } else if (providers.length) {
    const btns = providers.map((p) =>
      `<button class="acc-signin" data-provider="${p}">
         <span class="acc-ico">${p === 'github' ? '⌥' : '◉'}</span>
         Sign in with ${PROVIDER_LABEL[p] || p}
       </button>`).join('');
    accountEl.innerHTML = `
      <div class="acc-signin-wrap">
        <div class="acc-hint">Sign in to save your research across devices.</div>
        ${btns}
      </div>`;
    accountEl.querySelectorAll('.acc-signin').forEach((b) =>
      b.addEventListener('click', () => {
        const next = encodeURIComponent(location.href);
        location.href = `${API_BASE}/api/auth/${b.dataset.provider}/start?next=${next}`;
      }));
  } else {
    accountEl.innerHTML = '';
  }
}

// ── Sign-in gate modal (shown when a signed-out visitor tries to ask) ──
const loginModal = document.getElementById('loginModal');

function openLoginModal(pendingText) {
  if (!loginModal) return;
  // Stash the question so it's waiting in the composer after the OAuth round-trip.
  if (pendingText) { try { sessionStorage.setItem('leagle-pending-q', pendingText); } catch { /* ignore */ } }
  const body = loginModal.querySelector('.login-body');
  if (providers.length) {
    body.innerHTML = providers.map((p) =>
      `<button class="login-provider" data-provider="${p}">
         <span class="acc-ico">${p === 'github' ? '⌥' : '◉'}</span>
         Continue with ${PROVIDER_LABEL[p] || p}
       </button>`).join('');
    body.querySelectorAll('.login-provider').forEach((b) =>
      b.addEventListener('click', () => {
        const next = encodeURIComponent(location.href);
        location.href = `${API_BASE}/api/auth/${b.dataset.provider}/start?next=${next}`;
      }));
  } else {
    body.innerHTML = '<div class="login-hint">Sign-in is not available right now. Please try again later.</div>';
  }
  loginModal.classList.add('open');
  loginModal.setAttribute('aria-hidden', 'false');
}

function closeLoginModal() {
  if (!loginModal) return;
  loginModal.classList.remove('open');
  loginModal.setAttribute('aria-hidden', 'true');
}

document.getElementById('loginClose')?.addEventListener('click', closeLoginModal);
loginModal?.addEventListener('click', (e) => { if (e.target === loginModal) closeLoginModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLoginModal(); });

async function loadAuth() {
  try {
    const [meResp, provResp] = await Promise.all([
      api('/api/auth/me'),
      api('/api/auth/providers'),
    ]);
    me = meResp.ok ? await meResp.json() : null;
    providers = provResp.ok ? (await provResp.json()).providers || [] : [];
  } catch {
    me = null; providers = [];
  }
  authReady = true;
  renderAccount();
  refreshHistory();
}

async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
  me = null; currentSessionId = null;
  renderAccount();
  refreshHistory();
}

// Local (signed-out) history helpers.
function loadLocal() { try { return JSON.parse(localStorage.getItem(HKEY)) || []; } catch { return []; } }
function saveLocal(arr) { try { localStorage.setItem(HKEY, JSON.stringify(arr.slice(0, 30))); } catch (e) { /* ignore */ } }

function pushHistory(q) {
  if (me) return;            // signed-in history is saved server-side via autosave
  const arr = loadLocal().filter((x) => x.q !== q);
  arr.unshift({ q, t: Date.now() });
  saveLocal(arr);
  renderHistory();
}

async function refreshHistory() {
  if (me) {
    let sessions = [];
    try {
      const r = await api('/api/sessions');
      if (r.ok) sessions = (await r.json()).sessions || [];
    } catch { /* ignore */ }
    renderHistory(sessions);
  } else {
    renderHistory();
  }
}

function renderHistory(sessions) {
  historyEl.innerHTML = '';
  if (me) {
    (sessions || []).forEach((s) => {
      const b = document.createElement('button');
      b.className = 'hist-item';
      b.title = s.title;
      b.innerHTML = `<span class="ht-type">Research</span>${escapeHtml(s.title)}`;
      b.addEventListener('click', () => { closeNav(); openSession(s.id); });
      historyEl.appendChild(b);
    });
  } else {
    loadLocal().forEach((x) => {
      const b = document.createElement('button');
      b.className = 'hist-item';
      b.title = x.q;
      b.innerHTML = `<span class="ht-type">Research</span>${escapeHtml(x.q)}`;
      b.addEventListener('click', () => { closeNav(); currentMode = 'chat'; send(x.q); });
      historyEl.appendChild(b);
    });
  }
}

// Auto-save the active thread to the account after each completed turn.
async function autosaveSession() {
  if (!me || !messages.length) return;
  const title = (messages.find((m) => m.role === 'user') || {}).content || 'Research';
  try {
    const r = await api('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: currentSessionId, title: title.slice(0, 120), payload: messages }),
    });
    if (r.ok) { currentSessionId = (await r.json()).id; refreshHistory(); }
  } catch { /* ignore */ }
}

// Open a saved thread: load its transcript and render it read-only.
async function openSession(sessionId) {
  if (busy) return;
  let data;
  try {
    const r = await api('/api/sessions/' + sessionId);
    if (!r.ok) return;
    data = await r.json();
  } catch { return; }
  messages.length = 0;
  turnSeq = 0;
  currentSessionId = sessionId;
  currentMode = 'chat';
  chat.innerHTML = '';
  (data.payload || []).forEach((m) => {
    if (m.role === 'user') {
      addUser(m.content);
      messages.push({ role: 'user', content: m.content });
    } else if (m.role === 'assistant') {
      const el = document.createElement('div');
      el.className = 'turn bot';
      el.dataset.turn = ++turnSeq;
      el.innerHTML = `<div class="answer rendered">${renderMarkdown(m.content, turnSeq)}</div>`;
      chat.appendChild(el);
      messages.push({ role: 'assistant', content: m.content });
    }
  });
  scrollDown();
  input.focus();
}

authPromise = loadAuth();

// After an OAuth round-trip the user lands back here signed in; drop the
// question they were about to ask back into the composer so it's one tap to send.
try {
  const pending = sessionStorage.getItem('leagle-pending-q');
  if (pending) {
    sessionStorage.removeItem('leagle-pending-q');
    input.value = pending;
    input.dispatchEvent(new Event('input'));
    input.focus();
  }
} catch { /* ignore */ }

// ── Sidebar: nav items + research toolkit ────────────────────────
const TOOL_HINTS = {
  concept: 'Describe the legal concept or situation in plain English…',
  keyword: 'Enter keywords to search case law…',
  case: 'Enter a case name, e.g. Miranda v. Arizona',
  citation: 'Enter a citation, e.g. 384 U.S. 436',
};
document.querySelectorAll('.nav-item').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    // The leagleLM entry (data-action="new") starts a fresh reasoning session.
    if (b.dataset.action === 'new') { newResearch(); closeNav(); return; }
    const tool = b.dataset.tool;
    // Toolkit entry -> direct precise search in that mode.
    currentMode = (tool && TOOL_HINTS[tool]) ? tool : 'chat';
    if (tool && TOOL_HINTS[tool]) {
      input.placeholder = TOOL_HINTS[tool];
    } else {
      input.placeholder = DEFAULT_PLACEHOLDER;
    }
    input.focus();
    closeNav();
  });
});
