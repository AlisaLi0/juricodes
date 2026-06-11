# JuriCodex

Conversational retrieval over **real US case law**. Ask a legal question in plain
language; JuriCodex finds the actual court opinions behind it — with citations
and links you can open and read yourself.

> Repo/service id: `leagle-chat` (kept as the internal name; the product brand is
> **JuriCodex**).

It is the **web chat front-end** of the legal-research engine (paired with the
`legal-mcp` connector that serves the same retrieval to Claude/agents).

## The hard rule: search, not advice

The LLM is **only a conversational front-end for SEARCH**. It:
1. turns your plain-English question into a precise case-law query (or asks one
   clarifying question), and
2. organizes the **retrieved real cases** into a readable answer, citing them `[1] [2]`.

It **never** invents cases and **never** gives a legal conclusion or predicts an
outcome. What you receive is always real primary-source material (from
CourtListener, public domain) plus citation links — not model-generated legal
content. This keeps it on the right side of unauthorized-practice-of-law and the
post-*Mata v. Avianca* hallucination risk.

If the LLM endpoint is down, **retrieval still works**: the real cases are
returned without the organizing layer. The core — real cases — never depends on
the model being up.

## Multilingual UX

The web app supports English, Spanish, Simplified Chinese, Traditional Chinese,
French, Portuguese, Korean, Japanese, and Vietnamese UI. The selected language is
saved in the browser and sent with each research request so research plans,
explanations, Brief Review support notes, and case workbench summaries are
written in that language. US case names, reporter citations, statute/regulation
citations, URLs, and quoted source text stay in their original English so the
primary law remains verifiable.

## Architecture

```
web/                 static chat UI (no build step)
  index.html
  style.css
  app.js             SSE streaming, case cards, clickable [n] citations
server/
  app.py             FastAPI: /api/chat (SSE), /api/health, serves web/
  courtlistener.py   CourtListener v4 search client → real Case objects
  llm.py             OpenAI-compatible client (route + organize), env-configured
```

Per turn: **route** (LLM → search plan) → **search** (CourtListener → real cases)
→ **organize** (LLM streams an answer grounded only in those cases).

## Answer Flow

Default `chat` mode is a multi-step grounded research flow, not a direct LLM
answer. The LLM plans/refines/explains, while legal claims are grounded in
retrieved primary-law sources.

```mermaid
flowchart TD
  A[User submits question] --> B[Frontend POST /api/chat]
  B --> B1[messages + mode + language]
  B --> B2[signed cookie + CSRF token]
  B --> C{Authenticated and CSRF valid?}
  C -- No --> C1[401/403]
  C -- Yes --> D{Request valid?}
  D -- No --> D1[400/413]
  D -- Yes --> E[Resolve plan and atomically consume 1 question]
  E --> F{Quota available?}
  F -- No --> F1[402 quota_exceeded]
  F -- Yes --> G{mode}

  G -- chat --> H[LLM route JSON]
  G -- toolkit modes --> T[Direct tool path]
  G -- brief --> BR[Brief Review path]
  G -- laws --> LW[US Code + CFR search]

  H --> I{Need clarification?}
  I -- Yes --> I1[Send clarify SSE event and stop]
  I -- No --> J[LLM research_plan JSON]
  J --> K[Split into 1-4 legal issues]
  K --> L[Send research_plan SSE event]

  L --> M[Search CourtListener per issue]
  M --> N[LLM relevance assessment]
  N --> O{Results on point?}
  O -- No, attempts remain --> P[Refine query and search again]
  P --> M
  O -- Yes or max attempts --> Q[Collect issue cases]

  Q --> R[Deduplicate, rank, keep top 10]
  R --> S[Attach treatment-lite signals]
  S --> U{Federal statute/regulation query?}
  U -- Yes --> U1[Search US Code + eCFR]
  U -- No --> V[Use case sources only]
  U1 --> V

  V --> W{Any real sources found?}
  W -- No --> W1[Refund consumed question]
  W1 --> W2[Send no-results answer]
  W -- Yes --> X[Send cases/statutes SSE events]
  X --> Y[LLM organize final answer]
  Y --> Y1[Prompt includes only retrieved cases/statutes]
  Y1 --> Y2[Answer in selected language]
  Y2 --> Y3[Keep case names, citations, quotes, URLs in English]
  Y3 --> Z[Stream token SSE events]
  Z --> AA[Frontend renders safe Markdown + clickable citations]
  AA --> AB[Backend citation-range integrity check]
  AB --> AC[done SSE event]
  AC --> AD[Frontend autosaves session]
```

Brief Review follows the same source-first rule, but the pipeline starts by
extracting and checking citations from pasted legal text.

```mermaid
flowchart TD
  A[User selects Brief Review] --> B[Paste brief, memo, argument, or legal text]
  B --> C[POST /api/chat mode=brief]
  C --> D[Auth, CSRF, quota, validation]
  D --> E[Regex extract reporter citations and full case names]
  E --> E1[Capture nearby quote and context]
  E --> F{References found?}
  F -- No --> F1[Refund question and return empty review]
  F -- Yes --> G[Resolve each reference in CourtListener]
  G --> H{Resolved to real case?}
  H -- No --> H1[Row status: Case unresolved]
  H -- Yes --> I[Attach treatment-lite]
  I --> J{Nearby quote?}
  J -- Yes --> J1[Verify quote against real opinion text]
  J -- No --> J2[Quote status: no nearby quote]
  J1 --> K[Fetch focused passages]
  J2 --> K
  K --> L[LLM support-check JSON]
  L --> L1[Supports / Weak support / Unclear / Needs review]
  L1 --> M[Build Brief Review table]
  H1 --> M
  M --> N[Send brief_review SSE event]
  N --> O[Frontend renders extracted reference, authority, support, quote check]
```

The user can then inspect each source: open the full opinion, expand details and
PDF inventory, view focused passages and citing cases, copy citations, export a
Word-readable memo, or verify another quote against the real opinion text.

## Configuration (env)

| var | default | purpose |
|---|---|---|
| `LLM_BASE_URL` | `https://tianshu-gateway.cloud/v1` | OpenAI-compatible base |
| `LLM_API_KEY` | — | bearer key |
| `LLM_MODEL` | `auto` | model name |
| `COURTLISTENER_API_TOKEN` | — | optional; raises CourtListener limits (search works anonymously) |
| `LEAGLE_HOST` / `LEAGLE_PORT` | `127.0.0.1` / `8600` | bind |

## Run

```bash
pip install -r requirements.txt
python -m server.app          # or: uvicorn server.app:app --port 8600
# open http://127.0.0.1:8600
```

Search works with no keys at all (anonymous CourtListener). Set `LLM_*` to enable
the conversational query-refinement + organizing layer.

## Production Notes

Production runs behind nginx on `127.0.0.1:8600` as the dedicated `leagle-chat`
system user, not root. Keep `/opt/leagle-chat/leagle.env` and `leagle.db*` owned
by `leagle-chat:leagle-chat` with mode `600`; code, static files, and the virtual
environment can remain read-only. The systemd unit sets `UMask=0077`,
`NoNewPrivileges=true`, `PrivateTmp=true`, and `PYTHONDONTWRITEBYTECODE=1` so the
service does not need to write bytecode into the source tree.

## Roadmap

- More sources (eCFR statutes/regs, CAP historical) fused into one search ✓
- Quote verification + treatment ("is this still good law?") checks ✓
- Accounts (GitHub/Google OAuth) + saved research sessions ✓
- GPU semantic retrieval (embeddings) on top of keyword search
- Freemium billing on top of accounts

## License

[GNU AGPL-3.0](LICENSE). You may use, study, and self-host this code, but if you
run a modified version as a network service you must make your source available
to its users under the same license.
