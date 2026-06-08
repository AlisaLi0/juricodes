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
const form = document.getElementById('composer');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');

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

// Turn [1], [2] … into clickable references scoped to this turn's case list.
function renderWithCites(text, turnId) {
  return escapeHtml(text).replace(/\[(\d{1,2})\]/g, (m, n) =>
    `<span class="cite-ref" data-turn="${turnId}" data-n="${n}">[${n}]</span>`);
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
    <div class="answer" style="display:none"></div>`;
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

function renderStatutes(listEl, statutes) {
  listEl.innerHTML = '';
  statutes.forEach((s, i) => {
    const card = document.createElement('div');
    card.className = 'statute';
    card.innerHTML = `
      <div class="row1"><span class="rnum">R${i + 1}</span><span class="title">${escapeHtml(s.citation)}</span><span class="verified">Verified</span></div>
      ${s.heading ? `<div class="meta">${escapeHtml(s.heading)}</div>` : ''}
      ${s.excerpt ? `<div class="snip">${escapeHtml(s.excerpt.slice(0, 260))}…</div>` : ''}
      ${s.url ? `<a class="open" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">Open regulation ↗</a>` : ''}`;
    listEl.appendChild(card);
  });
}

// Click a [n] reference -> highlight the matching case card.
chat.addEventListener('click', (e) => {
  const ref = e.target.closest('.cite-ref');
  if (!ref) return;
  const card = document.getElementById(`case-${ref.dataset.turn}-${ref.dataset.n}`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.style.borderColor = 'var(--accent)';
    setTimeout(() => { card.style.borderColor = ''; }, 1200);
  }
});

async function send(text) {
  if (busy || !text.trim()) return;
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, mode: currentMode }),
    });
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
            renderStatutes(t.statListEl, obj.statutes || []);
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

document.querySelectorAll('.ex').forEach((b) =>
  b.addEventListener('click', () => { send(b.textContent); }));

// ── Sidebar: mobile drawer ───────────────────────────────────────
const app = document.querySelector('.app');
function closeNav() { app.classList.remove('nav-open'); }
document.getElementById('hamburger')?.addEventListener('click', () => app.classList.toggle('nav-open'));
app.addEventListener('click', (e) => {
  if (app.classList.contains('nav-open') && !e.target.closest('.sidebar') && !e.target.closest('.hamburger')) closeNav();
});

// ── Sidebar: research history (localStorage) ─────────────────────
const HKEY = 'leagle-history';
const historyEl = document.getElementById('history');
function loadHistory() { try { return JSON.parse(localStorage.getItem(HKEY)) || []; } catch { return []; } }
function saveHistory(arr) { try { localStorage.setItem(HKEY, JSON.stringify(arr.slice(0, 30))); } catch (e) { /* ignore */ } }
function pushHistory(q) {
  const arr = loadHistory().filter((x) => x.q !== q);
  arr.unshift({ q, t: Date.now() });
  saveHistory(arr);
  renderHistory();
}
function renderHistory() {
  const arr = loadHistory();
  historyEl.innerHTML = '';
  arr.forEach((x) => {
    const b = document.createElement('button');
    b.className = 'hist-item';
    b.title = x.q;
    b.innerHTML = `<span class="ht-type">Research</span>${escapeHtml(x.q)}`;
    b.addEventListener('click', () => { closeNav(); currentMode = 'chat'; send(x.q); });
    historyEl.appendChild(b);
  });
}
renderHistory();

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
    const tool = b.dataset.tool;
    // Toolkit entry -> direct precise search in that mode. The leagleLM entry
    // (data-action="new") goes back to the full conversational reasoning flow.
    currentMode = (tool && TOOL_HINTS[tool]) ? tool : 'chat';
    if (tool && TOOL_HINTS[tool]) {
      input.placeholder = TOOL_HINTS[tool];
    } else {
      input.placeholder = 'Ask in plain English…  (e.g. wrongful termination after reporting safety violations)';
    }
    input.focus();
    closeNav();
  });
});
