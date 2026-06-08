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

function addUser(text) {
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  chat.appendChild(el);
  scrollDown();
}

function newBotTurn() {
  const turnId = ++turnSeq;
  const el = document.createElement('div');
  el.className = 'msg bot';
  el.dataset.turn = turnId;
  el.innerHTML = `
    <div class="status"><span class="spinner"></span><span class="status-text">…</span></div>
    <div class="cases"></div>
    <div class="answer" style="display:none"></div>`;
  chat.appendChild(el);
  scrollDown();
  return {
    turnId,
    statusEl: el.querySelector('.status'),
    statusText: el.querySelector('.status-text'),
    casesEl: el.querySelector('.cases'),
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
    card.innerHTML = `
      <div class="row1"><span class="num">${n}</span><span class="title">${escapeHtml(c.title)}</span></div>
      <div class="meta">${escapeHtml(c.court || '')}${c.date ? ' · ' + escapeHtml(c.date) : ''}${c.cite_count ? ' · cited by ' + c.cite_count : ''}</div>
      ${cites ? `<div class="cites">${escapeHtml(cites)}</div>` : ''}
      ${c.snippet ? `<div class="snip">${escapeHtml(c.snippet.slice(0, 280))}…</div>` : ''}
      ${c.url ? `<a class="open" href="${escapeHtml(c.url)}" target="_blank" rel="noopener">Open full opinion ↗</a>` : ''}`;
    casesEl.appendChild(card);
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
  messages.push({ role: 'user', content: text });

  const t = newBotTurn();
  let answerRaw = '';
  let clarified = '';

  try {
    const resp = await fetch(API_BASE + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
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
        } else if (ev === 'clarify') {
          clarified = obj.question || '';
          t.answerEl.style.display = '';
          t.answerEl.className = 'answer clarify';
          t.answerEl.textContent = obj.question || '';
        } else if (ev === 'cases') {
          t.statusText.textContent = obj.count
            ? `Found ${obj.count} case${obj.count > 1 ? 's' : ''} for: ${obj.query}`
            : `No cases for: ${obj.query}`;
          renderCases(t.casesEl, t.turnId, obj.cases || []);
          scrollDown();
        } else if (ev === 'token') {
          answerRaw += obj.text || '';
          t.answerEl.style.display = '';
          t.answerEl.innerHTML = renderWithCites(answerRaw, t.turnId);
          scrollDown();
        } else if (ev === 'error') {
          t.answerEl.style.display = '';
          t.answerEl.className = 'answer note';
          t.answerEl.textContent = obj.message || 'Something went wrong.';
        } else if (ev === 'done') {
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
