"""eCFR statute/regulation search client (free, no API key).

The Code of Federal Regulations is the body of federal *rules* (what agencies
actually require), complementing CourtListener's case law. Many everyday legal
questions (overtime, safety, benefits) are answered by a regulation, not a court
opinion. eCFR's public search API returns section-level hits with a citable
hierarchy (Title / Part / Section) and links to the official current text.

This is statutes/regulations retrieval only — like the rest of leagle, we return
the real primary source, never model-generated legal content.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import httpx

ECFR_API = "https://www.ecfr.gov/api/search/v1/results"
ECFR_WEB = "https://www.ecfr.gov/current"
GOVINFO_API = "https://api.govinfo.gov/search"
_USER_AGENT = "leagle-chat/0.1 (+https://github.com/AlisaLi0/leagle-chat)"
_RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(slots=True)
class Statute:
    """A real CFR section hit (never model-generated)."""
    citation: str          # e.g. "29 CFR § 541.0"
    heading: str = ""      # section/part heading text
    title: str = ""        # CFR title number, e.g. "29"
    part: str = ""
    section: str = ""
    excerpt: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "citation": self.citation,
            "heading": self.heading,
            "title": self.title,
            "part": self.part,
            "section": self.section,
            "excerpt": self.excerpt,
            "url": self.url,
        }


class ECFR:
    def __init__(self, *, timeout: float = 20.0, max_retries: int = 4) -> None:
        self._timeout = timeout
        self._max_retries = max_retries

    async def search(self, query: str, *, max_results: int = 5) -> list[Statute]:
        q = (query or "").strip()
        if not q:
            return []
        # Over-fetch so that after de-duping repeated sections (eCFR returns the
        # same § under several historical versions) we still have enough.
        data = await self._get({"query": q, "per_page": max(1, min(max_results * 4, 40))})
        rows = data.get("results") or []
        seen: set[str] = set()
        out: list[Statute] = []
        for r in rows:
            st = self._parse(r)
            if not st or st.citation in seen:
                continue
            seen.add(st.citation)
            out.append(st)
            if len(out) >= max_results:
                break
        return out

    def _parse(self, r: dict) -> Statute | None:
        h = r.get("hierarchy") or {}
        title = str(h.get("title") or "").strip()
        part = str(h.get("part") or "").strip()
        sec = str(h.get("section") or "").strip()
        if not title:
            return None
        if sec:
            citation = f"{title} CFR § {sec}"
        elif part:
            citation = f"{title} CFR Part {part}"
        else:
            citation = f"{title} CFR"
        hh = r.get("hierarchy_headings") or {}
        heading = (hh.get("section") or hh.get("part") or "").strip()
        url = f"{ECFR_WEB}/title-{title}"
        if part:
            url += f"/part-{part}"
        if sec:
            url += f"#p-{sec}"
        excerpt = (r.get("full_text_excerpt") or "").replace("<strong>", "").replace("</strong>", "").strip()
        return Statute(
            citation=citation, heading=heading, title=title, part=part,
            section=sec, excerpt=excerpt, url=url,
        )

    async def _get(self, params: dict) -> dict:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True, headers=headers) as client:
            for attempt in range(self._max_retries):
                backoff = min(3.0 * (attempt + 1), 12.0)
                try:
                    resp = await client.get(ECFR_API, params=params)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code == 404:
                    return {}
                if resp.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"eCFR {resp.status_code}", request=resp.request, response=resp)
                    await asyncio.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        if last_exc:
            raise last_exc
        return {}


class USCode:
    """US Code (federal statutes) search via the govinfo API.

    The CFR is federal *regulations*; the US Code is the federal *statutes*
    Congress enacted (the laws the regulations implement). govinfo's USCODE
    collection is the official source. Requires a free api.data.gov key.
    """

    def __init__(self, api_key: str = "", *, timeout: float = 20.0, max_retries: int = 4) -> None:
        self._key = (api_key or "").strip()
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def search(self, query: str, *, max_results: int = 5) -> list[Statute]:
        q = (query or "").strip()
        if not q or not self._key:
            return []
        payload = {
            "query": f"collection:USCODE {q}",
            "pageSize": max(1, min(max_results * 3, 30)),
            "offsetMark": "*",
            "sorts": [{"field": "relevancy", "sortOrder": "DESC"}],
        }
        data = await self._post(payload)
        rows = data.get("results") or []
        seen: set[str] = set()
        out: list[Statute] = []
        for r in rows:
            st = self._parse(r)
            if not st or st.citation in seen:
                continue
            seen.add(st.citation)
            out.append(st)
            if len(out) >= max_results:
                break
        return out

    def _parse(self, r: dict) -> Statute | None:
        gid = r.get("granuleId") or ""
        m = re.search(r"title(\d+[A-Za-z]?)", gid)
        s = re.search(r"sec([0-9A-Za-z\-]+)", gid)
        if not m or not s:
            return None
        title = m.group(1)
        section = s.group(1)
        citation = f"{title} U.S.C. § {section}"
        # Official human-readable view on uscode.house.gov.
        prelim = gid.replace("USCODE-2024-", "USCODE-prelim-").replace("USCODE-2023-", "USCODE-prelim-")
        url = f"https://uscode.house.gov/view.xhtml?req=granuleid:{prelim}"
        return Statute(
            citation=citation, heading=(r.get("title") or "").strip(),
            title=title, part="", section=section, excerpt="", url=url,
        )

    async def _post(self, payload: dict) -> dict:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json",
                   "Content-Type": "application/json"}
        url = f"{GOVINFO_API}?api_key={self._key}"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True, headers=headers) as client:
            for attempt in range(self._max_retries):
                backoff = min(3.0 * (attempt + 1), 12.0)
                try:
                    resp = await client.post(url, json=payload)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await asyncio.sleep(backoff)
                    continue
                if resp.status_code in (401, 403, 404):
                    return {}
                if resp.status_code in _RETRY_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"govinfo {resp.status_code}", request=resp.request, response=resp)
                    await asyncio.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        if last_exc:
            raise last_exc
        return {}
