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
MAX_CHAT_MESSAGES = int(os.getenv("LEAGLE_MAX_CHAT_MESSAGES", "20"))
MAX_CHAT_MESSAGE_CHARS = int(os.getenv("LEAGLE_MAX_CHAT_MESSAGE_CHARS", "50000"))
MAX_SESSION_PAYLOAD_BYTES = int(os.getenv("LEAGLE_MAX_SESSION_PAYLOAD_BYTES", "2000000"))

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
    "\"reason\":\"one concise sentence\"}. Use Supports only when the passage "
    "directly supports the extracted proposition. Use Weak support when the case "
    "is related but the proposition is broader/narrower than the passage. Use "
    "Unclear when the provided passages do not let you decide. Use Needs review "
    "for procedural ambiguity or thin source text. Never say a case supports a "
    "proposition based on the case name alone."
)

_CASE_ANALYSIS_SYSTEM = (
    "You write source-grounded case workbench notes for legal research. Given case "
    "metadata and short passages from the real opinion, return ONLY JSON: "
    "{\"summary\":\"2-3 sentence source-grounded summary\","
    "\"why_it_matters\":\"one concise sentence\","
    "\"key_points\":[\"point from source text\"],"
    "\"limits\":[\"limits/uncertainties\"]}. Do not invent facts, holdings, "
    "or procedural history. If passages are thin, say the summary is limited."
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_LANG_LABELS = {
    "en": "English",
    "es": "Spanish",
    "zh": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "fr": "French",
    "pt": "Portuguese",
    "ko": "Korean",
    "ja": "Japanese",
    "vi": "Vietnamese",
}

_SERVER_TEXT = {
    "en": {
        "extract.status": "Extracting case citations and case names…",
        "extract.summary": "Citation Extractor found {count} unique case reference(s).",
        "extract.none": "No recognizable case citations or case names were found.",
        "resolver.status": "Resolving reference to candidate cases…",
        "resolver.summary": "Case Resolver returned {count} candidate(s). Open Details / PDFs to inspect source metadata.",
        "resolver.none": "No candidate case was found for that reference.",
        "laws.status": "Searching US Code and CFR for: {query}",
        "laws.summary": "Laws & Rules Search found {count} statute/regulation source(s).",
        "laws.none": "No matching federal statute or regulation was found.",
        "brief.extract": "Extracting citations and case references…",
        "brief.no_refs": "No recognizable case citations or case names were found. Paste a brief, memo, or argument with reporter citations (for example, 384 U.S. 436) or full case names.",
        "brief.resolve": "Resolving {count} reference(s) against primary-law sources…",
        "brief.support_unavailable": "Support check was unavailable.",
        "brief.summary": "Brief Review checked {count} extracted reference(s): {resolved} resolved to source-backed cases. {quotes}{supports} citation(s) were marked as directly supporting the extracted proposition. Use the table above as a verification checklist, not legal advice.",
        "brief.quotes": "{found}/{checked} nearby quote(s) were found in the matched opinions. ",
        "tool.search": "Searching case law {label}: {query}",
        "tool.verify": "Verifying how these authorities have been treated…",
        "tool.no_cases": "No matching case law was found. Try a different spelling, a fuller case name, or a precise reporter citation (e.g. 384 U.S. 436).",
        "chat.understand": "Understanding your question…",
        "chat.search_issue": "Searching {label}: {query}",
        "chat.refine_issue": "Refining {label}: {query}",
        "chat.issue": "issue",
        "chat.federal": "Searching federal statutes & regulations for: {query}",
        "chat.none": "No matching case law or federal regulation was found for this query. Try rephrasing with the parties, the legal issue, or a jurisdiction.",
        "chat.summarizer_empty": "(Showing retrieved cases above; the summarizer returned nothing.)",
        "chat.summarizer_down": "The summarizer is unavailable right now, but the real cases retrieved for your query are shown above — open any of them to read the source.",
        "warning.citations": "Citation check: marker(s) {refs} don't correspond to any retrieved case above and may be inaccurate — rely only on the {count} sources listed.",
        "search.by_concept": "by concept",
        "search.by_keyword": "by keyword",
        "search.by_case": "by case name",
        "search.by_citation": "by citation",
    },
    "es": {
        "extract.status": "Extrayendo citas y nombres de casos…",
        "extract.summary": "El Extractor de citas encontró {count} referencia(s) única(s) a casos.",
        "extract.none": "No se encontraron citas de casos ni nombres de casos reconocibles.",
        "resolver.status": "Resolviendo la referencia contra casos candidatos…",
        "resolver.summary": "El Resolvedor de casos devolvió {count} candidato(s). Abre Detalles / PDFs para inspeccionar los metadatos de fuente.",
        "resolver.none": "No se encontró ningún caso candidato para esa referencia.",
        "laws.status": "Buscando en US Code y CFR: {query}",
        "laws.summary": "La búsqueda de leyes y reglas encontró {count} fuente(s) de estatutos/regulaciones.",
        "laws.none": "No se encontró ningún estatuto o regulación federal coincidente.",
        "brief.extract": "Extrayendo citas y referencias a casos…",
        "brief.no_refs": "No se encontraron citas de casos ni nombres de casos reconocibles. Pega un brief, memorando o argumento con citas reporter (por ejemplo, 384 U.S. 436) o nombres completos de casos.",
        "brief.resolve": "Resolviendo {count} referencia(s) contra fuentes de derecho primario…",
        "brief.support_unavailable": "La comprobación de soporte no estuvo disponible.",
        "brief.summary": "Brief Review revisó {count} referencia(s) extraída(s): {resolved} se resolvieron a casos con fuente verificable. {quotes}{supports} cita(s) fueron marcadas como soporte directo de la proposición extraída. Usa la tabla como checklist de verificación, no como asesoría legal.",
        "brief.quotes": "{found}/{checked} cita(s) textual(es) cercana(s) se encontraron en las opiniones coincidentes. ",
        "tool.search": "Buscando jurisprudencia {label}: {query}",
        "tool.verify": "Verificando cómo estas autoridades han sido tratadas…",
        "tool.no_cases": "No se encontró jurisprudencia coincidente. Prueba otra ortografía, un nombre de caso más completo o una cita reporter precisa (p. ej., 384 U.S. 436).",
        "chat.understand": "Entendiendo tu pregunta…",
        "chat.search_issue": "Buscando {label}: {query}",
        "chat.refine_issue": "Afinando {label}: {query}",
        "chat.issue": "tema",
        "chat.federal": "Buscando estatutos y regulaciones federales: {query}",
        "chat.none": "No se encontró jurisprudencia ni regulación federal coincidente para esta búsqueda. Prueba reformular con las partes, el tema legal o una jurisdicción.",
        "chat.summarizer_empty": "(Se muestran los casos recuperados arriba; el resumidor no devolvió texto.)",
        "chat.summarizer_down": "El resumidor no está disponible ahora, pero los casos reales recuperados para tu búsqueda se muestran arriba; abre cualquiera para leer la fuente.",
        "warning.citations": "Comprobación de citas: los marcadores {refs} no corresponden a ningún caso recuperado arriba y pueden ser inexactos; apóyate solo en las {count} fuentes listadas.",
        "search.by_concept": "por concepto",
        "search.by_keyword": "por palabra clave",
        "search.by_case": "por nombre de caso",
        "search.by_citation": "por cita",
    },
    "zh": {
        "extract.status": "正在提取案例引用和案例名称…",
        "extract.summary": "引用提取器找到 {count} 条唯一案例引用。",
        "extract.none": "没有找到可识别的案例引用或案例名称。",
        "resolver.status": "正在把引用解析为候选案例…",
        "resolver.summary": "案例解析器返回 {count} 个候选案例。打开详情 / PDF 查看来源元数据。",
        "resolver.none": "没有为该引用找到候选案例。",
        "laws.status": "正在搜索 US Code 和 CFR：{query}",
        "laws.summary": "法律与规则搜索找到 {count} 条成文法/法规来源。",
        "laws.none": "没有找到匹配的联邦成文法或法规。",
        "brief.extract": "正在提取引用和案例参考…",
        "brief.no_refs": "没有找到可识别的案例引用或案例名称。请粘贴包含 reporter citation（例如 384 U.S. 436）或完整案例名称的 brief、memo 或论证。",
        "brief.resolve": "正在把 {count} 条引用解析到一手法律来源…",
        "brief.support_unavailable": "暂时无法进行支持性检查。",
        "brief.summary": "Brief Review 检查了 {count} 条提取引用：其中 {resolved} 条解析到有来源支持的案例。{quotes}{supports} 条引用被标记为直接支持提取出的命题。请把上方表格当作验证清单，而不是法律意见。",
        "brief.quotes": "在匹配意见中找到 {found}/{checked} 条附近 quote。",
        "tool.search": "正在按{label}搜索判例法：{query}",
        "tool.verify": "正在验证这些权威资料后续如何被引用/处理…",
        "tool.no_cases": "没有找到匹配的判例法。请尝试不同拼写、更完整的案例名称，或准确的 reporter citation（例如 384 U.S. 436）。",
        "chat.understand": "正在理解你的问题…",
        "chat.search_issue": "正在搜索{label}：{query}",
        "chat.refine_issue": "正在优化{label}：{query}",
        "chat.issue": "议题",
        "chat.federal": "正在搜索联邦成文法与法规：{query}",
        "chat.none": "没有为该查询找到匹配的判例法或联邦法规。请尝试加入当事人、法律问题或司法辖区重新表述。",
        "chat.summarizer_empty": "（上方已显示检索到的案例；总结器没有返回内容。）",
        "chat.summarizer_down": "总结器当前不可用，但上方已显示为你的查询检索到的真实案例；可以打开任一案例阅读来源。",
        "warning.citations": "引用检查：标记 {refs} 不对应上方任何检索到的案例，可能不准确；请只依赖列出的 {count} 个来源。",
        "search.by_concept": "概念",
        "search.by_keyword": "关键词",
        "search.by_case": "案例名称",
        "search.by_citation": "引用",
    },
    "zh-TW": {
        "extract.status": "正在擷取案例引用和案例名稱…",
        "extract.summary": "引用擷取器找到 {count} 條唯一案例引用。",
        "extract.none": "沒有找到可識別的案例引用或案例名稱。",
        "resolver.status": "正在把引用解析為候選案例…",
        "resolver.summary": "案例解析器返回 {count} 個候選案例。開啟詳情 / PDF 查看來源元資料。",
        "resolver.none": "沒有為該引用找到候選案例。",
        "laws.status": "正在搜尋 US Code 和 CFR：{query}",
        "laws.summary": "法律與規則搜尋找到 {count} 條成文法/法規來源。",
        "laws.none": "沒有找到匹配的聯邦成文法或法規。",
        "brief.extract": "正在擷取引用和案例參考…",
        "brief.no_refs": "沒有找到可識別的案例引用或案例名稱。請貼上包含 reporter citation（例如 384 U.S. 436）或完整案例名稱的 brief、memo 或論證。",
        "brief.resolve": "正在把 {count} 條引用解析到一手法律來源…",
        "brief.support_unavailable": "暫時無法進行支持性檢查。",
        "brief.summary": "Brief Review 檢查了 {count} 條擷取引用：其中 {resolved} 條解析到有來源支持的案例。{quotes}{supports} 條引用被標記為直接支持擷取出的命題。請把上方表格當作驗證清單，而不是法律意見。",
        "brief.quotes": "在匹配意見中找到 {found}/{checked} 條附近 quote。",
        "tool.search": "正在按{label}搜尋判例法：{query}",
        "tool.verify": "正在驗證這些權威資料後續如何被引用/處理…",
        "tool.no_cases": "沒有找到匹配的判例法。請嘗試不同拼寫、更完整的案例名稱，或準確的 reporter citation（例如 384 U.S. 436）。",
        "chat.understand": "正在理解你的問題…",
        "chat.search_issue": "正在搜尋{label}：{query}",
        "chat.refine_issue": "正在最佳化{label}：{query}",
        "chat.issue": "議題",
        "chat.federal": "正在搜尋聯邦成文法與法規：{query}",
        "chat.none": "沒有為該查詢找到匹配的判例法或聯邦法規。請嘗試加入當事人、法律問題或司法轄區重新表述。",
        "chat.summarizer_empty": "（上方已顯示檢索到的案例；總結器沒有返回內容。）",
        "chat.summarizer_down": "總結器目前不可用，但上方已顯示為你的查詢檢索到的真實案例；可以開啟任一案例閱讀來源。",
        "warning.citations": "引用檢查：標記 {refs} 不對應上方任何檢索到的案例，可能不準確；請只依賴列出的 {count} 個來源。",
        "search.by_concept": "概念",
        "search.by_keyword": "關鍵字",
        "search.by_case": "案例名稱",
        "search.by_citation": "引用",
    },
    "fr": {
        "extract.status": "Extraction des citations et noms de dossiers…",
        "extract.summary": "L’extracteur de citations a trouvé {count} référence(s) unique(s).",
        "extract.none": "Aucune citation ou nom de dossier reconnaissable n’a été trouvé.",
        "resolver.status": "Résolution de la référence vers des dossiers candidats…",
        "resolver.summary": "Le résolveur a retourné {count} candidat(s). Ouvrez Détails / PDFs pour inspecter les métadonnées de source.",
        "resolver.none": "Aucun dossier candidat n’a été trouvé pour cette référence.",
        "laws.status": "Recherche dans US Code et CFR : {query}",
        "laws.summary": "La recherche lois et règles a trouvé {count} source(s) de lois/règlements.",
        "laws.none": "Aucune loi ou réglementation fédérale correspondante n’a été trouvée.",
        "brief.extract": "Extraction des citations et références de dossiers…",
        "brief.no_refs": "Aucune citation ou nom de dossier reconnaissable n’a été trouvé. Collez un brief, mémo ou argument avec des citations reporter (par exemple, 384 U.S. 436) ou des noms complets de dossiers.",
        "brief.resolve": "Résolution de {count} référence(s) contre des sources de droit primaire…",
        "brief.support_unavailable": "La vérification du support n’était pas disponible.",
        "brief.summary": "Brief Review a vérifié {count} référence(s) extraites : {resolved} résolue(s) vers des dossiers sourcés. {quotes}{supports} citation(s) ont été marquées comme soutenant directement la proposition extraite. Utilisez le tableau comme liste de vérification, pas comme avis juridique.",
        "brief.quotes": "{found}/{checked} citation(s) proche(s) ont été trouvées dans les opinions correspondantes. ",
        "tool.search": "Recherche de jurisprudence {label} : {query}",
        "tool.verify": "Vérification du traitement de ces autorités…",
        "tool.no_cases": "Aucune jurisprudence correspondante n’a été trouvée. Essayez une autre orthographe, un nom plus complet ou une citation reporter précise (p. ex. 384 U.S. 436).",
        "chat.understand": "Compréhension de votre question…",
        "chat.search_issue": "Recherche {label} : {query}",
        "chat.refine_issue": "Affinage {label} : {query}",
        "chat.issue": "question",
        "chat.federal": "Recherche de lois et règlements fédéraux : {query}",
        "chat.none": "Aucune jurisprudence ou réglementation fédérale correspondante n’a été trouvée. Reformulez avec les parties, la question juridique ou la juridiction.",
        "chat.summarizer_empty": "(Les dossiers récupérés sont affichés ci-dessus ; le synthétiseur n’a rien retourné.)",
        "chat.summarizer_down": "Le synthétiseur est indisponible pour le moment, mais les dossiers réels récupérés sont affichés ci-dessus ; ouvrez-en un pour lire la source.",
        "warning.citations": "Vérification des citations : les marqueurs {refs} ne correspondent à aucun dossier récupéré et peuvent être inexacts ; fiez-vous seulement aux {count} sources listées.",
        "search.by_concept": "par concept",
        "search.by_keyword": "par mot-clé",
        "search.by_case": "par nom de dossier",
        "search.by_citation": "par citation",
    },
    "pt": {
        "extract.status": "Extraindo citações e nomes de casos…",
        "extract.summary": "O Extrator de citações encontrou {count} referência(s) única(s) a casos.",
        "extract.none": "Nenhuma citação ou nome de caso reconhecível foi encontrado.",
        "resolver.status": "Resolvendo a referência para casos candidatos…",
        "resolver.summary": "O Resolvedor retornou {count} candidato(s). Abra Detalhes / PDFs para inspecionar metadados de fonte.",
        "resolver.none": "Nenhum caso candidato foi encontrado para essa referência.",
        "laws.status": "Pesquisando US Code e CFR: {query}",
        "laws.summary": "A pesquisa de leis e regras encontrou {count} fonte(s) de estatuto/regulamento.",
        "laws.none": "Nenhum estatuto ou regulamento federal correspondente foi encontrado.",
        "brief.extract": "Extraindo citações e referências de casos…",
        "brief.no_refs": "Nenhuma citação ou nome de caso reconhecível foi encontrado. Cole uma peça, memorando ou argumento com citações reporter (por exemplo, 384 U.S. 436) ou nomes completos de casos.",
        "brief.resolve": "Resolvendo {count} referência(s) contra fontes de direito primário…",
        "brief.support_unavailable": "A checagem de suporte não estava disponível.",
        "brief.summary": "Brief Review verificou {count} referência(s) extraída(s): {resolved} resolvida(s) para casos com fonte. {quotes}{supports} citação(ões) foram marcadas como suporte direto da proposição extraída. Use a tabela como checklist de verificação, não como aconselhamento jurídico.",
        "brief.quotes": "{found}/{checked} citação(ões) próxima(s) foram encontradas nas opiniões correspondentes. ",
        "tool.search": "Pesquisando jurisprudência {label}: {query}",
        "tool.verify": "Verificando como essas autoridades foram tratadas…",
        "tool.no_cases": "Nenhuma jurisprudência correspondente foi encontrada. Tente outra ortografia, um nome de caso mais completo ou uma citação reporter precisa (ex.: 384 U.S. 436).",
        "chat.understand": "Entendendo sua pergunta…",
        "chat.search_issue": "Pesquisando {label}: {query}",
        "chat.refine_issue": "Refinando {label}: {query}",
        "chat.issue": "questão",
        "chat.federal": "Pesquisando estatutos e regulamentos federais: {query}",
        "chat.none": "Nenhuma jurisprudência ou regulamento federal correspondente foi encontrado. Reformule com as partes, a questão jurídica ou uma jurisdição.",
        "chat.summarizer_empty": "(Os casos recuperados aparecem acima; o resumidor não retornou texto.)",
        "chat.summarizer_down": "O resumidor está indisponível agora, mas os casos reais recuperados aparecem acima; abra qualquer um para ler a fonte.",
        "warning.citations": "Checagem de citações: os marcadores {refs} não correspondem a nenhum caso recuperado e podem estar incorretos; confie apenas nas {count} fontes listadas.",
        "search.by_concept": "por conceito",
        "search.by_keyword": "por palavra-chave",
        "search.by_case": "por nome de caso",
        "search.by_citation": "por citação",
    },
    "ko": {
        "extract.status": "사건 인용과 사건명을 추출하는 중…",
        "extract.summary": "인용 추출기가 고유한 사건 참조 {count}개를 찾았습니다.",
        "extract.none": "인식 가능한 사건 인용이나 사건명을 찾지 못했습니다.",
        "resolver.status": "참조를 후보 사건으로 해석하는 중…",
        "resolver.summary": "사건 해석기가 후보 {count}개를 반환했습니다. 세부 정보 / PDF를 열어 출처 메타데이터를 확인하세요.",
        "resolver.none": "해당 참조에 대한 후보 사건을 찾지 못했습니다.",
        "laws.status": "US Code 및 CFR 검색 중: {query}",
        "laws.summary": "법률 및 규칙 검색이 법령/규정 출처 {count}개를 찾았습니다.",
        "laws.none": "일치하는 연방 법령 또는 규정을 찾지 못했습니다.",
        "brief.extract": "인용과 사건 참조를 추출하는 중…",
        "brief.no_refs": "인식 가능한 사건 인용이나 사건명을 찾지 못했습니다. reporter citation(예: 384 U.S. 436) 또는 전체 사건명이 포함된 brief, 메모, 주장을 붙여 넣으세요.",
        "brief.resolve": "{count}개 참조를 1차 법률 출처에 대조하는 중…",
        "brief.support_unavailable": "지원성 확인을 사용할 수 없습니다.",
        "brief.summary": "Brief Review가 추출된 참조 {count}개를 확인했습니다. {resolved}개가 출처 있는 사건으로 해석되었습니다. {quotes}{supports}개 인용이 추출된 명제를 직접 지원하는 것으로 표시되었습니다. 위 표는 검증 체크리스트로 사용하고 법률 자문으로 보지 마세요.",
        "brief.quotes": "일치한 의견에서 주변 quote {found}/{checked}개를 찾았습니다. ",
        "tool.search": "판례법 검색 중({label}): {query}",
        "tool.verify": "이 권위 자료들이 후속 의견에서 어떻게 다뤄졌는지 확인하는 중…",
        "tool.no_cases": "일치하는 판례법을 찾지 못했습니다. 다른 철자, 더 완전한 사건명 또는 정확한 reporter citation(예: 384 U.S. 436)을 시도하세요.",
        "chat.understand": "질문을 이해하는 중…",
        "chat.search_issue": "{label} 검색 중: {query}",
        "chat.refine_issue": "{label} 검색을 다듬는 중: {query}",
        "chat.issue": "쟁점",
        "chat.federal": "연방 법령 및 규정 검색 중: {query}",
        "chat.none": "이 쿼리에 일치하는 판례법이나 연방 규정을 찾지 못했습니다. 당사자, 법적 쟁점 또는 관할을 넣어 다시 표현해 보세요.",
        "chat.summarizer_empty": "(검색된 사건은 위에 표시되었지만 요약기가 내용을 반환하지 않았습니다.)",
        "chat.summarizer_down": "요약기를 현재 사용할 수 없지만, 검색된 실제 사건이 위에 표시되어 있습니다. 출처를 읽으려면 열어 보세요.",
        "warning.citations": "인용 확인: {refs} 표지는 위의 검색된 사건과 일치하지 않아 부정확할 수 있습니다. 나열된 {count}개 출처만 신뢰하세요.",
        "search.by_concept": "개념",
        "search.by_keyword": "키워드",
        "search.by_case": "사건명",
        "search.by_citation": "인용",
    },
    "ja": {
        "extract.status": "事件引用と事件名を抽出中…",
        "extract.summary": "引用抽出が一意の事件参照を {count} 件見つけました。",
        "extract.none": "認識できる事件引用または事件名は見つかりませんでした。",
        "resolver.status": "参照を候補事件に解決中…",
        "resolver.summary": "事件リゾルバーが候補を {count} 件返しました。詳細 / PDF を開いて出典メタデータを確認してください。",
        "resolver.none": "その参照に対応する候補事件は見つかりませんでした。",
        "laws.status": "US Code と CFR を検索中: {query}",
        "laws.summary": "法律・規則検索が法令/規則の出典を {count} 件見つけました。",
        "laws.none": "一致する連邦法令または規則は見つかりませんでした。",
        "brief.extract": "引用と事件参照を抽出中…",
        "brief.no_refs": "認識できる事件引用または事件名は見つかりませんでした。reporter citation（例: 384 U.S. 436）または完全な事件名を含む brief、メモ、主張を貼り付けてください。",
        "brief.resolve": "{count} 件の参照を一次法出典に照合中…",
        "brief.support_unavailable": "支持性確認を利用できませんでした。",
        "brief.summary": "Brief Review は抽出された参照 {count} 件を確認しました。{resolved} 件が出典付き事件に解決されました。{quotes}{supports} 件の引用が抽出された命題を直接支持すると表示されました。上の表は検証チェックリストとして使い、法的助言として扱わないでください。",
        "brief.quotes": "一致した意見で近くの quote {found}/{checked} 件が見つかりました。",
        "tool.search": "判例法を検索中（{label}）: {query}",
        "tool.verify": "これらの権威資料が後続意見でどう扱われたか確認中…",
        "tool.no_cases": "一致する判例法は見つかりませんでした。別の表記、より完全な事件名、または正確な reporter citation（例: 384 U.S. 436）を試してください。",
        "chat.understand": "質問を理解中…",
        "chat.search_issue": "{label} を検索中: {query}",
        "chat.refine_issue": "{label} を調整中: {query}",
        "chat.issue": "論点",
        "chat.federal": "連邦法令・規則を検索中: {query}",
        "chat.none": "このクエリに一致する判例法または連邦規則は見つかりませんでした。当事者、法的論点、管轄を入れて言い換えてください。",
        "chat.summarizer_empty": "（検索された事件は上に表示されていますが、要約器は内容を返しませんでした。）",
        "chat.summarizer_down": "要約器は現在利用できませんが、検索された実在の事件は上に表示されています。出典を読むには開いてください。",
        "warning.citations": "引用確認: {refs} の標識は上の検索済み事件に対応せず不正確な可能性があります。列挙された {count} 件の出典だけに依拠してください。",
        "search.by_concept": "概念",
        "search.by_keyword": "キーワード",
        "search.by_case": "事件名",
        "search.by_citation": "引用",
    },
    "vi": {
        "extract.status": "Đang trích xuất trích dẫn và tên vụ án…",
        "extract.summary": "Bộ trích xuất đã tìm thấy {count} tham chiếu vụ án duy nhất.",
        "extract.none": "Không tìm thấy trích dẫn vụ án hoặc tên vụ án có thể nhận dạng.",
        "resolver.status": "Đang phân giải tham chiếu thành vụ án ứng viên…",
        "resolver.summary": "Bộ phân giải trả về {count} ứng viên. Mở Chi tiết / PDF để kiểm tra siêu dữ liệu nguồn.",
        "resolver.none": "Không tìm thấy vụ án ứng viên cho tham chiếu đó.",
        "laws.status": "Đang tìm trong US Code và CFR: {query}",
        "laws.summary": "Tìm kiếm luật và quy định tìm thấy {count} nguồn luật/quy định.",
        "laws.none": "Không tìm thấy luật hoặc quy định liên bang phù hợp.",
        "brief.extract": "Đang trích xuất trích dẫn và tham chiếu vụ án…",
        "brief.no_refs": "Không tìm thấy trích dẫn vụ án hoặc tên vụ án có thể nhận dạng. Hãy dán brief, memo hoặc lập luận có reporter citation (ví dụ, 384 U.S. 436) hoặc tên vụ án đầy đủ.",
        "brief.resolve": "Đang phân giải {count} tham chiếu với nguồn luật sơ cấp…",
        "brief.support_unavailable": "Không thể kiểm tra mức hỗ trợ.",
        "brief.summary": "Brief Review đã kiểm tra {count} tham chiếu được trích xuất: {resolved} tham chiếu được phân giải thành vụ án có nguồn. {quotes}{supports} trích dẫn được đánh dấu là trực tiếp hỗ trợ mệnh đề đã trích xuất. Dùng bảng trên như checklist xác minh, không phải tư vấn pháp lý.",
        "brief.quotes": "Tìm thấy {found}/{checked} quote gần đó trong các ý kiến khớp. ",
        "tool.search": "Đang tìm án lệ {label}: {query}",
        "tool.verify": "Đang kiểm tra các nguồn này đã được xử lý như thế nào…",
        "tool.no_cases": "Không tìm thấy án lệ phù hợp. Hãy thử cách viết khác, tên vụ án đầy đủ hơn hoặc reporter citation chính xác (ví dụ 384 U.S. 436).",
        "chat.understand": "Đang hiểu câu hỏi của bạn…",
        "chat.search_issue": "Đang tìm {label}: {query}",
        "chat.refine_issue": "Đang tinh chỉnh {label}: {query}",
        "chat.issue": "vấn đề",
        "chat.federal": "Đang tìm luật và quy định liên bang: {query}",
        "chat.none": "Không tìm thấy án lệ hoặc quy định liên bang phù hợp. Hãy diễn đạt lại với các bên, vấn đề pháp lý hoặc thẩm quyền.",
        "chat.summarizer_empty": "(Các vụ án đã truy xuất được hiển thị ở trên; bộ tóm tắt không trả về nội dung.)",
        "chat.summarizer_down": "Bộ tóm tắt hiện không khả dụng, nhưng các vụ án thật đã truy xuất được hiển thị ở trên; hãy mở một vụ để đọc nguồn.",
        "warning.citations": "Kiểm tra trích dẫn: các dấu {refs} không tương ứng với vụ án nào đã truy xuất ở trên và có thể không chính xác; chỉ dựa vào {count} nguồn được liệt kê.",
        "search.by_concept": "theo khái niệm",
        "search.by_keyword": "theo từ khóa",
        "search.by_case": "theo tên vụ án",
        "search.by_citation": "theo trích dẫn",
    },
}


def _normalize_language(value: object) -> str:
    lang = str(value or "en").strip().lower().replace("_", "-")
    if lang.startswith("zh-tw") or lang.startswith("zh-hk") or lang.startswith("zh-mo") or "hant" in lang:
        return "zh-TW"
    if lang.startswith("es"):
        return "es"
    if lang.startswith("fr"):
        return "fr"
    if lang.startswith("pt"):
        return "pt"
    if lang.startswith("ko"):
        return "ko"
    if lang.startswith("ja"):
        return "ja"
    if lang.startswith("vi"):
        return "vi"
    if lang.startswith("zh") or lang in {"cn", "chinese"}:
        return "zh"
    return "en"


def _lt(language: str, key: str, **kwargs: object) -> str:
    table = _SERVER_TEXT.get(language) or _SERVER_TEXT["en"]
    template = table.get(key) or _SERVER_TEXT["en"].get(key) or key
    return template.format(**kwargs)


def _language_instruction(language: str) -> str:
    label = _LANG_LABELS.get(language, _LANG_LABELS["en"])
    return (
        f"Write user-facing prose in {label}. Keep US case names, reporter "
        "citations, statute/regulation citations, docket numbers, URLs, and quoted "
        "legal source text in their original English. Do not translate citation "
        "markers like [1] or [R1]."
    )


async def _route(messages: list[dict], language: str = "en") -> dict:
    """Ask the LLM to turn the conversation into a search plan. Degrade to raw query."""
    system = _ROUTE_SYSTEM + "\n" + (
        "If the user asks in a non-English language, translate query and "
        "statute_query into concise English legal search terms. Write clarify in "
        f"{_LANG_LABELS.get(language, 'English')}."
    )
    convo = [{"role": "system", "content": system}, *messages]
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


async def _research_plan(messages: list[dict], fallback_query: str, route_plan: dict | None = None,
                         language: str = "en") -> dict:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), fallback_query)
    system = (
        _RESEARCH_PLAN_SYSTEM + "\n" + _language_instruction(language) + " "
        "Use English legal search terms for query and statute_query. Use the selected "
        "language for summary, depends_on, and issue labels."
    )
    convo = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"User question:\n{last}\n\nInitial router plan:\n{json.dumps(route_plan or {}, ensure_ascii=False)}"},
    ]
    try:
        plan = await llm.complete_json(convo, max_tokens=650)
    except (httpx.HTTPError, KeyError, ValueError):
        plan = {}
    issues = plan.get("issues") if isinstance(plan, dict) else None
    if not isinstance(issues, list) or not issues:
        rp = route_plan or {}
        primary_label = {"en": "Primary issue", "es": "Tema principal", "zh": "主要议题"}.get(language, "Primary issue")
        fallback_summary = {
            "en": "Search primary-law authorities and organize the answer.",
            "es": "Buscar autoridades de derecho primario y organizar la respuesta.",
            "zh": "搜索一手法律权威资料并组织答案。",
            "zh-TW": "搜尋一手法律權威資料並組織答案。",
            "fr": "Rechercher des autorités de droit primaire et organiser la réponse.",
            "pt": "Pesquisar autoridades de direito primário e organizar a resposta.",
            "ko": "1차 법률 권위를 검색하고 답변을 구성합니다.",
            "ja": "一次法の権威資料を検索し、回答を構成します。",
            "vi": "Tìm nguồn luật sơ cấp và tổ chức câu trả lời.",
        }.get(language, "Search primary-law authorities and organize the answer.")
        primary_label = {
            "en": "Primary issue", "es": "Tema principal", "zh": "主要议题",
            "zh-TW": "主要議題", "fr": "Question principale", "pt": "Questão principal",
            "ko": "주요 쟁점", "ja": "主要論点", "vi": "Vấn đề chính",
        }.get(language, primary_label)
        issues = [{
            "label": primary_label,
            "query": (rp.get("query") or fallback_query or last).strip(),
            "court": (rp.get("court") or "").strip(),
            "statute_query": (rp.get("statute_query") or "").strip(),
        }]
        plan = {"summary": fallback_summary,
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
        primary_label = {
            "en": "Primary issue", "es": "Tema principal", "zh": "主要议题",
            "zh-TW": "主要議題", "fr": "Question principale", "pt": "Questão principal",
            "ko": "주요 쟁점", "ja": "主要論点", "vi": "Vấn đề chính",
        }.get(language, "Primary issue")
        cleaned = [{"label": primary_label, "query": fallback_query or last, "court": "", "statute_query": ""}]
    default_summary = {
        "en": "Research the issue with primary-law sources.",
        "es": "Investigar el tema con fuentes de derecho primario.",
        "zh": "用一手法律来源调研该议题。",
        "zh-TW": "用一手法律來源調研該議題。",
        "fr": "Étudier la question avec des sources de droit primaire.",
        "pt": "Pesquisar a questão com fontes de direito primário.",
        "ko": "1차 법률 출처로 쟁점을 조사합니다.",
        "ja": "一次法の出典で論点をリサーチします。",
        "vi": "Nghiên cứu vấn đề bằng nguồn luật sơ cấp.",
    }.get(language, "Research the issue with primary-law sources.")
    return {"summary": (plan.get("summary") or default_summary)[:300],
            "depends_on": [str(x)[:140] for x in (plan.get("depends_on") or [])[:5]],
            "issues": cleaned}


async def _brief_support_check(ref: dict, case: object, quote_check: dict | None, passages: list[dict],
                               language: str = "en") -> dict:
    quote = ref.get("quote") or ""
    if quote and quote_check and not quote_check.get("found"):
        return {"status": "Quote not found", "quote_accuracy": "Not found",
                "reason": {
                    "en": "The nearby quoted language was not found in the matched opinion text.",
                    "es": "El texto citado cercano no se encontró en la opinión coincidente.",
                    "zh": "附近的引用原文没有在匹配的意见文本中找到。",
                    "zh-TW": "附近的引用原文沒有在匹配的意見文本中找到。",
                    "fr": "Le passage cité à proximité n’a pas été trouvé dans l’opinion correspondante.",
                    "pt": "O texto citado próximo não foi encontrado na opinião correspondente.",
                    "ko": "가까운 인용 문구를 일치한 의견 텍스트에서 찾지 못했습니다.",
                    "ja": "近くの引用文は一致した意見テキスト内で見つかりませんでした。",
                    "vi": "Không tìm thấy đoạn trích gần đó trong văn bản ý kiến khớp.",
                }.get(language, "The nearby quoted language was not found in the matched opinion text.")}
    if not passages:
        return {"status": "Needs review", "quote_accuracy": "No quote" if not quote else "Needs review",
                "reason": {
                    "en": "No source passage was available for a reliable support check.",
                    "es": "No hubo pasaje fuente suficiente para una comprobación fiable de soporte.",
                    "zh": "没有可用于可靠支持性检查的来源段落。",
                    "zh-TW": "沒有可用於可靠支持性檢查的來源段落。",
                    "fr": "Aucun passage source n’était disponible pour une vérification fiable du support.",
                    "pt": "Nenhum trecho de fonte estava disponível para uma checagem confiável de suporte.",
                    "ko": "신뢰할 수 있는 지원성 확인에 사용할 출처 구절이 없었습니다.",
                    "ja": "信頼できる支持性確認に利用できる出典箇所がありませんでした。",
                    "vi": "Không có đoạn nguồn đủ để kiểm tra mức hỗ trợ đáng tin cậy.",
                }.get(language, "No source passage was available for a reliable support check.")}
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
        {"role": "system", "content": _BRIEF_SUPPORT_SYSTEM + "\n" + _language_instruction(language)
         + " Keep status and quote_accuracy values exactly in the allowed English enum values; write reason in the selected language."},
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
    fallback_reason = {
        "en": "Review the source passage before relying on this citation.",
        "es": "Revisa el pasaje fuente antes de apoyarte en esta cita.",
        "zh": "在依赖该引用前，请先核对来源段落。",
        "zh-TW": "在依賴該引用前，請先核對來源段落。",
        "fr": "Vérifiez le passage source avant de vous appuyer sur cette citation.",
        "pt": "Revise o trecho da fonte antes de confiar nesta citação.",
        "ko": "이 인용에 의존하기 전에 출처 구절을 확인하세요.",
        "ja": "この引用に依拠する前に出典箇所を確認してください。",
        "vi": "Hãy kiểm tra đoạn nguồn trước khi dựa vào trích dẫn này.",
    }.get(language, "Review the source passage before relying on this citation.")
    return {"status": status, "quote_accuracy": qa,
            "reason": (out.get("reason") if isinstance(out, dict) else "") or fallback_reason}


async def _case_analysis(details: dict, focus: str = "", language: str = "en") -> dict:
    passages = details.get("focused_passages") or []
    if not passages:
        return {}
    payload = {
        "title": details.get("title"),
        "court": details.get("court"),
        "date": details.get("date"),
        "citations": details.get("citations") or [],
        "focus": focus,
        "source_availability": details.get("source_availability") or {},
        "passages": [p.get("text", "")[:900] for p in passages[:4]],
    }
    convo = [
        {"role": "system", "content": _CASE_ANALYSIS_SYSTEM + "\n" + _language_instruction(language)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        out = await llm.complete_json(convo, max_tokens=450)
    except (httpx.HTTPError, KeyError, ValueError):
        out = {}
    if not isinstance(out, dict):
        return {}
    return {
        "summary": str(out.get("summary") or "")[:900],
        "why_it_matters": str(out.get("why_it_matters") or "")[:500],
        "key_points": [str(x)[:300] for x in (out.get("key_points") or [])[:5]],
        "limits": [str(x)[:300] for x in (out.get("limits") or [])[:4]],
    }


async def _organize(question: str, cases: list, statutes: list | None = None,
                    research_plan: dict | None = None,
                    language: str = "en") -> AsyncIterator[str]:
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
        {"role": "system", "content": _ORGANIZE_SYSTEM + "\n" + _language_instruction(language)},
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
    raw_messages = body.get("messages") or []
    if not isinstance(raw_messages, list) or len(raw_messages) > MAX_CHAT_MESSAGES:
        return JSONResponse(status_code=400, content={"error": "bad_messages"})
    messages = []
    for m in raw_messages:
        if not isinstance(m, dict) or not m.get("content"):
            continue
        content = str(m.get("content", ""))
        if len(content) > MAX_CHAT_MESSAGE_CHARS:
            return JSONResponse(status_code=413, content={"error": "message_too_long"})
        role = str(m.get("role", "user"))
        messages.append({"role": role if role in {"user", "assistant"} else "user",
                         "content": content})
    if not messages:
        return JSONResponse(status_code=400, content={"error": "no messages"})
    language = _normalize_language(body.get("language"))
    # Monthly question quota per plan. Counted atomically only AFTER the request
    # is known-valid; refunded inside gen() on any path that delivers no value
    # (search outage / nothing found) so a failed request never burns a question.
    # An active one-off day pass raises the effective plan to Max-level.
    eff = db.effective_plan(user)
    plan = db.PLANS.get(eff, db.PLANS[db.DEFAULT_PLAN])
    limit = int(plan["monthly_questions"])
    if not db.try_consume_question(user.id, limit):
        quota_msg = {
            "en": f"You've used all {limit} research questions on the {plan['label']} plan this month. Upgrade for more.",
            "es": f"Ya usaste las {limit} preguntas de investigación del plan {plan['label']} este mes. Mejora el plan para tener más.",
            "zh": f"你已用完 {plan['label']} 计划本月 {limit} 次调研问题额度。升级可获得更多额度。",
            "zh-TW": f"你已用完 {plan['label']} 方案本月 {limit} 次調研問題額度。升級可獲得更多額度。",
            "fr": f"Vous avez utilisé les {limit} questions de recherche du forfait {plan['label']} ce mois-ci. Passez à un forfait supérieur pour en obtenir davantage.",
            "pt": f"Você usou todas as {limit} perguntas de pesquisa do plano {plan['label']} este mês. Faça upgrade para obter mais.",
            "ko": f"이번 달 {plan['label']} 플랜의 조사 질문 {limit}개를 모두 사용했습니다. 더 사용하려면 업그레이드하세요.",
            "ja": f"今月の {plan['label']} プランのリサーチ質問 {limit} 件を使い切りました。追加するにはアップグレードしてください。",
            "vi": f"Bạn đã dùng hết {limit} câu hỏi nghiên cứu của gói {plan['label']} trong tháng này. Hãy nâng cấp để có thêm.",
        }.get(language)
        return JSONResponse(status_code=402, content={
            "error": "quota_exceeded",
            "plan": eff,
            "limit": limit,
            "message": quota_msg,
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
            yield _sse("status", {"message": _lt(language, "extract.status")})
            refs = _extract_legal_references(question)
            if not refs:
                refund()
            yield _sse("citation_extract", {"count": len(refs), "refs": refs})
            yield _sse("token", {"text": _lt(language, "extract.summary", count=len(refs)) if refs else _lt(language, "extract.none")})
            yield _sse("done", {})
            return

        # ── Standalone Case Resolver ─────────────────────────────────────────
        if mode == "resolver":
            yield _sse("status", {"message": _lt(language, "resolver.status")})
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
            yield _sse("token", {"text": _lt(language, "resolver.summary", count=len(cases)) if cases else _lt(language, "resolver.none")})
            yield _sse("done", {})
            return

        # ── Laws & Rules: direct US Code + CFR search ────────────────────────
        if mode == "laws":
            yield _sse("status", {"message": _lt(language, "laws.status", query=question)})
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
            yield _sse("token", {"text": _lt(language, "laws.summary", count=len(statutes)) if statutes else _lt(language, "laws.none")})
            yield _sse("done", {})
            return

        # ── Brief Review: extract refs, resolve, quote-check, support-check ──
        if mode == "brief":
            yield _sse("status", {"message": _lt(language, "brief.extract")})
            refs = _extract_legal_references(question)
            if not refs:
                refund()
                yield _sse("brief_review", {"count": 0, "rows": []})
                yield _sse("token", {"text": _lt(language, "brief.no_refs")})
                yield _sse("done", {})
                return
            yield _sse("status", {"message": _lt(language, "brief.resolve", count=len(refs))})

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
                    row["support_check"] = await _brief_support_check(ref, case, row.get("quote_check"), passages, language)
                    row["status"] = row["support_check"].get("status") or row["status"]
                except Exception:
                    row["support_check"] = {"status": "Needs review", "quote_accuracy": "Needs review", "reason": _lt(language, "brief.support_unavailable")}
                    row["status"] = "Needs review"
                return row

            rows = await asyncio.gather(*(resolve_one(r) for r in refs), return_exceptions=True)
            clean_rows = [r for r in rows if isinstance(r, dict)]
            yield _sse("brief_review", {"count": len(clean_rows), "rows": clean_rows})
            resolved = sum(1 for r in clean_rows if r.get("case"))
            checked = sum(1 for r in clean_rows if r.get("quote_check"))
            found = sum(1 for r in clean_rows if (r.get("quote_check") or {}).get("found"))
            supports = sum(1 for r in clean_rows if (r.get("support_check") or {}).get("status") == "Supports")
            quote_text = _lt(language, "brief.quotes", found=found, checked=checked) if checked else ""
            yield _sse("token", {"text": _lt(language, "brief.summary", count=len(clean_rows),
                                              resolved=resolved, quotes=quote_text,
                                              supports=supports)})
            yield _sse("done", {})
            return

        # ── Toolkit: direct precise search, no LLM routing or answer ──────────
        if mode in ("concept", "keyword", "case", "citation"):
            label = {"concept": _lt(language, "search.by_concept"), "keyword": _lt(language, "search.by_keyword"),
                     "case": _lt(language, "search.by_case"), "citation": _lt(language, "search.by_citation")}[mode]
            yield _sse("status", {"message": _lt(language, "tool.search", label=label, query=question)})
            try:
                cases = await cl.search(question, mode=mode, max_results=10)
            except httpx.HTTPError as exc:
                refund()
                yield _sse("error", {"message": f"search failed: {exc}"})
                yield _sse("done", {})
                return
            if cases:
                yield _sse("status", {"message": _lt(language, "tool.verify")})
                try:
                    await cl.attach_treatment(cases, top=8)
                except Exception:
                    pass
            yield _sse("cases", {"query": question, "court": "",
                                 "count": len(cases),
                                 "cases": [c.to_dict() for c in cases]})
            if not cases:
                refund()
                yield _sse("token", {"text": _lt(language, "tool.no_cases")})
            yield _sse("done", {})
            return

        yield _sse("status", {"message": _lt(language, "chat.understand")})
        plan = await _route(messages, language)

        if plan.get("action") == "clarify" and plan.get("clarify"):
            yield _sse("clarify", {"question": plan["clarify"]})
            yield _sse("done", {})
            return

        research_plan = await _research_plan(messages, question, plan, language)
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
                issue_label = issue.get('label') or _lt(language, "chat.issue")
                yield _sse("status", {"message": _lt(language, "chat.search_issue", label=issue_label, query=query)})
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
                yield _sse("status", {"message": _lt(language, "chat.refine_issue", label=issue_label, query=new_query)})
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
            yield _sse("status", {"message": _lt(language, "tool.verify")})
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
            yield _sse("status", {"message": _lt(language, "chat.federal", query=statute_query)})
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
            yield _sse("token", {"text": _lt(language, "chat.none")})
            yield _sse("done", {})
            return

        # Organize the retrieved cases (LLM). Degrade gracefully if it is down.
        try:
            got_any = False
            answer = ""
            async for delta in _organize(question, cases, statutes, research_plan, language):
                got_any = True
                answer += delta
                yield _sse("token", {"text": delta})
            if not got_any:
                yield _sse("token", {"text": _lt(language, "chat.summarizer_empty")})
            else:
                # Anti-hallucination guard: every [n] marker must point at a real
                # retrieved case. If the model invented an out-of-range index,
                # warn the reader rather than letting it pass silently.
                bad = _out_of_range_citations(answer, len(cases))
                if bad:
                    refs = ", ".join(f"[{i}]" for i in bad)
                    yield _sse("warning", {"message": _lt(language, "warning.citations",
                                                          refs=refs, count=len(cases))})
        except httpx.HTTPError:
            yield _sse("token", {"text": _lt(language, "chat.summarizer_down")})
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
    if not cluster_id.isdigit():
        return JSONResponse(status_code=400, content={"error": "invalid_cluster_id"})
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
    if not cluster_id.isdigit():
        return JSONResponse(status_code=400, content={"error": "invalid_cluster_id"})
    focus = request.query_params.get("focus", "")[:800]
    language = _normalize_language(request.query_params.get("language"))
    try:
        result = await cl.case_details(cluster_id, focus=focus)
        result["case_analysis"] = await _case_analysis(result, focus, language)
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


def _same_origin_request(request: Request) -> bool:
    """Reject browser-initiated cross-origin state changes.

    Missing Origin/Referer is allowed for non-browser clients, but if either is
    present it must match the public base URL or request Host.
    """
    from urllib.parse import urlparse
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    candidate = origin or referer
    if not candidate:
        return True
    allowed_hosts = {h for h in [request.headers.get("host", "").lower()] if h}
    if auth.PUBLIC_BASE:
        try:
            allowed_hosts.add(urlparse(auth.PUBLIC_BASE).netloc.lower())
        except ValueError:
            pass
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in allowed_hosts


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
def auth_logout(request: Request):
    if not _same_origin_request(request):
        return JSONResponse(status_code=403, content={"error": "bad_origin"})
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
    if not _same_origin_request(request):
        return JSONResponse(status_code=403, content={"error": "bad_origin"})
    body = await request.json()
    payload = body.get("payload")
    if not isinstance(payload, (list, dict)):
        return JSONResponse(status_code=400, content={"error": "bad_payload"})
    try:
        payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "bad_payload"})
    if payload_bytes > MAX_SESSION_PAYLOAD_BYTES:
        return JSONResponse(status_code=413, content={"error": "payload_too_large"})
    sid = db.save_session(user.id, session_id=body.get("id"),
                          title=str(body.get("title") or "Untitled research"),
                          payload=payload)
    return {"id": sid}


@app.delete("/api/sessions/{session_id}")
def sessions_delete(session_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    if not _same_origin_request(request):
        return JSONResponse(status_code=403, content={"error": "bad_origin"})
    return {"ok": db.delete_session(user.id, session_id)}


# Static frontend (mounted last so /api/* takes precedence).
app.mount("/", StaticFiles(directory=os.path.abspath(WEB_DIR), html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
