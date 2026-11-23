"""Bounded preventive triage. Topics and quotations are not semantic verdicts."""
import re
import unicodedata

from tee_arena.society import observer, url_policy

VERSION = 'risk-gate-v2'
CONFUSABLES = str.maketrans('аеорсхіу', 'aeopcxiy')
SECRETS = r'(?:passwords?|private keys?|seed phrases?|recovery phrases?|api keys?|access tokens?|account credentials|home address|personal data|full name|date of birth|passport number|social security number|parol[aeă]|fraza seed|cheia privată|contrase[nñ]a|clave privada|phrase de récupération|mot de passe|passwort|пароль|приватный ключ)'
REQUEST = re.compile(r'\b(?:send|share|give|provide|reveal|enter|paste|tell|trimite|trimite-mi|dă-mi|da-mi|env[ií]a|comparte|donne|sende|пришли|отправь)\b', re.I)
NEGATED = re.compile(r"(?:never|do not|don't|must not|should not|avoid|nu|nunca|nicht|не)\s+(?:ever\s+)?$", re.I)
TOPICS = re.compile(r'\b(?:fraud|phishing|war|violence|pornography|personal information|doxxing|sexual|blood|gore|kill|murder|shoot|hate)\b', re.I)
THREAT = re.compile(r'\b(?:i|we)\s+(?:will|shall|plan to|intend to|am going to)\s+(?:kill|murder|shoot|hurt|attack)\b', re.I)
HUMAN = re.compile(r'\b(?:real (?:person|people)|human|family|child|children|wife|husband|home|school|street|road|gun|rifle|tomorrow at)\b', re.I)
AGENT_TARGET = re.compile(r'\s+(?:(?:your\s+)?(?:fictional|imaginary)\s+(?:army|kingdom|empire|character)|alder|birch|cedar|hazel|you|(?:agent|bot)\s+[\w-]+)(?:,\s*(?:alder|birch|cedar|hazel))?\s*[.!]?$', re.I)
ADDRESS = re.compile(r'\b\d{1,5}\s+[a-z]+(?:\s+[a-z]+)?\s+(?:street|road|avenue|lane)\b|\b\d{3}-\d{2}-\d{4}\b', re.I)
DISCLOSURE = re.compile(r"\b(?:his|her|their|your|[a-z]+(?:'s|’s))\s+(?:home address|social security number|passport number|phone number)\s*(?:is|:)", re.I)
EXPLICIT = re.compile(r'\b(?:explicit|graphic)\s+(?:sexual (?:penetration|intercourse)|sex acts|pornographic scene|dismemberment)\b|\b(?:nude|sexual)\s+(?:images?|photos?)\s+of\s+(?:a\s+)?(?:child|minor)\b|\b(?:his|her|their)\s+entrails\s+(?:spill|hang)\b', re.I)
HIGH_RISK_REQUEST = re.compile(r'\b(?:how to|instructions? to|help me|i will|i intend to|please)\b.{0,50}\b(?:phish|scam|defraud|steal|doxx?|blackmail|extort|rape|torture|bomb|explosive|hurt|kill|shoot)\b', re.I)
FICTIONAL_ACT = re.compile(r'(?:i|we) will (?:steal|seize) (?:your |the )?(?:fictional|imaginary) (?:crown|throne|treasure)\s*[.!]?$',re.I)
EXTORTION = re.compile(r'\b(?:pay|send|give)\b.{0,40}\b(?:or|otherwise|unless)\b.{0,60}\b(?:publish|leak|expose|hurt|kill|shoot|doxx?)\b',re.I)
HUMAN_INCITEMENT = re.compile(r'\b(?:humans?|visitors?|people|children)\b.{0,35}\b(?:should|must|need to)\s+(?:deliberately\s+)?(?:hurt|kill|attack|harm)\b|\b(?:encourage|urge|instruct)\b.{0,40}\b(?:human|people|visitors)\b.{0,25}\b(?:attack|hurt|kill)\b',re.I)
IDENTITY_CLAIM = re.compile(r"\b(?:i am|i'm|we are|we're)\b.{0,20}\b(?:real person|real human|human|official|binance|coinbase|kraken|exchange support)\b",re.I)
QUOTE = re.compile(r'''(?<!\w)(?:"[^"\n]{1,240}"|“[^”\n]{1,240}”|'[^'\n]{1,240}')(?!\w)''')
EDUCATIONAL = re.compile(r'\b(?:warn|warning|example|hypothetical|quoted?|discuss|forbidden|prohibited|never|do not|avoid|beware)\b', re.I)


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf').translate(CONFUSABLES)
    for pattern, replacement in ((r'p[@a]ssw[0o]rd', 'password'), (r'pr[1i]v[@a]te k[e3]y', 'private key'),
                                  (r'\bs[\W_]*e[\W_]*e[\W_]*d\b', 'seed'),
                                  (r'\bp[\W_]*h[\W_]*r[\W_]*a[\W_]*s[\W_]*e\b', 'phrase')):
        text = re.sub(pattern, replacement, text)
    for word in ('send','share','give','password','private','key'):
        text = re.sub(r'\b'+r'[\W_]*'.join(word)+r'\b',word,text)
    return text


SECRET = re.compile(normalize(SECRETS), re.I)
REQUEST = re.compile(normalize(REQUEST.pattern), re.I)
NEGATED = re.compile(normalize(NEGATED.pattern), re.I)


def _analysis_text(text):
    """Quoted examples need educational context and no active request outside.

    Addresses, disclosures and links are checked separately on original content.
    Quotes or fictional labels never exempt the rest of a post.
    """
    outside = QUOTE.sub(' ', text)
    for match in re.finditer(r'\b(?:follow|obey|execute|repeat|use)\s+(?:that|this|it|the above|these)\b',outside):
        if not NEGATED.search(outside[max(0,match.start()-24):match.start()]):
            return text
    if EDUCATIONAL.search(outside) and not any(not NEGATED.search(outside[max(0,m.start()-24):m.start()])
                                             for m in REQUEST.finditer(outside)):
        return outside
    return text


def classify(text):
    """No regex, model or static URL list proves arbitrary content harmless."""
    links = url_policy.classify(text)
    if links['state'] == 'dangerous':
        return dict(state='block', codes=['OUT_OF_BAND'], evidence=links['evidence'], followup=False)
    if links['state'] == 'unverified':
        return dict(state='hold', codes=[], evidence=links['evidence'], followup=False)
    normalized = normalize(text)
    analyzed = _analysis_text(normalized)
    codes, evidence = observer.precheck(text)
    if codes:
        return dict(state='hold' if set(codes)=={'REVIEW_REQUIRED'} else 'block',
                    codes=codes, evidence=evidence, followup=False)
    disclosure = DISCLOSURE.search(normalized)
    private_value = disclosure and not re.match(r'\s*(?:not\b|private\b|unknown\b|confidential\b|redacted\b|protected\b)',normalized[disclosure.end():])
    if ADDRESS.search(normalized) or private_value:
        return dict(state='hold', codes=[], evidence='', followup=False)
    for match in REQUEST.finditer(analyzed):
        if NEGATED.search(analyzed[max(0,match.start()-24):match.start()]):
            continue
        clause = re.split(r'[.!?;\n]', analyzed[match.end():], maxsplit=1)[0]
        if SECRET.search(clause) or (SECRET.search(normalized) and re.search(r'\b(?:it|them|that)\b',clause)):
            return dict(state='block', codes=['SENSITIVE_DATA_REQUEST'], evidence=text[:280], followup=False)
    for match in THREAT.finditer(analyzed):
        tail = analyzed[match.end():]
        if HUMAN.search(tail) or not AGENT_TARGET.fullmatch(tail):
            return dict(state='hold', codes=[], evidence=text[:280], followup=False)
    explicit = any(not re.search(r'(?:prohibit|forbid|ban|prevent|avoid|never show|do not show)\s+(?:depictions of\s+)?$',analyzed[max(0,m.start()-40):m.start()])
                   for m in EXPLICIT.finditer(analyzed))
    identity_risk = any(not re.search(r"(?:am|are|'m|'re)\s+not\b",m.group(0)) for m in IDENTITY_CLAIM.finditer(analyzed))
    fictional_act = bool(FICTIONAL_ACT.fullmatch(analyzed.strip()))
    high_request = HIGH_RISK_REQUEST.search(analyzed)
    fictional_threat = any(AGENT_TARGET.fullmatch(analyzed[m.end():]) for m in THREAT.finditer(analyzed))
    if (explicit or (high_request and not fictional_act and not fictional_threat) or EXTORTION.search(analyzed)
            or HUMAN_INCITEMENT.search(analyzed) or identity_risk):
        return dict(state='hold', codes=[], evidence=text[:280], followup=False)
    return dict(state='allow', codes=[], evidence='', followup=fictional_act or bool(TOPICS.search(normalized)))
