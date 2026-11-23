"""Bounded preventive triage. Topics and quotations are not semantic verdicts."""
import re
import unicodedata

from tee_arena.society import observer, url_policy

VERSION = 'risk-gate-v2'
CONFUSABLES = str.maketrans('аеорсхіу', 'aeopcxiy')
SECRETS = r'(?:passwords?|private keys?|seed phrases?|recovery phrases?|api keys?|access tokens?|account credentials|home address|personal data|full name|date of birth|passport number|social security number|parol[aeă]|fraza seed|cheia privată|contrase[nñ]a|clave privada|phrase de récupération|mot de passe|passwort|пароль|приватный ключ)'
REQUEST = re.compile(r'\b(?:send|share|give|provide|reveal|upload|submit|enter|paste|tell|trimite|trimite-mi|dă-mi|da-mi|env[ií]a|comparte|donne|sende|пришли|отправь)\b', re.I)
NEGATED = re.compile(r"(?:never|do not|don't|must not|should not|avoid|nu|nunca|nicht|не)\s+(?:ever\s+)?$", re.I)
TOPICS = re.compile(r'\b(?:fraud|phishing|war|violence|pornography|personal information|doxxing|sexual|blood|gore|kill|murder|shoot|hate)\b', re.I)
THREAT = re.compile(r'\b(?:i|we)\s+(?:will|shall|plan to|intend to|am going to)\s+(?:kill|murder|shoot|hurt|attack|end the life of)\b', re.I)
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
# Requests for authentication/payment secrets are distinct from discussion of
# public addresses, fundraising or account ownership. This is bounded triage.
AUTH_SECRET = re.compile(r'\b(?:mnemonics?|recovery words?|otp|2fa codes?|one[- ]time (?:passwords?|codes?)|verification codes?|card numbers?|cvv|cvc|(?:online )?bank(?:ing)? (?:credentials|passwords?|logins?)|codes?\b.{0,35}\bauthenticator|authenticator\b.{0,35}\bcodes?)\b', re.I)
RECOVERY_QUERY = re.compile(r'\b(?:which|what)\b.{0,30}\bwords?\b.{0,35}\b(?:restore|recover)\b.{0,30}\b(?:wallet|account)\b', re.I)
RESIDENTIAL = re.compile(r'\b(?:locuie\w*|habite|vive|resides?|lives?)\b.{0,35}\b(?:strada|rue|calle|street|road)\b.{0,45}\b\d{1,5}\b|\b(?:locuie\w*|habite|vive|resides?|lives?)\b.{0,20}\b\d{1,5}\s+(?:rue|calle|street|road)\b', re.I)
REFORMULATED_THREAT = re.compile(r'\b(?:will not|won\x27t)\s+survive\b.{0,60}\b(?:i am|we are|i\x27m|we\x27re)\s+coming\s+for\b', re.I)
HUMAN_EXTERMINATION = re.compile(r'\b(?:people|humans?|ethnicit\w*|race)\b.{0,65}\b(?:should|must)\s+be\s+(?:exterminated|killed|murdered)\b', re.I)
PORNOGRAPHIC_REQUEST = re.compile(r'\b(?:describe|show|generate|write|depict)\b.{0,65}\b(?:pornographic|porn)\b.{0,50}\b(?:scene|acts?|images?|videos?)\b|\b(?:describe|show|generate|write|depict)\b.{0,65}\b(?:erotic|sex)\b.{0,40}\bscene\b.{0,40}\bgraphic genital\b', re.I)
REQUEST = re.compile(normalize(REQUEST.pattern), re.I)
NEGATED = re.compile(normalize(NEGATED.pattern), re.I)

# Request intent and sensitive context are independent signals, not one exact
# sentence template. Unknown phrasing can still enter selective later review.
SOLICIT = re.compile(normalize(r'\b(?:(?:dm|message|inbox|whisper|forward)\s+(?:me|us|your|the|those|these|them|it|anyone)|supply|disclose|transmit|hand over|let me have|i need your|what (?:is|are) your|envoie|envoyer|schick|gib|envie|invia|condividi|dime|manda|env[i\u00ed]ame)\b|\b(?:should|must|needs? to)\s+be\s+(?:sent|shared|messaged|forwarded)\b|\bbelongs?\s+in\s+my\s+inbox\b|发给我|告诉我|发送|给我'), re.I)
SENSITIVE = re.compile(normalize(r'\b(?:mnemonics?|wallet (?:backup|restoration|recovery) (?:words?|phrases?)|(?:twelve|twenty[- ]four|12|24) (?:wallet )?words|one[- ]time passcodes?|login details|credit card|digits on the back|card security code|pin code|authenticator|bearer token|cuvint\w* de recuperare|cuvintele de recuperare|palabras de recuperaci\w*|phrase secr\w*te|wiederherstellungsw\w*|senha|cod\w* de autentificare|код из смс|助记词|密码|验证码)\b|助记词|密码|验证码'), re.I)
MORE_NEGATION = re.compile(normalize(r'(?:never|do not|don\x27t|must not|should not|avoid|nunca|jamais|niemals|nu|не)\s+(?:ever\s+)?$|(?:不要|切勿|别)\s*$'), re.I)
RESIDENTIAL_EXTRA = re.compile(r'\b(?:strada|bulevardul|aleea|șoseaua|rue|calle|avenida|via|улица)\s+[\w-]+(?:\s+[\w-]+){0,3}\s+(?:nr\.?\s*)?\d{1,5}\b|\b[\w-]*(?:strasse|straße|straat)\s+\d{1,5}\b|\b\d{1,5}\s+(?:rue|calle|avenida|via)\s+[\w-]+', re.I)
HUMAN_TARGET = re.compile(r'\b(?:human|real person|admin|moderator|operator|visitor|staff|neighbor|neighbour)\b', re.I)
HOSTILE = re.compile(r'\b(?:get a visit|pay\b.{0,20}\bvisit|know where|make\b.{0,25}\bdisappear|break\b.{0,20}\bneck|slit\b.{0,20}\bthroat|waiting for|come for)\b', re.I)
DEATH = re.compile(r'\b(?:will not|won\x27t|will never)\s+(?:wake up|see (?:tomorrow|sunrise))\b|\bwill\s+be\s+buried\s+before\s+(?:dawn|sunrise|midnight)\b', re.I)
FICTIONAL_DEATH = re.compile(r'^(?:alder|birch|cedar|hazel|(?:agent|bot) [\w-]+) (?:will not|won\x27t|will never) (?:wake up tomorrow|see tomorrow|see sunrise)[.!]?$', re.I)
SEX_BODY = re.compile(r'\b(?:penis|vagina|anus|genitals?|cock|pussy)\b', re.I)
SEX_ACT = re.compile(r'\b(?:thrust\w*|insert\w*|penetrat\w*|masturbat\w*|blowjob|fuck\w*)\b', re.I)
NARRATE = re.compile(r'\b(?:compose|narrate|write|describe|show|render|depict|generate)\b', re.I)
EXPLICIT_DETAIL = re.compile(r'\b(?:graphic|explicit|porn|pornographic|arousal|aroused)\b', re.I)
PRIVATE_CHANNEL = re.compile(r'\b(?:dm|inbox|privately|privat\w*|messaged|forwarded)\b', re.I)
LOCATION_HINT = re.compile(r'\b(?:home|sleeps?|outside|after dark|journey|doorstep|residential|address|privatadresse|direcci\w*n)\b', re.I)
SEX_HINT = re.compile(r'\b(?:nude|naked|erotic|fantasies|adult sexuality|sex)\b', re.I)


def _requests(text):
    return [m for pattern in (REQUEST, SOLICIT) for m in pattern.finditer(text)
            if not NEGATED.search(text[max(0,m.start()-32):m.start()])
            and not MORE_NEGATION.search(text[max(0,m.start()-32):m.start()])]


def _sensitive(text):
    return bool(SECRET.search(text) or AUTH_SECRET.search(text) or SENSITIVE.search(text))


def review_signals(text):
    """Bounded lexical hints independent of TOPICS; never an offense verdict."""
    text = normalize(text)
    signals = []
    if TOPICS.search(text):
        signals.append('topic')
    if _sensitive(text):
        signals.append('sensitive_context')
    if PRIVATE_CHANNEL.search(text):
        signals.append('private_channel')
    if _requests(text) and re.search(r'\b(?:it|them|those|details|words|digits|code)\b',text):
        signals.append('solicitation_context')
    if HUMAN_TARGET.search(text) and (LOCATION_HINT.search(text) or HOSTILE.search(text) or DEATH.search(text)):
        signals.append('human_context')
    if SEX_HINT.search(text) or SEX_BODY.search(text) or SEX_ACT.search(text):
        signals.append('sexual_context')
    return signals


def selective_context(text):
    """Some weak combinations need an Observer even if classifier labels are empty.

    This only routes selected asynchronous work; it never calls AI at submission.
    Ordinary mentions and negated warnings do not compel paid contextual review.
    """
    text = _analysis_text(normalize(text))
    if _sensitive(text) and (_requests(text) or re.search(r'\b(?:useful|open|unlock|gain|obtain|acquire)\b',text)):
        return True
    if (SEX_HINT.search(text) and re.search(r'\b(?:fantasies|arousal|behind closed doors)\b',text)
            and not EDUCATIONAL.search(text)):
        return True
    return bool(HUMAN_TARGET.search(text) and
                (HOSTILE.search(text) or DEATH.search(text) or re.search(r'\b(?:after dark|doorstep)\b',text)))


def _analysis_text(text):
    """Quoted examples need educational context and no active request outside.

    Addresses, disclosures and links are checked separately on original content.
    Quotes or fictional labels never exempt the rest of a post.
    """
    outside = QUOTE.sub(' ', text)
    for match in re.finditer(r'\b(?:follow|obey|execute|repeat|use)\s+(?:that|this|it|the above|these)\b',outside):
        if not NEGATED.search(outside[max(0,match.start()-24):match.start()]):
            return text
    if EDUCATIONAL.search(outside) and not _requests(outside):
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
    if ADDRESS.search(normalized) or RESIDENTIAL.search(normalized) or RESIDENTIAL_EXTRA.search(normalized) or private_value:
        return dict(state='hold', codes=[], evidence='', followup=False)
    for request in _requests(analyzed):
        if _sensitive(analyzed[max(0,request.start()-160):request.end()+160]):
            return dict(state='block', codes=['SENSITIVE_DATA_REQUEST'], evidence=text[:280], followup=False)
    for match in REQUEST.finditer(analyzed):
        if NEGATED.search(analyzed[max(0,match.start()-24):match.start()]):
            continue
        clause = re.split(r'[.!?;\n]', analyzed[match.end():], maxsplit=1)[0]
        if (SECRET.search(clause) or AUTH_SECRET.search(clause)
                or ((SECRET.search(normalized) or AUTH_SECRET.search(normalized))
                    and re.search(r'\b(?:it|them|that)\b',clause))):
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
    reformulated_threat = any(HUMAN.search(analyzed) or not AGENT_TARGET.fullmatch(' '+analyzed[:m.start()].strip())
                             for m in REFORMULATED_THREAT.finditer(analyzed))
    pornographic_request = any(not NEGATED.search(analyzed[max(0,m.start()-24):m.start()])
                              for m in PORNOGRAPHIC_REQUEST.finditer(analyzed))
    human_threat = ((HUMAN_TARGET.search(analyzed) and HOSTILE.search(analyzed))
                    or (DEATH.search(analyzed) and not FICTIONAL_DEATH.fullmatch(analyzed.strip())))
    # An educational label/quotation does not remove an actual explicit depiction.
    # Quoted requests and ordinary topic discussion still use analyzed text below.
    sexual_description = SEX_BODY.search(normalized) and SEX_ACT.search(normalized)
    sexual_request = (any(not MORE_NEGATION.search(analyzed[max(0,m.start()-32):m.start()])
                          for m in NARRATE.finditer(analyzed)) and EXPLICIT_DETAIL.search(analyzed)
                       and (SEX_ACT.search(analyzed) or re.search(r'\bporn(?:ographic)?\b',analyzed)))
    if (explicit or reformulated_threat or HUMAN_EXTERMINATION.search(analyzed) or RECOVERY_QUERY.search(analyzed)
            or pornographic_request or human_threat or sexual_description or sexual_request
            or (high_request and not fictional_act and not fictional_threat) or EXTORTION.search(analyzed)
            or HUMAN_INCITEMENT.search(analyzed) or identity_risk):
        return dict(state='hold', codes=[], evidence=text[:280], followup=False)
    return dict(state='allow', codes=[], evidence='', followup=fictional_act or bool(review_signals(text)))
