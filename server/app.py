"""leagle-chat backend — conversational retrieval over real US case law.

Boundary (hard rule): the LLM is ONLY a conversational front-end for SEARCH.
It turns the user's plain-English question into a precise query, and organizes
the *retrieved real cases* into a readable answer with citations. It never
invents cases and never gives a legal conclusion ("you will win/lose"). What
the user receives is always real primary-source material from CourtListener
plus citation links — not model-generated legal content.

Flow per turn:
  1. route()    — LLM turns the question into {action: search|clarify, query, court}
  2. if clarify — ask one clarifying question, stop (no search yet)
  3. search()   — CourtListener returns REAL cases
  4. organize() — LLM streams an answer grounded ONLY in those cases, citing [n]

Graceful degradation: if the LLM endpoint is unreachable, retrieval still works
(query falls back to the raw question; the real cases are returned without the
LLM's organizing layer). The product's core — real cases — never depends on the
model being up.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .courtlistener import CourtListener
from .statutes import ECFR, USCode
from . import llm
from . import db, auth, billing

HOST = os.getenv("LEAGLE_HOST", "127.0.0.1")
PORT = int(os.getenv("LEAGLE_PORT", "8600"))
WEB_DIR = os.getenv("LEAGLE_WEB_DIR", os.path.join(os.path.dirname(__file__), "..", "web"))
# Comma-separated allowed origins for the browser front-end (e.g. GitHub Pages).
# "*" allows any origin (fine here: the API is public, read-only, no cookies).
CORS_ORIGINS = os.getenv("LEAGLE_CORS_ORIGINS", "*")
# Per-IP rate limit on /api/chat. Each chat turn fans out to several LLM calls
# plus CourtListener / govinfo / eCFR requests, so it is the expensive endpoint
# and the one whose abuse would burn our upstream quotas (govinfo 1000/h, the
# LLM gateway, CourtListener). 0 disables the limit.
CHAT_MAX_PER_HOUR = int(os.getenv("LEAGLE_CHAT_MAX_PER_HOUR", "30"))

cl = CourtListener(api_token=os.getenv("COURTLISTENER_API_TOKEN"))
ecfr = ECFR()
uscode = USCode(os.getenv("GOVINFO_API_KEY", ""))
db.init_db()
app = FastAPI(title="leagle-chat")

# Credentialed (cookie) auth only works same-origin or with an explicit origin
# allow-list — the CORS spec forbids credentials with a "*" origin. The login /
# saved-sessions UI is served same-origin from the backend (no CORS involved);
# the wildcard cross-origin path (e.g. the GitHub Pages demo) stays read-only
# and uncredentialed.
_cors_origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
_allow_credentials = _cors_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class _ChatRateLimit:
    """Pure-ASGI per-IP sliding-hour rate limit for POST /api/chat.

    Pure ASGI (not a Starlette BaseHTTPMiddleware) so it never buffers the
    response body — essential for the SSE stream. Only the expensive chat
    endpoint is counted; static files, /api/health and CORS preflight pass
    through. Returns 429 with a short JSON body when over the limit.
    """

    def __init__(self, app, max_per_hour: int):
        self.app = app
        self.max = max_per_hour
        self._hits: dict[str, deque] = {}

    def _client_ip(self, scope) -> str:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        xri = headers.get("x-real-ip")
        if xri:
            return xri.strip()
        xff = headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _over_limit(self, ip: str) -> bool:
        now = time.time()
        bucket = self._hits.setdefault(ip, deque())
        while bucket and now - bucket[0] > 3600:
            bucket.popleft()
        if len(bucket) >= self.max:
            return True
        bucket.append(now)
        return False

    async def __call__(self, scope, receive, send):
        if (self.max > 0 and scope.get("type") == "http"
                and scope.get("method") == "POST"
                and scope.get("path", "").rstrip("/") == "/api/chat"):
            ip = self._client_ip(scope)
            if self._over_limit(ip):
                body = json.dumps({
                    "error": "rate_limited",
                    "message": f"Too many requests. Limit is {self.max} per hour. "
                               "Please slow down and try again later.",
                }).encode()
                await send({
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"3600"),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


app.add_middleware(_ChatRateLimit, max_per_hour=CHAT_MAX_PER_HOUR)

_ROUTE_SYSTEM = (
    "You are the front-end of JuriCodex, a US legal RESEARCH engine grounded in real "
    "case law AND federal regulations. Your job is to turn the user's plain-English "
    "message into a precise case-law search, OR — when key details are missing — ask "
    "ONE short clarifying question before researching.\n"
    "Return ONLY a JSON object: {\"action\": \"search\" | \"clarify\", "
    "\"query\": \"<keywords for full-text case-law search>\", "
    "\"court\": \"<optional CourtListener court id like scotus/ca9/cal, or empty>\", "
    "\"statute_query\": \"<keywords for federal regulation (CFR) search, ONLY if the "
    "question is plausibly governed by a FEDERAL rule — e.g. overtime/wage, workplace "
    "safety, environmental, benefits, food/drug, immigration procedure; else empty>\", "
    "\"clarify\": \"<one short question, only if action=clarify>\"}.\n"
    "Use statute_query ONLY for areas actually regulated federally. Leave it empty for "
    "purely state-law topics (landlord-tenant, most contracts, family, small claims).\n"
    "Clarify before answering when the jurisdiction, the procedural posture, a key "
    "fact, or which competing theory the user means is genuinely missing and would "
    "change which authorities matter. Otherwise prefer action=search. Keep queries "
    "concise and legally meaningful."
)

_ORGANIZE_SYSTEM = (
    "You are JuriCodex, a US legal RESEARCH assistant. You are given a user question "
    "and a numbered list of REAL court opinions retrieved from CourtListener. Reason "
    "through the question grounded in those authorities. Rules:\n"
    "1. Ground every legal statement ONLY in the provided cases. Never mention a case "
    "that is not in the list and never invent citations. Cite inline as [1], [2], … "
    "matching the numbers given.\n"
    "2. Work the question: state the rule the authorities establish, apply it to the "
    "facts the user gave, and explain what the answer depends on (which facts, which "
    "jurisdiction, which line of cases). It is fine to give a reasoned analysis.\n"
    "3. Be honest about limits: where the retrieved cases are mixed, distinguishable, "
    "or do not actually resolve the question, say so plainly rather than overstating. "
    "Flag where a result is uncertain or jurisdiction-specific.\n"
    "4. These are research results the user must verify against the primary sources "
    "(the linked opinions) before relying on them; for an actual decision they should "
    "consult a licensed attorney. State this briefly at the end, once. Be concise."
)

_ASSESS_SYSTEM = (
    "You are the retrieval-quality checker of a US legal research tool. Given the "
    "user's question and the list of cases a keyword search returned, decide whether "
    "those cases are actually on-point - same legal issue, and (if the user named one) "
    "the right jurisdiction. Judge by legal substance, not surface words.\n"
    "Return ONLY a JSON object: {\"relevant\": true|false, "
    "\"query\": \"<an improved full-text search query, only when relevant=false>\", "
    "\"court\": \"<optional CourtListener court id like scotus/ca9/cal, or empty>\"}.\n"
    "relevant=true if at least a few of the cases squarely address the user's issue. "
    "If the list is empty or off-topic, relevant=false and propose a BETTER query that "
    "changes strategy - use the controlling statute or doctrine name, the cause of "
    "action, or the jurisdiction - rather than mere synonyms of the last query."
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _route(messages: list[dict]) -> dict:
    """Ask the LLM to turn the conversation into a search plan. Degrade to raw query."""
    convo = [{"role": "system", "content": _ROUTE_SYSTEM}, *messages]
    try:
        plan = await llm.complete_json(convo, max_tokens=300)
    except (httpx.HTTPError, KeyError, ValueError):
        plan = {}
    if not plan or "action" not in plan:
        # Degrade: use the latest user message verbatim as the query.
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return {"action": "search", "query": last, "court": "", "degraded": True}
    return plan


def _case_block(cases: list) -> str:
    lines = []
    for i, c in enumerate(cases, 1):
        cite = "; ".join(c.citations[:3]) if c.citations else "no reporter cite"
        snip = (c.snippet or "")[:300]
        treat = ""
        if getattr(c, "cited_by", None) is not None:
            treat = (f" Treatment: cited by {c.cited_by} later opinions"
                     + (f", most recently {c.last_cited}" if c.last_cited else "")
                     + (f" ({c.treatment})." if c.treatment else "."))
        lines.append(
            f"[{i}] {c.title} — {c.court}, {c.date} ({cite}); "
            f"cited by {c.cite_count}.{treat} Excerpt: {snip}"
        )
    return "\n".join(lines)


def _case_titles(cases: list) -> str:
    """Compact one-line-per-case view for the relevance check (token-light)."""
    return "\n".join(
        f"[{i}] {c.title} - {c.court or '?'}" + (f", {c.date}" if c.date else "")
        for i, c in enumerate(cases, 1)
    ) or "(no cases found)"


async def _assess(question: str, cases: list) -> dict:
    """Judge whether retrieved cases are on-point; if not, propose a better query.

    Returns {"relevant": bool, "query": str, "court": str}. Degrades to
    relevant=True on any failure so a usable answer is never blocked by the check.
    """
    convo = [
        {"role": "system", "content": _ASSESS_SYSTEM},
        {"role": "user", "content": f"User question:\n{question}\n\n"
                                    f"Cases returned by the search:\n{_case_titles(cases)}"},
    ]
    try:
        verdict = await llm.complete_json(convo, max_tokens=200)
    except (httpx.HTTPError, KeyError, ValueError):
        return {"relevant": True}
    if not isinstance(verdict, dict) or "relevant" not in verdict:
        return {"relevant": True}
    return verdict


async def _organize(question: str, cases: list, statutes: list | None = None) -> AsyncIterator[str]:
    """Stream an answer grounded only in the retrieved cases (+ any CFR sections)."""
    parts = [
        f"User question:\n{question}\n",
        f"Retrieved real cases (the ONLY cases you may discuss):\n{_case_block(cases)}",
    ]
    if statutes:
        lines = [f"[R{i}] {s.citation}" + (f" — {s.heading}" if s.heading else "")
                 + (f": {(s.excerpt or '')[:200]}" if s.excerpt else "")
                 for i, s in enumerate(statutes, 1)]
        parts.append("Retrieved federal regulations (CFR) — real primary law you may "
                     "also cite, as [R1], [R2], …:\n" + "\n".join(lines))
    convo = [
        {"role": "system", "content": _ORGANIZE_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    async for delta in llm.stream_chat(convo, max_tokens=900):
        yield delta


@app.post("/api/chat")
async def chat(request: Request):
    # Asking requires an account: the research flow fans out to the LLM and
    # several upstream APIs, so it is gated behind sign-in (the frontend shows a
    # login modal on 401). Verified from the signed session cookie, not a header.
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    # Monthly question quota per plan. Count the ask up-front (atomic); refund it
    # if the request fails before producing anything. Over the limit -> 402 so
    # the frontend can show the upgrade prompt. An active one-off day pass raises
    # the effective plan to Max-level for its duration.
    eff = db.effective_plan(user)
    plan = db.PLANS.get(eff, db.PLANS[db.DEFAULT_PLAN])
    limit = int(plan["monthly_questions"])
    if not db.try_consume_question(user.id, limit):
        return JSONResponse(status_code=402, content={
            "error": "quota_exceeded",
            "plan": eff,
            "limit": limit,
            "message": f"You've used all {limit} research questions on the "
                       f"{plan['label']} plan this month. Upgrade for more.",
        })
    body = await request.json()
    messages = body.get("messages") or []
    messages = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in messages
        if m.get("content")
    ]
    if not messages:
        db.refund_question(user.id)
        return JSONResponse(status_code=400, content={"error": "no messages"})
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    # Toolkit mode: one of concept|keyword|case|citation runs a direct, precise
    # search (no conversational routing/answer). Default "chat" is the full
    # leagleLM reasoning flow.
    mode = str(body.get("mode") or "chat").strip().lower()

    async def gen() -> AsyncIterator[str]:
        # ── Toolkit: direct precise search, no LLM routing or answer ──────────
        if mode in ("concept", "keyword", "case", "citation"):
            label = {"concept": "by concept", "keyword": "by keyword",
                     "case": "by case name", "citation": "by citation"}[mode]
            yield _sse("status", {"message": f"Searching case law {label}: {question}"})
            try:
                cases = await cl.search(question, mode=mode, max_results=10)
            except httpx.HTTPError as exc:
                yield _sse("error", {"message": f"search failed: {exc}"})
                yield _sse("done", {})
                return
            if cases:
                yield _sse("status", {"message": "Verifying how these authorities have been treated…"})
                try:
                    await cl.attach_treatment(cases, top=8)
                except Exception:
                    pass
            yield _sse("cases", {"query": question, "court": "",
                                 "count": len(cases),
                                 "cases": [c.to_dict() for c in cases]})
            if not cases:
                yield _sse("token", {"text": "No matching case law was found. Try a "
                                     "different spelling, a fuller case name, or a "
                                     "precise reporter citation (e.g. 384 U.S. 436)."})
            yield _sse("done", {})
            return

        yield _sse("status", {"message": "Understanding your question…"})
        plan = await _route(messages)

        if plan.get("action") == "clarify" and plan.get("clarify"):
            yield _sse("clarify", {"question": plan["clarify"]})
            yield _sse("done", {})
            return

        query = (plan.get("query") or question).strip()
        court = (plan.get("court") or "").strip()

        # Search, then check the results are actually on-point; if not, let the
        # model refine the query and search again (bounded). This is the
        # "multi-step research, handled for you" loop.
        MAX_SEARCHES = 3
        cases: list = []
        tried: set = set()
        for attempt in range(1, MAX_SEARCHES + 1):
            yield _sse("status", {"message": f"Searching case law for: {query}"})
            try:
                cases = await cl.search(query, court=court, max_results=8)
            except httpx.HTTPError as exc:
                yield _sse("error", {"message": f"search failed: {exc}"})
                yield _sse("done", {})
                return
            tried.add(query.lower())
            if attempt == MAX_SEARCHES:
                break
            # Ask the model whether these results answer the question.
            verdict = await _assess(question, cases)
            if verdict.get("relevant", True):
                break
            new_query = (verdict.get("query") or "").strip()
            if not new_query or new_query.lower() in tried:
                break  # nothing better to try
            court = (verdict.get("court") or court).strip()
            yield _sse("status", {"message":
                       f"Those weren't on point - refining search: {new_query}"})
            query = new_query

        # Cytator: check how each leading case has been treated (cited by later
        # opinions, how recently) as a good-law signal. Best-effort, never fatal.
        if cases:
            yield _sse("status", {"message": "Verifying how these authorities have been treated…"})
            try:
                await cl.attach_treatment(cases, top=6)
            except Exception:
                pass

        yield _sse("cases", {"query": query, "court": court,
                             "count": len(cases),
                             "cases": [c.to_dict() for c in cases]})

        # Federal statutes (US Code) + regulations (CFR), when the router judged
        # the question to be plausibly governed by federal law. Queried in
        # parallel; best-effort, never fatal. US Code (the enacted law) is listed
        # before CFR (the implementing rules).
        statutes: list = []
        statute_query = (plan.get("statute_query") or "").strip()
        if statute_query:
            yield _sse("status", {"message": f"Searching federal statutes & regulations for: {statute_query}"})
            try:
                code_hits, cfr_hits = await asyncio.gather(
                    uscode.search(statute_query, max_results=4),
                    ecfr.search(statute_query, max_results=4),
                    return_exceptions=True,
                )
            except Exception:
                code_hits, cfr_hits = [], []
            statutes = []
            if isinstance(code_hits, list):
                statutes += code_hits
            if isinstance(cfr_hits, list):
                statutes += cfr_hits
            if statutes:
                yield _sse("statutes", {"query": statute_query,
                                        "count": len(statutes),
                                        "statutes": [s.to_dict() for s in statutes]})

        if not cases and not statutes:
            yield _sse("token", {"text": "No matching case law or federal regulation "
                                 "was found for this query. Try rephrasing with the "
                                 "parties, the legal issue, or a jurisdiction."})
            yield _sse("done", {})
            return

        # Organize the retrieved cases (LLM). Degrade gracefully if it is down.
        try:
            got_any = False
            async for delta in _organize(question, cases, statutes):
                got_any = True
                yield _sse("token", {"text": delta})
            if not got_any:
                yield _sse("token", {"text": "(Showing retrieved cases above; the "
                                     "summarizer returned nothing.)"})
        except httpx.HTTPError:
            yield _sse("token", {"text": "The summarizer is unavailable right now, "
                                 "but the real cases retrieved for your query are "
                                 "shown above — open any of them to read the source."})
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm": llm.LLM_BASE_URL, "model": llm.LLM_MODEL}


# ── Billing (Freemius): public config, quota, and the signed webhook ────────

@app.get("/api/config")
def config(request: Request) -> dict:
    """Feature flags + public checkout params for the frontend, plus the
    signed-in user's plan/quota when available."""
    out: dict = {"billing": billing.enabled(), "freemius": billing.public_config(),
                 "plans": db.PLANS}
    user = _current_user(request)
    if user:
        eff = db.effective_plan(user)
        plan = db.PLANS.get(eff, db.PLANS[db.DEFAULT_PLAN])
        used = db.usage_this_month(user.id)
        pass_end = db.active_day_pass_end(user.id)
        out["me"] = {
            "plan": eff,
            "limit": int(plan["monthly_questions"]),
            "used": used,
            "remaining": max(0, int(plan["monthly_questions"]) - used),
            "day_pass_until": pass_end,
        }
    return out


@app.post("/api/billing/freemius/webhook")
async def freemius_webhook(request: Request):
    if not billing.enabled():
        return JSONResponse(status_code=503, content={"error": "billing_not_configured"})
    raw = await request.body()
    sig = request.headers.get("x-signature", "") or request.headers.get("authorization", "")
    if not billing.verify_signature(raw, sig):
        return JSONResponse(status_code=401, content={"error": "bad_signature"})
    try:
        evt = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    result = billing.handle_event(evt)
    return JSONResponse(status_code=200, content=result)


# ── Authentication (OAuth sign-in + signed-cookie sessions) ─────────────────

def _current_user(request: Request) -> db.User | None:
    """Resolve the signed-in account from the session cookie, or None."""
    uid = auth.user_id_from_cookie(request.cookies.get(auth.COOKIE_NAME))
    return db.get_user(uid) if uid else None


@app.get("/api/auth/providers")
def auth_providers() -> dict:
    """Which OAuth providers are configured (so the UI shows only those)."""
    return {"providers": auth.configured_providers()}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    return user.to_public()


@app.get("/api/auth/{provider}/start")
def auth_start(provider: str, request: Request):
    if provider not in auth.configured_providers():
        return JSONResponse(status_code=404, content={"error": "provider_not_configured"})
    next_url = request.query_params.get("next", "")
    state = auth.make_state(provider, next_url)
    resp = RedirectResponse(auth.authorize_url(provider, state), status_code=302)
    # Echo state in a short-lived cookie too, to defend against CSRF on callback.
    resp.set_cookie("leagle_oauth_state", state, max_age=600, httponly=True,
                    secure=True, samesite="lax", path=auth.COOKIE_PATH)
    return resp


@app.get("/api/auth/{provider}/callback")
async def auth_callback(provider: str, request: Request):
    if provider not in auth.configured_providers():
        return JSONResponse(status_code=404, content={"error": "provider_not_configured"})
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    cookie_state = request.cookies.get("leagle_oauth_state", "")
    # CSRF: the state must verify AND match the one we set at /start.
    if not code or not state or state != cookie_state or not auth.read_state(state, provider):
        return JSONResponse(status_code=400, content={"error": "invalid_oauth_state"})
    try:
        user = await auth.exchange_code(provider, code)
    except httpx.HTTPError:
        user = None
    if not user:
        return JSONResponse(status_code=400, content={"error": "oauth_exchange_failed"})
    state_data = auth.read_state(state, provider) or {}
    dest = state_data.get("n") or (auth.PUBLIC_BASE + "/" if auth.PUBLIC_BASE else "/")
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie(auth.COOKIE_NAME, auth.make_session_cookie(user.id),
                    max_age=auth.COOKIE_MAX_AGE, httponly=True, secure=True,
                    samesite="lax", path=auth.COOKIE_PATH)
    resp.delete_cookie("leagle_oauth_state", path=auth.COOKIE_PATH)
    return resp


@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path=auth.COOKIE_PATH)
    return resp


# ── Saved research sessions (per signed-in user) ────────────────────────────

@app.get("/api/sessions")
def sessions_list(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    return {"sessions": db.list_sessions(user.id)}


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    s = db.get_session(user.id, session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return s


@app.post("/api/sessions")
async def sessions_save(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    body = await request.json()
    payload = body.get("payload")
    if not isinstance(payload, (list, dict)):
        return JSONResponse(status_code=400, content={"error": "bad_payload"})
    sid = db.save_session(user.id, session_id=body.get("id"),
                          title=str(body.get("title") or "Untitled research"),
                          payload=payload)
    return {"id": sid}


@app.delete("/api/sessions/{session_id}")
def sessions_delete(session_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    return {"ok": db.delete_session(user.id, session_id)}


# Static frontend (mounted last so /api/* takes precedence).
app.mount("/", StaticFiles(directory=os.path.abspath(WEB_DIR), html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
