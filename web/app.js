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
// Snapshot the intro/landing markup at load so "New research" can restore it.
const INTRO_HTML = chat.innerHTML;
const form = document.getElementById('composer');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const DEFAULT_PLACEHOLDER = 'Ask in plain English…  (e.g. wrongful termination after reporting safety violations)';
const LANG_STORAGE_KEY = 'juricodex-language';
const LANGS = {
  en: { label: 'English', backend: 'en' },
  es: { label: 'Español', backend: 'es' },
  zh: { label: '中文', backend: 'zh' },
  'zh-TW': { label: '繁體中文', backend: 'zh-TW' },
  fr: { label: 'Français', backend: 'fr' },
  pt: { label: 'Português', backend: 'pt' },
  ko: { label: '한국어', backend: 'ko' },
  ja: { label: '日本語', backend: 'ja' },
  vi: { label: 'Tiếng Việt', backend: 'vi' },
};

const I18N = {
  en: {
    'topbar.tag': 'Find the law · reason through it · verify every step',
    'nav.engineGroup': 'Legal Reasoning Engine',
    'nav.researchEngine': 'Research Engine',
    'nav.toolkitGroup': 'Legal Research Toolkit',
    'nav.concept': 'Search by Concept',
    'nav.keyword': 'Search by Keyword',
    'nav.case': 'Search by Case Name',
    'nav.citation': 'Search by Citation',
    'nav.laws': 'Laws & Rules Search',
    'nav.extractor': 'Citation Extractor',
    'nav.resolver': 'Case Resolver',
    'nav.brief': 'Brief Review',
    'nav.history': 'Research History',
    'history.empty': 'No research yet',
    'intro.h1': 'Find the law. Reason through it. Verify every step.',
    'intro.lead': 'JuriCodex Platform is a legal research workspace with two ways in: a Legal Reasoning Engine for working through questions, and a Legal Research Toolkit for searching, checking, and verifying primary law directly.',
    'intro.engineKicker': 'Legal Reasoning Engine',
    'intro.engineTitle': 'When the work goes beyond search.',
    'intro.engineBody': 'Ask a question in plain English. JuriCodex clarifies when facts matter, searches real authorities, refines the query when needed, and shows what the answer depends on.',
    'intro.toolkitKicker': 'Legal Research Toolkit',
    'intro.toolkitTitle': 'Search it. Cite it. Verify it.',
    'intro.toolkitBody': 'Start from a concept, keyword, case name, citation, or pasted brief. Find source-backed cases, statutes, regulations, quote checks, and treatment-lite signals you can inspect.',
    'intro.disclaimer': 'JuriCodex reasons from <strong>real primary sources</strong> you can open and verify. It is a <strong>research tool</strong> — verify the authorities before you rely on the analysis, and consult a licensed attorney for an actual decision.',
    'example.0': 'Can my landlord keep my security deposit for normal wear and tear?',
    'example.1': 'What did Miranda v. Arizona actually hold?',
    'example.2': 'Fourth Amendment limits on warrantless car searches',
    'placeholder.default': DEFAULT_PLACEHOLDER,
    'placeholder.concept': 'Describe the legal concept or situation in plain English…',
    'placeholder.keyword': 'Enter keywords to search case law…',
    'placeholder.case': 'Enter a case name, e.g. Miranda v. Arizona',
    'placeholder.citation': 'Enter a citation, e.g. 384 U.S. 436',
    'placeholder.laws': 'Describe a federal statute or regulation topic…',
    'placeholder.extractor': 'Paste legal text to extract case citations and case names…',
    'placeholder.resolver': 'Enter a citation, case name, short cite, docket, or messy reference…',
    'placeholder.brief': 'Paste a brief, memo, argument, or legal text to extract citations and verify quotes…',
    'login.title': 'Sign in to ask',
    'login.sub': 'JuriCodex grounds every answer in real court opinions. Sign in to research and keep your work across devices.',
    'login.fine': 'Free to use · We never post on your behalf.',
    'login.unavailable': 'Sign-in is not available right now. Please try again later.',
    'login.continue': 'Continue with {provider}',
    'account.hint': 'Sign in to save your research across devices.',
    'account.signIn': 'Sign in with {provider}',
    'account.signedIn': 'Signed in',
    'account.signOut': 'Sign out',
    'account.signOutTitle': 'Sign out',
    'account.upgrade': 'Upgrade',
    'account.manage': 'Manage billing',
    'account.emailWarn': 'Add a verified email with your sign-in provider before subscribing.',
    'account.usage': '{used}/{limit} this month',
    'account.plan': '{plan} plan',
    'upgrade.title': 'Choose a Platform workspace plan',
    'upgrade.sub': 'Source-backed research, verification, history, and export.',
    'upgrade.quota': "You've used all {limit} questions on the Free plan this month.",
    'upgrade.emailNote': 'Add and verify an email with your sign-in provider before subscribing. We need a real verified email to attach the purchase to your account.',
    'upgrade.monthly': 'Monthly',
    'upgrade.yearly': 'Yearly',
    'upgrade.save': 'Save 2 months',
    'upgrade.oneTime': 'One-time',
    'upgrade.annualPlan': 'Annual plan',
    'upgrade.monthlyPlan': 'Monthly plan',
    'upgrade.note': 'By continuing, you agree to the <a href="/terms.html" target="_blank" rel="noopener">Terms</a> and acknowledge the <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>.',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 source-backed research runs, verification, history, and export.',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'High-volume workspace for Brief Review, quote checks, export, and saved sessions.',
    'plan.day_pass.name': '3-Day Pass',
    'plan.day_pass.pitch': 'Try Max-level access for 3 days. No subscription.',
    'toast.emailRequired': 'Please add and verify an email with your sign-in provider before subscribing.',
    'toast.checkoutOpening': 'Opening secure checkout…',
    'toast.checkoutFallback': 'Checkout popup could not load. Opening hosted checkout…',
    'toast.billingPortal': 'Use your Freemius receipt email to manage billing, or contact support.',
    'toast.authFailed': "Sign-in didn't complete. Please try again.",
    'label.question': 'Question',
    'label.authorities': 'Table of Authorities',
    'label.statutes': 'Federal Statutes & Regulations',
    'label.verified': 'Verified',
    'label.copy': '⧉ Copy',
    'label.copied': '✓ Copied',
    'label.export': '↓ Export',
    'label.exported': '✓ Exported',
    'label.copyCitation': 'Copy citation',
    'label.copiedShort': 'Copied',
    'label.openOpinion': 'Open full opinion ↗',
    'label.openRegulation': 'Open regulation ↗',
    'label.details': 'Details / PDFs',
    'label.verifyQuote': '✓ Verify a quote',
    'label.verifyQuotePlaceholder': 'Paste a quote attributed to this case…',
    'label.check': 'Check',
    'label.loadingDetails': 'Loading case details…',
    'label.detailsFailed': 'Could not load details right now.',
    'label.date': 'Date',
    'label.court': 'Court',
    'label.docket': 'Docket',
    'label.status': 'Status',
    'label.citations': 'Citations',
    'label.caseAnalysis': 'Case analysis',
    'label.whyItMatters': 'Why it matters:',
    'label.limits': 'Limits:',
    'label.focusedPassages': 'Focused passages',
    'label.citingCases': 'Citing cases',
    'label.latest': 'Latest',
    'label.mostCited': 'Most cited',
    'label.selected': 'Selected',
    'label.open': 'open',
    'label.opinionInventory': 'Opinion inventory',
    'label.noInventory': 'No opinion inventory was available.',
    'label.opinionText': 'Opinion text',
    'label.noText': 'No text',
    'label.pdfAvailable': 'PDF available',
    'label.noPdf': 'No PDF',
    'label.opinionsChecked': '{checked}/{total} opinions checked',
    'label.textPdfCount': '{text} text · {pdf} PDF',
    'label.partialInventory': 'Partial inventory',
    'step.analyze': 'Analyze',
    'step.search': 'Search',
    'step.authorities': 'Authorities',
    'step.answer': 'Reasoning',
    'brief.title': 'Brief Review',
    'brief.refs': '· {count} reference{plural}',
    'brief.none': 'No citations or case names were detected.',
    'brief.extracted': 'Extracted reference',
    'brief.resolved': 'Resolved authority',
    'brief.quoteCheck': 'Quote check',
    'brief.source': 'Source',
    'brief.unresolved': 'Unresolved',
    'brief.quoteFound': 'Found ({match})',
    'brief.quoteNotFound': 'Not found ({match})',
    'brief.quoteNotChecked': 'Quote not checked',
    'brief.noNearbyQuote': 'No nearby quote',
    'extract.title': 'Citation Extractor',
    'extract.reference': 'Reference',
    'extract.context': 'Context',
    'extract.none': 'No references detected.',
    'plan.title': 'Research plan',
    'plan.issues': '· {count} issue{plural}',
    'plan.defaultSummary': 'Search primary-law authorities and organize the answer.',
    'plan.issue': 'Issue',
    'plan.dependsOn': 'Depends on',
    'cases.found': 'Found {count} authorit{suffix} for: {query}',
    'cases.none': 'No authorities for: {query}',
    'rateLimited': "You're sending requests a little too fast. Please wait a moment and try again.",
    'error.generic': 'Something went wrong.',
    'warning.prefix': '⚠ {message}',
    'warning.default': 'Please double-check the citations.',
    'timeout': 'This took too long and was stopped. Please try again — if it keeps happening, try a shorter or more specific question.',
    'connectionError': 'Connection error: {message}',
    'assistant.fallback': '(cases shown)',
    'verify.shortQuote': 'Enter a longer quote to check.',
    'verify.checking': 'Checking the real opinion text…',
    'verify.rateLimited': 'Too many checks too fast — please wait a moment.',
    'verify.found': '✓ Found in the opinion',
    'verify.noText': "⚠ The full opinion text isn't available to check this quote.",
    'verify.partial': '⚠ Not found in the {searched} of {total} opinions we could search — it may appear in another. Treat as unconfirmed and open the full opinion to check.',
    'verify.notFound': "✗ Not found in this opinion's text — treat the quote as unverified.",
    'verify.failed': 'Verification failed — please try again.',
    'export.title': 'JuriCodex — Research Memo',
    'export.generated': 'Generated {stamp} · juricodex.online · Research tool, not legal advice.',
    'export.authorities': 'Table of Authorities',
    'export.disclaimer': 'Verify every authority before relying on it. JuriCodex is a research tool and does not provide legal advice.',
    'cookie.text': 'We use a single essential cookie to keep you signed in. We don\'t use advertising or third-party tracking cookies. See our <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>.',
    'cookie.decline': 'Essential only',
    'cookie.accept': 'Got it',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Terms</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacy</a> · Research tool, not legal advice.',
  },
  es: {
    'topbar.tag': 'Encuentra la ley · razona con ella · verifica cada paso',
    'nav.engineGroup': 'Motor de razonamiento legal',
    'nav.researchEngine': 'Motor de investigación',
    'nav.toolkitGroup': 'Herramientas de investigación legal',
    'nav.concept': 'Buscar por concepto',
    'nav.keyword': 'Buscar por palabra clave',
    'nav.case': 'Buscar por nombre del caso',
    'nav.citation': 'Buscar por cita',
    'nav.laws': 'Buscar leyes y reglas',
    'nav.extractor': 'Extractor de citas',
    'nav.resolver': 'Resolvedor de casos',
    'nav.brief': 'Revisión de escrito',
    'nav.history': 'Historial de investigación',
    'history.empty': 'Aún no hay investigaciones',
    'intro.h1': 'Encuentra la ley. Razona con ella. Verifica cada paso.',
    'intro.lead': 'JuriCodex Platform es un espacio de investigación legal con dos formas de empezar: un Motor de razonamiento legal para trabajar preguntas y un conjunto de Herramientas de investigación legal para buscar, comprobar y verificar derecho primario directamente.',
    'intro.engineKicker': 'Motor de razonamiento legal',
    'intro.engineTitle': 'Cuando el trabajo va más allá de buscar.',
    'intro.engineBody': 'Haz una pregunta en lenguaje claro. JuriCodex aclara cuando los hechos importan, busca autoridades reales, afina la búsqueda cuando hace falta y muestra de qué depende la respuesta.',
    'intro.toolkitKicker': 'Herramientas de investigación legal',
    'intro.toolkitTitle': 'Busca. Cita. Verifica.',
    'intro.toolkitBody': 'Empieza con un concepto, palabra clave, nombre de caso, cita o escrito pegado. Encuentra casos, estatutos, regulaciones, verificación de citas y señales de tratamiento que puedes inspeccionar.',
    'intro.disclaimer': 'JuriCodex razona desde <strong>fuentes primarias reales</strong> que puedes abrir y verificar. Es una <strong>herramienta de investigación</strong>: verifica las autoridades antes de apoyarte en el análisis y consulta a un abogado autorizado para una decisión real.',
    'example.0': '¿Puede mi arrendador quedarse con mi depósito por desgaste normal?',
    'example.1': '¿Qué sostuvo realmente Miranda v. Arizona?',
    'example.2': 'Límites de la Cuarta Enmienda en registros de autos sin orden judicial',
    'placeholder.default': 'Pregunta en lenguaje claro…  (p. ej., despido por reportar violaciones de seguridad)',
    'placeholder.concept': 'Describe el concepto legal o la situación en lenguaje claro…',
    'placeholder.keyword': 'Ingresa palabras clave para buscar jurisprudencia…',
    'placeholder.case': 'Ingresa un caso, p. ej. Miranda v. Arizona',
    'placeholder.citation': 'Ingresa una cita, p. ej. 384 U.S. 436',
    'placeholder.laws': 'Describe un tema de estatuto o regulación federal…',
    'placeholder.extractor': 'Pega texto legal para extraer citas y nombres de casos…',
    'placeholder.resolver': 'Ingresa una cita, nombre de caso, short cite, expediente o referencia imprecisa…',
    'placeholder.brief': 'Pega un escrito, memorando, argumento o texto legal para extraer citas y verificar citas textuales…',
    'login.title': 'Inicia sesión para preguntar',
    'login.sub': 'JuriCodex fundamenta cada respuesta en opiniones judiciales reales. Inicia sesión para investigar y conservar tu trabajo en tus dispositivos.',
    'login.fine': 'Gratis para usar · Nunca publicamos en tu nombre.',
    'login.unavailable': 'El inicio de sesión no está disponible ahora. Inténtalo más tarde.',
    'login.continue': 'Continuar con {provider}',
    'account.hint': 'Inicia sesión para guardar tu investigación en todos tus dispositivos.',
    'account.signIn': 'Iniciar sesión con {provider}',
    'account.signedIn': 'Sesión iniciada',
    'account.signOut': 'Salir',
    'account.signOutTitle': 'Cerrar sesión',
    'account.upgrade': 'Mejorar plan',
    'account.manage': 'Gestionar facturación',
    'account.emailWarn': 'Agrega un correo verificado con tu proveedor de inicio de sesión antes de suscribirte.',
    'account.usage': '{used}/{limit} este mes',
    'account.plan': 'plan {plan}',
    'upgrade.title': 'Elige un plan de workspace',
    'upgrade.sub': 'Investigación con fuentes, verificación, historial y exportación.',
    'upgrade.quota': 'Ya usaste las {limit} preguntas del plan Free este mes.',
    'upgrade.emailNote': 'Agrega y verifica un correo con tu proveedor de inicio de sesión antes de suscribirte. Necesitamos un correo real verificado para asociar la compra a tu cuenta.',
    'upgrade.monthly': 'Mensual',
    'upgrade.yearly': 'Anual',
    'upgrade.save': 'Ahorra 2 meses',
    'upgrade.oneTime': 'Pago único',
    'upgrade.annualPlan': 'Plan anual',
    'upgrade.monthlyPlan': 'Plan mensual',
    'upgrade.note': 'Al continuar, aceptas los <a href="/terms.html" target="_blank" rel="noopener">Términos</a> y reconoces la <a href="/privacy.html" target="_blank" rel="noopener">Política de privacidad</a>.',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 investigaciones con fuentes, verificación, historial y exportación.',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'Workspace de alto volumen para Brief Review, verificación de citas textuales, exportación y sesiones guardadas.',
    'plan.day_pass.name': 'Pase de 3 días',
    'plan.day_pass.pitch': 'Prueba acceso de nivel Max por 3 días. Sin suscripción.',
    'toast.emailRequired': 'Agrega y verifica un correo con tu proveedor de inicio de sesión antes de suscribirte.',
    'toast.checkoutOpening': 'Abriendo checkout seguro…',
    'toast.checkoutFallback': 'No se pudo abrir el checkout emergente. Abriendo checkout alojado…',
    'toast.billingPortal': 'Usa el correo del recibo de Freemius para gestionar la facturación, o contacta soporte.',
    'toast.authFailed': 'El inicio de sesión no se completó. Inténtalo otra vez.',
    'label.question': 'Pregunta',
    'label.authorities': 'Tabla de autoridades',
    'label.statutes': 'Estatutos y regulaciones federales',
    'label.verified': 'Verificado',
    'label.copy': '⧉ Copiar',
    'label.copied': '✓ Copiado',
    'label.export': '↓ Exportar',
    'label.exported': '✓ Exportado',
    'label.copyCitation': 'Copiar cita',
    'label.copiedShort': 'Copiado',
    'label.openOpinion': 'Abrir opinión completa ↗',
    'label.openRegulation': 'Abrir regulación ↗',
    'label.details': 'Detalles / PDFs',
    'label.verifyQuote': '✓ Verificar cita textual',
    'label.verifyQuotePlaceholder': 'Pega una cita atribuida a este caso…',
    'label.check': 'Comprobar',
    'label.loadingDetails': 'Cargando detalles del caso…',
    'label.detailsFailed': 'No se pudieron cargar los detalles ahora.',
    'label.date': 'Fecha',
    'label.court': 'Tribunal',
    'label.docket': 'Expediente',
    'label.status': 'Estado',
    'label.citations': 'Citas',
    'label.caseAnalysis': 'Análisis del caso',
    'label.whyItMatters': 'Por qué importa:',
    'label.limits': 'Límites:',
    'label.focusedPassages': 'Pasajes relevantes',
    'label.citingCases': 'Casos que citan',
    'label.latest': 'Más recientes',
    'label.mostCited': 'Más citados',
    'label.selected': 'Seleccionados',
    'label.open': 'abrir',
    'label.opinionInventory': 'Inventario de opiniones',
    'label.noInventory': 'No hubo inventario de opiniones disponible.',
    'label.opinionText': 'Texto de la opinión',
    'label.noText': 'Sin texto',
    'label.pdfAvailable': 'PDF disponible',
    'label.noPdf': 'Sin PDF',
    'label.opinionsChecked': '{checked}/{total} opiniones revisadas',
    'label.textPdfCount': '{text} texto · {pdf} PDF',
    'label.partialInventory': 'Inventario parcial',
    'step.analyze': 'Analizar',
    'step.search': 'Buscar',
    'step.authorities': 'Autoridades',
    'step.answer': 'Razonamiento',
    'brief.title': 'Revisión de escrito',
    'brief.refs': '· {count} referencia{plural}',
    'brief.none': 'No se detectaron citas ni nombres de casos.',
    'brief.extracted': 'Referencia extraída',
    'brief.resolved': 'Autoridad resuelta',
    'brief.quoteCheck': 'Verificación de cita textual',
    'brief.source': 'Fuente',
    'brief.unresolved': 'Sin resolver',
    'brief.quoteFound': 'Encontrada ({match})',
    'brief.quoteNotFound': 'No encontrada ({match})',
    'brief.quoteNotChecked': 'Cita textual no comprobada',
    'brief.noNearbyQuote': 'Sin cita textual cercana',
    'extract.title': 'Extractor de citas',
    'extract.reference': 'Referencia',
    'extract.context': 'Contexto',
    'extract.none': 'No se detectaron referencias.',
    'plan.title': 'Plan de investigación',
    'plan.issues': '· {count} tema{plural}',
    'plan.defaultSummary': 'Buscar autoridades de derecho primario y organizar la respuesta.',
    'plan.issue': 'Tema',
    'plan.dependsOn': 'Depende de',
    'cases.found': 'Se encontraron {count} autoridad{suffix} para: {query}',
    'cases.none': 'No se encontraron autoridades para: {query}',
    'rateLimited': 'Estás enviando solicitudes demasiado rápido. Espera un momento e inténtalo de nuevo.',
    'error.generic': 'Algo salió mal.',
    'warning.prefix': '⚠ {message}',
    'warning.default': 'Revisa las citas con cuidado.',
    'timeout': 'Esto tardó demasiado y se detuvo. Inténtalo de nuevo; si sigue pasando, usa una pregunta más corta o específica.',
    'connectionError': 'Error de conexión: {message}',
    'assistant.fallback': '(casos mostrados)',
    'verify.shortQuote': 'Ingresa una cita más larga para comprobarla.',
    'verify.checking': 'Comprobando el texto real de la opinión…',
    'verify.rateLimited': 'Demasiadas comprobaciones demasiado rápido; espera un momento.',
    'verify.found': '✓ Encontrada en la opinión',
    'verify.noText': '⚠ El texto completo de la opinión no está disponible para comprobar esta cita.',
    'verify.partial': '⚠ No se encontró en {searched} de {total} opiniones que pudimos revisar; puede aparecer en otra. Trátala como no confirmada y abre la opinión completa para comprobarla.',
    'verify.notFound': '✗ No se encontró en el texto de esta opinión; trata la cita como no verificada.',
    'verify.failed': 'Falló la verificación; inténtalo otra vez.',
    'export.title': 'JuriCodex — Memorando de investigación',
    'export.generated': 'Generado {stamp} · juricodex.online · Herramienta de investigación, no asesoría legal.',
    'export.authorities': 'Tabla de autoridades',
    'export.disclaimer': 'Verifica cada autoridad antes de apoyarte en ella. JuriCodex es una herramienta de investigación y no proporciona asesoría legal.',
    'cookie.text': 'Usamos una sola cookie esencial para mantener tu sesión. No usamos publicidad ni cookies de rastreo de terceros. Consulta nuestra <a href="/privacy.html" target="_blank" rel="noopener">Política de privacidad</a>.',
    'cookie.decline': 'Solo esencial',
    'cookie.accept': 'Entendido',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Términos</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacidad</a> · Herramienta de investigación, no asesoría legal.',
  },
  zh: {
    'topbar.tag': '找到法律 · 推理分析 · 每一步可验证',
    'nav.engineGroup': '法律推理引擎',
    'nav.researchEngine': '调研引擎',
    'nav.toolkitGroup': '法律调研工具箱',
    'nav.concept': '按概念搜索',
    'nav.keyword': '按关键词搜索',
    'nav.case': '按案例名称搜索',
    'nav.citation': '按引用搜索',
    'nav.laws': '法律与规则搜索',
    'nav.extractor': '引用提取器',
    'nav.resolver': '案例解析器',
    'nav.brief': 'Brief Review',
    'nav.history': '调研历史',
    'history.empty': '还没有调研记录',
    'intro.h1': '找到法律。推理分析。每一步可验证。',
    'intro.lead': 'JuriCodex Platform 是一个法律调研工作台：你可以用法律推理引擎处理问题，也可以用法律调研工具箱直接搜索、检查和验证一手法律资料。',
    'intro.engineKicker': '法律推理引擎',
    'intro.engineTitle': '当任务不只是搜索。',
    'intro.engineBody': '用自然语言提问。JuriCodex 会在关键事实不清时追问，检索真实权威资料，必要时优化检索，并说明答案取决于哪些条件。',
    'intro.toolkitKicker': '法律调研工具箱',
    'intro.toolkitTitle': '搜索。引用。验证。',
    'intro.toolkitBody': '从法律概念、关键词、案例名称、引用或粘贴的 brief 开始。查找可溯源的案例、法规、quote 检查和可查看的处理信号。',
    'intro.disclaimer': 'JuriCodex 基于你可以打开和验证的<strong>真实一手资料</strong>进行推理。它是<strong>调研工具</strong>：在依赖分析前请核验权威资料；真实决策请咨询持牌律师。',
    'example.0': '房东可以因为正常磨损扣留我的押金吗？',
    'example.1': 'Miranda v. Arizona 到底确立了什么规则？',
    'example.2': '第四修正案对无令状车辆搜查的限制',
    'placeholder.default': '用自然语言提问…（例如：举报安全违规后被解雇）',
    'placeholder.concept': '用自然语言描述法律概念或情境…',
    'placeholder.keyword': '输入关键词搜索判例法…',
    'placeholder.case': '输入案例名称，例如 Miranda v. Arizona',
    'placeholder.citation': '输入引用，例如 384 U.S. 436',
    'placeholder.laws': '描述联邦成文法或法规主题…',
    'placeholder.extractor': '粘贴法律文本以提取案例引用和案例名称…',
    'placeholder.resolver': '输入引用、案例名、short cite、案号或不完整引用…',
    'placeholder.brief': '粘贴 brief、memo、论证或法律文本，以提取引用并验证 quote…',
    'login.title': '登录后提问',
    'login.sub': 'JuriCodex 的每个回答都基于真实法院意见。登录后可以调研并在不同设备保留工作。',
    'login.fine': '免费使用 · 我们不会代表你发布任何内容。',
    'login.unavailable': '当前无法登录。请稍后再试。',
    'login.continue': '使用 {provider} 继续',
    'account.hint': '登录以跨设备保存你的调研。',
    'account.signIn': '使用 {provider} 登录',
    'account.signedIn': '已登录',
    'account.signOut': '退出',
    'account.signOutTitle': '退出登录',
    'account.upgrade': '升级',
    'account.manage': '管理账单',
    'account.emailWarn': '订阅前请先通过登录提供方添加并验证邮箱。',
    'account.usage': '本月 {used}/{limit}',
    'account.plan': '{plan} 计划',
    'upgrade.title': '选择 Platform 工作台计划',
    'upgrade.sub': '基于来源的调研、验证、历史记录和导出。',
    'upgrade.quota': '你已用完 Free 计划本月 {limit} 次问题额度。',
    'upgrade.emailNote': '订阅前请先通过登录提供方添加并验证邮箱。我们需要真实且已验证的邮箱把购买绑定到你的账户。',
    'upgrade.monthly': '月付',
    'upgrade.yearly': '年付',
    'upgrade.save': '省 2 个月',
    'upgrade.oneTime': '一次性',
    'upgrade.annualPlan': '年付计划',
    'upgrade.monthlyPlan': '月付计划',
    'upgrade.note': '继续即表示你同意 <a href="/terms.html" target="_blank" rel="noopener">Terms</a>，并知悉 <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>。',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 次基于来源的调研，包含验证、历史记录和导出。',
    'plan.max.name': 'Max',
    'plan.max.pitch': '适合高频使用的工作台：Brief Review、quote 检查、导出和保存会话。',
    'plan.day_pass.name': '3 天通行证',
    'plan.day_pass.pitch': '试用 3 天 Max 级访问权限。无需订阅。',
    'toast.emailRequired': '订阅前请先通过登录提供方添加并验证邮箱。',
    'toast.checkoutOpening': '正在打开安全 checkout…',
    'toast.checkoutFallback': '无法加载 checkout 弹窗。正在打开托管 checkout…',
    'toast.billingPortal': '请使用 Freemius 收据邮箱管理账单，或联系支持。',
    'toast.authFailed': '登录未完成。请重试。',
    'label.question': '问题',
    'label.authorities': '权威资料表',
    'label.statutes': '联邦成文法与法规',
    'label.verified': '已验证',
    'label.copy': '⧉ 复制',
    'label.copied': '✓ 已复制',
    'label.export': '↓ 导出',
    'label.exported': '✓ 已导出',
    'label.copyCitation': '复制引用',
    'label.copiedShort': '已复制',
    'label.openOpinion': '打开完整意见 ↗',
    'label.openRegulation': '打开法规 ↗',
    'label.details': '详情 / PDF',
    'label.verifyQuote': '✓ 验证 quote',
    'label.verifyQuotePlaceholder': '粘贴归属于该案例的 quote…',
    'label.check': '检查',
    'label.loadingDetails': '正在加载案例详情…',
    'label.detailsFailed': '暂时无法加载详情。',
    'label.date': '日期',
    'label.court': '法院',
    'label.docket': '案号',
    'label.status': '状态',
    'label.citations': '引用',
    'label.caseAnalysis': '案例分析',
    'label.whyItMatters': '重要性：',
    'label.limits': '限制：',
    'label.focusedPassages': '相关段落',
    'label.citingCases': '引用本案的案例',
    'label.latest': '最新',
    'label.mostCited': '引用最多',
    'label.selected': '精选',
    'label.open': '打开',
    'label.opinionInventory': '意见清单',
    'label.noInventory': '没有可用的意见清单。',
    'label.opinionText': '意见文本',
    'label.noText': '无文本',
    'label.pdfAvailable': '有 PDF',
    'label.noPdf': '无 PDF',
    'label.opinionsChecked': '已检查 {checked}/{total} 份意见',
    'label.textPdfCount': '{text} 份文本 · {pdf} 份 PDF',
    'label.partialInventory': '部分清单',
    'step.analyze': '分析',
    'step.search': '搜索',
    'step.authorities': '权威资料',
    'step.answer': '推理',
    'brief.title': 'Brief Review',
    'brief.refs': '· {count} 条引用',
    'brief.none': '没有检测到引用或案例名称。',
    'brief.extracted': '提取的引用',
    'brief.resolved': '解析出的权威资料',
    'brief.quoteCheck': 'Quote 检查',
    'brief.source': '来源',
    'brief.unresolved': '未解析',
    'brief.quoteFound': '找到（{match}）',
    'brief.quoteNotFound': '未找到（{match}）',
    'brief.quoteNotChecked': '未检查 quote',
    'brief.noNearbyQuote': '附近没有 quote',
    'extract.title': '引用提取器',
    'extract.reference': '引用',
    'extract.context': '上下文',
    'extract.none': '未检测到引用。',
    'plan.title': '调研计划',
    'plan.issues': '· {count} 个议题',
    'plan.defaultSummary': '搜索一手法律权威资料并组织答案。',
    'plan.issue': '议题',
    'plan.dependsOn': '取决于',
    'cases.found': '找到 {count} 条权威资料：{query}',
    'cases.none': '没有找到权威资料：{query}',
    'rateLimited': '你的请求发送得有点太快。请稍等片刻再试。',
    'error.generic': '出错了。',
    'warning.prefix': '⚠ {message}',
    'warning.default': '请仔细核对引用。',
    'timeout': '这次请求耗时太久，已停止。请重试；如果持续发生，请尝试更短或更具体的问题。',
    'connectionError': '连接错误：{message}',
    'assistant.fallback': '（已显示案例）',
    'verify.shortQuote': '请输入更长的 quote 再检查。',
    'verify.checking': '正在检查真实意见文本…',
    'verify.rateLimited': '检查过于频繁，请稍等片刻。',
    'verify.found': '✓ 在意见中找到',
    'verify.noText': '⚠ 无法取得完整意见文本，不能检查此 quote。',
    'verify.partial': '⚠ 在可搜索的 {searched}/{total} 份意见中未找到；它可能出现在其他意见中。请视为未确认，并打开完整意见核验。',
    'verify.notFound': '✗ 未在该意见文本中找到；请将此 quote 视为未验证。',
    'verify.failed': '验证失败，请重试。',
    'export.title': 'JuriCodex — 调研备忘录',
    'export.generated': '生成于 {stamp} · juricodex.online · 调研工具，不构成法律意见。',
    'export.authorities': '权威资料表',
    'export.disclaimer': '在依赖前请核验每一项权威资料。JuriCodex 是调研工具，不提供法律意见。',
    'cookie.text': '我们只使用一个必要 cookie 来保持登录状态。我们不使用广告或第三方跟踪 cookie。请查看我们的 <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>。',
    'cookie.decline': '仅必要',
    'cookie.accept': '知道了',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Terms</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacy</a> · 调研工具，不构成法律意见。',
  },
  'zh-TW': {
    'topbar.tag': '找到法律 · 推理分析 · 每一步可驗證',
    'nav.engineGroup': '法律推理引擎',
    'nav.researchEngine': '調研引擎',
    'nav.toolkitGroup': '法律調研工具箱',
    'nav.concept': '按概念搜尋',
    'nav.keyword': '按關鍵字搜尋',
    'nav.case': '按案例名稱搜尋',
    'nav.citation': '按引用搜尋',
    'nav.laws': '法律與規則搜尋',
    'nav.extractor': '引用擷取器',
    'nav.resolver': '案例解析器',
    'nav.brief': 'Brief Review',
    'nav.history': '調研歷史',
    'history.empty': '還沒有調研紀錄',
    'intro.h1': '找到法律。推理分析。每一步可驗證。',
    'intro.lead': 'JuriCodex Platform 是一個法律調研工作台：你可以用法律推理引擎處理問題，也可以用法律調研工具箱直接搜尋、檢查和驗證一手法律資料。',
    'intro.engineKicker': '法律推理引擎',
    'intro.engineTitle': '當任務不只是搜尋。',
    'intro.engineBody': '用自然語言提問。JuriCodex 會在關鍵事實不清時追問，檢索真實權威資料，必要時最佳化檢索，並說明答案取決於哪些條件。',
    'intro.toolkitKicker': '法律調研工具箱',
    'intro.toolkitTitle': '搜尋。引用。驗證。',
    'intro.toolkitBody': '從法律概念、關鍵字、案例名稱、引用或貼上的 brief 開始。查找可溯源的案例、法規、quote 檢查和可查看的處理訊號。',
    'intro.disclaimer': 'JuriCodex 基於你可以打開和驗證的<strong>真實一手資料</strong>進行推理。它是<strong>調研工具</strong>：在依賴分析前請核驗權威資料；真實決策請諮詢持牌律師。',
    'example.0': '房東可以因為正常磨損扣留我的押金嗎？',
    'example.1': 'Miranda v. Arizona 到底確立了什麼規則？',
    'example.2': '第四修正案對無令狀車輛搜查的限制',
    'placeholder.default': '用自然語言提問…（例如：舉報安全違規後被解雇）',
    'placeholder.concept': '用自然語言描述法律概念或情境…',
    'placeholder.keyword': '輸入關鍵字搜尋判例法…',
    'placeholder.case': '輸入案例名稱，例如 Miranda v. Arizona',
    'placeholder.citation': '輸入引用，例如 384 U.S. 436',
    'placeholder.laws': '描述聯邦成文法或法規主題…',
    'placeholder.extractor': '貼上法律文本以擷取案例引用和案例名稱…',
    'placeholder.resolver': '輸入引用、案例名、short cite、案號或不完整引用…',
    'placeholder.brief': '貼上 brief、memo、論證或法律文本，以擷取引用並驗證 quote…',
    'login.title': '登入後提問',
    'login.sub': 'JuriCodex 的每個回答都基於真實法院意見。登入後可以調研並在不同裝置保留工作。',
    'login.fine': '免費使用 · 我們不會代表你發布任何內容。',
    'login.unavailable': '目前無法登入。請稍後再試。',
    'login.continue': '使用 {provider} 繼續',
    'account.hint': '登入以跨裝置保存你的調研。',
    'account.signIn': '使用 {provider} 登入',
    'account.signedIn': '已登入',
    'account.signOut': '登出',
    'account.signOutTitle': '登出',
    'account.upgrade': '升級',
    'account.manage': '管理帳單',
    'account.emailWarn': '訂閱前請先透過登入提供方新增並驗證電子郵件。',
    'account.usage': '本月 {used}/{limit}',
    'account.plan': '{plan} 方案',
    'upgrade.title': '選擇 Platform 工作台方案',
    'upgrade.sub': '基於來源的調研、驗證、歷史紀錄和匯出。',
    'upgrade.quota': '你已用完 Free 方案本月 {limit} 次問題額度。',
    'upgrade.emailNote': '訂閱前請先透過登入提供方新增並驗證電子郵件。我們需要真實且已驗證的電子郵件把購買綁定到你的帳戶。',
    'upgrade.monthly': '月付',
    'upgrade.yearly': '年付',
    'upgrade.save': '省 2 個月',
    'upgrade.oneTime': '一次性',
    'upgrade.annualPlan': '年付方案',
    'upgrade.monthlyPlan': '月付方案',
    'upgrade.note': '繼續即表示你同意 <a href="/terms.html" target="_blank" rel="noopener">Terms</a>，並知悉 <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>。',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 次基於來源的調研，包含驗證、歷史紀錄和匯出。',
    'plan.max.name': 'Max',
    'plan.max.pitch': '適合高頻使用的工作台：Brief Review、quote 檢查、匯出和保存會話。',
    'plan.day_pass.name': '3 天通行證',
    'plan.day_pass.pitch': '試用 3 天 Max 級存取權限。無需訂閱。',
    'toast.emailRequired': '訂閱前請先透過登入提供方新增並驗證電子郵件。',
    'toast.checkoutOpening': '正在開啟安全 checkout…',
    'toast.checkoutFallback': '無法載入 checkout 彈窗。正在開啟託管 checkout…',
    'toast.billingPortal': '請使用 Freemius 收據電子郵件管理帳單，或聯絡支援。',
    'toast.authFailed': '登入未完成。請重試。',
    'label.question': '問題',
    'label.authorities': '權威資料表',
    'label.statutes': '聯邦成文法與法規',
    'label.verified': '已驗證',
    'label.copy': '⧉ 複製',
    'label.copied': '✓ 已複製',
    'label.export': '↓ 匯出',
    'label.exported': '✓ 已匯出',
    'label.copyCitation': '複製引用',
    'label.copiedShort': '已複製',
    'label.openOpinion': '開啟完整意見 ↗',
    'label.openRegulation': '開啟法規 ↗',
    'label.details': '詳情 / PDF',
    'label.verifyQuote': '✓ 驗證 quote',
    'label.verifyQuotePlaceholder': '貼上歸屬於該案例的 quote…',
    'label.check': '檢查',
    'label.loadingDetails': '正在載入案例詳情…',
    'label.detailsFailed': '暫時無法載入詳情。',
    'label.date': '日期',
    'label.court': '法院',
    'label.docket': '案號',
    'label.status': '狀態',
    'label.citations': '引用',
    'label.caseAnalysis': '案例分析',
    'label.whyItMatters': '重要性：',
    'label.limits': '限制：',
    'label.focusedPassages': '相關段落',
    'label.citingCases': '引用本案的案例',
    'label.latest': '最新',
    'label.mostCited': '引用最多',
    'label.selected': '精選',
    'label.open': '開啟',
    'label.opinionInventory': '意見清單',
    'label.noInventory': '沒有可用的意見清單。',
    'label.opinionText': '意見文本',
    'label.noText': '無文本',
    'label.pdfAvailable': '有 PDF',
    'label.noPdf': '無 PDF',
    'label.opinionsChecked': '已檢查 {checked}/{total} 份意見',
    'label.textPdfCount': '{text} 份文本 · {pdf} 份 PDF',
    'label.partialInventory': '部分清單',
    'step.analyze': '分析',
    'step.search': '搜尋',
    'step.authorities': '權威資料',
    'step.answer': '推理',
    'brief.title': 'Brief Review',
    'brief.refs': '· {count} 條引用',
    'brief.none': '沒有偵測到引用或案例名稱。',
    'brief.extracted': '擷取的引用',
    'brief.resolved': '解析出的權威資料',
    'brief.quoteCheck': 'Quote 檢查',
    'brief.source': '來源',
    'brief.unresolved': '未解析',
    'brief.quoteFound': '找到（{match}）',
    'brief.quoteNotFound': '未找到（{match}）',
    'brief.quoteNotChecked': '未檢查 quote',
    'brief.noNearbyQuote': '附近沒有 quote',
    'extract.title': '引用擷取器',
    'extract.reference': '引用',
    'extract.context': '上下文',
    'extract.none': '未偵測到引用。',
    'plan.title': '調研計畫',
    'plan.issues': '· {count} 個議題',
    'plan.defaultSummary': '搜尋一手法律權威資料並組織答案。',
    'plan.issue': '議題',
    'plan.dependsOn': '取決於',
    'cases.found': '找到 {count} 條權威資料：{query}',
    'cases.none': '沒有找到權威資料：{query}',
    'rateLimited': '你的請求送出得有點太快。請稍等片刻再試。',
    'error.generic': '出錯了。',
    'warning.prefix': '⚠ {message}',
    'warning.default': '請仔細核對引用。',
    'timeout': '這次請求耗時太久，已停止。請重試；如果持續發生，請嘗試更短或更具體的問題。',
    'connectionError': '連線錯誤：{message}',
    'assistant.fallback': '（已顯示案例）',
    'verify.shortQuote': '請輸入更長的 quote 再檢查。',
    'verify.checking': '正在檢查真實意見文本…',
    'verify.rateLimited': '檢查過於頻繁，請稍等片刻。',
    'verify.found': '✓ 在意見中找到',
    'verify.noText': '⚠ 無法取得完整意見文本，不能檢查此 quote。',
    'verify.partial': '⚠ 在可搜尋的 {searched}/{total} 份意見中未找到；它可能出現在其他意見中。請視為未確認，並開啟完整意見核驗。',
    'verify.notFound': '✗ 未在該意見文本中找到；請將此 quote 視為未驗證。',
    'verify.failed': '驗證失敗，請重試。',
    'export.title': 'JuriCodex — 調研備忘錄',
    'export.generated': '生成於 {stamp} · juricodex.online · 調研工具，不構成法律意見。',
    'export.authorities': '權威資料表',
    'export.disclaimer': '在依賴前請核驗每一項權威資料。JuriCodex 是調研工具，不提供法律意見。',
    'cookie.text': '我們只使用一個必要 cookie 來保持登入狀態。我們不使用廣告或第三方追蹤 cookie。請查看我們的 <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>。',
    'cookie.decline': '僅必要',
    'cookie.accept': '知道了',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Terms</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacy</a> · 調研工具，不構成法律意見。',
  },
  fr: {
    'topbar.tag': 'Trouver le droit · raisonner · vérifier chaque étape',
    'nav.engineGroup': 'Moteur de raisonnement juridique',
    'nav.researchEngine': 'Moteur de recherche',
    'nav.toolkitGroup': 'Boîte à outils de recherche juridique',
    'nav.concept': 'Recherche par concept',
    'nav.keyword': 'Recherche par mot-clé',
    'nav.case': 'Recherche par nom de dossier',
    'nav.citation': 'Recherche par citation',
    'nav.laws': 'Recherche lois et règles',
    'nav.extractor': 'Extracteur de citations',
    'nav.resolver': 'Résolution de dossiers',
    'nav.brief': 'Revue de mémoire',
    'nav.history': 'Historique de recherche',
    'history.empty': 'Aucune recherche pour le moment',
    'intro.h1': 'Trouver le droit. Raisonner. Vérifier chaque étape.',
    'intro.lead': 'JuriCodex Platform est un espace de recherche juridique avec deux entrées : un moteur de raisonnement juridique pour travailler les questions, et une boîte à outils pour rechercher, contrôler et vérifier directement le droit primaire.',
    'intro.engineKicker': 'Moteur de raisonnement juridique',
    'intro.engineTitle': 'Quand le travail va au-delà de la recherche.',
    'intro.engineBody': 'Posez une question en langage clair. JuriCodex clarifie les faits importants, recherche des autorités réelles, affine la requête si nécessaire et montre de quoi dépend la réponse.',
    'intro.toolkitKicker': 'Boîte à outils de recherche juridique',
    'intro.toolkitTitle': 'Rechercher. Citer. Vérifier.',
    'intro.toolkitBody': 'Commencez par un concept, un mot-clé, un nom de dossier, une citation ou un mémoire collé. Trouvez des dossiers, lois, règlements, contrôles de citations et signaux de traitement appuyés par des sources.',
    'intro.disclaimer': 'JuriCodex raisonne à partir de <strong>sources primaires réelles</strong> que vous pouvez ouvrir et vérifier. C’est un <strong>outil de recherche</strong> : vérifiez les autorités avant de vous appuyer sur l’analyse et consultez un avocat autorisé pour une décision réelle.',
    'example.0': 'Mon propriétaire peut-il garder mon dépôt de garantie pour une usure normale ?',
    'example.1': 'Que décide réellement Miranda v. Arizona ?',
    'example.2': 'Limites du Quatrième Amendement sur les fouilles de voitures sans mandat',
    'placeholder.default': 'Posez une question en langage clair…  (p. ex. licenciement après signalement de violations de sécurité)',
    'placeholder.concept': 'Décrivez le concept juridique ou la situation en langage clair…',
    'placeholder.keyword': 'Saisissez des mots-clés pour rechercher la jurisprudence…',
    'placeholder.case': 'Saisissez un nom de dossier, p. ex. Miranda v. Arizona',
    'placeholder.citation': 'Saisissez une citation, p. ex. 384 U.S. 436',
    'placeholder.laws': 'Décrivez un sujet de loi ou règlement fédéral…',
    'placeholder.extractor': 'Collez un texte juridique pour extraire citations et noms de dossiers…',
    'placeholder.resolver': 'Saisissez une citation, un nom, une short cite, un docket ou une référence imparfaite…',
    'placeholder.brief': 'Collez un mémoire, mémo, argument ou texte juridique pour extraire les citations et vérifier les passages cités…',
    'login.title': 'Connectez-vous pour poser une question',
    'login.sub': 'JuriCodex fonde chaque réponse sur de vraies opinions judiciaires. Connectez-vous pour chercher et conserver votre travail sur vos appareils.',
    'login.fine': 'Utilisation gratuite · Nous ne publions jamais en votre nom.',
    'login.unavailable': 'La connexion est indisponible pour le moment. Réessayez plus tard.',
    'login.continue': 'Continuer avec {provider}',
    'account.hint': 'Connectez-vous pour sauvegarder vos recherches sur tous vos appareils.',
    'account.signIn': 'Se connecter avec {provider}',
    'account.signedIn': 'Connecté',
    'account.signOut': 'Déconnexion',
    'account.signOutTitle': 'Déconnexion',
    'account.upgrade': 'Mettre à niveau',
    'account.manage': 'Gérer la facturation',
    'account.emailWarn': 'Ajoutez une adresse e-mail vérifiée avec votre fournisseur de connexion avant de vous abonner.',
    'account.usage': '{used}/{limit} ce mois-ci',
    'account.plan': 'forfait {plan}',
    'upgrade.title': 'Choisir un forfait Platform',
    'upgrade.sub': 'Recherche sourcée, vérification, historique et export.',
    'upgrade.quota': 'Vous avez utilisé les {limit} questions du forfait Free ce mois-ci.',
    'upgrade.emailNote': 'Ajoutez et vérifiez une adresse e-mail avec votre fournisseur de connexion avant de vous abonner. Nous avons besoin d’une vraie adresse vérifiée pour rattacher l’achat à votre compte.',
    'upgrade.monthly': 'Mensuel',
    'upgrade.yearly': 'Annuel',
    'upgrade.save': 'Économisez 2 mois',
    'upgrade.oneTime': 'Paiement unique',
    'upgrade.annualPlan': 'Forfait annuel',
    'upgrade.monthlyPlan': 'Forfait mensuel',
    'upgrade.note': 'En continuant, vous acceptez les <a href="/terms.html" target="_blank" rel="noopener">Conditions</a> et reconnaissez la <a href="/privacy.html" target="_blank" rel="noopener">Politique de confidentialité</a>.',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 recherches sourcées, vérification, historique et export.',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'Espace de travail intensif pour Brief Review, contrôle de citations, export et sessions sauvegardées.',
    'plan.day_pass.name': 'Pass 3 jours',
    'plan.day_pass.pitch': 'Essayez un accès niveau Max pendant 3 jours. Pas d’abonnement.',
    'toast.emailRequired': 'Ajoutez et vérifiez une adresse e-mail avec votre fournisseur de connexion avant de vous abonner.',
    'toast.checkoutOpening': 'Ouverture du paiement sécurisé…',
    'toast.checkoutFallback': 'La fenêtre de paiement n’a pas pu se charger. Ouverture du paiement hébergé…',
    'toast.billingPortal': 'Utilisez l’e-mail de reçu Freemius pour gérer la facturation, ou contactez le support.',
    'toast.authFailed': 'La connexion n’a pas abouti. Réessayez.',
    'label.question': 'Question',
    'label.authorities': 'Table des autorités',
    'label.statutes': 'Lois et règlements fédéraux',
    'label.verified': 'Vérifié',
    'label.copy': '⧉ Copier',
    'label.copied': '✓ Copié',
    'label.export': '↓ Exporter',
    'label.exported': '✓ Exporté',
    'label.copyCitation': 'Copier la citation',
    'label.copiedShort': 'Copié',
    'label.openOpinion': 'Ouvrir l’opinion complète ↗',
    'label.openRegulation': 'Ouvrir le règlement ↗',
    'label.details': 'Détails / PDFs',
    'label.verifyQuote': '✓ Vérifier une citation',
    'label.verifyQuotePlaceholder': 'Collez une citation attribuée à ce dossier…',
    'label.check': 'Vérifier',
    'label.loadingDetails': 'Chargement des détails du dossier…',
    'label.detailsFailed': 'Impossible de charger les détails pour le moment.',
    'label.date': 'Date',
    'label.court': 'Tribunal',
    'label.docket': 'Docket',
    'label.status': 'Statut',
    'label.citations': 'Citations',
    'label.caseAnalysis': 'Analyse du dossier',
    'label.whyItMatters': 'Pourquoi c’est important :',
    'label.limits': 'Limites :',
    'label.focusedPassages': 'Passages ciblés',
    'label.citingCases': 'Dossiers citant cette décision',
    'label.latest': 'Récents',
    'label.mostCited': 'Les plus cités',
    'label.selected': 'Sélection',
    'label.open': 'ouvrir',
    'label.opinionInventory': 'Inventaire des opinions',
    'label.noInventory': 'Aucun inventaire d’opinions disponible.',
    'label.opinionText': 'Texte de l’opinion',
    'label.noText': 'Pas de texte',
    'label.pdfAvailable': 'PDF disponible',
    'label.noPdf': 'Pas de PDF',
    'label.opinionsChecked': '{checked}/{total} opinions vérifiées',
    'label.textPdfCount': '{text} texte · {pdf} PDF',
    'label.partialInventory': 'Inventaire partiel',
    'step.analyze': 'Analyser',
    'step.search': 'Rechercher',
    'step.authorities': 'Autorités',
    'step.answer': 'Raisonnement',
    'brief.title': 'Revue de mémoire',
    'brief.refs': '· {count} référence{plural}',
    'brief.none': 'Aucune citation ni nom de dossier détecté.',
    'brief.extracted': 'Référence extraite',
    'brief.resolved': 'Autorité résolue',
    'brief.quoteCheck': 'Contrôle de citation',
    'brief.source': 'Source',
    'brief.unresolved': 'Non résolu',
    'brief.quoteFound': 'Trouvée ({match})',
    'brief.quoteNotFound': 'Non trouvée ({match})',
    'brief.quoteNotChecked': 'Citation non vérifiée',
    'brief.noNearbyQuote': 'Pas de citation proche',
    'extract.title': 'Extracteur de citations',
    'extract.reference': 'Référence',
    'extract.context': 'Contexte',
    'extract.none': 'Aucune référence détectée.',
    'plan.title': 'Plan de recherche',
    'plan.issues': '· {count} question{plural}',
    'plan.defaultSummary': 'Rechercher des autorités de droit primaire et organiser la réponse.',
    'plan.issue': 'Question',
    'plan.dependsOn': 'Dépend de',
    'cases.found': 'Autorités trouvées ({count}) pour : {query}',
    'cases.none': 'Aucune autorité pour : {query}',
    'rateLimited': 'Vous envoyez des requêtes un peu trop vite. Attendez un instant puis réessayez.',
    'error.generic': 'Une erreur est survenue.',
    'warning.prefix': '⚠ {message}',
    'warning.default': 'Vérifiez soigneusement les citations.',
    'timeout': 'Cette opération a pris trop longtemps et a été arrêtée. Réessayez avec une question plus courte ou plus précise.',
    'connectionError': 'Erreur de connexion : {message}',
    'assistant.fallback': '(dossiers affichés)',
    'verify.shortQuote': 'Saisissez une citation plus longue pour la vérifier.',
    'verify.checking': 'Vérification du vrai texte de l’opinion…',
    'verify.rateLimited': 'Trop de vérifications trop vite ; attendez un instant.',
    'verify.found': '✓ Trouvée dans l’opinion',
    'verify.noText': '⚠ Le texte complet de l’opinion n’est pas disponible pour vérifier cette citation.',
    'verify.partial': '⚠ Non trouvée dans {searched} des {total} opinions vérifiables ; elle peut apparaître ailleurs. Traitez-la comme non confirmée et ouvrez l’opinion complète.',
    'verify.notFound': '✗ Non trouvée dans le texte de cette opinion ; traitez la citation comme non vérifiée.',
    'verify.failed': 'La vérification a échoué ; réessayez.',
    'export.title': 'JuriCodex — Mémo de recherche',
    'export.generated': 'Généré le {stamp} · juricodex.online · Outil de recherche, pas un avis juridique.',
    'export.authorities': 'Table des autorités',
    'export.disclaimer': 'Vérifiez chaque autorité avant de vous y appuyer. JuriCodex est un outil de recherche et ne fournit pas d’avis juridique.',
    'cookie.text': 'Nous utilisons un seul cookie essentiel pour maintenir votre connexion. Nous n’utilisons pas de cookies publicitaires ni de suivi tiers. Voir notre <a href="/privacy.html" target="_blank" rel="noopener">Politique de confidentialité</a>.',
    'cookie.decline': 'Essentiel seulement',
    'cookie.accept': 'Compris',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Conditions</a> · <a href="/privacy.html" target="_blank" rel="noopener">Confidentialité</a> · Outil de recherche, pas un avis juridique.',
  },
  pt: {
    'topbar.tag': 'Encontre a lei · raciocine · verifique cada passo',
    'nav.engineGroup': 'Motor de raciocínio jurídico',
    'nav.researchEngine': 'Motor de pesquisa',
    'nav.toolkitGroup': 'Kit de pesquisa jurídica',
    'nav.concept': 'Pesquisar por conceito',
    'nav.keyword': 'Pesquisar por palavra-chave',
    'nav.case': 'Pesquisar por nome do caso',
    'nav.citation': 'Pesquisar por citação',
    'nav.laws': 'Pesquisar leis e regras',
    'nav.extractor': 'Extrator de citações',
    'nav.resolver': 'Resolvedor de casos',
    'nav.brief': 'Revisão de peça',
    'nav.history': 'Histórico de pesquisa',
    'history.empty': 'Nenhuma pesquisa ainda',
    'intro.h1': 'Encontre a lei. Raciocine. Verifique cada passo.',
    'intro.lead': 'JuriCodex Platform é um espaço de pesquisa jurídica com duas formas de começar: um Motor de raciocínio jurídico para trabalhar perguntas e um Kit de pesquisa jurídica para buscar, conferir e verificar direito primário diretamente.',
    'intro.engineKicker': 'Motor de raciocínio jurídico',
    'intro.engineTitle': 'Quando o trabalho vai além da busca.',
    'intro.engineBody': 'Faça uma pergunta em linguagem clara. JuriCodex esclarece fatos importantes, busca autoridades reais, refina a consulta quando necessário e mostra de que a resposta depende.',
    'intro.toolkitKicker': 'Kit de pesquisa jurídica',
    'intro.toolkitTitle': 'Pesquise. Cite. Verifique.',
    'intro.toolkitBody': 'Comece com um conceito, palavra-chave, nome de caso, citação ou peça colada. Encontre casos, estatutos, regulamentos, verificações de citações e sinais de tratamento com fontes.',
    'intro.disclaimer': 'JuriCodex raciocina a partir de <strong>fontes primárias reais</strong> que você pode abrir e verificar. É uma <strong>ferramenta de pesquisa</strong>: verifique as autoridades antes de confiar na análise e consulte um advogado licenciado para uma decisão real.',
    'example.0': 'Meu locador pode ficar com meu depósito por desgaste normal?',
    'example.1': 'O que Miranda v. Arizona realmente decidiu?',
    'example.2': 'Limites da Quarta Emenda em buscas de carros sem mandado',
    'placeholder.default': 'Pergunte em linguagem clara…  (ex.: demissão após denunciar violações de segurança)',
    'placeholder.concept': 'Descreva o conceito jurídico ou a situação em linguagem clara…',
    'placeholder.keyword': 'Digite palavras-chave para pesquisar jurisprudência…',
    'placeholder.case': 'Digite um nome de caso, ex. Miranda v. Arizona',
    'placeholder.citation': 'Digite uma citação, ex. 384 U.S. 436',
    'placeholder.laws': 'Descreva um tema de estatuto ou regulamento federal…',
    'placeholder.extractor': 'Cole texto jurídico para extrair citações e nomes de casos…',
    'placeholder.resolver': 'Digite uma citação, nome de caso, short cite, docket ou referência imprecisa…',
    'placeholder.brief': 'Cole uma peça, memorando, argumento ou texto jurídico para extrair citações e verificar trechos citados…',
    'login.title': 'Entre para perguntar',
    'login.sub': 'JuriCodex fundamenta cada resposta em opiniões judiciais reais. Entre para pesquisar e manter seu trabalho em todos os dispositivos.',
    'login.fine': 'Uso gratuito · Nunca publicamos em seu nome.',
    'login.unavailable': 'O login não está disponível agora. Tente novamente mais tarde.',
    'login.continue': 'Continuar com {provider}',
    'account.hint': 'Entre para salvar sua pesquisa em todos os dispositivos.',
    'account.signIn': 'Entrar com {provider}',
    'account.signedIn': 'Conectado',
    'account.signOut': 'Sair',
    'account.signOutTitle': 'Sair',
    'account.upgrade': 'Fazer upgrade',
    'account.manage': 'Gerenciar cobrança',
    'account.emailWarn': 'Adicione um e-mail verificado com seu provedor de login antes de assinar.',
    'account.usage': '{used}/{limit} este mês',
    'account.plan': 'plano {plan}',
    'upgrade.title': 'Escolha um plano de workspace',
    'upgrade.sub': 'Pesquisa com fontes, verificação, histórico e exportação.',
    'upgrade.quota': 'Você usou todas as {limit} perguntas do plano Free este mês.',
    'upgrade.emailNote': 'Adicione e verifique um e-mail com seu provedor de login antes de assinar. Precisamos de um e-mail real verificado para vincular a compra à sua conta.',
    'upgrade.monthly': 'Mensal',
    'upgrade.yearly': 'Anual',
    'upgrade.save': 'Economize 2 meses',
    'upgrade.oneTime': 'Pagamento único',
    'upgrade.annualPlan': 'Plano anual',
    'upgrade.monthlyPlan': 'Plano mensal',
    'upgrade.note': 'Ao continuar, você concorda com os <a href="/terms.html" target="_blank" rel="noopener">Termos</a> e reconhece a <a href="/privacy.html" target="_blank" rel="noopener">Política de Privacidade</a>.',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 pesquisas com fontes, verificação, histórico e exportação.',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'Workspace de alto volume para Brief Review, checagem de citações, exportação e sessões salvas.',
    'plan.day_pass.name': 'Passe de 3 dias',
    'plan.day_pass.pitch': 'Teste acesso nível Max por 3 dias. Sem assinatura.',
    'toast.emailRequired': 'Adicione e verifique um e-mail com seu provedor de login antes de assinar.',
    'toast.checkoutOpening': 'Abrindo checkout seguro…',
    'toast.checkoutFallback': 'O popup de checkout não carregou. Abrindo checkout hospedado…',
    'toast.billingPortal': 'Use o e-mail do recibo Freemius para gerenciar a cobrança ou contate o suporte.',
    'toast.authFailed': 'O login não foi concluído. Tente novamente.',
    'label.question': 'Pergunta',
    'label.authorities': 'Tabela de autoridades',
    'label.statutes': 'Estatutos e regulamentos federais',
    'label.verified': 'Verificado',
    'label.copy': '⧉ Copiar',
    'label.copied': '✓ Copiado',
    'label.export': '↓ Exportar',
    'label.exported': '✓ Exportado',
    'label.copyCitation': 'Copiar citação',
    'label.copiedShort': 'Copiado',
    'label.openOpinion': 'Abrir opinião completa ↗',
    'label.openRegulation': 'Abrir regulamento ↗',
    'label.details': 'Detalhes / PDFs',
    'label.verifyQuote': '✓ Verificar uma citação',
    'label.verifyQuotePlaceholder': 'Cole uma citação atribuída a este caso…',
    'label.check': 'Checar',
    'label.loadingDetails': 'Carregando detalhes do caso…',
    'label.detailsFailed': 'Não foi possível carregar os detalhes agora.',
    'label.date': 'Data',
    'label.court': 'Tribunal',
    'label.docket': 'Docket',
    'label.status': 'Status',
    'label.citations': 'Citações',
    'label.caseAnalysis': 'Análise do caso',
    'label.whyItMatters': 'Por que importa:',
    'label.limits': 'Limites:',
    'label.focusedPassages': 'Trechos focados',
    'label.citingCases': 'Casos que citam',
    'label.latest': 'Mais recentes',
    'label.mostCited': 'Mais citados',
    'label.selected': 'Selecionados',
    'label.open': 'abrir',
    'label.opinionInventory': 'Inventário de opiniões',
    'label.noInventory': 'Nenhum inventário de opiniões disponível.',
    'label.opinionText': 'Texto da opinião',
    'label.noText': 'Sem texto',
    'label.pdfAvailable': 'PDF disponível',
    'label.noPdf': 'Sem PDF',
    'label.opinionsChecked': '{checked}/{total} opiniões verificadas',
    'label.textPdfCount': '{text} texto · {pdf} PDF',
    'label.partialInventory': 'Inventário parcial',
    'step.analyze': 'Analisar',
    'step.search': 'Pesquisar',
    'step.authorities': 'Autoridades',
    'step.answer': 'Raciocínio',
    'brief.title': 'Revisão de peça',
    'brief.refs': '· {count} referência{plural}',
    'brief.none': 'Nenhuma citação ou nome de caso detectado.',
    'brief.extracted': 'Referência extraída',
    'brief.resolved': 'Autoridade resolvida',
    'brief.quoteCheck': 'Checagem de citação',
    'brief.source': 'Fonte',
    'brief.unresolved': 'Não resolvido',
    'brief.quoteFound': 'Encontrada ({match})',
    'brief.quoteNotFound': 'Não encontrada ({match})',
    'brief.quoteNotChecked': 'Citação não checada',
    'brief.noNearbyQuote': 'Sem citação próxima',
    'extract.title': 'Extrator de citações',
    'extract.reference': 'Referência',
    'extract.context': 'Contexto',
    'extract.none': 'Nenhuma referência detectada.',
    'plan.title': 'Plano de pesquisa',
    'plan.issues': '· {count} questão{plural}',
    'plan.defaultSummary': 'Pesquisar autoridades de direito primário e organizar a resposta.',
    'plan.issue': 'Questão',
    'plan.dependsOn': 'Depende de',
    'cases.found': 'Encontradas {count} autoridade(s) para: {query}',
    'cases.none': 'Nenhuma autoridade para: {query}',
    'rateLimited': 'Você está enviando solicitações rápido demais. Aguarde um momento e tente novamente.',
    'error.generic': 'Algo deu errado.',
    'warning.prefix': '⚠ {message}',
    'warning.default': 'Confira cuidadosamente as citações.',
    'timeout': 'Isso demorou demais e foi interrompido. Tente novamente com uma pergunta mais curta ou específica.',
    'connectionError': 'Erro de conexão: {message}',
    'assistant.fallback': '(casos exibidos)',
    'verify.shortQuote': 'Digite uma citação mais longa para verificar.',
    'verify.checking': 'Verificando o texto real da opinião…',
    'verify.rateLimited': 'Muitas verificações rápidas demais; aguarde um momento.',
    'verify.found': '✓ Encontrada na opinião',
    'verify.noText': '⚠ O texto completo da opinião não está disponível para verificar esta citação.',
    'verify.partial': '⚠ Não encontrada em {searched} de {total} opiniões que conseguimos pesquisar; pode aparecer em outra. Trate como não confirmada e abra a opinião completa.',
    'verify.notFound': '✗ Não encontrada no texto desta opinião; trate a citação como não verificada.',
    'verify.failed': 'Falha na verificação; tente novamente.',
    'export.title': 'JuriCodex — Memorando de pesquisa',
    'export.generated': 'Gerado em {stamp} · juricodex.online · Ferramenta de pesquisa, não aconselhamento jurídico.',
    'export.authorities': 'Tabela de autoridades',
    'export.disclaimer': 'Verifique cada autoridade antes de confiar nela. JuriCodex é uma ferramenta de pesquisa e não fornece aconselhamento jurídico.',
    'cookie.text': 'Usamos um único cookie essencial para manter você conectado. Não usamos publicidade nem cookies de rastreamento de terceiros. Veja nossa <a href="/privacy.html" target="_blank" rel="noopener">Política de Privacidade</a>.',
    'cookie.decline': 'Somente essencial',
    'cookie.accept': 'Entendi',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Termos</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacidade</a> · Ferramenta de pesquisa, não aconselhamento jurídico.',
  },
  ko: {
    'topbar.tag': '법 찾기 · reasoning · 모든 단계 검증',
    'nav.engineGroup': '법률 추론 엔진',
    'nav.researchEngine': '조사 엔진',
    'nav.toolkitGroup': '법률 조사 도구',
    'nav.concept': '개념으로 검색',
    'nav.keyword': '키워드로 검색',
    'nav.case': '사건명으로 검색',
    'nav.citation': '인용으로 검색',
    'nav.laws': '법률 및 규칙 검색',
    'nav.extractor': '인용 추출기',
    'nav.resolver': '사건 해석기',
    'nav.brief': 'Brief Review',
    'nav.history': '조사 기록',
    'history.empty': '아직 조사 기록이 없습니다',
    'intro.h1': '법을 찾고, 추론하고, 모든 단계를 검증하세요.',
    'intro.lead': 'JuriCodex Platform은 두 가지 방식으로 시작하는 법률 조사 작업 공간입니다. 법률 추론 엔진으로 질문을 다루고, 법률 조사 도구로 1차 법원을 직접 검색하고 확인합니다.',
    'intro.engineKicker': '법률 추론 엔진',
    'intro.engineTitle': '검색을 넘어서는 작업을 위해.',
    'intro.engineBody': '일상 언어로 질문하세요. JuriCodex는 중요한 사실을 확인하고, 실제 권위를 검색하며, 필요하면 검색을 다듬고 답변이 무엇에 달려 있는지 보여줍니다.',
    'intro.toolkitKicker': '법률 조사 도구',
    'intro.toolkitTitle': '검색. 인용. 검증.',
    'intro.toolkitBody': '개념, 키워드, 사건명, 인용 또는 붙여 넣은 brief에서 시작하세요. 출처가 있는 사건, 법령, 규정, quote 확인 및 처리 신호를 찾을 수 있습니다.',
    'intro.disclaimer': 'JuriCodex는 열어 보고 확인할 수 있는 <strong>실제 1차 자료</strong>를 바탕으로 추론합니다. 이는 <strong>조사 도구</strong>입니다. 분석에 의존하기 전에 권위를 확인하고 실제 결정은 자격 있는 변호사와 상담하세요.',
    'example.0': '집주인이 정상적인 마모를 이유로 보증금을 가져갈 수 있나요?',
    'example.1': 'Miranda v. Arizona는 실제로 무엇을 판시했나요?',
    'example.2': '영장 없는 차량 수색에 대한 수정헌법 제4조의 제한',
    'placeholder.default': '일상 언어로 질문하세요…  (예: 안전 위반 신고 후 해고)',
    'placeholder.concept': '법률 개념이나 상황을 일상 언어로 설명하세요…',
    'placeholder.keyword': '판례법 검색 키워드를 입력하세요…',
    'placeholder.case': '사건명을 입력하세요. 예: Miranda v. Arizona',
    'placeholder.citation': '인용을 입력하세요. 예: 384 U.S. 436',
    'placeholder.laws': '연방 법령 또는 규정 주제를 설명하세요…',
    'placeholder.extractor': '법률 텍스트를 붙여 넣어 사건 인용과 사건명을 추출하세요…',
    'placeholder.resolver': '인용, 사건명, short cite, docket 또는 불완전한 참조를 입력하세요…',
    'placeholder.brief': 'brief, 메모, 주장 또는 법률 텍스트를 붙여 넣어 인용을 추출하고 quote를 검증하세요…',
    'login.title': '로그인하고 질문하기',
    'login.sub': 'JuriCodex는 모든 답변을 실제 법원 의견에 기반합니다. 로그인하면 조사를 저장하고 여러 기기에서 이어갈 수 있습니다.',
    'login.fine': '무료 사용 · 사용자를 대신해 게시하지 않습니다.',
    'login.unavailable': '현재 로그인을 사용할 수 없습니다. 나중에 다시 시도하세요.',
    'login.continue': '{provider}로 계속',
    'account.hint': '로그인하여 여러 기기에서 조사를 저장하세요.',
    'account.signIn': '{provider}로 로그인',
    'account.signedIn': '로그인됨',
    'account.signOut': '로그아웃',
    'account.signOutTitle': '로그아웃',
    'account.upgrade': '업그레이드',
    'account.manage': '결제 관리',
    'account.emailWarn': '구독 전에 로그인 제공자에서 인증된 이메일을 추가하세요.',
    'account.usage': '이번 달 {used}/{limit}',
    'account.plan': '{plan} 플랜',
    'upgrade.title': 'Platform 작업 공간 플랜 선택',
    'upgrade.sub': '출처 기반 조사, 검증, 기록 및 내보내기.',
    'upgrade.quota': '이번 달 Free 플랜의 {limit}개 질문을 모두 사용했습니다.',
    'upgrade.emailNote': '구독 전에 로그인 제공자에서 이메일을 추가하고 인증하세요. 구매를 계정에 연결하려면 실제 인증 이메일이 필요합니다.',
    'upgrade.monthly': '월간',
    'upgrade.yearly': '연간',
    'upgrade.save': '2개월 절약',
    'upgrade.oneTime': '일회성',
    'upgrade.annualPlan': '연간 플랜',
    'upgrade.monthlyPlan': '월간 플랜',
    'upgrade.note': '계속하면 <a href="/terms.html" target="_blank" rel="noopener">Terms</a>에 동의하고 <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>를 확인한 것으로 간주됩니다.',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '출처 기반 조사 300회, 검증, 기록 및 내보내기.',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'Brief Review, quote 확인, 내보내기, 저장된 세션을 위한 고용량 작업 공간.',
    'plan.day_pass.name': '3일 패스',
    'plan.day_pass.pitch': '3일 동안 Max 수준 접근을 체험하세요. 구독 없음.',
    'toast.emailRequired': '구독 전에 로그인 제공자에서 이메일을 추가하고 인증하세요.',
    'toast.checkoutOpening': '보안 checkout 여는 중…',
    'toast.checkoutFallback': 'checkout 팝업을 불러올 수 없습니다. 호스팅 checkout을 엽니다…',
    'toast.billingPortal': 'Freemius 영수증 이메일로 결제를 관리하거나 지원팀에 문의하세요.',
    'toast.authFailed': '로그인이 완료되지 않았습니다. 다시 시도하세요.',
    'label.question': '질문',
    'label.authorities': '권위 자료 표',
    'label.statutes': '연방 법령 및 규정',
    'label.verified': '검증됨',
    'label.copy': '⧉ 복사',
    'label.copied': '✓ 복사됨',
    'label.export': '↓ 내보내기',
    'label.exported': '✓ 내보냄',
    'label.copyCitation': '인용 복사',
    'label.copiedShort': '복사됨',
    'label.openOpinion': '전체 의견 열기 ↗',
    'label.openRegulation': '규정 열기 ↗',
    'label.details': '세부 정보 / PDF',
    'label.verifyQuote': '✓ quote 검증',
    'label.verifyQuotePlaceholder': '이 사건에 귀속된 quote를 붙여 넣으세요…',
    'label.check': '확인',
    'label.loadingDetails': '사건 세부 정보 불러오는 중…',
    'label.detailsFailed': '지금은 세부 정보를 불러올 수 없습니다.',
    'label.date': '날짜',
    'label.court': '법원',
    'label.docket': '도켓',
    'label.status': '상태',
    'label.citations': '인용',
    'label.caseAnalysis': '사건 분석',
    'label.whyItMatters': '중요한 이유:',
    'label.limits': '한계:',
    'label.focusedPassages': '관련 구절',
    'label.citingCases': '인용한 사건',
    'label.latest': '최신',
    'label.mostCited': '가장 많이 인용',
    'label.selected': '선택됨',
    'label.open': '열기',
    'label.opinionInventory': '의견 목록',
    'label.noInventory': '사용 가능한 의견 목록이 없습니다.',
    'label.opinionText': '의견 텍스트',
    'label.noText': '텍스트 없음',
    'label.pdfAvailable': 'PDF 있음',
    'label.noPdf': 'PDF 없음',
    'label.opinionsChecked': '{checked}/{total}개 의견 확인',
    'label.textPdfCount': '텍스트 {text}개 · PDF {pdf}개',
    'label.partialInventory': '부분 목록',
    'step.analyze': '분석',
    'step.search': '검색',
    'step.authorities': '권위 자료',
    'step.answer': '추론',
    'brief.title': 'Brief Review',
    'brief.refs': '· 참조 {count}개',
    'brief.none': '인용이나 사건명이 감지되지 않았습니다.',
    'brief.extracted': '추출된 참조',
    'brief.resolved': '해석된 권위 자료',
    'brief.quoteCheck': 'Quote 확인',
    'brief.source': '출처',
    'brief.unresolved': '미해결',
    'brief.quoteFound': '찾음({match})',
    'brief.quoteNotFound': '찾지 못함({match})',
    'brief.quoteNotChecked': 'Quote 미확인',
    'brief.noNearbyQuote': '주변 quote 없음',
    'extract.title': '인용 추출기',
    'extract.reference': '참조',
    'extract.context': '문맥',
    'extract.none': '참조가 감지되지 않았습니다.',
    'plan.title': '조사 계획',
    'plan.issues': '· 쟁점 {count}개',
    'plan.defaultSummary': '1차 법률 권위를 검색하고 답변을 구성합니다.',
    'plan.issue': '쟁점',
    'plan.dependsOn': '좌우되는 요소',
    'cases.found': '{query}에 대해 권위 자료 {count}개를 찾았습니다',
    'cases.none': '{query}에 대한 권위 자료가 없습니다',
    'rateLimited': '요청을 조금 빠르게 보내고 있습니다. 잠시 후 다시 시도하세요.',
    'error.generic': '문제가 발생했습니다.',
    'warning.prefix': '⚠ {message}',
    'warning.default': '인용을 다시 확인하세요.',
    'timeout': '너무 오래 걸려 중단되었습니다. 더 짧거나 구체적인 질문으로 다시 시도하세요.',
    'connectionError': '연결 오류: {message}',
    'assistant.fallback': '(사건 표시됨)',
    'verify.shortQuote': '확인하려면 더 긴 quote를 입력하세요.',
    'verify.checking': '실제 의견 텍스트 확인 중…',
    'verify.rateLimited': '확인이 너무 빠르게 반복되었습니다. 잠시 기다리세요.',
    'verify.found': '✓ 의견에서 찾음',
    'verify.noText': '⚠ 이 quote를 확인할 전체 의견 텍스트가 없습니다.',
    'verify.partial': '⚠ 검색 가능한 {total}개 의견 중 {searched}개에서 찾지 못했습니다. 다른 의견에 있을 수 있으니 미확인으로 보고 전체 의견을 확인하세요.',
    'verify.notFound': '✗ 이 의견 텍스트에서 찾지 못했습니다. quote를 미검증으로 보세요.',
    'verify.failed': '검증에 실패했습니다. 다시 시도하세요.',
    'export.title': 'JuriCodex — 조사 메모',
    'export.generated': '{stamp} 생성 · juricodex.online · 조사 도구이며 법률 자문이 아닙니다.',
    'export.authorities': '권위 자료 표',
    'export.disclaimer': '의존하기 전에 모든 권위를 확인하세요. JuriCodex는 조사 도구이며 법률 자문을 제공하지 않습니다.',
    'cookie.text': '로그인 유지를 위해 필수 쿠키 하나만 사용합니다. 광고나 제3자 추적 쿠키를 사용하지 않습니다. <a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>를 확인하세요.',
    'cookie.decline': '필수만',
    'cookie.accept': '확인',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Terms</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacy</a> · 조사 도구이며 법률 자문이 아닙니다.',
  },
  ja: {
    'topbar.tag': '法律を見つける · 推論する · すべての手順を検証する',
    'nav.engineGroup': '法的推論エンジン',
    'nav.researchEngine': 'リサーチエンジン',
    'nav.toolkitGroup': '法務リサーチツールキット',
    'nav.concept': '概念で検索',
    'nav.keyword': 'キーワードで検索',
    'nav.case': '事件名で検索',
    'nav.citation': '引用で検索',
    'nav.laws': '法律・規則検索',
    'nav.extractor': '引用抽出',
    'nav.resolver': '事件リゾルバー',
    'nav.brief': 'Brief Review',
    'nav.history': 'リサーチ履歴',
    'history.empty': 'まだリサーチはありません',
    'intro.h1': '法律を見つける。推論する。すべての手順を検証する。',
    'intro.lead': 'JuriCodex Platform は、質問を扱う法的推論エンジンと、一次法を直接検索・確認・検証する法務リサーチツールキットを備えた法務リサーチ用ワークスペースです。',
    'intro.engineKicker': '法的推論エンジン',
    'intro.engineTitle': '検索だけでは足りない作業に。',
    'intro.engineBody': '自然な言葉で質問してください。JuriCodex は重要な事実を確認し、実在する権威資料を検索し、必要に応じて検索を調整し、答えが何に依存するかを示します。',
    'intro.toolkitKicker': '法務リサーチツールキット',
    'intro.toolkitTitle': '検索。引用。検証。',
    'intro.toolkitBody': '概念、キーワード、事件名、引用、または貼り付けた brief から始められます。出典に基づく事件、法令、規則、quote 確認、処理シグナルを調べられます。',
    'intro.disclaimer': 'JuriCodex は、開いて確認できる<strong>実在の一次資料</strong>に基づいて推論します。これは<strong>リサーチツール</strong>です。分析に依拠する前に権威資料を確認し、実際の判断には資格ある弁護士に相談してください。',
    'example.0': '通常の損耗を理由に家主は敷金を保持できますか？',
    'example.1': 'Miranda v. Arizona は実際に何を判示しましたか？',
    'example.2': '令状なしの車両捜索に対する第四修正の制限',
    'placeholder.default': '自然な言葉で質問…（例：安全違反を報告した後の解雇）',
    'placeholder.concept': '法的概念または状況を自然な言葉で説明してください…',
    'placeholder.keyword': '判例法を検索するキーワードを入力…',
    'placeholder.case': '事件名を入力、例：Miranda v. Arizona',
    'placeholder.citation': '引用を入力、例：384 U.S. 436',
    'placeholder.laws': '連邦法令または規則のテーマを説明…',
    'placeholder.extractor': '法的テキストを貼り付けて事件引用と事件名を抽出…',
    'placeholder.resolver': '引用、事件名、short cite、docket、不完全な参照を入力…',
    'placeholder.brief': 'brief、メモ、主張、法的テキストを貼り付けて引用を抽出し quote を検証…',
    'login.title': 'ログインして質問',
    'login.sub': 'JuriCodex はすべての回答を実在する裁判所意見に基づけます。ログインするとリサーチを保存し、複数デバイスで作業を続けられます。',
    'login.fine': '無料で利用 · あなたの代わりに投稿することはありません。',
    'login.unavailable': '現在ログインは利用できません。後でもう一度お試しください。',
    'login.continue': '{provider} で続行',
    'account.hint': 'ログインしてリサーチを複数デバイスで保存します。',
    'account.signIn': '{provider} でログイン',
    'account.signedIn': 'ログイン済み',
    'account.signOut': 'ログアウト',
    'account.signOutTitle': 'ログアウト',
    'account.upgrade': 'アップグレード',
    'account.manage': '請求を管理',
    'account.emailWarn': '購読前にログインプロバイダーで確認済みメールを追加してください。',
    'account.usage': '今月 {used}/{limit}',
    'account.plan': '{plan} プラン',
    'upgrade.title': 'Platform ワークスペースプランを選択',
    'upgrade.sub': '出典に基づくリサーチ、検証、履歴、エクスポート。',
    'upgrade.quota': '今月の Free プランの {limit} 件の質問を使い切りました。',
    'upgrade.emailNote': '購読前にログインプロバイダーでメールを追加し確認してください。購入をアカウントに紐付けるため、実在する確認済みメールが必要です。',
    'upgrade.monthly': '月払い',
    'upgrade.yearly': '年払い',
    'upgrade.save': '2か月分お得',
    'upgrade.oneTime': '一回払い',
    'upgrade.annualPlan': '年額プラン',
    'upgrade.monthlyPlan': '月額プラン',
    'upgrade.note': '続行すると、<a href="/terms.html" target="_blank" rel="noopener">Terms</a> に同意し、<a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a> を確認したものとみなされます。',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '出典に基づくリサーチ 300 回、検証、履歴、エクスポート。',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'Brief Review、quote 確認、エクスポート、保存セッション向けの高容量ワークスペース。',
    'plan.day_pass.name': '3日パス',
    'plan.day_pass.pitch': '3日間 Max レベルのアクセスを試用。購読不要。',
    'toast.emailRequired': '購読前にログインプロバイダーでメールを追加し確認してください。',
    'toast.checkoutOpening': '安全な checkout を開いています…',
    'toast.checkoutFallback': 'checkout ポップアップを読み込めませんでした。ホスト型 checkout を開きます…',
    'toast.billingPortal': 'Freemius の領収書メールで請求を管理するか、サポートに連絡してください。',
    'toast.authFailed': 'ログインが完了しませんでした。もう一度お試しください。',
    'label.question': '質問',
    'label.authorities': '権威資料一覧',
    'label.statutes': '連邦法令・規則',
    'label.verified': '検証済み',
    'label.copy': '⧉ コピー',
    'label.copied': '✓ コピー済み',
    'label.export': '↓ エクスポート',
    'label.exported': '✓ エクスポート済み',
    'label.copyCitation': '引用をコピー',
    'label.copiedShort': 'コピー済み',
    'label.openOpinion': '全文意見を開く ↗',
    'label.openRegulation': '規則を開く ↗',
    'label.details': '詳細 / PDF',
    'label.verifyQuote': '✓ quote を検証',
    'label.verifyQuotePlaceholder': 'この事件に帰属する quote を貼り付け…',
    'label.check': '確認',
    'label.loadingDetails': '事件詳細を読み込み中…',
    'label.detailsFailed': '現在、詳細を読み込めません。',
    'label.date': '日付',
    'label.court': '裁判所',
    'label.docket': 'Docket',
    'label.status': 'ステータス',
    'label.citations': '引用',
    'label.caseAnalysis': '事件分析',
    'label.whyItMatters': '重要な理由:',
    'label.limits': '限界:',
    'label.focusedPassages': '関連箇所',
    'label.citingCases': '引用した事件',
    'label.latest': '最新',
    'label.mostCited': '最多引用',
    'label.selected': '選択',
    'label.open': '開く',
    'label.opinionInventory': '意見一覧',
    'label.noInventory': '利用可能な意見一覧はありません。',
    'label.opinionText': '意見テキスト',
    'label.noText': 'テキストなし',
    'label.pdfAvailable': 'PDFあり',
    'label.noPdf': 'PDFなし',
    'label.opinionsChecked': '{checked}/{total} 件の意見を確認',
    'label.textPdfCount': 'テキスト {text} 件 · PDF {pdf} 件',
    'label.partialInventory': '部分一覧',
    'step.analyze': '分析',
    'step.search': '検索',
    'step.authorities': '権威資料',
    'step.answer': '推論',
    'brief.title': 'Brief Review',
    'brief.refs': '· 参照 {count} 件',
    'brief.none': '引用または事件名は検出されませんでした。',
    'brief.extracted': '抽出された参照',
    'brief.resolved': '解決済み権威資料',
    'brief.quoteCheck': 'Quote 確認',
    'brief.source': '出典',
    'brief.unresolved': '未解決',
    'brief.quoteFound': '見つかりました（{match}）',
    'brief.quoteNotFound': '見つかりません（{match}）',
    'brief.quoteNotChecked': 'Quote 未確認',
    'brief.noNearbyQuote': '近くに quote なし',
    'extract.title': '引用抽出',
    'extract.reference': '参照',
    'extract.context': '文脈',
    'extract.none': '参照は検出されませんでした。',
    'plan.title': 'リサーチ計画',
    'plan.issues': '· 論点 {count} 件',
    'plan.defaultSummary': '一次法の権威資料を検索し、回答を構成します。',
    'plan.issue': '論点',
    'plan.dependsOn': '依存する要素',
    'cases.found': '{query} について {count} 件の権威資料が見つかりました',
    'cases.none': '{query} の権威資料は見つかりませんでした',
    'rateLimited': 'リクエストが少し速すぎます。少し待ってから再試行してください。',
    'error.generic': '問題が発生しました。',
    'warning.prefix': '⚠ {message}',
    'warning.default': '引用を慎重に確認してください。',
    'timeout': '時間がかかりすぎたため停止しました。より短い、または具体的な質問で再試行してください。',
    'connectionError': '接続エラー: {message}',
    'assistant.fallback': '（事件を表示済み）',
    'verify.shortQuote': '確認するにはより長い quote を入力してください。',
    'verify.checking': '実際の意見テキストを確認中…',
    'verify.rateLimited': '確認が速すぎます。少し待ってください。',
    'verify.found': '✓ 意見中に見つかりました',
    'verify.noText': '⚠ この quote を確認するための全文意見テキストがありません。',
    'verify.partial': '⚠ 検索可能な {total} 件の意見のうち {searched} 件では見つかりませんでした。他の意見にある可能性があります。未確認として扱い、全文意見を開いて確認してください。',
    'verify.notFound': '✗ この意見テキストでは見つかりませんでした。quote は未検証として扱ってください。',
    'verify.failed': '検証に失敗しました。もう一度お試しください。',
    'export.title': 'JuriCodex — リサーチメモ',
    'export.generated': '{stamp} 生成 · juricodex.online · リサーチツールであり法的助言ではありません。',
    'export.authorities': '権威資料一覧',
    'export.disclaimer': '依拠する前にすべての権威資料を確認してください。JuriCodex はリサーチツールであり、法的助言を提供しません。',
    'cookie.text': 'ログイン状態を維持するため、必須 cookie を 1 つだけ使用します。広告や第三者追跡 cookie は使用しません。<a href="/privacy.html" target="_blank" rel="noopener">Privacy Policy</a> をご確認ください。',
    'cookie.decline': '必須のみ',
    'cookie.accept': '了解',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Terms</a> · <a href="/privacy.html" target="_blank" rel="noopener">Privacy</a> · リサーチツールであり法的助言ではありません。',
  },
  vi: {
    'topbar.tag': 'Tìm luật · suy luận · kiểm chứng từng bước',
    'nav.engineGroup': 'Bộ máy suy luận pháp lý',
    'nav.researchEngine': 'Bộ máy nghiên cứu',
    'nav.toolkitGroup': 'Bộ công cụ nghiên cứu pháp lý',
    'nav.concept': 'Tìm theo khái niệm',
    'nav.keyword': 'Tìm theo từ khóa',
    'nav.case': 'Tìm theo tên vụ án',
    'nav.citation': 'Tìm theo trích dẫn',
    'nav.laws': 'Tìm luật và quy định',
    'nav.extractor': 'Trích xuất trích dẫn',
    'nav.resolver': 'Phân giải vụ án',
    'nav.brief': 'Rà soát brief',
    'nav.history': 'Lịch sử nghiên cứu',
    'history.empty': 'Chưa có nghiên cứu nào',
    'intro.h1': 'Tìm luật. Suy luận. Kiểm chứng từng bước.',
    'intro.lead': 'JuriCodex Platform là không gian nghiên cứu pháp lý với hai cách bắt đầu: Bộ máy suy luận pháp lý để xử lý câu hỏi và Bộ công cụ nghiên cứu pháp lý để tìm kiếm, kiểm tra và xác minh trực tiếp nguồn luật sơ cấp.',
    'intro.engineKicker': 'Bộ máy suy luận pháp lý',
    'intro.engineTitle': 'Khi công việc vượt ra ngoài tìm kiếm.',
    'intro.engineBody': 'Hãy hỏi bằng ngôn ngữ thường ngày. JuriCodex làm rõ khi sự kiện quan trọng, tìm nguồn có thẩm quyền thật, tinh chỉnh truy vấn khi cần và cho thấy câu trả lời phụ thuộc vào điều gì.',
    'intro.toolkitKicker': 'Bộ công cụ nghiên cứu pháp lý',
    'intro.toolkitTitle': 'Tìm. Trích dẫn. Xác minh.',
    'intro.toolkitBody': 'Bắt đầu từ khái niệm, từ khóa, tên vụ án, trích dẫn hoặc brief được dán vào. Tìm vụ án, luật, quy định, kiểm tra quote và tín hiệu xử lý có nguồn hỗ trợ.',
    'intro.disclaimer': 'JuriCodex suy luận từ <strong>nguồn sơ cấp thật</strong> mà bạn có thể mở và xác minh. Đây là <strong>công cụ nghiên cứu</strong>: hãy kiểm chứng nguồn có thẩm quyền trước khi dựa vào phân tích và hỏi luật sư được cấp phép cho quyết định thực tế.',
    'example.0': 'Chủ nhà có thể giữ tiền đặt cọc vì hao mòn thông thường không?',
    'example.1': 'Miranda v. Arizona thực sự đã phán quyết điều gì?',
    'example.2': 'Giới hạn của Tu chính án thứ tư đối với việc khám xe không có lệnh',
    'placeholder.default': 'Hỏi bằng ngôn ngữ thường ngày…  (ví dụ: bị sa thải sau khi báo cáo vi phạm an toàn)',
    'placeholder.concept': 'Mô tả khái niệm pháp lý hoặc tình huống bằng ngôn ngữ thường ngày…',
    'placeholder.keyword': 'Nhập từ khóa để tìm án lệ…',
    'placeholder.case': 'Nhập tên vụ án, ví dụ Miranda v. Arizona',
    'placeholder.citation': 'Nhập trích dẫn, ví dụ 384 U.S. 436',
    'placeholder.laws': 'Mô tả chủ đề luật hoặc quy định liên bang…',
    'placeholder.extractor': 'Dán văn bản pháp lý để trích xuất trích dẫn và tên vụ án…',
    'placeholder.resolver': 'Nhập trích dẫn, tên vụ án, short cite, docket hoặc tham chiếu chưa rõ…',
    'placeholder.brief': 'Dán brief, memo, lập luận hoặc văn bản pháp lý để trích xuất trích dẫn và xác minh quote…',
    'login.title': 'Đăng nhập để hỏi',
    'login.sub': 'JuriCodex đặt mọi câu trả lời trên nền tảng ý kiến tòa án thật. Đăng nhập để nghiên cứu và giữ công việc trên các thiết bị.',
    'login.fine': 'Miễn phí sử dụng · Chúng tôi không bao giờ đăng thay bạn.',
    'login.unavailable': 'Hiện chưa thể đăng nhập. Vui lòng thử lại sau.',
    'login.continue': 'Tiếp tục với {provider}',
    'account.hint': 'Đăng nhập để lưu nghiên cứu trên các thiết bị.',
    'account.signIn': 'Đăng nhập bằng {provider}',
    'account.signedIn': 'Đã đăng nhập',
    'account.signOut': 'Đăng xuất',
    'account.signOutTitle': 'Đăng xuất',
    'account.upgrade': 'Nâng cấp',
    'account.manage': 'Quản lý thanh toán',
    'account.emailWarn': 'Hãy thêm email đã xác minh qua nhà cung cấp đăng nhập trước khi đăng ký.',
    'account.usage': '{used}/{limit} tháng này',
    'account.plan': 'gói {plan}',
    'upgrade.title': 'Chọn gói workspace Platform',
    'upgrade.sub': 'Nghiên cứu có nguồn, xác minh, lịch sử và xuất dữ liệu.',
    'upgrade.quota': 'Bạn đã dùng hết {limit} câu hỏi của gói Free tháng này.',
    'upgrade.emailNote': 'Hãy thêm và xác minh email qua nhà cung cấp đăng nhập trước khi đăng ký. Chúng tôi cần email thật đã xác minh để gắn giao dịch với tài khoản của bạn.',
    'upgrade.monthly': 'Hàng tháng',
    'upgrade.yearly': 'Hàng năm',
    'upgrade.save': 'Tiết kiệm 2 tháng',
    'upgrade.oneTime': 'Một lần',
    'upgrade.annualPlan': 'Gói năm',
    'upgrade.monthlyPlan': 'Gói tháng',
    'upgrade.note': 'Bằng cách tiếp tục, bạn đồng ý với <a href="/terms.html" target="_blank" rel="noopener">Điều khoản</a> và xác nhận <a href="/privacy.html" target="_blank" rel="noopener">Chính sách quyền riêng tư</a>.',
    'plan.pro.name': 'Pro',
    'plan.pro.pitch': '300 lượt nghiên cứu có nguồn, xác minh, lịch sử và xuất dữ liệu.',
    'plan.max.name': 'Max',
    'plan.max.pitch': 'Workspace dung lượng cao cho Brief Review, kiểm tra quote, xuất dữ liệu và phiên đã lưu.',
    'plan.day_pass.name': 'Vé 3 ngày',
    'plan.day_pass.pitch': 'Dùng thử quyền truy cập cấp Max trong 3 ngày. Không cần đăng ký.',
    'toast.emailRequired': 'Hãy thêm và xác minh email qua nhà cung cấp đăng nhập trước khi đăng ký.',
    'toast.checkoutOpening': 'Đang mở checkout an toàn…',
    'toast.checkoutFallback': 'Không tải được cửa sổ checkout. Đang mở checkout lưu trữ…',
    'toast.billingPortal': 'Dùng email nhận biên lai Freemius để quản lý thanh toán hoặc liên hệ hỗ trợ.',
    'toast.authFailed': 'Đăng nhập chưa hoàn tất. Vui lòng thử lại.',
    'label.question': 'Câu hỏi',
    'label.authorities': 'Bảng nguồn có thẩm quyền',
    'label.statutes': 'Luật và quy định liên bang',
    'label.verified': 'Đã xác minh',
    'label.copy': '⧉ Sao chép',
    'label.copied': '✓ Đã sao chép',
    'label.export': '↓ Xuất',
    'label.exported': '✓ Đã xuất',
    'label.copyCitation': 'Sao chép trích dẫn',
    'label.copiedShort': 'Đã sao chép',
    'label.openOpinion': 'Mở toàn văn ý kiến ↗',
    'label.openRegulation': 'Mở quy định ↗',
    'label.details': 'Chi tiết / PDF',
    'label.verifyQuote': '✓ Xác minh quote',
    'label.verifyQuotePlaceholder': 'Dán quote được gán cho vụ án này…',
    'label.check': 'Kiểm tra',
    'label.loadingDetails': 'Đang tải chi tiết vụ án…',
    'label.detailsFailed': 'Hiện không tải được chi tiết.',
    'label.date': 'Ngày',
    'label.court': 'Tòa',
    'label.docket': 'Docket',
    'label.status': 'Trạng thái',
    'label.citations': 'Trích dẫn',
    'label.caseAnalysis': 'Phân tích vụ án',
    'label.whyItMatters': 'Vì sao quan trọng:',
    'label.limits': 'Giới hạn:',
    'label.focusedPassages': 'Đoạn liên quan',
    'label.citingCases': 'Vụ án trích dẫn',
    'label.latest': 'Mới nhất',
    'label.mostCited': 'Được trích dẫn nhiều nhất',
    'label.selected': 'Đã chọn',
    'label.open': 'mở',
    'label.opinionInventory': 'Danh mục ý kiến',
    'label.noInventory': 'Không có danh mục ý kiến.',
    'label.opinionText': 'Văn bản ý kiến',
    'label.noText': 'Không có văn bản',
    'label.pdfAvailable': 'Có PDF',
    'label.noPdf': 'Không có PDF',
    'label.opinionsChecked': 'Đã kiểm tra {checked}/{total} ý kiến',
    'label.textPdfCount': '{text} văn bản · {pdf} PDF',
    'label.partialInventory': 'Danh mục một phần',
    'step.analyze': 'Phân tích',
    'step.search': 'Tìm kiếm',
    'step.authorities': 'Nguồn có thẩm quyền',
    'step.answer': 'Suy luận',
    'brief.title': 'Rà soát brief',
    'brief.refs': '· {count} tham chiếu',
    'brief.none': 'Không phát hiện trích dẫn hoặc tên vụ án.',
    'brief.extracted': 'Tham chiếu đã trích xuất',
    'brief.resolved': 'Nguồn đã phân giải',
    'brief.quoteCheck': 'Kiểm tra quote',
    'brief.source': 'Nguồn',
    'brief.unresolved': 'Chưa phân giải',
    'brief.quoteFound': 'Tìm thấy ({match})',
    'brief.quoteNotFound': 'Không tìm thấy ({match})',
    'brief.quoteNotChecked': 'Quote chưa kiểm tra',
    'brief.noNearbyQuote': 'Không có quote gần đó',
    'extract.title': 'Trích xuất trích dẫn',
    'extract.reference': 'Tham chiếu',
    'extract.context': 'Ngữ cảnh',
    'extract.none': 'Không phát hiện tham chiếu.',
    'plan.title': 'Kế hoạch nghiên cứu',
    'plan.issues': '· {count} vấn đề',
    'plan.defaultSummary': 'Tìm nguồn luật sơ cấp và tổ chức câu trả lời.',
    'plan.issue': 'Vấn đề',
    'plan.dependsOn': 'Phụ thuộc vào',
    'cases.found': 'Tìm thấy {count} nguồn có thẩm quyền cho: {query}',
    'cases.none': 'Không có nguồn có thẩm quyền cho: {query}',
    'rateLimited': 'Bạn đang gửi yêu cầu hơi nhanh. Hãy chờ một chút rồi thử lại.',
    'error.generic': 'Đã xảy ra lỗi.',
    'warning.prefix': '⚠ {message}',
    'warning.default': 'Hãy kiểm tra kỹ các trích dẫn.',
    'timeout': 'Yêu cầu mất quá lâu và đã dừng. Hãy thử lại với câu hỏi ngắn hơn hoặc cụ thể hơn.',
    'connectionError': 'Lỗi kết nối: {message}',
    'assistant.fallback': '(đã hiển thị vụ án)',
    'verify.shortQuote': 'Nhập quote dài hơn để kiểm tra.',
    'verify.checking': 'Đang kiểm tra văn bản ý kiến thật…',
    'verify.rateLimited': 'Quá nhiều lượt kiểm tra quá nhanh; hãy chờ một chút.',
    'verify.found': '✓ Tìm thấy trong ý kiến',
    'verify.noText': '⚠ Không có toàn văn ý kiến để kiểm tra quote này.',
    'verify.partial': '⚠ Không tìm thấy trong {searched} trên {total} ý kiến có thể tìm kiếm; có thể nằm ở ý kiến khác. Hãy coi là chưa xác nhận và mở toàn văn để kiểm tra.',
    'verify.notFound': '✗ Không tìm thấy trong văn bản ý kiến này; coi quote là chưa được xác minh.',
    'verify.failed': 'Xác minh thất bại; vui lòng thử lại.',
    'export.title': 'JuriCodex — Bản ghi nhớ nghiên cứu',
    'export.generated': 'Tạo ngày {stamp} · juricodex.online · Công cụ nghiên cứu, không phải tư vấn pháp lý.',
    'export.authorities': 'Bảng nguồn có thẩm quyền',
    'export.disclaimer': 'Hãy xác minh mọi nguồn trước khi dựa vào. JuriCodex là công cụ nghiên cứu và không cung cấp tư vấn pháp lý.',
    'cookie.text': 'Chúng tôi dùng một cookie thiết yếu để giữ bạn đăng nhập. Chúng tôi không dùng cookie quảng cáo hoặc theo dõi bên thứ ba. Xem <a href="/privacy.html" target="_blank" rel="noopener">Chính sách quyền riêng tư</a>.',
    'cookie.decline': 'Chỉ thiết yếu',
    'cookie.accept': 'Đã hiểu',
    'footer.html': 'JuriCodex · <a href="/terms.html" target="_blank" rel="noopener">Điều khoản</a> · <a href="/privacy.html" target="_blank" rel="noopener">Quyền riêng tư</a> · Công cụ nghiên cứu, không phải tư vấn pháp lý.',
  },
};

function detectLanguage() {
  try {
    const saved = localStorage.getItem(LANG_STORAGE_KEY);
    if (saved && LANGS[saved]) return saved;
  } catch { /* ignore */ }
  const lang = (navigator.language || '').toLowerCase();
  if (lang.startsWith('zh-tw') || lang.startsWith('zh-hk') || lang.startsWith('zh-mo') || lang.includes('hant')) return 'zh-TW';
  if (lang.startsWith('es')) return 'es';
  if (lang.startsWith('zh')) return 'zh';
  if (lang.startsWith('fr')) return 'fr';
  if (lang.startsWith('pt')) return 'pt';
  if (lang.startsWith('ko')) return 'ko';
  if (lang.startsWith('ja')) return 'ja';
  if (lang.startsWith('vi')) return 'vi';
  return 'en';
}

let currentLang = detectLanguage();

function tr(key, vars = {}) {
  const table = I18N[currentLang] || I18N.en;
  const template = table[key] ?? I18N.en[key] ?? key;
  return String(template).replace(/\{(\w+)\}/g, (_, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : '');
}

function plural(count, singular = '', pluralSuffix = 's') {
  if (['zh', 'zh-TW', 'ko', 'ja', 'vi'].includes(currentLang)) return '';
  if (currentLang === 'es') return count === 1 ? singular : pluralSuffix;
  return count === 1 ? singular : pluralSuffix;
}

function placeholderForMode(mode = currentMode) {
  return tr(`placeholder.${mode === 'chat' ? 'default' : mode}`);
}

function applyI18n() {
  document.documentElement.lang = currentLang === 'zh' ? 'zh-Hans' : (currentLang === 'zh-TW' ? 'zh-Hant' : currentLang);
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = tr(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach((el) => { el.innerHTML = tr(el.dataset.i18nHtml); });
  document.querySelectorAll('.examples .ex').forEach((b, i) => { b.textContent = tr(`example.${i}`); });
  document.querySelector('.foot')?.replaceChildren();
  const foot = document.querySelector('.foot');
  if (foot) foot.innerHTML = tr('footer.html');
  const cookieText = document.querySelector('.cookie-text');
  if (cookieText) cookieText.innerHTML = tr('cookie.text');
  const cookieDecline = document.getElementById('cookieDecline');
  if (cookieDecline) cookieDecline.textContent = tr('cookie.decline');
  const cookieAccept = document.getElementById('cookieAccept');
  if (cookieAccept) cookieAccept.textContent = tr('cookie.accept');
  const langSelect = document.getElementById('langSelect');
  if (langSelect) langSelect.value = currentLang;
  if (input) input.placeholder = placeholderForMode();
  if (historyEl) historyEl.dataset.empty = tr('history.empty');
  renderAccount();
}

function setLanguage(lang) {
  if (!LANGS[lang]) return;
  currentLang = lang;
  try { localStorage.setItem(LANG_STORAGE_KEY, lang); } catch { /* ignore */ }
  applyI18n();
}

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

// Only allow http(s) URLs (e.g. avatar images from OAuth providers). Rejects
// javascript:/data: and other schemes so a hostile profile field can't inject
// an active URL. Returns '' when the URL isn't a safe absolute http(s) link.
function safeUrl(u) {
  const s = String(u || '').trim();
  if (!s) return '';
  try {
    const parsed = new URL(s, location.origin);
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') ? parsed.href : '';
  } catch {
    return '';
  }
}

// A same-origin "return here after sign-in" target (path only — never the full
// URL, so it can't be turned into an off-site redirect).
function selfNext() {
  return encodeURIComponent(location.pathname + location.search + location.hash);
}

// Lightweight transient toast (top-center). Used for non-blocking notices like
// a failed sign-in. Auto-dismisses; safe to call before DOM helpers exist.
function showToast(msg, ms = 4000) {
  try {
    let host = document.getElementById('toastHost');
    if (!host) {
      host = document.createElement('div');
      host.id = 'toastHost';
      host.className = 'toast-host';
      document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = String(msg || '');
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 300);
    }, ms);
  } catch { /* ignore */ }
}

// Citation linking: [1],[2] → case refs; [R1],[R2] → statute/regulation refs.
// Each becomes a clickable chip scoped to this turn's authority lists.
function linkifyCites(s, turnId) {
  return s
    .replace(/\[(\d{1,2})\]/g, (m, n) =>
      `<span class="cite-ref" data-kind="case" data-turn="${turnId}" data-n="${n}">[${n}]</span>`)
    .replace(/\[R(\d{1,2})\]/g, (m, n) =>
      `<span class="cite-ref" data-kind="statute" data-turn="${turnId}" data-n="${n}">[R${n}]</span>`);
}

// Streaming render: escape + link citations; shown pre-wrap while tokens arrive.
function renderWithCites(text, turnId) {
  return linkifyCites(escapeHtml(text), turnId);
}

// Inline markdown on already-escaped text: bold, italic, inline code, + citations.
function renderInline(s, turnId) {
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
       .replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>')
       .replace(/`([^`]+)`/g, '<code>$1</code>');
  return linkifyCites(s, turnId);
}

// Minimal, safe Markdown → HTML for the final answer. Escapes first (no raw HTML
// from the model or case text ever reaches the DOM), then builds paragraphs,
// bullet/numbered lists and headings with inline formatting + clickable cites.
function renderMarkdown(text, turnId) {
  const lines = escapeHtml(text).split('\n');
  let html = '', listType = null, para = [];
  const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
  const flushPara = () => { if (para.length) { html += `<p>${renderInline(para.join(' '), turnId)}</p>`; para = []; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); closeList(); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); closeList();
      const lvl = Math.min(m[1].length + 2, 4);
      html += `<h${lvl}>${renderInline(m[2], turnId)}</h${lvl}>`;
    } else if ((m = line.match(/^[-*•]\s+(.*)$/))) {
      flushPara();
      if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
      html += `<li>${renderInline(m[1], turnId)}</li>`;
    } else if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
      html += `<li>${renderInline(m[1], turnId)}</li>`;
    } else {
      closeList(); para.push(line);
    }
  }
  flushPara(); closeList();
  return html;
}

function scrollDown() { chat.scrollTop = chat.scrollHeight; }

// The visible research workflow (mirrors the backend SSE phases).
const STEPS = [['analyze', 'step.analyze'], ['search', 'step.search'], ['authorities', 'step.authorities'], ['answer', 'step.answer']];

function buildStepper() {
  return STEPS.map(([k, lblKey], i) =>
    `${i ? '<div class="step-sep"></div>' : ''}` +
    `<div class="step" data-step="${k}"><span class="dot"><span class="n">${i + 1}</span></span><span class="lbl">${tr(lblKey)}</span></div>`
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
  el.innerHTML = `<div class="qcard"><span class="ql">${escapeHtml(tr('label.question'))}</span><span class="qt">${escapeHtml(text)}</span></div>`;
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
      <div class="auth-head">${escapeHtml(tr('label.authorities'))} <span class="cnt"></span></div>
      <div class="cases"></div>
    </div>
    <div class="statutes" style="display:none">
      <div class="auth-head">${escapeHtml(tr('label.statutes'))} <span class="cnt"></span></div>
      <div class="statlist"></div>
    </div>
    <div class="answer" style="display:none"></div>
    <div class="answer-actions" style="display:none"><button type="button" class="copy-btn">${escapeHtml(tr('label.copy'))}</button><button type="button" class="export-btn">${escapeHtml(tr('label.export'))}</button></div>`;
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
    actionsEl: el.querySelector('.answer-actions'),
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
      cyt = `<span class="cytator cyt-${escapeHtml(c.treatment || 'cited')}" title="Cited by ${c.cited_by} later opinions${recent}. Heuristic citation-frequency signal only — NOT an authoritative good-law check (Shepard's/KeyCite) and not a negative-history (overruled) check. Always read the opinion before relying on it.">▣ ${label} · cited by ${c.cited_by}${recent}</span>`;
    }
    card.innerHTML = `
      <div class="row1"><span class="num">${n}</span><span class="title">${escapeHtml(c.title)}</span><span class="verified">${escapeHtml(tr('label.verified'))}</span></div>
      <div class="meta">${escapeHtml(c.court || '')}${c.date ? ' · ' + escapeHtml(c.date) : ''}${c.cite_count ? ' · cited by ' + c.cite_count : ''}</div>
      ${cyt ? `<div class="treatment">${cyt}</div>` : ''}
      ${cites ? `<div class="cites">${escapeHtml(cites)}</div>` : ''}
      ${c.snippet ? `<div class="snip">${escapeHtml(c.snippet.slice(0, 280))}…</div>` : ''}
      ${c.url ? `<a class="open" href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(tr('label.openOpinion'))}</a>` : ''}
      ${c.id ? `<button type="button" class="details-toggle" data-cluster="${escapeHtml(String(c.id))}">${escapeHtml(tr('label.details'))}</button><div class="case-details" style="display:none"></div>` : ''}
      ${c.id ? `<div class="verify" data-cluster="${escapeHtml(String(c.id))}">
        <button type="button" class="verify-toggle">${escapeHtml(tr('label.verifyQuote'))}</button>
        <div class="verify-box" style="display:none">
          <textarea class="verify-q" rows="2" placeholder="${escapeHtml(tr('label.verifyQuotePlaceholder'))}"></textarea>
          <button type="button" class="verify-run">${escapeHtml(tr('label.check'))}</button>
          <div class="verify-result"></div>
        </div>
      </div>` : ''}`;
    casesEl.appendChild(card);
  });
}

function renderCaseDetailsBox(box, details) {
  const citations = (details.citations || []).slice(0, 6).join(' · ');
  const availability = details.source_availability || {};
  const citing = (details.citing_cases || {}).cases || [];
  const latestCiting = (details.citing_cases || {}).latest || [];
  const strongCiting = (details.citing_cases || {}).most_cited || [];
  const passages = details.focused_passages || [];
  const analysis = details.case_analysis || {};
  const opinions = (details.opinions || []).map((op) => `
    <li>
      <span class="op-type">${escapeHtml(op.type || 'opinion')}</span>
      ${op.author ? `<span class="op-author">${escapeHtml(op.author)}</span>` : ''}
      ${op.has_text ? `<span class="op-text">text</span>` : ''}
      ${op.url ? `<a href="${escapeHtml(op.url)}" target="_blank" rel="noopener">opinion</a>` : ''}
      ${op.pdf_url ? `<a href="${escapeHtml(op.pdf_url)}" target="_blank" rel="noopener">PDF</a>` : ''}
    </li>`).join('');
  box.innerHTML = `
    <div class="source-availability">
      <span class="avail ${availability.has_text ? 'yes' : 'no'}">${availability.has_text ? escapeHtml(tr('label.opinionText')) : escapeHtml(tr('label.noText'))}</span>
      <span class="avail ${availability.has_pdf ? 'yes' : 'no'}">${availability.has_pdf ? escapeHtml(tr('label.pdfAvailable')) : escapeHtml(tr('label.noPdf'))}</span>
      <span class="avail">${escapeHtml(tr('label.opinionsChecked', { checked: availability.opinions_found || 0, total: availability.opinions_total || 0 }))}</span>
      <span class="avail">${escapeHtml(tr('label.textPdfCount', { text: availability.text_count || 0, pdf: availability.pdf_count || 0 }))}</span>
      ${availability.partial ? `<span class="avail warn">${escapeHtml(tr('label.partialInventory'))}</span>` : ''}
    </div>
    <div class="detail-grid">
      ${details.date ? `<div><b>${escapeHtml(tr('label.date'))}</b><span>${escapeHtml(details.date)}</span></div>` : ''}
      ${details.court ? `<div><b>${escapeHtml(tr('label.court'))}</b><span>${escapeHtml(details.court)}</span></div>` : ''}
      ${details.docket_number ? `<div><b>${escapeHtml(tr('label.docket'))}</b><span>${escapeHtml(details.docket_number)}</span></div>` : ''}
      ${details.precedential_status ? `<div><b>${escapeHtml(tr('label.status'))}</b><span>${escapeHtml(details.precedential_status)}</span></div>` : ''}
    </div>
    ${citations ? `<div class="detail-cites"><b>${escapeHtml(tr('label.citations'))}</b> ${escapeHtml(citations)} <button type="button" class="copy-cite" data-cite="${escapeHtml((details.citations || [])[0] || citations)}">${escapeHtml(tr('label.copyCitation'))}</button></div>` : ''}
    ${analysis.summary ? `<div class="case-analysis"><b>${escapeHtml(tr('label.caseAnalysis'))}</b><p>${escapeHtml(analysis.summary)}</p>${analysis.why_it_matters ? `<p><strong>${escapeHtml(tr('label.whyItMatters'))}</strong> ${escapeHtml(analysis.why_it_matters)}</p>` : ''}${(analysis.key_points || []).length ? `<ul>${analysis.key_points.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : ''}${(analysis.limits || []).length ? `<div class="analysis-limits"><strong>${escapeHtml(tr('label.limits'))}</strong> ${escapeHtml(analysis.limits.join('; '))}</div>` : ''}</div>` : ''}
    ${passages.length ? `<div class="focused-passages"><b>${escapeHtml(tr('label.focusedPassages'))}</b>${passages.map((p) => `<blockquote>${escapeHtml(p.text || '')}</blockquote>`).join('')}</div>` : ''}
    ${(latestCiting.length || strongCiting.length || citing.length) ? `<div class="citing-cases"><b>${escapeHtml(tr('label.citingCases'))}</b>${renderCitingGroup(tr('label.latest'), latestCiting)}${renderCitingGroup(tr('label.mostCited'), strongCiting)}${(!latestCiting.length && !strongCiting.length) ? renderCitingGroup(tr('label.selected'), citing) : ''}</div>` : ''}
    ${opinions ? `<div class="opinion-inventory"><b>${escapeHtml(tr('label.opinionInventory'))}</b><ul>${opinions}</ul></div>` : `<div class="detail-empty">${escapeHtml(tr('label.noInventory'))}</div>`}`;
}

function renderCitingGroup(label, cases) {
  if (!cases || !cases.length) return '';
  return `<div class="citing-group"><span>${escapeHtml(label)}</span><ul>${cases.slice(0, 5).map((c) => `<li>${escapeHtml(c.title || '')}${c.date ? ` <span>${escapeHtml(c.date)}</span>` : ''}${c.citations && c.citations.length ? ` <em>${escapeHtml(c.citations[0])}</em>` : ''}${c.url ? ` <a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(tr('label.open'))}</a>` : ''}</li>`).join('')}</ul></div>`;
}

function renderBriefReview(turnEl, payload) {
  let panel = turnEl.querySelector('.brief-review');
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'brief-review';
    turnEl.querySelector('.answer')?.insertAdjacentElement('beforebegin', panel);
  }
  const rows = payload.rows || [];
  if (!rows.length) {
    panel.innerHTML = `<div class="auth-head">${escapeHtml(tr('brief.title'))} <span class="cnt">${escapeHtml(tr('brief.refs', { count: 0, plural: plural(0) }))}</span></div><div class="answer note">${escapeHtml(tr('brief.none'))}</div>`;
    return;
  }
  const body = rows.map((r, i) => {
    const c = r.case || {};
    const qc = r.quote_check || null;
    const sc = r.support_check || {};
    const status = r.status || sc.status || (c.title ? 'Needs review' : 'Case unresolved');
    const quoteStatus = qc
      ? (qc.found ? tr('brief.quoteFound', { match: qc.match || 'match' }) : tr('brief.quoteNotFound', { match: qc.match || 'check' }))
      : (r.ref && r.ref.quote ? tr('brief.quoteNotChecked') : tr('brief.noNearbyQuote'));
    const statusClass = qc && qc.found ? 'br-ok' : (qc ? 'br-warn' : '');
    return `<tr>
      <td><span class="br-num">${i + 1}</span></td>
      <td><div class="br-ref">${escapeHtml((r.ref || {}).text || '')}</div><div class="br-kind">${escapeHtml((r.ref || {}).kind || '')}</div></td>
      <td>${c.title ? `<div class="br-case">${escapeHtml(c.title)}</div><div class="br-meta">${escapeHtml(c.court || '')}${c.date ? ' · ' + escapeHtml(c.date) : ''}</div>${(c.citations || []).length ? `<div class="br-cites">${escapeHtml((c.citations || []).slice(0, 2).join(' · '))}</div>` : ''}` : `<span class="br-miss">${escapeHtml(tr('brief.unresolved'))}</span>`}</td>
      <td><span class="support-status ${supportClass(status)}">${escapeHtml(status)}</span>${sc.reason ? `<div class="br-reason">${escapeHtml(sc.reason)}</div>` : ''}${(r.ref || {}).proposition ? `<div class="br-prop">${escapeHtml((r.ref || {}).proposition.slice(0, 220))}</div>` : ''}</td>
      <td class="${statusClass}">${escapeHtml(quoteStatus)}${(r.ref || {}).quote ? `<div class="br-quote">“${escapeHtml((r.ref || {}).quote.slice(0, 180))}”</div>` : ''}</td>
      <td>${c.id ? `<button type="button" class="details-toggle" data-cluster="${escapeHtml(String(c.id))}" data-focus="${escapeHtml((r.ref || {}).quote || (r.ref || {}).context || (r.ref || {}).text || '')}">${escapeHtml(tr('label.details'))}</button><div class="case-details" style="display:none"></div>` : ''}</td>
    </tr>`;
  }).join('');
  panel.innerHTML = `
    <div class="auth-head">${escapeHtml(tr('brief.title'))} <span class="cnt">${escapeHtml(tr('brief.refs', { count: rows.length, plural: plural(rows.length) }))}</span></div>
    <div class="brief-table-wrap"><table class="brief-table">
      <thead><tr><th></th><th>${escapeHtml(tr('brief.extracted'))}</th><th>${escapeHtml(tr('brief.resolved'))}</th><th>${escapeHtml(tr('label.status'))}</th><th>${escapeHtml(tr('brief.quoteCheck'))}</th><th>${escapeHtml(tr('brief.source'))}</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
}

function supportClass(status) {
  const s = String(status || '').toLowerCase();
  if (s.includes('supports')) return 'support-ok';
  if (s.includes('weak')) return 'support-weak';
  if (s.includes('unresolved') || s.includes('not found')) return 'support-bad';
  return 'support-review';
}

function renderCitationExtract(turnEl, payload) {
  let panel = turnEl.querySelector('.citation-extract');
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'citation-extract';
    turnEl.querySelector('.answer')?.insertAdjacentElement('beforebegin', panel);
  }
  const refs = payload.refs || [];
  const rows = refs.map((r, i) => `<tr><td><span class="br-num">${i + 1}</span></td><td><div class="br-ref">${escapeHtml(r.text || '')}</div><div class="br-kind">${escapeHtml(r.kind || '')}</div></td><td>${escapeHtml((r.context || '').slice(0, 260))}</td></tr>`).join('');
  panel.innerHTML = `<div class="auth-head">${escapeHtml(tr('extract.title'))} <span class="cnt">${escapeHtml(tr('brief.refs', { count: refs.length, plural: plural(refs.length) }))}</span></div>
    ${refs.length ? `<div class="brief-table-wrap"><table class="brief-table"><thead><tr><th></th><th>${escapeHtml(tr('extract.reference'))}</th><th>${escapeHtml(tr('extract.context'))}</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="answer note">${escapeHtml(tr('extract.none'))}</div>`}`;
}

function renderResearchPlan(turnEl, plan) {
  let panel = turnEl.querySelector('.research-plan');
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'research-plan';
    turnEl.querySelector('.authorities')?.insertAdjacentElement('beforebegin', panel);
  }
  const issues = plan.issues || [];
  panel.innerHTML = `<div class="auth-head">${escapeHtml(tr('plan.title'))} <span class="cnt">${escapeHtml(tr('plan.issues', { count: issues.length, plural: plural(issues.length) }))}</span></div>
    <div class="plan-box"><p>${escapeHtml(plan.summary || tr('plan.defaultSummary'))}</p>
    ${issues.length ? `<ol>${issues.map((x) => `<li><b>${escapeHtml(x.label || tr('plan.issue'))}</b><span>${escapeHtml(x.query || '')}</span></li>`).join('')}</ol>` : ''}
    ${(plan.depends_on || []).length ? `<div class="depends"><b>${escapeHtml(tr('plan.dependsOn'))}</b> ${escapeHtml((plan.depends_on || []).join('; '))}</div>` : ''}</div>`;
}

function renderStatutes(listEl, turnId, statutes) {
  listEl.innerHTML = '';
  statutes.forEach((s, i) => {
    const card = document.createElement('div');
    card.className = 'statute';
    card.id = `statute-${turnId}-${i + 1}`;
    card.innerHTML = `
      <div class="row1"><span class="rnum">R${i + 1}</span><span class="title">${escapeHtml(s.citation)}</span><span class="verified">${escapeHtml(tr('label.verified'))}</span></div>
      ${s.heading ? `<div class="meta">${escapeHtml(s.heading)}</div>` : ''}
      ${s.excerpt ? `<div class="snip">${escapeHtml(s.excerpt.slice(0, 260))}…</div>` : ''}
      ${s.url ? `<a class="open" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(tr('label.openRegulation'))}</a>` : ''}`;
    listEl.appendChild(card);
  });
}

// Click a [n] / [Rn] reference -> highlight the matching authority card.
chat.addEventListener('click', (e) => {
  const ref = e.target.closest('.cite-ref');
  if (!ref) return;
  const prefix = ref.dataset.kind === 'statute' ? 'statute' : 'case';
  const card = document.getElementById(`${prefix}-${ref.dataset.turn}-${ref.dataset.n}`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.style.borderColor = 'var(--accent)';
    setTimeout(() => { card.style.borderColor = ''; }, 1200);
  }
});

// Case metadata / opinion inventory / PDF links.
chat.addEventListener('click', async (e) => {
  const btn = e.target.closest('.details-toggle');
  if (!btn) return;
  const box = btn.parentElement.querySelector('.case-details');
  const clusterId = btn.dataset.cluster || '';
  const focus = btn.dataset.focus || '';
  if (!box || !clusterId) return;
  if (box.style.display !== 'none' && box.innerHTML) {
    box.style.display = 'none';
    return;
  }
  box.style.display = '';
  box.className = 'case-details loading';
  box.textContent = tr('label.loadingDetails');
  try {
    const qs = new URLSearchParams();
    if (focus) qs.set('focus', focus);
    qs.set('language', currentLang);
    const resp = await api('/api/case-details/' + encodeURIComponent(clusterId) + '?' + qs.toString());
    if (resp.status === 401) { me = null; renderAccount(); openLoginModal(); box.textContent = ''; return; }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const details = await resp.json();
    box.className = 'case-details';
    renderCaseDetailsBox(box, details);
  } catch {
    box.className = 'case-details vr-warn';
    box.textContent = tr('label.detailsFailed');
  }
});

chat.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-cite');
  if (!btn) return;
  const cite = btn.dataset.cite || '';
  if (!cite || !navigator.clipboard) return;
  navigator.clipboard.writeText(cite).then(() => {
    btn.textContent = tr('label.copiedShort');
    setTimeout(() => { btn.textContent = tr('label.copyCitation'); }, 1200);
  }).catch(() => {});
});

// Copy a turn's reasoning to the clipboard.
chat.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-btn');
  if (!btn) return;
  const ans = btn.closest('.turn')?.querySelector('.answer');
  const text = ans ? ans.innerText.trim() : '';
  if (!text || !navigator.clipboard) return;
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied'); btn.textContent = tr('label.copied');
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = tr('label.copy'); }, 1500);
  }).catch(() => {});
});

// Export a turn (answer + table of authorities) as a Word-openable .doc file.
chat.addEventListener('click', (e) => {
  const btn = e.target.closest('.export-btn');
  if (!btn) return;
  const turn = btn.closest('.turn');
  if (!turn) return;
  const answerHtml = turn.querySelector('.answer')?.innerHTML || '';
  // Collect the cited authorities into a clean list.
  const auth = [...turn.querySelectorAll('.case')].map((c) => {
    const title = c.querySelector('.title')?.textContent?.trim() || '';
    const meta = c.querySelector('.meta')?.textContent?.trim() || '';
    const cites = c.querySelector('.cites')?.textContent?.trim() || '';
    const url = c.querySelector('.open')?.getAttribute('href') || '';
    return `<li><strong>${escapeHtml(title)}</strong><br/>${escapeHtml(meta)}` +
           (cites ? `<br/><em>${escapeHtml(cites)}</em>` : '') +
           (url ? `<br/><a href="${escapeHtml(url)}">${escapeHtml(url)}</a>` : '') + `</li>`;
  }).join('');
  const stamp = new Date().toISOString().slice(0, 10);
  const doc = `<!DOCTYPE html><html xmlns:o="urn:schemas-microsoft-com:office:office" `
    + `xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">`
    + `<head><meta charset="utf-8"><title>JuriCodex Research</title></head><body>`
    + `<h1 style="font-family:Georgia,serif">${escapeHtml(tr('export.title'))}</h1>`
    + `<p style="color:#666;font-size:12px">${escapeHtml(tr('export.generated', { stamp }))}</p>`
    + `<div style="font-family:Georgia,serif;font-size:14px;line-height:1.6">${answerHtml}</div>`
    + (auth ? `<h2 style="font-family:Georgia,serif">${escapeHtml(tr('export.authorities'))}</h2><ol style="font-family:Georgia,serif;font-size:13px">${auth}</ol>` : '')
    + `<hr/><p style="color:#888;font-size:11px">${escapeHtml(tr('export.disclaimer'))}</p>`
    + `</body></html>`;
  const blob = new Blob(['\ufeff', doc], { type: 'application/msword' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `juricodex-research-${stamp}.doc`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  btn.textContent = tr('label.exported');
  setTimeout(() => { btn.textContent = tr('label.export'); }, 1500);
});

// Quote verification: confirm a quote really appears in a case's opinion text.
chat.addEventListener('click', async (e) => {
  const toggle = e.target.closest('.verify-toggle');
  if (toggle) {
    const box = toggle.parentElement.querySelector('.verify-box');
    if (box) box.style.display = box.style.display === 'none' ? '' : 'none';
    return;
  }
  const run = e.target.closest('.verify-run');
  if (!run) return;
  const wrap = run.closest('.verify');
  const clusterId = wrap?.dataset.cluster || '';
  const quote = wrap?.querySelector('.verify-q')?.value.trim() || '';
  const out = wrap?.querySelector('.verify-result');
  if (!out) return;
  if (quote.length < 6) { out.className = 'verify-result vr-miss'; out.textContent = tr('verify.shortQuote'); return; }
  run.disabled = true; out.className = 'verify-result'; out.textContent = tr('verify.checking');
  try {
    const resp = await api('/api/verify-quote', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cluster_id: clusterId, quote }),
    });
    if (resp.status === 401) { me = null; renderAccount(); openLoginModal(); out.textContent = ''; return; }
    if (resp.status === 429) {
      let info = {}; try { info = await resp.json(); } catch { /* ignore */ }
      out.className = 'verify-result vr-warn';
      out.textContent = '⚠ ' + (info.message || tr('verify.rateLimited'));
      return;
    }
    const r = await resp.json();
    if (r.found) {
      out.className = 'verify-result vr-ok';
      out.innerHTML = `<strong>${escapeHtml(tr('verify.found'))}</strong> (${escapeHtml(r.match)} match).` +
        (r.context ? `<div class="vr-ctx">…${escapeHtml(r.context)}…</div>` : '');
    } else if (r.match === 'no_text') {
      out.className = 'verify-result vr-warn';
      out.textContent = tr('verify.noText');
    } else if (r.partial) {
      // We couldn't search every sub-opinion, so "not found" isn't conclusive.
      out.className = 'verify-result vr-warn';
      out.textContent = tr('verify.partial', { searched: r.opinions_searched, total: r.opinions_total });
    } else {
      out.className = 'verify-result vr-miss';
      out.textContent = tr('verify.notFound');
    }
  } catch {
    out.className = 'verify-result vr-warn';
    out.textContent = tr('verify.failed');
  } finally {
    run.disabled = false;
  }
});

async function send(text) {
  if (busy || !text.trim()) return;
  // Require sign-in before asking. The whole research flow (LLM + retrieval) is
  // gated behind an account, so a signed-out visitor who tries to ask is shown
  // the sign-in dialog instead. Wait for the initial auth check if it's still
  // in flight so we don't prompt a user who actually has a valid session.
  if (!authReady && authPromise) { try { await authPromise; } catch { /* ignore */ } }
  if (!me) { openLoginModal(text); return; }
  busy = true; sendBtn.disabled = true;
  document.querySelector('.intro')?.remove();

  addUser(text);
  pushHistory(text);
  messages.push({ role: 'user', content: text });

  const t = newBotTurn();
  let answerRaw = '';
  let clarified = '';

  // Abort the stream if it stalls (no bytes) for too long, so a wedged
  // connection surfaces as a clear error instead of an endless spinner. The
  // timer is pushed forward on every chunk received.
  const STREAM_IDLE_MS = 60000;
  const ctrl = new AbortController();
  let idleTimer = setTimeout(() => ctrl.abort('timeout'), STREAM_IDLE_MS);
  const bumpIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => ctrl.abort('timeout'), STREAM_IDLE_MS);
  };

  try {
    const resp = await fetch(API_BASE + '/api/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, mode: currentMode, language: currentLang }),
      signal: ctrl.signal,
    });
    // Session expired (or never signed in): drop back to the sign-in gate and
    // re-queue this question so it's ready after they authenticate.
    if (resp.status === 401) {
      me = null; renderAccount();
      t.el.remove();
      messages.pop();
      busy = false; sendBtn.disabled = false;
      openLoginModal(text);
      return;
    }
    // Monthly quota used up: show the upgrade dialog instead of an error.
    if (resp.status === 402) {
      let info = {};
      try { info = await resp.json(); } catch { /* ignore */ }
      t.el.remove();
      messages.pop();
      busy = false; sendBtn.disabled = false;
      openUpgradeModal(info);
      return;
    }
    // Rate limited (too many requests too fast): ask them to slow down rather
    // than burning the turn or showing a raw error.
    if (resp.status === 429) {
      let info = {};
      try { info = await resp.json(); } catch { /* ignore */ }
      clearTimeout(idleTimer);
      setStep(t.el, 'analyze', 'done');
      t.statusEl.style.display = 'none';
      t.answerEl.style.display = '';
      t.answerEl.className = 'answer note';
      t.answerEl.textContent = info.message
        || tr('rateLimited');
      messages.pop();
      busy = false; sendBtn.disabled = false;
      return;
    }
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bumpIdle();
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
          if (/^(search|refining|resolving|verifying|buscando|afinando|resolviendo|verificando|recherche|résolution|vérification|buscando|refinando|resolvendo|verificando|검색|해석|검증|検索|調整|確認|Tìm|Đang tìm|Đang phân giải|Đang kiểm tra|正在搜索|正在搜尋|正在优化|正在最佳化|正在把|正在驗證|正在验证)/i.test(obj.message || '')) setStep(t.el, 'search', 'active', true);
          else setStep(t.el, 'analyze', 'active');
        } else if (ev === 'research_plan') {
          setStep(t.el, 'analyze', 'done');
          setStep(t.el, 'search', 'active');
          renderResearchPlan(t.el, obj);
          scrollDown();
        } else if (ev === 'clarify') {
          clarified = obj.question || '';
          setStep(t.el, 'analyze', 'done');
          t.statusEl.style.display = 'none';
          t.answerEl.style.display = '';
          t.answerEl.className = 'answer clarify';
          t.answerEl.textContent = obj.question || '';
        } else if (ev === 'cases') {
          const authoritySuffix = currentLang === 'en'
            ? (obj.count > 1 ? 'ies' : 'y')
            : (currentLang === 'es' ? (obj.count === 1 ? '' : 'es') : '');
          t.statusText.textContent = obj.count
            ? tr('cases.found', { count: obj.count, suffix: authoritySuffix, query: obj.query })
            : tr('cases.none', { query: obj.query });
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
            renderStatutes(t.statListEl, t.turnId, obj.statutes || []);
          }
          scrollDown();
        } else if (ev === 'brief_review') {
          setStep(t.el, 'authorities', 'done', true);
          setStep(t.el, 'answer', 'active');
          renderBriefReview(t.el, obj);
          scrollDown();
        } else if (ev === 'citation_extract') {
          setStep(t.el, 'authorities', 'done', true);
          setStep(t.el, 'answer', 'active');
          renderCitationExtract(t.el, obj);
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
          t.answerEl.textContent = obj.message || tr('error.generic');
        } else if (ev === 'warning') {
          // Non-fatal advisory (e.g. a citation-integrity check). Show it as a
          // distinct caution note above/with the answer without replacing it.
          let warn = t.el.querySelector('.answer-warn');
          if (!warn) {
            warn = document.createElement('div');
            warn.className = 'answer-warn';
            t.answerEl.insertAdjacentElement('beforebegin', warn);
          }
          warn.textContent = tr('warning.prefix', { message: obj.message || tr('warning.default') });
        } else if (ev === 'done') {
          clearTimeout(idleTimer);
          if (!clarified) setStep(t.el, 'answer', 'done', true);
          t.statusEl.style.display = 'none';
          // Final pass: render the streamed answer as Markdown and expose Copy.
          if (answerRaw.trim()) {
            t.answerEl.className = 'answer rendered';
            t.answerEl.innerHTML = renderMarkdown(answerRaw, t.turnId);
            t.actionsEl.style.display = '';
          }
        }
      }
    }
  } catch (err) {
    clearTimeout(idleTimer);
    t.statusEl.style.display = 'none';
    t.answerEl.style.display = '';
    t.answerEl.className = 'answer note';
    const aborted = err && (err.name === 'AbortError' || ctrl.signal.aborted);
    t.answerEl.textContent = aborted
      ? tr('timeout')
      : tr('connectionError', { message: err && err.message ? err.message : 'please try again.' });
  } finally {
    clearTimeout(idleTimer);
  }

  messages.push({ role: 'assistant', content: clarified || answerRaw || tr('assistant.fallback') });
  busy = false; sendBtn.disabled = false;
  // Persist the thread to the account when signed in (durable, cross-device).
  autosaveSession();
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

// ── Examples + New research ──────────────────────────────────────
function bindExamples() {
  document.querySelectorAll('.ex').forEach((b) =>
    b.addEventListener('click', () => { currentMode = 'chat'; send(b.textContent); }));
}
bindExamples();

// Start a fresh research session: clear the conversation and restore the intro.
function newResearch() {
  if (busy) return;
  messages.length = 0;
  turnSeq = 0;
  currentMode = 'chat';
  currentSessionId = null;
  chat.innerHTML = INTRO_HTML;
  bindExamples();
  applyI18n();
  input.value = ''; input.style.height = 'auto';
  input.focus();
}

// ── Sidebar: mobile drawer ───────────────────────────────────────
const app = document.querySelector('.app');
function closeNav() { app.classList.remove('nav-open'); }
document.getElementById('hamburger')?.addEventListener('click', () => app.classList.toggle('nav-open'));
app.addEventListener('click', (e) => {
  if (app.classList.contains('nav-open') && !e.target.closest('.sidebar') && !e.target.closest('.hamburger')) closeNav();
});

// ── Account + research history (backend when signed in, else localStorage) ──
// Signed-in users get durable, cross-device research history stored on their
// account; signed-out users get a local-only recent list (localStorage). The
// account block offers OAuth sign-in (only the providers the server has
// configured) and sign-out.
const HKEY = 'leagle-history';
const historyEl = document.getElementById('history');
const accountEl = document.getElementById('account');
let me = null;                 // current signed-in user, or null
let providers = [];            // configured OAuth providers, e.g. ['github']
let currentSessionId = null;   // backend id of the active thread (when signed in)
let authReady = false;         // true once /api/auth/me has resolved
let authPromise = null;        // the in-flight initial auth load
let billingCfg = null;         // { product_id, public_key, plans } or null
let planInfo = null;           // { plan, limit, used, remaining } for current user

document.getElementById('langSelect')?.addEventListener('change', (e) => setLanguage(e.target.value));
applyI18n();

function api(path, opts = {}) {
  return fetch(API_BASE + path, { credentials: 'include', ...opts });
}

const PROVIDER_LABEL = { github: 'GitHub', google: 'Google', x: 'X' };
const PROVIDER_ICON = { github: '⌥', google: '◉', x: '𝕏' };

function renderAccount() {
  if (!accountEl) return;
  if (me) {
    const initial = (me.name || me.email || '?').trim().charAt(0).toUpperCase();
    const avatarSrc = safeUrl(me.avatar_url);
    const avatar = avatarSrc
      ? `<img class="acc-avatar" src="${escapeHtml(avatarSrc)}" alt="" referrerpolicy="no-referrer" />`
      : `<span class="acc-avatar acc-initial">${escapeHtml(initial)}</span>`;
    const planName = (planInfo && planInfo.plan) || (me.plan || 'free');
    const usage = planInfo
      ? tr('account.usage', { used: planInfo.used, limit: planInfo.limit >= 100000 ? '∞' : planInfo.limit })
      : tr('account.plan', { plan: escapeHtml(planName) });
    const upgrade = (billingCfg && planName === 'free')
      ? `<button class="acc-upgrade" id="upgradeBtn">${escapeHtml(tr('account.upgrade'))}</button>` : '';
    const manage = (billingCfg && planName !== 'free')
      ? `<button class="acc-manage" id="manageBillingBtn">${escapeHtml(tr('account.manage'))}</button>` : '';
    const emailWarn = !hasBillingEmail()
      ? `<div class="acc-email-warn">${escapeHtml(tr('account.emailWarn'))}</div>`
      : '';
    accountEl.innerHTML = `
      <div class="acc-user">
        ${avatar}
        <div class="acc-meta">
          <div class="acc-name">${escapeHtml(me.name || me.email || tr('account.signedIn'))}</div>
          <div class="acc-plan">${escapeHtml(planName)} · ${usage}</div>
        </div>
        <button class="acc-logout" id="logoutBtn" title="${escapeHtml(tr('account.signOutTitle'))}">${escapeHtml(tr('account.signOut'))}</button>
      </div>
      ${emailWarn}
      ${upgrade}
      ${manage}`;
    document.getElementById('logoutBtn').addEventListener('click', logout);
    document.getElementById('upgradeBtn')?.addEventListener('click', () => openUpgradeModal());
    document.getElementById('manageBillingBtn')?.addEventListener('click', openBillingPortal);
  } else if (providers.length) {
    const btns = providers.map((p) =>
      `<button class="acc-signin" data-provider="${p}">
         <span class="acc-ico">${PROVIDER_ICON[p] || '◉'}</span>
         ${escapeHtml(tr('account.signIn', { provider: PROVIDER_LABEL[p] || p }))}
       </button>`).join('');
    accountEl.innerHTML = `
      <div class="acc-signin-wrap">
        <div class="acc-hint">${escapeHtml(tr('account.hint'))}</div>
        ${btns}
      </div>`;
    accountEl.querySelectorAll('.acc-signin').forEach((b) =>
      b.addEventListener('click', () => {
        location.href = `${API_BASE}/api/auth/${b.dataset.provider}/start?next=${selfNext()}`;
      }));
  } else {
    accountEl.innerHTML = '';
  }
}

// ── Sign-in gate modal (shown when a signed-out visitor tries to ask) ──
const loginModal = document.getElementById('loginModal');

function openLoginModal(pendingText) {
  if (!loginModal) return;
  // Stash the question so it's waiting in the composer after the OAuth round-trip.
  if (pendingText) { try { sessionStorage.setItem('leagle-pending-q', pendingText); } catch { /* ignore */ } }
  const body = loginModal.querySelector('.login-body');
  if (providers.length) {
    body.innerHTML = providers.map((p) =>
      `<button class="login-provider" data-provider="${p}">
         <span class="acc-ico">${PROVIDER_ICON[p] || '◉'}</span>
         ${escapeHtml(tr('login.continue', { provider: PROVIDER_LABEL[p] || p }))}
       </button>`).join('');
    body.querySelectorAll('.login-provider').forEach((b) =>
      b.addEventListener('click', () => {
        location.href = `${API_BASE}/api/auth/${b.dataset.provider}/start?next=${selfNext()}`;
      }));
  } else {
    body.innerHTML = `<div class="login-hint">${escapeHtml(tr('login.unavailable'))}</div>`;
  }
  loginModal.classList.add('open');
  loginModal.setAttribute('aria-hidden', 'false');
}

function closeLoginModal() {
  if (!loginModal) return;
  loginModal.classList.remove('open');
  loginModal.setAttribute('aria-hidden', 'true');
}

document.getElementById('loginClose')?.addEventListener('click', closeLoginModal);
loginModal?.addEventListener('click', (e) => { if (e.target === loginModal) closeLoginModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLoginModal(); });

// ── Upgrade / pricing modal + Freemius checkout ─────────────────────────────
const upgradeModal = document.getElementById('upgradeModal');
let fsLoading = null;
let billingCycle = 'monthly';

function loadScriptOnce(src, id, ready) {
  if (ready && ready()) return Promise.resolve();
  const existing = document.querySelector(`script[data-loader-id="${id}"]`);
  if (existing && existing.dataset.loaded === '1') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = existing || document.createElement('script');
    script.dataset.loaderId = id;
    const cleanup = () => {
      script.removeEventListener('load', onLoad);
      script.removeEventListener('error', onError);
    };
    const onLoad = () => {
      script.dataset.loaded = '1';
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error(`Failed to load ${src}`));
    };
    script.addEventListener('load', onLoad, { once: true });
    script.addEventListener('error', onError, { once: true });
    if (!existing) {
      script.src = src;
      document.head.appendChild(script);
    }
  }).then(() => {
    if (ready && !ready()) throw new Error(`${id} did not initialize`);
  });
}

function loadFreemius() {
  if (window.FS && window.FS.Checkout) return Promise.resolve();
  if (fsLoading) return fsLoading;
  fsLoading = (async () => {
    // Freemius checkout.min.js expects a global jQuery on some builds.
    await loadScriptOnce('https://code.jquery.com/jquery-3.7.1.min.js', 'jquery', () => !!window.jQuery);
    await loadScriptOnce('https://checkout.freemius.com/checkout.min.js', 'freemius-checkout',
      () => !!(window.FS && window.FS.Checkout));
  })();
  return fsLoading;
}

const PLAN_BLURB = {
  pro: {
    name: 'Pro', monthly: '$9.98/mo', yearly: '$99.80/yr',
    pitch: '300 source-backed research runs, verification, history, and export.',
  },
  max: {
    name: 'Max', monthly: '$29.98/mo', yearly: '$299.80/yr',
    pitch: 'High-volume workspace for Brief Review, quote checks, export, and saved sessions.',
    featured: true,
  },
  day_pass: {
    name: '3-Day Pass', monthly: '$2.98', yearly: '$2.98',
    pitch: 'Try Max-level access for 3 days. No subscription.', oneoff: true,
  },
};

function hasBillingEmail() {
  const e = String((me && me.email) || '').trim();
  return !!e && !/@users\.juricodex\.online$/i.test(e);
}

function openUpgradeModal(quota) {
  if (!upgradeModal) return;
  if (!billingCfg) {        // billing not configured — nothing to sell yet
    return;
  }
  const sub = quota && quota.limit
    ? `<p class="up-quota">${escapeHtml(tr('upgrade.quota', { limit: quota.limit }))}</p>`
    : '';
  const emailNote = !hasBillingEmail()
    ? `<p class="up-quota" style="color:var(--warn)">${escapeHtml(tr('upgrade.emailNote'))}</p>`
    : '';
  const cycle = billingCycle === 'annual' ? 'annual' : 'monthly';
  const cycleTabs = `<div class="billing-cycle" role="tablist" aria-label="Billing cycle">
      <button type="button" class="cycle-btn ${cycle === 'monthly' ? 'active' : ''}" data-cycle="monthly">${escapeHtml(tr('upgrade.monthly'))}</button>
      <button type="button" class="cycle-btn ${cycle === 'annual' ? 'active' : ''}" data-cycle="annual">${escapeHtml(tr('upgrade.yearly'))} <span class="cycle-save">${escapeHtml(tr('upgrade.save'))}</span></button>
    </div>`;
  const planOrder = ['day_pass', 'pro', 'max'];
  const planEntries = planOrder
    .filter((p) => (billingCfg.plans || {})[p])
    .map((p) => [p, billingCfg.plans[p]]);
  const cards = planEntries.map(([ourPlan, planId]) => {
    const b = PLAN_BLURB[ourPlan] || { name: ourPlan, price: '', pitch: '' };
    const pricingId = (billingCfg.pricing || {})[ourPlan] || '';
    const price = b.oneoff ? b.monthly : (cycle === 'annual' ? b.yearly : b.monthly);
    const cycleLabel = b.oneoff ? tr('upgrade.oneTime') : (cycle === 'annual' ? tr('upgrade.annualPlan') : tr('upgrade.monthlyPlan'));
    return `<button class="up-plan ${b.featured ? 'up-featured' : ''}" data-plan-id="${escapeHtml(planId)}" data-pricing-id="${escapeHtml(pricingId)}" data-cycle="${b.oneoff ? '' : cycle}">
        <span class="up-plan-name">${escapeHtml(tr(`plan.${ourPlan}.name`))}</span>
        <span class="up-plan-price">${escapeHtml(price)}</span>
        <span class="up-plan-cycle">${escapeHtml(cycleLabel)}</span>
        <span class="up-plan-pitch">${escapeHtml(tr(`plan.${ourPlan}.pitch`) || b.pitch)}</span>
      </button>`;
  }).join('');
  const billingNote = `<div class="up-note">${tr('upgrade.note')}</div>`;
  upgradeModal.querySelector('.up-body').innerHTML = sub + emailNote + cycleTabs + cards + billingNote;
  upgradeModal.querySelectorAll('.cycle-btn').forEach((b) =>
    b.addEventListener('click', () => { billingCycle = b.dataset.cycle || 'monthly'; openUpgradeModal(quota); }));
  upgradeModal.querySelectorAll('.up-plan').forEach((b) =>
    b.addEventListener('click', () => startCheckout(b.dataset.planId, b.dataset.pricingId, b.dataset.cycle, b)));
  upgradeModal.classList.add('open');
  upgradeModal.setAttribute('aria-hidden', 'false');
}

function closeUpgradeModal() {
  if (!upgradeModal) return;
  upgradeModal.classList.remove('open');
  upgradeModal.setAttribute('aria-hidden', 'true');
}

async function startCheckout(planId, pricingId, cycle, button) {
  if (!billingCfg) return;
  if (!hasBillingEmail()) {
    showToast(tr('toast.emailRequired'));
    return;
  }
  if (button) {
    button.disabled = true;
    button.classList.add('loading');
    button.setAttribute('aria-busy', 'true');
  }
  showToast(tr('toast.checkoutOpening'), 1400);
  try {
    await loadFreemius();
    if (!window.FS || !window.FS.Checkout || !window.FS.Checkout.configure || !window.FS.Checkout.open) {
      throw new Error('Freemius checkout did not initialize');
    }
    window.FS.Checkout.configure({
      plugin_id: billingCfg.product_id,
      public_key: billingCfg.public_key,
    });
    const opts = {
      plan_id: planId,
      pricing_id: pricingId || undefined,
      billing_cycle: cycle || undefined,
      sandbox: !!billingCfg.sandbox,
      name: 'JuriCodex',
      // Do not prefill user_email: Freemius can treat merely opening checkout
      // as an abandoned cart and email the signed-in address if checkout is not completed.
      purchaseCompleted: () => {
        // Freemius confirms purchase; the webhook flips our DB. Re-pull our state.
        setTimeout(() => loadAuth(), 1500);
      },
      success: () => { closeUpgradeModal(); },
    };
    window.FS.Checkout.open(opts);
  } catch (err) {
    console.error('Freemius checkout failed', err);
    showToast(tr('toast.checkoutFallback'), 2200);
    location.href = hostedCheckoutUrl(planId, pricingId, cycle);
  } finally {
    if (button) {
      button.disabled = false;
      button.classList.remove('loading');
      button.removeAttribute('aria-busy');
    }
  }
}

function hostedCheckoutUrl(planId, pricingId, cycle) {
  const productId = encodeURIComponent(billingCfg.product_id);
  const url = new URL(`https://checkout.freemius.com/product/${productId}/plan/${encodeURIComponent(planId)}/`);
  if (pricingId) url.searchParams.set('pricing_id', pricingId);
  if (cycle) url.searchParams.set('billing_cycle', cycle);
  if (billingCfg.sandbox) url.searchParams.set('sandbox', 'true');
  url.searchParams.set('name', 'JuriCodex');
  return url.toString();
}

function openBillingPortal() {
  const url = billingCfg && safeUrl(billingCfg.portal_url);
  if (url) {
    window.open(url, '_blank', 'noopener');
    return;
  }
  const email = (billingCfg && billingCfg.support_email) || 'support@juricodex.online';
  showToast(tr('toast.billingPortal'));
  location.href = `mailto:${email}?subject=${encodeURIComponent('Manage JuriCodex billing')}`;
}

document.getElementById('upgradeClose')?.addEventListener('click', closeUpgradeModal);
upgradeModal?.addEventListener('click', (e) => { if (e.target === upgradeModal) closeUpgradeModal(); });

async function loadAuth() {
  try {
    const [meResp, provResp, cfgResp] = await Promise.all([
      api('/api/auth/me'),
      api('/api/auth/providers'),
      api('/api/config'),
    ]);
    me = meResp.ok ? await meResp.json() : null;
    providers = provResp.ok ? (await provResp.json()).providers || [] : [];
    if (cfgResp.ok) {
      const cfg = await cfgResp.json();
      billingCfg = cfg.billing ? cfg.freemius : null;
      planInfo = cfg.me || null;
    }
  } catch {
    me = null; providers = [];
  }
  authReady = true;
  renderAccount();
  refreshHistory();
}

async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
  me = null; currentSessionId = null;
  renderAccount();
  refreshHistory();
}

// Local (signed-out) history helpers.
function loadLocal() { try { return JSON.parse(localStorage.getItem(HKEY)) || []; } catch { return []; } }
function saveLocal(arr) { try { localStorage.setItem(HKEY, JSON.stringify(arr.slice(0, 30))); } catch (e) { /* ignore */ } }

function pushHistory(q) {
  if (me) return;            // signed-in history is saved server-side via autosave
  const arr = loadLocal().filter((x) => x.q !== q);
  arr.unshift({ q, t: Date.now() });
  saveLocal(arr);
  renderHistory();
}

async function refreshHistory() {
  if (me) {
    let sessions = [];
    try {
      const r = await api('/api/sessions');
      if (r.ok) sessions = (await r.json()).sessions || [];
    } catch { /* ignore */ }
    renderHistory(sessions);
  } else {
    renderHistory();
  }
}

function renderHistory(sessions) {
  historyEl.innerHTML = '';
  if (me) {
    (sessions || []).forEach((s) => {
      const b = document.createElement('button');
      b.className = 'hist-item';
      b.title = s.title;
      b.innerHTML = `<span class="ht-type">Research</span>${escapeHtml(s.title)}`;
      b.addEventListener('click', () => { closeNav(); openSession(s.id); });
      historyEl.appendChild(b);
    });
  } else {
    loadLocal().forEach((x) => {
      const b = document.createElement('button');
      b.className = 'hist-item';
      b.title = x.q;
      b.innerHTML = `<span class="ht-type">Research</span>${escapeHtml(x.q)}`;
      b.addEventListener('click', () => { closeNav(); currentMode = 'chat'; send(x.q); });
      historyEl.appendChild(b);
    });
  }
}

// Auto-save the active thread to the account after each completed turn.
async function autosaveSession() {
  if (!me || !messages.length) return;
  const title = (messages.find((m) => m.role === 'user') || {}).content || 'Research';
  try {
    const r = await api('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: currentSessionId, title: title.slice(0, 120), payload: messages }),
    });
    if (r.ok) { currentSessionId = (await r.json()).id; refreshHistory(); }
  } catch { /* ignore */ }
}

// Open a saved thread: load its transcript and render it read-only.
async function openSession(sessionId) {
  if (busy) return;
  let data;
  try {
    const r = await api('/api/sessions/' + sessionId);
    if (!r.ok) return;
    data = await r.json();
  } catch { return; }
  messages.length = 0;
  turnSeq = 0;
  currentSessionId = sessionId;
  currentMode = 'chat';
  chat.innerHTML = '';
  (data.payload || []).forEach((m) => {
    if (m.role === 'user') {
      addUser(m.content);
      messages.push({ role: 'user', content: m.content });
    } else if (m.role === 'assistant') {
      const el = document.createElement('div');
      el.className = 'turn bot';
      el.dataset.turn = ++turnSeq;
      el.innerHTML = `<div class="answer rendered">${renderMarkdown(m.content, turnSeq)}</div>`;
      chat.appendChild(el);
      messages.push({ role: 'assistant', content: m.content });
    }
  });
  scrollDown();
  input.focus();
}

authPromise = loadAuth();

// ── Cookie consent banner ──────────────────────────────────────────────
// We only set one essential auth cookie, but show a clear notice (and record
// the choice) so EU/CA visitors get an explicit, dismissible disclosure.
(function cookieConsent() {
  const KEY = 'leagle-cookie-consent';
  const banner = document.getElementById('cookieBanner');
  if (!banner) return;
  let decided = '';
  try { decided = localStorage.getItem(KEY) || ''; } catch { /* ignore */ }
  if (decided) return;
  banner.classList.add('open');
  const close = (choice) => {
    try { localStorage.setItem(KEY, choice); } catch { /* ignore */ }
    banner.classList.remove('open');
  };
  document.getElementById('cookieAccept')?.addEventListener('click', () => close('accepted'));
  document.getElementById('cookieDecline')?.addEventListener('click', () => close('essential'));
})();

// If we just came back from a failed/cancelled OAuth round-trip the backend
// redirects to /?auth_error=1 — surface a friendly message and clean the URL.
try {
  const params = new URLSearchParams(location.search);
  if (params.get('auth_error')) {
    params.delete('auth_error');
    const qs = params.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    showToast(tr('toast.authFailed'));
  }
} catch { /* ignore */ }

// After an OAuth round-trip the user lands back here signed in; drop the
// question they were about to ask back into the composer so it's one tap to send.
try {
  const pending = sessionStorage.getItem('leagle-pending-q');
  if (pending) {
    sessionStorage.removeItem('leagle-pending-q');
    input.value = pending;
    input.dispatchEvent(new Event('input'));
    input.focus();
  }
} catch { /* ignore */ }

// ── Sidebar: nav items + research toolkit ────────────────────────
const TOOL_HINTS = {
  concept: 'placeholder.concept',
  keyword: 'placeholder.keyword',
  case: 'placeholder.case',
  citation: 'placeholder.citation',
  laws: 'placeholder.laws',
  extractor: 'placeholder.extractor',
  resolver: 'placeholder.resolver',
  brief: 'placeholder.brief',
};
document.querySelectorAll('.nav-item').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    // The leagleLM entry (data-action="new") starts a fresh reasoning session.
    if (b.dataset.action === 'new') { newResearch(); closeNav(); return; }
    const tool = b.dataset.tool;
    // Toolkit entry -> direct precise search in that mode.
    currentMode = (tool && TOOL_HINTS[tool]) ? tool : 'chat';
    if (tool && TOOL_HINTS[tool]) {
      input.placeholder = tr(TOOL_HINTS[tool]);
    } else {
      input.placeholder = tr('placeholder.default');
    }
    input.focus();
    closeNav();
  });
});
