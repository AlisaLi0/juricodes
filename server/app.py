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

import json
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .courtlistener import CourtListener
from . import llm

HOST = os.getenv("LEAGLE_HOST", "127.0.0.1")
PORT = int(os.getenv("LEAGLE_PORT", "8600"))
WEB_DIR = os.getenv("LEAGLE_WEB_DIR", os.path.join(os.path.dirname(__file__), "..", "web"))
# Comma-separated allowed origins for the browser front-end (e.g. GitHub Pages).
# "*" allows any origin (fine here: the API is public, read-only, no cookies).
CORS_ORIGINS = os.getenv("LEAGLE_CORS_ORIGINS", "*")

cl = CourtListener(api_token=os.getenv("COURTLISTENER_API_TOKEN"))
app = FastAPI(title="leagle-chat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS.split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

_ROUTE_SYSTEM = (
    "You are the search front-end of a US legal RESEARCH tool. You do NOT give "
    "legal advice or opinions. Your only job is to turn the user's plain-English "
    "message into a precise case-law search, OR — if the message is too vague to "
    "search well — ask ONE short clarifying question.\n"
    "Return ONLY a JSON object: {\"action\": \"search\" | \"clarify\", "
    "\"query\": \"<keywords for full-text case-law search>\", "
    "\"court\": \"<optional CourtListener court id like scotus/ca9/cal, or empty>\", "
    "\"clarify\": \"<one short question, only if action=clarify>\"}.\n"
    "Prefer action=search. Only clarify when jurisdiction or the legal issue is "
    "genuinely missing. Keep query concise and legally meaningful."
)

_ORGANIZE_SYSTEM = (
    "You are a US legal RESEARCH assistant. You are given a user question and a "
    "numbered list of REAL court opinions retrieved from CourtListener. Follow "
    "these rules strictly:\n"
    "1. Use ONLY the provided cases. Never mention a case that is not in the list. "
    "Never invent citations.\n"
    "2. Do NOT give a legal conclusion or predict an outcome (never say whether the "
    "user will win, what they should do, or what the law 'means' for them). You are "
    "not a lawyer and this is not legal advice.\n"
    "3. Instead, explain WHICH retrieved cases are relevant and WHY (what each one "
    "is about), citing them inline as [1], [2], etc. matching the numbers given.\n"
    "4. If the retrieved cases do not actually address the question, say so plainly "
    "rather than stretching them.\n"
    "5. End by reminding the user these are search results to review, not legal "
    "advice. Be concise."
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
        lines.append(
            f"[{i}] {c.title} — {c.court}, {c.date} ({cite}); "
            f"cited by {c.cite_count}. Excerpt: {snip}"
        )
    return "\n".join(lines)


async def _organize(question: str, cases: list) -> AsyncIterator[str]:
    """Stream an answer grounded only in the retrieved cases."""
    user = (
        f"User question:\n{question}\n\n"
        f"Retrieved real cases (the ONLY cases you may discuss):\n{_case_block(cases)}"
    )
    convo = [
        {"role": "system", "content": _ORGANIZE_SYSTEM},
        {"role": "user", "content": user},
    ]
    async for delta in llm.stream_chat(convo, max_tokens=900):
        yield delta


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    messages = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in messages
        if m.get("content")
    ]
    if not messages:
        return JSONResponse(status_code=400, content={"error": "no messages"})
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

    async def gen() -> AsyncIterator[str]:
        yield _sse("status", {"message": "Understanding your question…"})
        plan = await _route(messages)

        if plan.get("action") == "clarify" and plan.get("clarify"):
            yield _sse("clarify", {"question": plan["clarify"]})
            yield _sse("done", {})
            return

        query = (plan.get("query") or question).strip()
        court = (plan.get("court") or "").strip()
        yield _sse("status", {"message": f"Searching case law for: {query}"})

        try:
            cases = await cl.search(query, court=court, max_results=8)
        except httpx.HTTPError as exc:
            yield _sse("error", {"message": f"search failed: {exc}"})
            yield _sse("done", {})
            return

        yield _sse("cases", {"query": query, "court": court,
                             "count": len(cases),
                             "cases": [c.to_dict() for c in cases]})

        if not cases:
            yield _sse("token", {"text": "No matching case law was found for this "
                                 "query. Try rephrasing with the parties, the legal "
                                 "issue, or a jurisdiction."})
            yield _sse("done", {})
            return

        # Organize the retrieved cases (LLM). Degrade gracefully if it is down.
        try:
            got_any = False
            async for delta in _organize(question, cases):
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


# Static frontend (mounted last so /api/* takes precedence).
app.mount("/", StaticFiles(directory=os.path.abspath(WEB_DIR), html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
