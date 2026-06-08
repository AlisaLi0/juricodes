"""CourtListener search client (anonymous search works; token raises limits).

A thin async client over the public CourtListener v4 API, reduced to what the
chat backend needs: full-text case-law search returning real opinions with
citations and links. Mirrors the legal-mcp source but standalone (no MCP deps).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

CL_API = "https://www.courtlistener.com/api/rest/v4"
CL_WEB = "https://www.courtlistener.com"
_USER_AGENT = "leagle-chat/0.1 (+https://github.com/AlisaLi0/leagle-chat)"
_RETRY_STATUS = {429, 500, 502, 503, 504}

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
    ) -> list[Case]:
        """Retrieve real case-law hits.

        Recall/ranking fix: CourtListener's text relevance ("score desc") and
        authority ("citeCount desc") each fail on a different kind of question -
        score buries a leading case under same-name noise (e.g. a Miranda query
        returns "Enoc Miranda v. CSC Sugar" instead of Miranda v. Arizona),
        while citeCount drags in a famous but off-topic case for narrow factual
        questions. So we run BOTH rankings and fuse them with Reciprocal Rank
        Fusion - the result that is strong on either axis rises to the top.
        """
        q = (query or "").strip()
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
