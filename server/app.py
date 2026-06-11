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
import re
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
    "Use statute_query ONLY for areas actually regulated federally. Leave it EMPTY for "
    "purely state-law topics. Examples where statute_query MUST be empty: a residential "
    "landlord withholding a security deposit (state landlord-tenant); a breach of a "
    "private services contract (state contract law); child custody or divorce (state "
    "family law); a slip-and-fall or car-accident negligence claim (state tort law); a "
    "neighbor boundary/property dispute (state property law); a small-claims debt; a "
    "state speeding ticket. Examples where statute_query IS appropriate: unpaid "
    "overtime (FLSA, 29 CFR), an OSHA workplace-safety citation, an EPA emissions "
    "permit, a denied Social Security/SSDI claim, an FDA labeling rule, removal/asylum "
    "procedure. When in doubt for an everyday state-court matter, leave it empty.\n"
    "Clarify before answering when the jurisdiction, the procedural posture, a key "
    "fact, or which competing theory the user means is genuinely missing and would "
    "change which authorities matter. Otherwise prefer action=search. Keep queries "
    "concise and legally meaningful."
)

_ORGANIZE_SYSTEM = (
    "You are JuriCodex, a US legal RESEARCH assistant. You are given a user question "
    "and a numbered list of REAL US court opinions. Reason "
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
    "4. When a research plan is provided, organize the answer around the issues "
    "considered and be explicit about what facts/jurisdiction the analysis depends on. "
    "End with a short 'What this depends on' section.\n"
    "5. These are research results the user must verify against the primary sources "
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

_RESEARCH_PLAN_SYSTEM = (
    "You plan source-backed US legal research. Given the user's question, split it "
    "into 2-4 legal issues only when useful, otherwise 1 issue. Return ONLY JSON: "
    "{\"summary\":\"short research plan\",\"depends_on\":[\"jurisdiction/fact/etc\"],"
    "\"issues\":[{\"label\":\"issue label\",\"query\":\"case-law search query\","
    "\"court\":\"optional CourtListener court id or empty\",\"statute_query\":"
    "\"optional federal law/rule query or empty\"}]}. Keep queries concise. "
    "Do not invent law. If facts/jurisdiction are missing, put that in depends_on."
)

_BRIEF_SUPPORT_SYSTEM = (
    "You are a legal citation-checking assistant. You are given a proposition from "
    "a brief, a resolved real case, optional verified quote result, and source "
    "passages from that case. Decide whether the case supports the proposition. "
    "Return ONLY JSON: {\"status\":\"Supports|Weak support|Unclear|Needs review\","
    "\"quote_accuracy\":\"Accurate|Not found|No quote|Needs review\","
    "\"reason\":\"one concise sentence\"}. Be conservative. If the passages do "
    "not squarely support the proposition, use Weak support or Unclear. Never say "
    "a case supports a proposition based on the case name alone."
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


async def _research_plan(messages: list[dict], fallback_query: str, route_plan: dict | None = None) -> dict:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), fallback_query)
    convo = [
        {"role": "system", "content": _RESEARCH_PLAN_SYSTEM},
        {"role": "user", "content": f"User question:\n{last}\n\nInitial router plan:\n{json.dumps(route_plan or {}, ensure_ascii=False)}"},
    ]
    try:
        plan = await llm.complete_json(convo, max_tokens=650)
    except (httpx.HTTPError, KeyError, ValueError):
        plan = {}
    issues = plan.get("issues") if isinstance(plan, dict) else None
    if not isinstance(issues, list) or not issues:
        rp = route_plan or {}
        issues = [{
            "label": "Primary issue",
            "query": (rp.get("query") or fallback_query or last).strip(),
            "court": (rp.get("court") or "").strip(),
            "statute_query": (rp.get("statute_query") or "").strip(),
        }]
        plan = {"summary": "Search primary-law authorities and organize the answer.",
                "depends_on": [], "issues": issues, "degraded": True}
    cleaned = []
    for i, issue in enumerate(issues[:4], 1):
        if not isinstance(issue, dict):
            continue
        q = (issue.get("query") or fallback_query or last).strip()
        if not q:
            continue
        cleaned.append({
            "label": (issue.get("label") or f"Issue {i}").strip()[:120],
            "query": q[:240],
            "court": (issue.get("court") or "").strip()[:40],
            "statute_query": (issue.get("statute_query") or "").strip()[:180],
        })
    if not cleaned:
        cleaned = [{"label": "Primary issue", "query": fallback_query or last, "court": "", "statute_query": ""}]
    return {"summary": (plan.get("summary") or "Research the issue with primary-law sources.")[:300],
            "depends_on": [str(x)[:140] for x in (plan.get("depends_on") or [])[:5]],
            "issues": cleaned}


async def _brief_support_check(ref: dict, case: object, quote_check: dict | None, passages: list[dict]) -> dict:
    quote = ref.get("quote") or ""
    if quote and quote_check and not quote_check.get("found"):
        return {"status": "Quote not found", "quote_accuracy": "Not found",
                "reason": "The nearby quoted language was not found in the matched opinion text."}
    if not passages:
        return {"status": "Needs review", "quote_accuracy": "No quote" if not quote else "Needs review",
                "reason": "No source passage was available for a reliable support check."}
    payload = {
        "proposition": ref.get("proposition") or ref.get("context") or ref.get("text"),
        "reference": ref.get("text"),
        "quote": quote,
        "case": getattr(case, "title", ""),
        "citations": getattr(case, "citations", []),
        "quote_check": quote_check or {},
        "passages": [p.get("text", "")[:900] for p in passages[:3]],
    }
    convo = [
        {"role": "system", "content": _BRIEF_SUPPORT_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        out = await llm.complete_json(convo, max_tokens=250)
    except (httpx.HTTPError, KeyError, ValueError):
        out = {}
    status = out.get("status") if isinstance(out, dict) else ""
    allowed = {"Supports", "Weak support", "Unclear", "Needs review"}
    if status not in allowed:
        status = "Needs review"
    qa = out.get("quote_accuracy") if isinstance(out, dict) else ""
    if qa not in {"Accurate", "Not found", "No quote", "Needs review"}:
        qa = "Accurate" if quote_check and quote_check.get("found") else ("No quote" if not quote else "Needs review")
    return {"status": status, "quote_accuracy": qa,
            "reason": (out.get("reason") if isinstance(out, dict) else "") or "Review the source passage before relying on this citation."}


async def _organize(question: str, cases: list, statutes: list | None = None,
                    research_plan: dict | None = None) -> AsyncIterator[str]:
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
    if research_plan:
        parts.append("Research plan / issues considered:\n" + json.dumps(research_plan, ensure_ascii=False))
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
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    messages = body.get("messages") or []
    messages = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in messages
        if m.get("content")
    ]
    if not messages:
        return JSONResponse(status_code=400, content={"error": "no messages"})
    # Monthly question quota per plan. Counted atomically only AFTER the request
    # is known-valid; refunded inside gen() on any path that delivers no value
    # (search outage / nothing found) so a failed request never burns a question.
    # An active one-off day pass raises the effective plan to Max-level.
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
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    # Toolkit mode: concept|keyword|case|citation runs direct precise search;
    # brief runs a source-backed citation/quote review over pasted legal text.
    # Default "chat" is the full JuriCodex Legal Reasoning Engine flow.
    mode = str(body.get("mode") or "chat").strip().lower()

    async def gen() -> AsyncIterator[str]:
        # Refund the consumed question on any path that delivers no value (search
        # outage or nothing found). Idempotent so we never double-credit.
        refunded = {"v": False}

        def refund() -> None:
            if not refunded["v"]:
                refunded["v"] = True
                try:
                    db.refund_question(user.id)
                except Exception:
                    pass

        # ── Standalone Citation Extractor ────────────────────────────────────
        if mode == "extractor":
            yield _sse("status", {"message": "Extracting case citations and case names…"})
            refs = _extract_legal_references(question)
            if not refs:
                refund()
            yield _sse("citation_extract", {"count": len(refs), "refs": refs})
            yield _sse("token", {"text": f"Citation Extractor found {len(refs)} unique case reference(s)." if refs else "No recognizable case citations or case names were found."})
            yield _sse("done", {})
            return

        # ── Standalone Case Resolver ─────────────────────────────────────────
        if mode == "resolver":
            yield _sse("status", {"message": "Resolving reference to candidate cases…"})
            kind = "citation" if _REPORTER_CITE_RE.search(question) else ("case" if " v." in question else "")
            try:
                if kind == "citation":
                    cases = await cl.search(question, mode="citation", max_results=8)
                elif kind == "case":
                    cases = await cl.search(question, mode="case", max_results=8)
                else:
                    cases = await cl.search(question, mode="keyword", max_results=8)
            except httpx.HTTPError as exc:
                refund()
                yield _sse("error", {"message": f"resolver failed: {exc}"})
                yield _sse("done", {})
                return
            if not cases:
                refund()
            yield _sse("cases", {"query": question, "court": "", "count": len(cases),
                                 "cases": [c.to_dict() for c in cases]})
            yield _sse("token", {"text": f"Case Resolver returned {len(cases)} candidate(s). Open Details / PDFs to inspect source metadata." if cases else "No candidate case was found for that reference."})
            yield _sse("done", {})
            return

        # ── Laws & Rules: direct US Code + CFR search ────────────────────────
        if mode == "laws":
            yield _sse("status", {"message": f"Searching US Code and CFR for: {question}"})
            try:
                code_hits, cfr_hits = await asyncio.gather(
                    uscode.search(question, max_results=6),
                    ecfr.search(question, max_results=6),
                    return_exceptions=True,
                )
            except Exception:
                code_hits, cfr_hits = [], []
            statutes = []
            if isinstance(code_hits, list):
                statutes += code_hits
            if isinstance(cfr_hits, list):
                statutes += cfr_hits
            if not statutes:
                refund()
            yield _sse("statutes", {"query": question, "count": len(statutes),
                                    "statutes": [s.to_dict() for s in statutes]})
            yield _sse("token", {"text": f"Laws & Rules Search found {len(statutes)} statute/regulation source(s)." if statutes else "No matching federal statute or regulation was found."})
            yield _sse("done", {})
            return

        # ── Brief Review: extract refs, resolve, quote-check, support-check ──
        if mode == "brief":
            yield _sse("status", {"message": "Extracting citations and case references…"})
            refs = _extract_legal_references(question)
            if not refs:
                refund()
                yield _sse("brief_review", {"count": 0, "rows": []})
                yield _sse("token", {"text": "No recognizable case citations or case names were found. Paste a brief, memo, or argument with reporter citations (for example, 384 U.S. 436) or full case names."})
                yield _sse("done", {})
                return
            yield _sse("status", {"message": f"Resolving {len(refs)} reference(s) against primary-law sources…"})

            async def resolve_one(ref: dict) -> dict:
                row = {"ref": ref, "case": None, "quote_check": None, "status": "Case unresolved"}
                try:
                    case = await cl.resolve_reference(ref["text"], kind=ref["kind"])
                except Exception:
                    case = None
                if not case:
                    return row
                try:
                    await cl.attach_treatment([case], top=1)
                except Exception:
                    pass
                row["case"] = case.to_dict()
                row["status"] = "resolved"
                if ref.get("quote"):
                    try:
                        row["quote_check"] = await cl.verify_quote(case.id, ref["quote"])
                    except Exception:
                        row["quote_check"] = {"found": False, "match": "error", "context": ""}
                try:
                    passages = await cl.focused_passages(case.id, ref.get("quote") or ref.get("context") or ref["text"], limit=3)
                except Exception:
                    passages = []
                row["passages"] = passages
                try:
                    row["support_check"] = await _brief_support_check(ref, case, row.get("quote_check"), passages)
                    row["status"] = row["support_check"].get("status") or row["status"]
                except Exception:
                    row["support_check"] = {"status": "Needs review", "quote_accuracy": "Needs review", "reason": "Support check was unavailable."}
                    row["status"] = "Needs review"
                return row

            rows = await asyncio.gather(*(resolve_one(r) for r in refs), return_exceptions=True)
            clean_rows = [r for r in rows if isinstance(r, dict)]
            yield _sse("brief_review", {"count": len(clean_rows), "rows": clean_rows})
            resolved = sum(1 for r in clean_rows if r.get("case"))
            checked = sum(1 for r in clean_rows if r.get("quote_check"))
            found = sum(1 for r in clean_rows if (r.get("quote_check") or {}).get("found"))
            supports = sum(1 for r in clean_rows if (r.get("support_check") or {}).get("status") == "Supports")
            yield _sse("token", {"text":
                f"Brief Review checked {len(clean_rows)} extracted reference(s): "
                f"{resolved} resolved to source-backed cases. "
                + (f"{found}/{checked} nearby quote(s) were found in the matched opinions. " if checked else "")
                + f"{supports} citation(s) were marked as directly supporting the extracted proposition. "
                + "Use the table above as a verification checklist, not legal advice."})
            yield _sse("done", {})
            return

        # ── Toolkit: direct precise search, no LLM routing or answer ──────────
        if mode in ("concept", "keyword", "case", "citation"):
            label = {"concept": "by concept", "keyword": "by keyword",
                     "case": "by case name", "citation": "by citation"}[mode]
            yield _sse("status", {"message": f"Searching case law {label}: {question}"})
            try:
                cases = await cl.search(question, mode=mode, max_results=10)
            except httpx.HTTPError as exc:
                refund()
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
                refund()
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

        research_plan = await _research_plan(messages, question, plan)
        yield _sse("research_plan", research_plan)

        # Search each planned issue, then check/refine results when needed.
        MAX_SEARCHES = 3
        cases: list = []
        seen_case_ids: set[str] = set()
        for issue in (research_plan.get("issues") or [])[:4]:
            query = (issue.get("query") or question).strip()
            court = (issue.get("court") or "").strip()
            tried: set = set()
            issue_cases: list = []
            for attempt in range(1, MAX_SEARCHES + 1):
                yield _sse("status", {"message": f"Searching {issue.get('label') or 'issue'}: {query}"})
                try:
                    issue_cases = await cl.search(query, court=court, max_results=6)
                except httpx.HTTPError as exc:
                    refund()
                    yield _sse("error", {"message": f"search failed: {exc}"})
                    yield _sse("done", {})
                    return
                tried.add(query.lower())
                if attempt == MAX_SEARCHES:
                    break
                verdict = await _assess(question, issue_cases)
                if verdict.get("relevant", True):
                    break
                new_query = (verdict.get("query") or "").strip()
                if not new_query or new_query.lower() in tried:
                    break
                court = (verdict.get("court") or court).strip()
                yield _sse("status", {"message": f"Refining {issue.get('label') or 'issue'}: {new_query}"})
                query = new_query
            for c in issue_cases:
                cid = str(getattr(c, "id", ""))
                if cid and cid not in seen_case_ids:
                    seen_case_ids.add(cid)
                    cases.append(c)
        cases = sorted(cases, key=lambda c: int(getattr(c, "cite_count", 0) or 0), reverse=True)[:10]

        # Cytator: check how each leading case has been treated (cited by later
        # opinions, how recently) as a good-law signal. Best-effort, never fatal.
        if cases:
            yield _sse("status", {"message": "Verifying how these authorities have been treated…"})
            try:
                await cl.attach_treatment(cases, top=6)
            except Exception:
                pass

        display_query = "; ".join(
            i.get("query", "") for i in (research_plan.get("issues") or [])[:4]
            if i.get("query")
        ) or question
        display_court = next(
            (i.get("court", "") for i in (research_plan.get("issues") or []) if i.get("court")), ""
        )
        yield _sse("cases", {"query": display_query, "court": display_court,
                             "count": len(cases),
                             "cases": [c.to_dict() for c in cases]})

        # Federal statutes (US Code) + regulations (CFR), when the router judged
        # the question to be plausibly governed by federal law. Queried in
        # parallel; best-effort, never fatal. US Code (the enacted law) is listed
        # before CFR (the implementing rules).
        statutes: list = []
        statute_queries = []
        for issue in (research_plan.get("issues") or [])[:4]:
            sq = (issue.get("statute_query") or "").strip()
            if sq and sq.lower() not in {x.lower() for x in statute_queries}:
                statute_queries.append(sq)
        if not statute_queries and (plan.get("statute_query") or "").strip():
            statute_queries = [(plan.get("statute_query") or "").strip()]
        if statute_queries:
            statute_query = statute_queries[0]
            yield _sse("status", {"message": f"Searching federal statutes & regulations for: {statute_query}"})
            try:
                tasks = []
                for sq in statute_queries[:3]:
                    tasks += [uscode.search(sq, max_results=3), ecfr.search(sq, max_results=3)]
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                results = []
            statutes = []
            seen_stat = set()
            for res in results:
                if isinstance(res, list):
                    for s in res:
                        key = getattr(s, "url", "") or getattr(s, "citation", "")
                        if key and key not in seen_stat:
                            seen_stat.add(key); statutes.append(s)
            if statutes:
                yield _sse("statutes", {"query": "; ".join(statute_queries),
                                        "count": len(statutes),
                                        "statutes": [s.to_dict() for s in statutes]})

        if not cases and not statutes:
            refund()
            yield _sse("token", {"text": "No matching case law or federal regulation "
                                 "was found for this query. Try rephrasing with the "
                                 "parties, the legal issue, or a jurisdiction."})
            yield _sse("done", {})
            return

        # Organize the retrieved cases (LLM). Degrade gracefully if it is down.
        try:
            got_any = False
            answer = ""
            async for delta in _organize(question, cases, statutes, research_plan):
                got_any = True
                answer += delta
                yield _sse("token", {"text": delta})
            if not got_any:
                yield _sse("token", {"text": "(Showing retrieved cases above; the "
                                     "summarizer returned nothing.)"})
            else:
                # Anti-hallucination guard: every [n] marker must point at a real
                # retrieved case. If the model invented an out-of-range index,
                # warn the reader rather than letting it pass silently.
                bad = _out_of_range_citations(answer, len(cases))
                if bad:
                    refs = ", ".join(f"[{i}]" for i in bad)
                    yield _sse("warning", {"message":
                        f"Citation check: marker(s) {refs} don't correspond to any "
                        f"retrieved case above and may be inaccurate — rely only on "
                        f"the {len(cases)} sources listed."})
        except httpx.HTTPError:
            yield _sse("token", {"text": "The summarizer is unavailable right now, "
                                 "but the real cases retrieved for your query are "
                                 "shown above — open any of them to read the source."})
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm": llm.LLM_BASE_URL, "model": llm.LLM_MODEL}


# Per-account sliding-minute rate limit for /api/verify-quote (cheap single
# fetch, but unauthenticated-cheap is still a scraping lever — cap it).
VERIFY_MAX_PER_MIN = int(os.getenv("LEAGLE_VERIFY_MAX_PER_MIN", "20"))
_verify_hits: dict[int, deque] = {}


def _verify_over_limit(user_id: int) -> bool:
    if VERIFY_MAX_PER_MIN <= 0:
        return False
    now = time.time()
    bucket = _verify_hits.setdefault(user_id, deque())
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= VERIFY_MAX_PER_MIN:
        return True
    bucket.append(now)
    return False


@app.post("/api/verify-quote")
async def verify_quote(request: Request):
    """Anti-hallucination check: confirm a quote really appears in a case's
    opinion text. Signed-in only; does not count against the chat quota (it is
    a cheap single fetch, and we want users to verify freely). Lightly rate
    limited per account so it can't be turned into a scraping/DoS lever."""
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    if _verify_over_limit(user.id):
        return JSONResponse(status_code=429, content={
            "error": "rate_limited",
            "message": "Too many verification requests. Please slow down and try "
                       "again in a moment.",
        }, headers={"retry-after": "60"})
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    cluster_id = str(body.get("cluster_id") or "").strip()
    quote = str(body.get("quote") or "").strip()
    if not cluster_id or not quote:
        return JSONResponse(status_code=400, content={"error": "cluster_id and quote required"})
    try:
        result = await cl.verify_quote(cluster_id, quote)
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": f"verify failed: {exc}"})
    return result


@app.get("/api/case-details/{cluster_id}")
async def case_details(cluster_id: str, request: Request):
    """Case metadata + opinion inventory/PDF links for a known cluster.

    Signed-in only. Used by authority cards and Brief Review rows so users can
    inspect source metadata without leaving the workspace.
    """
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    focus = request.query_params.get("focus", "")[:800]
    try:
        result = await cl.case_details(cluster_id, focus=focus)
    except httpx.HTTPError as exc:
        return JSONResponse(status_code=502, content={"error": f"details failed: {exc}"})
    return result


# Extension-less aliases for the legal pages (referenced by Google's OAuth
# consent screen and Freemius checkout as /terms and /privacy).
@app.get("/terms")
def terms_alias():
    return RedirectResponse("/terms.html", status_code=302)


@app.get("/privacy")
def privacy_alias():
    return RedirectResponse("/privacy.html", status_code=302)


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


def _safe_redirect(next_url: str | None) -> str:
    """Only allow post-login redirects to our own site — never an external URL
    (prevents the OAuth flow being abused for phishing/open-redirect)."""
    from urllib.parse import urlparse
    base = auth.PUBLIC_BASE or ""
    home = (base + "/") if base else "/"
    if not next_url:
        return home
    try:
        p = urlparse(next_url)
    except ValueError:
        return home
    # Relative path (no scheme/host) is fine.
    if not p.scheme and not p.netloc:
        return next_url if next_url.startswith("/") else home
    # Absolute URL: must match our public host.
    if base:
        bp = urlparse(base)
        if p.scheme in ("http", "https") and p.netloc == bp.netloc:
            return next_url
    return home


_CITE_RE = re.compile(r"\[(\d{1,3})\]")

# MVP citation/reference extraction for Brief Review. This is intentionally
# deterministic regex + source lookup. ML/LLM extraction can be layered later,
# but this already catches common reporter citations and full case names.
_REPORTER_CITE_RE = re.compile(
    r"\b\d{1,4}\s+(?:"
    r"U\.S\.|S\.\s?Ct\.|L\.\s?Ed\.\s?2d|"
    r"F\.\s?\d+d|F\.\s?Supp\.?\s?\d*d?|F\.\s?App'?x|"
    r"Cal\.\s?\d+(?:th)?|N\.Y\.\s?\d+d|A\.\s?\d+d|"
    r"S\.W\.\s?\d+d|N\.E\.\s?\d+d|N\.W\.\s?\d+d|P\.\s?\d+d"
    r")\s+\d{1,5}\b",
    re.I,
)
_CASE_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&'’.,\- ]{2,80}\s+v\.\s+"
    r"[A-Z][A-Za-z0-9&'’.,\- ]{2,80}\b"
)
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{20,900})[\"”]")


def _out_of_range_citations(text: str, n_cases: int) -> list[int]:
    """Return the sorted set of [n] citation markers in *text* that point past
    the list of retrieved cases (n < 1 or n > n_cases) — i.e. invented refs."""
    bad: set[int] = set()
    for m in _CITE_RE.finditer(text or ""):
        n = int(m.group(1))
        if n < 1 or n > n_cases:
            bad.add(n)
    return sorted(bad)


def _nearest_quote(text: str, start: int, end: int) -> str:
    best: tuple[int, str] | None = None
    for q in _QUOTE_RE.finditer(text or ""):
        dist = min(abs(q.end() - start), abs(q.start() - end))
        if dist <= 700 and (best is None or dist < best[0]):
            best = (dist, q.group(1).strip())
    return best[1] if best else ""


def _reference_context(text: str, start: int, end: int, *, pad: int = 320) -> str:
    raw = text or ""
    left = max(0, start - pad)
    right = min(len(raw), end + pad)
    lstop = max(raw.rfind(".", left, start), raw.rfind(";", left, start), raw.rfind("\n", left, start))
    rstop_candidates = [x for x in [raw.find(".", end, right), raw.find(";", end, right), raw.find("\n", end, right)] if x != -1]
    if lstop != -1:
        left = max(left, lstop + 1)
    if rstop_candidates:
        right = min(right, min(rstop_candidates) + 1)
    return re.sub(r"\s+", " ", raw[left:right]).strip()


def _extract_legal_references(text: str, *, limit: int = 24) -> list[dict]:
    refs: list[dict] = []
    seen: set[str] = set()

    def add(kind: str, value: str, start: int, end: int) -> None:
        clean = re.sub(r"\s+", " ", value or "").strip(" ,.;")
        key = f"{kind}:{clean.lower()}"
        if not clean or key in seen or len(refs) >= limit:
            return
        seen.add(key)
        refs.append({
            "kind": kind,
            "text": clean,
            "quote": _nearest_quote(text, start, end),
            "context": _reference_context(text, start, end),
        })

    for m in _REPORTER_CITE_RE.finditer(text or ""):
        add("citation", m.group(0), m.start(), m.end())
    for m in _CASE_NAME_RE.finditer(text or ""):
        # Avoid treating long ordinary prose as a caption; require a compact-ish
        # full case reference with the canonical "v." marker.
        value = re.sub(r"\s+", " ", m.group(0))
        if len(value) <= 140:
            add("case", value, m.start(), m.end())
    for ref in refs:
        ctx = ref.get("context") or ref.get("text") or ""
        ref["proposition"] = ctx.replace(ref.get("text", ""), "").strip(" ,;.") or ctx
    return refs


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
    # Providers that require PKCE (X) get a verifier (stashed in the signed state)
    # and a derived challenge on the authorize URL.
    verifier = challenge = ""
    if auth.PROVIDERS[provider].get("pkce"):
        verifier, challenge = auth.make_pkce()
    state = auth.make_state(provider, next_url, verifier)
    resp = RedirectResponse(auth.authorize_url(provider, state, challenge), status_code=302)
    # Echo state in a short-lived cookie too, to defend against CSRF on callback.
    resp.set_cookie("leagle_oauth_state", state, max_age=600, httponly=True,
                    secure=True, samesite="lax", path=auth.COOKIE_PATH)
    return resp


@app.get("/api/auth/{provider}/callback")
async def auth_callback(provider: str, request: Request):
    if provider not in auth.configured_providers():
        return JSONResponse(status_code=404, content={"error": "provider_not_configured"})
    base = auth.PUBLIC_BASE or ""
    failed_dest = (base or "") + "/?auth_error=1"
    if request.query_params.get("error"):
        return RedirectResponse(failed_dest, status_code=302)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    cookie_state = request.cookies.get("leagle_oauth_state", "")
    # CSRF: the state must verify AND match the one we set at /start.
    if not code or not state or state != cookie_state or not auth.read_state(state, provider):
        return JSONResponse(status_code=400, content={"error": "invalid_oauth_state"})
    state_data = auth.read_state(state, provider) or {}
    try:
        user = await auth.exchange_code(provider, code, state_data.get("v", ""))
    except httpx.HTTPError:
        user = None
    if not user:
        # Bounce back to the home page with an error flag the frontend can show.
        return RedirectResponse(failed_dest, status_code=302)
    # Apply any purchase that was parked before this account was matchable
    # (e.g. paid with a real email after signing in via X under a synthetic one).
    try:
        billing.reconcile_pending(user)
    except Exception:
        pass
    dest = _safe_redirect(state_data.get("n"))
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
