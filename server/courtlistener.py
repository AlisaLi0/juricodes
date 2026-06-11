"""CourtListener search client (anonymous search works; token raises limits).

A thin async client over the public CourtListener v4 API, reduced to what the
chat backend needs: full-text case-law search returning real opinions with
citations and links. Mirrors the legal-mcp source but standalone (no MCP deps).
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field

import httpx

CL_API = "https://www.courtlistener.com/api/rest/v4"
CL_WEB = "https://www.courtlistener.com"
_USER_AGENT = "leagle-chat/0.1 (+https://github.com/AlisaLi0/leagle-chat)"
_RETRY_STATUS = {429, 500, 502, 503, 504}
_STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "into", "case",
    "court", "law", "legal", "brief", "memo", "cited", "citation", "quote",
}

_SORT_MAP = {
    "relevance": "score desc",
    "newest": "dateFiled desc",
    "oldest": "dateFiled asc",
    "most_cited": "citeCount desc",
}


@dataclass(slots=True)
class Case:
    """A real case-law hit retrieved from CourtListener (never model-generated)."""
    id: str
    title: str
    court: str = ""
    date: str = ""
    citations: list[str] = field(default_factory=list)
    docket_number: str = ""
    cite_count: int = 0
    status: str = ""
    snippet: str = ""
    url: str = ""
    # Cytator (treatment) signal: how many later opinions cite this one and how
    # recently — a proxy for whether the case is still "good law". Populated by
    # CourtListener.attach_treatment(); None means not checked.
    cited_by: int | None = None
    last_cited: str = ""
    treatment: str = ""  # one of: "", "landmark", "frequently-cited", "cited", "rarely-cited"
    # Negative-treatment signal: number of later opinions that cite this case
    # AND contain overruling language, plus a few examples. This is a heuristic
    # search signal (NOT an authoritative Shepard's/KeyCite check); None means
    # not checked.
    negative_count: int | None = None
    negative_examples: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "court": self.court,
            "date": self.date,
            "citations": self.citations,
            "docket_number": self.docket_number,
            "cite_count": self.cite_count,
            "status": self.status,
            "snippet": self.snippet,
            "url": self.url,
            "cited_by": self.cited_by,
            "last_cited": self.last_cited,
            "treatment": self.treatment,
            "negative_count": self.negative_count,
            "negative_examples": self.negative_examples,
        }


def _result_key(r: dict) -> str:
    """Stable identity for de-duping a search hit across rankings."""
    return str(r.get("cluster_id") or r.get("id") or r.get("absolute_url") or id(r))


def _rrf_fuse(rankings: list[list[dict]], *, k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion of several ranked result lists.

    Each list is ordered best-first. A result's fused score is the sum over the
    lists it appears in of 1/(k + rank). Results strong on either ranking (text
    relevance OR citation authority) bubble up; the first-seen dict is kept.
    """
    scores: dict[str, float] = {}
    first: dict[str, dict] = {}
    seen_in: dict[str, int] = {}
    for ranking in rankings:
        for rank, r in enumerate(ranking):
            key = _result_key(r)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            seen_in[key] = seen_in.get(key, 0) + 1
            if key not in first:
                first[key] = r

    # A result that shows up in BOTH rankings (relevant AND authoritative) is the
    # strongest signal, so give it a small bonus. This lifts true leading cases
    # (e.g. Miranda v. Arizona, which both matches and is heavily cited) above
    # single-list noise without letting raw citation count hijack narrow factual
    # questions the way a global citeCount sort does.
    def _final(key: str) -> float:
        bonus = 0.5 / k if seen_in.get(key, 0) >= 2 else 0.0
        return scores[key] + bonus

    ordered = sorted(first.values(), key=lambda r: _final(_result_key(r)), reverse=True)
    return ordered


def _treatment_label(cited_by: int) -> str:
    """Coarse good-law signal from how many later opinions cite this one.

    This is a citation-frequency proxy, NOT a Shepard's/KeyCite negative-history
    check - it says how influential/established a case is, not whether it was
    overruled. The UI labels it accordingly.
    """
    if cited_by >= 1000:
        return "landmark"
    if cited_by >= 100:
        return "frequently-cited"
    if cited_by >= 5:
        return "cited"
    return "rarely-cited"


# Words that, when a later opinion both cites a case AND uses them, suggest the
# case may have been treated negatively. Used only as tight name-anchored
# phrases (e.g. "overruled Miranda"), never as bare terms - bare "overruled"
# matches almost every citing opinion and is useless as a signal.
def _normalize_for_match(text: str) -> str:
    """Normalize text for robust quote matching.

    Lowercases, converts smart quotes/dashes to ASCII, drops most punctuation,
    and collapses all whitespace to single spaces. This lets a quote match the
    opinion text despite typographic differences (curly vs straight quotes,
    line breaks, page-number artifacts in the middle of a sentence are NOT
    removed, but spacing/quoting differences are).
    """
    text = unicodedata.normalize("NFKD", text or "")
    # Smart quotes / dashes -> ASCII.
    trans = {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...", "\xa0": " ",
    }
    text = text.translate({ord(k): v for k, v in trans.items()})
    text = text.lower()
    # Drop punctuation except intra-word apostrophes/hyphens are fine to drop too
    # for matching purposes; we only care about word content + order.
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text



def _build_query(query: str, mode: str) -> str:
    """Shape a raw user query into CourtListener search syntax for a given mode.

    Already-formed field queries (the router emits plain keywords) pass through;
    the toolkit modes wrap the input so each entry searches the right field.
    """
    q = (query or "").strip()
    if not q:
        return ""
    # If the caller already used field syntax, don't double-wrap.
    if any(tag in q for tag in ("caseName:", "citation:", "cites:")):
        return q
    if mode == "case":
        return f'caseName:("{q}")'
    if mode == "citation":
        return f'citation:("{q}")'
    if mode == "keyword":
        # Exact-phrase match when the user gave a short phrase; otherwise leave
        # the terms as-is (CourtListener ANDs them).
        return f'"{q}"' if (" " in q and '"' not in q) else q
    # concept (default): natural-language full-text search.
    return q


class CourtListener:
    def __init__(
        self,
        *,
        api_token: str | None = None,
        base_url: str = CL_API,
        timeout: float = 20.0,
        max_retries: int = 4,
    ) -> None:
        self._token = (api_token or "").strip() or None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        court: str = "",
        sort_by: str = "relevance",
        mode: str = "concept",
    ) -> list[Case]:
        """Retrieve real case-law hits.

        `mode` shapes the CourtListener query so the sidebar toolkit entries each
        search the way a lawyer expects:
          - concept  : natural-language full text (default)
          - keyword  : exact phrase match ("...")
          - case     : caseName:("...") — find a decision by its name
          - citation : citation:("...") — pinpoint a reporter cite like 384 U.S. 436

        Recall/ranking fix: CourtListener's text relevance ("score desc") and
        authority ("citeCount desc") each fail on a different kind of question -
        score buries a leading case under same-name noise (e.g. a Miranda query
        returns "Enoc Miranda v. CSC Sugar" instead of Miranda v. Arizona),
        while citeCount drags in a famous but off-topic case for narrow factual
        questions. So we run BOTH rankings and fuse them with Reciprocal Rank
        Fusion - the result that is strong on either axis rises to the top.
        """
        q = _build_query(query, mode)
        if not q:
            return []

        async def _ranked(order_by: str) -> list[dict]:
            params: dict = {"q": q, "type": "o", "order_by": order_by}
            if court.strip():
                params["court"] = court.strip()
            data = await self._get("/search/", params)
            return data.get("results") or []

        # Two complementary rankings, fetched concurrently.
        try:
            by_score, by_cites = await asyncio.gather(
                _ranked("score desc"), _ranked("citeCount desc")
            )
        except Exception:
            # Fall back to a single ranking if the gather fails for any reason.
            by_score, by_cites = (await _ranked(_SORT_MAP.get(sort_by, "score desc")), [])

        fused = _rrf_fuse([by_score, by_cites], k=60)
        rows = fused[: max(1, min(max_results, 20))]
        return [self._parse(r) for r in rows]

    async def cited_by(self, cluster_id: str) -> tuple[int, str]:
        """Cytator-lite: how many later opinions cite this case, and the most
        recent citing date. Uses the anonymous `cites:(cluster_id)` search.

        Returns (count, last_cited_date). Degrades to (0, "") on any error so it
        never blocks the main answer.
        """
        cid = str(cluster_id or "").strip()
        if not cid.isdigit():
            return 0, ""
        try:
            data = await self._get(
                "/search/",
                {"q": f"cites:({cid})", "type": "o", "order_by": "dateFiled desc"},
            )
        except Exception:
            return 0, ""
        count = int(data.get("count") or 0)
        results = data.get("results") or []
        last = (results[0].get("dateFiled") or "")[:10] if results else ""
        return count, last

    async def citing_cases(self, cluster_id: str, *, limit: int = 5) -> dict:
        """Return later cases citing this cluster (best-effort)."""
        cid = str(cluster_id or "").strip()
        if not cid.isdigit():
            return {"count": 0, "cases": []}
        try:
            data = await self._get(
                "/search/",
                {"q": f"cites:({cid})", "type": "o", "order_by": "dateFiled desc"},
            )
        except Exception:
            return {"count": 0, "cases": []}
        rows = (data.get("results") or [])[:limit]
        return {"count": int(data.get("count") or 0), "cases": [self._parse(r).to_dict() for r in rows]}

    async def attach_treatment(self, cases: list[Case], *, top: int = 6) -> None:
        """Populate cited_by / last_cited / treatment for the first `top` cases,
        concurrently. Mutates the Case objects in place. Best-effort: any case
        whose lookup fails just keeps treatment unset.
        """
        targets = [c for c in cases[:top] if str(c.id).isdigit()]
        if not targets:
            return
        results = await asyncio.gather(
            *(self.cited_by(c.id) for c in targets), return_exceptions=True
        )
        for c, res in zip(targets, results):
            if isinstance(res, Exception):
                continue
            count, last = res
            c.cited_by = count
            c.last_cited = last
            c.treatment = _treatment_label(count)

    async def resolve_reference(self, ref: str, *, kind: str = "") -> Case | None:
        """Resolve a citation/case-name-ish reference to the best matching Case.

        MVP strategy for Brief Review: use CourtListener's citation-lookup API
        when we have a reporter citation, then fall back to the same search modes
        exposed in the Toolkit. This stays deterministic and source-backed; ML
        or LLM extraction can be layered later.
        """
        text = (ref or "").strip()
        if not text:
            return None
        if kind == "citation":
            hit = await self.lookup_citation(text)
            if hit:
                return hit
            results = await self.search(text, mode="citation", max_results=1)
            return results[0] if results else None
        mode = "case" if kind == "case" else "keyword"
        results = await self.search(text, mode=mode, max_results=1)
        return results[0] if results else None

    async def lookup_citation(self, citation: str) -> Case | None:
        """Use CourtListener's citation lookup endpoint when available.

        The endpoint returns cluster/search-like objects for exact citations.
        If it is unavailable or the response shape changes, callers fall back to
        `search(..., mode="citation")`.
        """
        cite = (citation or "").strip()
        if not cite:
            return None
        try:
            data = await self._post("/citation-lookup/", {"text": cite})
        except Exception:
            data = None
        rows = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else None)
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        # citation-lookup may return a cluster-like object instead of search row.
        if "cluster_id" not in row and row.get("id"):
            row = {**row, "cluster_id": row.get("id")}
        return self._parse(row)

    async def focused_passages(self, cluster_id: str, focus: str = "", *, limit: int = 3) -> list[dict]:
        """Return short source passages relevant to a focus string.

        MVP: fetch real opinion text, split into readable windows, and score by
        overlap with non-stopword focus terms. This is deterministic; an LLM or
        embedding reranker can replace it later.
        """
        text, fetched, total = await self.opinion_text(cluster_id)
        if not text:
            return []
        terms = [t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9']{3,}", (focus or "").lower())
                 if t not in _STOPWORDS]
        chunks = []
        # Split on paragraph breaks first, then sentence-ish boundaries.
        raw_parts = re.split(r"\n{2,}|(?<=[.;!?])\s+(?=[A-Z])", text)
        for part in raw_parts:
            clean = re.sub(r"\s+", " ", part).strip()
            if len(clean) < 80:
                continue
            chunks.append(clean[:900])
            if len(chunks) >= 120:
                break
        if not chunks:
            return []
        scored = []
        for i, chunk in enumerate(chunks):
            low = chunk.lower()
            score = sum(1 for t in terms if t in low)
            # Prefer earlier majority/lead passages when no focus terms hit.
            scored.append((score, -i, chunk))
        scored.sort(reverse=True)
        out = []
        for score, _, chunk in scored[:limit]:
            out.append({"text": chunk, "score": int(score), "source": "opinion text"})
        return out

    async def case_details(self, cluster_id: str, *, focus: str = "") -> dict:
        """Return source-backed case metadata + opinion inventory/PDF links.

        Best-effort and conservative: if a field or PDF link is absent, omit it
        rather than inventing. The frontend uses this for the "Details / PDFs"
        expander on authority cards and Brief Review rows.
        """
        cid = str(cluster_id or "").strip()
        if not cid.isdigit():
            return {"cluster_id": cid, "opinions": []}
        cluster = await self._get(f"/clusters/{cid}/", {})
        if not cluster:
            return {"cluster_id": cid, "opinions": []}
        abs_url = cluster.get("absolute_url") or ""
        sub = cluster.get("sub_opinions") or []
        op_ids: list[str] = []
        for u in sub:
            m = re.search(r"/opinions/(\d+)/", str(u))
            if m:
                op_ids.append(m.group(1))
        op_ids = op_ids[:12]
        details = await asyncio.gather(
            *(self._get(f"/opinions/{oid}/", {}) for oid in op_ids),
            return_exceptions=True,
        ) if op_ids else []
        opinions: list[dict] = []
        has_pdf = False
        has_text = False
        for oid, op in zip(op_ids, details):
            if isinstance(op, Exception) or not isinstance(op, dict):
                continue
            op_abs = op.get("absolute_url") or ""
            pdf = op.get("download_url") or op.get("local_path") or ""
            if pdf and pdf.startswith("/"):
                pdf = f"{CL_WEB}{pdf}"
            op_has_text = bool((op.get("plain_text") or op.get("html_with_citations") or op.get("html") or "").strip())
            has_text = has_text or op_has_text
            has_pdf = has_pdf or (isinstance(pdf, str) and pdf.startswith("http"))
            opinions.append({
                "id": oid,
                "type": op.get("type") or op.get("type_name") or "opinion",
                "author": op.get("author_str") or op.get("author") or "",
                "url": f"{CL_WEB}{op_abs}" if op_abs else "",
                "pdf_url": pdf if isinstance(pdf, str) and pdf.startswith("http") else "",
                "has_text": op_has_text,
            })
        citations = cluster.get("citations") or cluster.get("citation") or []
        if citations and isinstance(citations[0] if isinstance(citations, list) else None, dict):
            citations = [c.get("cite") or c.get("citation") or "" for c in citations]
        citing = await self.citing_cases(cid, limit=5)
        passages = await self.focused_passages(cid, focus, limit=3)
        return {
            "cluster_id": cid,
            "title": cluster.get("case_name") or cluster.get("caseName") or cluster.get("case_name_full") or "",
            "court": cluster.get("court") or "",
            "date": (cluster.get("date_filed") or cluster.get("dateFiled") or "")[:10],
            "docket_number": cluster.get("docket_number") or cluster.get("docketNumber") or "",
            "precedential_status": cluster.get("precedential_status") or "",
            "citations": [c for c in citations if c] if isinstance(citations, list) else [],
            "url": f"{CL_WEB}{abs_url}" if abs_url else "",
            "opinions_total": len(op_ids),
            "opinions": opinions,
            "source_availability": {
                "has_text": has_text,
                "has_pdf": has_pdf,
                "opinions_found": len(opinions),
                "opinions_total": len(op_ids),
                "partial": len(opinions) < len(op_ids),
            },
            "citing_cases": citing,
            "focused_passages": passages,
        }

    async def negative_treatment(self, cluster_id: str, case_name: str = "") -> tuple[int, list[dict]]:
        """Heuristic negative-treatment signal for a case.

        Searches for later opinions that cite this case AND use overruling
        language *directed at this case by name* (e.g. "overruled Miranda"),
        rather than merely containing the word "overruled" somewhere (which is
        far too common - most opinions discuss some other case being overruled).
        A non-zero count is a flag to investigate; it does NOT prove the case is
        bad law (a hit could be "we decline to overrule Miranda"). Returns
        (count, examples) where examples is a short list of {title, date, url}.

        Degrades to (0, []) on any error so it never blocks the main answer.
        """
        cid = str(cluster_id or "").strip()
        if not cid.isdigit():
            return 0, []
        # Anchor on the FULL caption ("Miranda v. Arizona"), not just the first
        # party ("Miranda") - many party names (Miranda, Smith, Brown) are common
        # surnames, so a one-word anchor matches unrelated cases. Require the full
        # "X v. Y" form so the overruling language is plausibly about THIS case.
        caption = (case_name or "").strip()
        m = re.match(r"(.+?\sv\.?\s.+?)(?:\s*,|\s*\(|$)", caption)
        if not m:
            return 0, []
        full = re.sub(r"[^A-Za-z .'-]", "", m.group(1)).strip()
        # Need both parties present and a reasonable length to be distinctive.
        if " v" not in f" {full.lower()} " or len(full) < 7:
            return 0, []
        phrases = [
            f'"overruled {full}"', f'"overruling {full}"', f'"overrule {full}"',
            f'"{full} is overruled"', f'"{full} was overruled"',
            f'"{full} has been overruled"', f'"abrogated {full}"',
            f'"{full} is no longer good law"',
        ]
        q = f"cites:({cid}) AND (" + " OR ".join(phrases) + ")"
        try:
            data = await self._get(
                "/search/", {"q": q, "type": "o", "order_by": "dateFiled desc"})
        except Exception:
            return 0, []
        count = int(data.get("count") or 0)
        examples: list[dict] = []
        for r in (data.get("results") or [])[:3]:
            abs_url = r.get("absolute_url") or ""
            examples.append({
                "title": r.get("caseName") or r.get("caseNameFull") or "(untitled)",
                "date": (r.get("dateFiled") or "")[:10],
                "url": f"{CL_WEB}{abs_url}" if abs_url else "",
            })
        return count, examples

    async def attach_negative(self, cases: list[Case], *, top: int = 3) -> None:
        """Populate negative_count / negative_examples for the first `top` cases,
        concurrently. Mutates in place. Best-effort: failures leave it unset.
        """
        targets = [c for c in cases[:top] if str(c.id).isdigit()]
        if not targets:
            return
        results = await asyncio.gather(
            *(self.negative_treatment(c.id, c.title) for c in targets),
            return_exceptions=True,
        )
        for c, res in zip(targets, results):
            if isinstance(res, Exception):
                continue
            count, examples = res
            c.negative_count = count
            c.negative_examples = examples

    async def opinion_text(self, cluster_id: str) -> tuple[str, int, int]:
        """Fetch the full opinion text for a cluster.

        Resolves the cluster's sub-opinions and concatenates their plain text
        (falling back to stripped HTML). Returns (text, fetched, total) where
        `fetched` is how many sub-opinions were retrieved and `total` is how many
        the cluster has — so callers can tell the user when a quote search wasn't
        exhaustive. Returns ("", 0, 0) if unavailable. Detail endpoints work
        anonymously for most opinions; a token raises limits.
        """
        cid = str(cluster_id or "").strip()
        if not cid.isdigit():
            return "", 0, 0
        try:
            cluster = await self._get(f"/clusters/{cid}/", {})
        except httpx.HTTPError:
            return "", 0, 0
        sub = cluster.get("sub_opinions") or []
        # sub_opinions are absolute API URLs; extract the trailing opinion id.
        # Cap how many we fetch: the lead opinion plus concurrences/dissents is
        # enough to verify a quote, and it bounds load (each opinion can be
        # hundreds of KB, and fetching all of a landmark's sub-opinions at once
        # invites rate limiting). The cap is generous enough to cover the
        # majority/concurrence/dissent split of even big landmark decisions.
        MAX_OPINIONS = 12
        all_ids: list[str] = []
        for u in sub:
            m = re.search(r"/opinions/(\d+)/", str(u))
            if m:
                all_ids.append(m.group(1))
        total = len(all_ids)
        op_ids = all_ids[:MAX_OPINIONS]
        if not op_ids:
            return "", 0, total
        texts = await asyncio.gather(
            *(self._get(f"/opinions/{oid}/", {}) for oid in op_ids),
            return_exceptions=True,
        )
        parts: list[str] = []
        for t in texts:
            if isinstance(t, Exception) or not isinstance(t, dict):
                continue
            body = (t.get("plain_text") or "").strip()
            if not body:
                html = t.get("html_with_citations") or t.get("html") or t.get("xml_harvard") or ""
                body = re.sub(r"<[^>]+>", " ", html)
                body = re.sub(r"\s+", " ", body).strip()
            if body:
                parts.append(body)
        return "\n\n".join(parts), len(op_ids), total

    async def verify_quote(self, cluster_id: str, quote: str) -> dict:
        """Check whether `quote` actually appears in the opinion's real text.

        This is leagle's anti-hallucination check: given a quote a user (or a
        model) attributes to a case, we fetch the REAL opinion text and confirm
        the words are there - returning the surrounding context when found, so
        the user can see it in situ. Matching is whitespace/punctuation/quote
        insensitive (normalized), so typographic differences don't cause false
        misses.

        Returns: {found: bool, match: "exact"|"normalized"|"not_found"|"no_text",
                  context: str, quote: str, cluster_id: str, chars: int,
                  opinions_searched: int, opinions_total: int}.
        """
        q_raw = (quote or "").strip()
        out = {"found": False, "match": "not_found", "context": "",
               "quote": q_raw, "cluster_id": str(cluster_id or ""), "chars": 0,
               "opinions_searched": 0, "opinions_total": 0}
        if len(q_raw) < 6:
            out["match"] = "too_short"
            return out
        text, fetched, total = await self.opinion_text(cluster_id)
        out["opinions_searched"] = fetched
        out["opinions_total"] = total
        if not text:
            out["match"] = "no_text"
            return out
        out["chars"] = len(text)
        # 1) Exact substring (case-insensitive) -> strongest confirmation.
        lo_text, lo_q = text.lower(), q_raw.lower()
        idx = lo_text.find(lo_q)
        if idx >= 0:
            out.update(found=True, match="exact",
                       context=self._context(text, idx, len(q_raw)))
            return out
        # 2) Normalized match (ignore whitespace/punctuation/smart-quote diffs).
        norm_text = _normalize_for_match(text)
        norm_q = _normalize_for_match(q_raw)
        if norm_q and norm_q in norm_text:
            # Map back approximately: find the first word of the quote in raw text.
            first = norm_q.split(" ")[0]
            approx = text.lower().find(first)
            ctx = self._context(text, max(approx, 0), len(q_raw)) if approx >= 0 else ""
            out.update(found=True, match="normalized", context=ctx)
            return out
        # Not found. If we couldn't search every sub-opinion, say so — the quote
        # may live in one we didn't fetch, so "not found" isn't conclusive.
        if total > fetched:
            out["partial"] = True
        return out

    @staticmethod
    def _context(text: str, idx: int, qlen: int, *, pad: int = 160) -> str:
        """A readable window of the opinion around a matched quote."""
        start = max(0, idx - pad)
        end = min(len(text), idx + qlen + pad)
        snippet = text[start:end].strip()
        snippet = re.sub(r"\s+", " ", snippet)
        return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")

    def _parse(self, r: dict) -> Case:
        abs_url = r.get("absolute_url") or ""
        opinions = r.get("opinions") or []
        snippet = ""
        if opinions and isinstance(opinions[0], dict):
            snippet = (opinions[0].get("snippet") or "").strip()
        return Case(
            id=str(r.get("cluster_id") or ""),
            title=r.get("caseName") or r.get("caseNameFull") or "(untitled)",
            court=r.get("court") or "",
            date=(r.get("dateFiled") or "")[:10],
            citations=list(r.get("citation") or []),
            docket_number=r.get("docketNumber") or "",
            cite_count=r.get("citeCount") or 0,
            status=r.get("status") or "",
            snippet=snippet,
            url=f"{CL_WEB}{abs_url}" if abs_url else "",
        )

    # -- HTTP with retry/backoff -------------------------------------------

    def _headers(self) -> dict:
        h = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Token {self._token}"
        return h

    async def _get(self, path: str, params: dict) -> dict:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, headers=self._headers()
        ) as client:
            for attempt in range(self._max_retries):
                backoff = min(3.0 * (attempt + 1), 12.0)
                try:
                    resp = await client.get(url, params=params)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code == 404:
                    return {}
                if resp.status_code in (401, 403):
                    # Search is anonymous; detail endpoints need a token.
                    return {}
                if resp.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"CourtListener {resp.status_code}",
                        request=resp.request, response=resp)
                    await asyncio.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        if last_exc:
            raise last_exc
        return {}

    async def _post(self, path: str, json_body: dict) -> dict | list:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True, headers=self._headers()
        ) as client:
            for attempt in range(self._max_retries):
                backoff = min(3.0 * (attempt + 1), 12.0)
                try:
                    resp = await client.post(url, json=json_body)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code == 404:
                    return {}
                if resp.status_code in (401, 403):
                    return {}
                if resp.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"CourtListener {resp.status_code}",
                        request=resp.request, response=resp)
                    await asyncio.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        if last_exc:
            raise last_exc
        return {}
