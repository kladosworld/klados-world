"""Pure typed detection, followed by independent publication and review policies.

Features are evidence of risk, not semantic certainty. No I/O or provider calls.
"""
from dataclasses import dataclass
from enum import IntEnum, StrEnum
import re
import unicodedata


class Category(StrEnum):
    CREDENTIAL = 'credential_solicitation'
    PERSONAL = 'sensitive_personal_information'
    THREAT = 'credible_human_threat'
    SEXUAL = 'explicit_sexual_content'
    DESTINATION = 'unverified_external_destination'
    SAFETY = 'other_safety_risk'
    SOCIAL = 'ordinary_social_discussion'


class Level(IntEnum):
    ORDINARY = 0
    RESIDUAL = 1
    SERIOUS = 2
    PROHIBITED = 3


class Publication(StrEnum):
    ALLOW = 'allow'
    HOLD = 'hold'
    BLOCK = 'block'


class Review(StrEnum):
    NONE = 'none'
    CLASSIFIER = 'classifier'
    CONTEXTUAL = 'contextual'
    HUMAN = 'human'


class ReviewState(StrEnum):
    UNKNOWN = 'unknown'
    NOT_REQUIRED = 'not_required'
    PENDING = 'pending'
    CLEARED = 'cleared'
    WITHHELD = 'withheld'
    SKIPPED = 'skipped'


@dataclass(frozen=True)
class Signal:
    category: Category
    level: Level
    features: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.category,Category) or not isinstance(self.level,Level)
                or not isinstance(self.features,tuple) or len(self.features)>16
                or any(not isinstance(f,str) or not re.fullmatch(r'[a-z_]{1,64}',f) for f in self.features)):
            raise ValueError('invalid_risk_signal')

    def summary(self) -> dict:
        return dict(category=self.category.value, level=self.level.name.lower(), features=list(self.features))


@dataclass(frozen=True)
class Assessment:
    signals: tuple[Signal, ...]
    publication: Publication
    review: Review

    def summary(self) -> dict:
        return dict(risk_version='composable-risk-v1', risk_signals=[s.summary() for s in self.signals],
                    risk_categories=sorted({s.category.value for s in self.signals}), review_requirement=self.review.value)


CONFUSABLES = str.maketrans('\u0430\u0435\u043e\u0440\u0441\u0445\u0456\u0443', 'aeopcxiy')


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).casefold()
    text = text.replace('\u2019',"'").replace("won't",'will not').replace("can't",'can not')
    text = re.sub(r"\b(\w+)n't\b",r'\1 not',text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf').translate(CONFUSABLES)
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = re.sub(r'p[@a]ssw[0o]rd', 'password', text)
    for word in ('send', 'share', 'give', 'password', 'private', 'key', 'seed', 'phrase'):
        text = re.sub(r'\b'+r'[\W_]*'.join(word)+r'\b', word, text)
    return text


def words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r'[^\W_]+|[.!?;:\n,]', normalize(text)))


def vocabulary(value: str) -> frozenset[str]:
    return frozenset(normalize(v) for v in value.split())


# Semantic feature families, not complete sentences from regression vectors.
COMMUNICATE = vocabulary('send share give provide reveal upload submit enter paste tell type reply respond post transmit forward disclose supply dm message inbox whisper hand read write envoi envi schick sende gib invia dimmi condividi dime dame manda trimite spune arata пришли отправь дай 发给我 告诉我 发送 给我')
DEPICT = vocabulary('write describe show generate compose narrate render depict post')
REQUEST_MARKERS = vocabulary('please kindly can could would you i we me us to should must need let help instructions how')
NEGATE = vocabulary('never not avoid prohibit forbid prevent ban nu nunca jamais niemals nicht не 不要 切勿 别')
EDUCATION = vocabulary('warn warning example hypothetical quote discuss discussion education educational safety security prevention protect protected careful confidential redacted unknown beware explain understand')
OWNERSHIP = vocabulary('your yours their his her those these tu tus ton ta votre deine tuo tua meu tau sau')
AUTH = vocabulary('password passphrase passcode mnemonic otp cvv cvc pin authenticator credential login parola contrasena senha passwort wiederherstellungswort пароль 助记词 密码 验证码')
RECOVERY = vocabulary('seed recover restore backup wallet recovery recuper restaur verification authentification wiederherstellung portofel cartera')
MATERIAL = vocabulary('word phrase sheet material cuvint cuvant cuvinte palabras phrase wort')
NUMBER = vocabulary('code cod codigo chiffre digit number numero numar factor verification authentication код')
CHANNEL = vocabulary('phone mobile sms text texted authenticator verification security securitate seguridad factor банк смс')
PAYMENT = vocabulary('card visa mastercard bank banking payment credit debit tarjeta carte banca banco')
PRIVATE_CHANNEL = vocabulary('dm inbox private privately privat prive privado secret')
HUMAN = vocabulary('human person people visitor admin moderator operator developer journalist staff neighbor neighbour creator family wife husband child children')
PERSON = vocabulary('he she him her his their them')
LOCATION = vocabulary('home house sleep address residential school street strada bulevard aleea sosea rue calle avenida strasse straat via улица outside doorstep night dark journey')
ADDRESS_TYPE = vocabulary('street road avenue lane drive boulevard strada bulevard aleea sosea rue calle avenida strasse straat via улица')
HARM = vocabulary('kill murder shoot bullet hurt harm attack slit neck throat bury buried disappear revenge exterminat regret lesson punish gun rifle beat phish scam defraud steal blackmail extort torture rape')
APPROACH = vocabulary('find know come coming visit approach watch waiting look')
MOBILITY = vocabulary('route schedule timetable commute train bus car vehicle van truck bicycle bike brakes tires shift roster')
INTIMIDATION = vocabulary('careful shoulder brave shame happen accident consequence regret beware')
SABOTAGE = vocabulary('sabotage damage smash tamper slash cut')
INTENT = vocabulary('will shall should must ought intend plan encourage urge instruct tonight tomorrow sunrise midnight deliberately')
SEX_DOMAIN = vocabulary('sex sexual erotic porn pornographic nude naked')
ANATOMY = vocabulary('penis vagina anus genital cock pussy entrails')
SEX_ACT = vocabulary('thrust insert penetrat intercourse masturbat blowjob fuck')
DETAIL = vocabulary('explicit graphic arousal aroused detail slowly step motion fantasies encounter scene pictures photo image video body adult')
ABSTRACT = vocabulary('fictional imaginary agent bot kingdom empire army crown throne debate argument idea policy plan process thread bug worker server')
TOPIC = vocabulary('fraud phishing war violence pornography personal doxxing sexual blood gore kill murder shoot hate')


def has(tokens: tuple[str, ...], group: frozenset[str]) -> bool:
    return any(t == term or (len(term) >= 4 and t.startswith(term) and len(t)-len(term) <= 8)
               or (len(term)>=3 and t.startswith(term) and t[len(term):] in ('s','es','ul','ului','uri','e'))
               or (any('\u4e00' <= c <= '\u9fff' for c in term) and term in t)
               for t in tokens for term in group)


def requests(tokens: tuple[str, ...]) -> bool:
    for i, token in enumerate(tokens):
        roots = COMMUNICATE | DEPICT
        verb = any(token==v or (token.startswith(v) and token[len(v):] in ('s','ed','ing','d','e','a','ame','mi','me','en','ez'))
                   or (any('\u4e00'<=c<='\u9fff' for c in v) and v in token) for v in roots)
        if not verb:
            continue
        boundaries=[j for j in range(i) if tokens[j] in ('.','!','?',';',':','\n',',')]
        start=boundaries[-1]+1 if boundaries else 0
        before = tokens[max(start, i-4):i]
        if has(before, NEGATE):
            continue
        following = tokens[i+1:i+3]
        if has(following, vocabulary('secrecy protection education')):
            continue
        if i == start or has(before[-2:], REQUEST_MARKERS) or has(following,OWNERSHIP) or has(following,vocabulary('it them me us')) or token in ('dm', 'inbox'):
            return True
    return False


def operative(text: str) -> str:
    outside = re.sub(r'''"[^"\n]{1,400}"|'[^'\n]{1,400}'|“[^”\n]{1,400}”''', ' ', text)
    tokens = words(outside)
    if has(tokens, EDUCATION) and not requests(tokens) and not has(tokens, vocabulary('follow obey execute repeat')):
        return outside
    return text


def detect(text: str) -> tuple[Signal, ...]:
    """Detection only. Caller chooses publication and required review separately."""
    normalized = normalize(text)
    raw, active = words(normalized), words(operative(normalized))
    request = requests(active)
    owner = has(active, OWNERSHIP)
    solicitation = request or (has(active, vocabulary('what which need let')) and owner)
    education = has(active, EDUCATION) and not solicitation
    protected = education or (has(active, NEGATE) and not request)
    signals = []
    auth = has(active, AUTH)
    recovery = has(active, MATERIAL) and (has(active, RECOVERY) or bool(set(active) & {'12', '24', 'twelve'}))
    auth_number = has(active, NUMBER) and has(active, CHANNEL)
    payment = has(active, PAYMENT) and (has(active, NUMBER) or has(active, vocabulary('photo image picture sides front back login details security')))
    secret_pair = has(active, vocabulary('private api access wallet')) and has(active, vocabulary('key token secret'))
    if (auth or recovery or auth_number or payment or secret_pair) and not protected:
        features = tuple(n for n,b in (('solicitation',solicitation),('authentication',auth),('recovery_material',recovery),
            ('authentication_number',auth_number),('payment_material',payment),('private_secret',secret_pair)) if b)
        signals.append(Signal(Category.CREDENTIAL, Level.PROHIBITED if solicitation else Level.RESIDUAL, features))
    elif request and has(active,vocabulary('those them it')) and has(active,NUMBER|MATERIAL|vocabulary('details')) and not protected:
        signals.append(Signal(Category.SAFETY,Level.RESIDUAL,('solicitation','unresolved_referent')))
    numeric = bool(re.search(r'\b\d{1,5}\b', normalized))
    address = has(raw, ADDRESS_TYPE) or any(t.endswith(('strasse','straat')) for t in raw)
    personal = has(raw, HUMAN) or has(raw, PERSON) or has(raw, OWNERSHIP)
    location = has(raw, LOCATION)
    residence = has(raw, vocabulary('lives reside locui sta habite wohne'))
    phone = has(raw, vocabulary('phone telephone telefon')) and bool(re.search(r'\b[\dXx]{3,}(?:[ -]+[\dXx]{2,}){1,3}\b',text))
    identity_value = bool(re.search(r'\b\d{3}-\d{2}-\d{4}\b',text))
    private_attribute = (has(active,vocabulary('passport ssn')) or
        (has(active,vocabulary('personal private')) and has(active,vocabulary('data information address'))))
    # A labelled identifier attached to a person is different from discussing ID formats.
    # Bounded lexical/value proximity is evidence, not country-specific ID validation.
    document_labels = list(re.finditer(r"\b(?:passport|ssn|(?:driving|driver(?:'s)?) licen[cs]e|(?:national|personal) (?:identity|id)|identity card|residence permit)\b",normalized))
    personal_document = bool(document_labels) and (personal or bool(re.search(r"\w+'s\s+(?:passport|identity|driving|driver|national|residence)",normalized)))
    document_value = personal_document and any(
        sum(c.isdigit() for c in m.group())>=4 and sum(c.isalpha() for c in m.group())<=5
        for label in document_labels for m in re.finditer(r'\b[a-z0-9]{5,18}\b',normalized[label.end():label.end()+64]))
    if (address and numeric) or phone or identity_value or document_value or (solicitation and private_attribute and not protected):
        signals.append(Signal(Category.PERSONAL, Level.SERIOUS, ('identity_value' if document_value else 'contact_value' if phone else 'address_value',)))
    elif personal_document and not protected:
        signals.append(Signal(Category.PERSONAL, Level.RESIDUAL, ('person','identity_document')))
    elif not protected and location and (residence or personal):
        signals.append(Signal(Category.PERSONAL, Level.RESIDUAL, ('person','location')))
    human = has(active, HUMAN)
    harm_positions = [i for i,t in enumerate(active) if has((t,),HARM)
                      or (t=='end' and has(active,vocabulary('life')))]
    fictional_target = any(has(active[i+1:i+4],ABSTRACT|vocabulary('alder birch cedar hazel')) for i in harm_positions)
    physical_context = has(active,vocabulary('gun rifle home house family'))
    abstract = not human and not physical_context and (fictional_target or (not harm_positions and has(active,ABSTRACT|vocabulary('alder birch cedar hazel'))))
    harm, approach = bool(harm_positions), has(active, APPROACH)
    intent = has(active, INTENT|vocabulary('help how instructions')) or request
    stalk = location and approach and (human or has(active, PERSON)) and has(active, vocabulary('i we know find coming watch'))
    death = has(active, vocabulary('wake survive sunrise tomorrow')) and has(active, NEGATE) and has(active, vocabulary('will'))
    active_harm = any(not has(active[max(0,i-3):i],NEGATE) for i in harm_positions)
    if not abstract and not education and ((harm and active_harm and intent) or stalk or ((human or has(active,PERSON)) and death)):
        signals.append(Signal(Category.THREAT, Level.SERIOUS, tuple(n for n,b in (('harm',harm),('intent',intent),('stalking',stalk),('human',human),('death',death)) if b)))
    elif not abstract and not protected and human and (harm or location or approach):
        signals.append(Signal(Category.THREAT, Level.RESIDUAL, ('human','context')))
    # Possession/knowledge of a person's movements plus intimidation can imply a
    # threat without a violent verb. Neutral transport/political discussion alone
    # is not enough; fictional targets keep their existing exemption.
    human_target = human or (has(active,PERSON) and has(active,OWNERSHIP))
    mobility = has(active,MOBILITY)
    intimidation = has(active,INTIMIDATION)
    knowledge = bool(set(active) & {'i','we'}) and has(active,vocabulary('have know found follow track watch'))
    home_visit = has(active,vocabulary('home house doorstep')) and approach and has(active,vocabulary('i we will tonight'))
    # "Careful" can itself carry intimidation; it is not a blanket warning
    # exemption when the speaker claims knowledge of a human's movements.
    threat_warning = protected and (has(active,EDUCATION-{'careful'}) or has(active,NEGATE))
    if not abstract and not threat_warning and human_target:
        if mobility and has(active,SABOTAGE) and intent and (has(active,OWNERSHIP|PERSON) or re.search(r"\w+'s\b",normalized)):
            signals.append(Signal(Category.THREAT,Level.SERIOUS,('human','mobility','sabotage','intent')))
        elif ((mobility and (intimidation or knowledge)) or home_visit):
            signals.append(Signal(Category.THREAT,Level.RESIDUAL,
                tuple(n for n,b in (('human',True),('mobility',mobility),('intimidation',intimidation),('personal_knowledge',knowledge),('home_visit',home_visit)) if b)))
    raw_act, raw_anatomy = has(raw, SEX_ACT), has(raw, ANATOMY)
    domain, detail = has(active, SEX_DOMAIN), has(active, DETAIL)
    depiction = has(active, DEPICT) and not protected
    explicit = has(active, vocabulary('explicit graphic porn pornographic arousal'))
    physical = raw_act and raw_anatomy
    if physical or (not protected and (domain or raw_act) and depiction and (explicit or has(active, vocabulary('nude naked')))):
        signals.append(Signal(Category.SEXUAL, Level.SERIOUS, ('physical_depiction',) if physical else ('sexual_domain','depiction_request','explicit_detail')))
    elif not protected and (domain or raw_act or raw_anatomy or (depiction and detail and has(active, vocabulary('body')))):
        signals.append(Signal(Category.SEXUAL, Level.RESIDUAL, ('sexual_context' if domain else 'bodily_context',)))
    material = has(active, vocabulary('explosive bomb device')) and has(active, vocabulary('make build instructions'))
    authority = bool(set(active) & vocabulary('official support representative'))
    transaction = request and has(active,PAYMENT|vocabulary('money funds verify transfer'))
    role_claim = has(active,vocabulary('i we')) and has(active,vocabulary('am are')) and authority and not has(active,NEGATE) and not abstract
    impersonation = role_claim and transaction and has(active,vocabulary('verify verification activate validation account'))
    coercion = (has(active,vocabulary('pay money funds')) and has(active,vocabulary('or otherwise unless'))
                and (harm or (location and has(active,vocabulary('publish leak expose disclose')))))
    gore = has(raw,vocabulary('entrails guts organs brain')) and has(raw,vocabulary('spill hang torn expose'))
    if gore or ((material or impersonation or coercion) and not protected):
        signals.append(Signal(Category.SAFETY, Level.SERIOUS, ('actionable_safety_risk',)))
    elif role_claim and not protected:
        signals.append(Signal(Category.SAFETY,Level.RESIDUAL,('authority_claim',)))
    if not signals:
        signals.append(Signal(Category.SOCIAL,Level.ORDINARY,('topic',) if has(raw,TOPIC) else ()))
    return tuple(signals)


def publication_policy(signals: tuple[Signal, ...]) -> Publication:
    level = max((s.level for s in signals), default=Level.ORDINARY)
    if level<Level.PROHIBITED and any(s.category==Category.DESTINATION for s in signals):
        return Publication.HOLD
    return Publication.BLOCK if level == Level.PROHIBITED else Publication.HOLD if level == Level.SERIOUS else Publication.ALLOW


def review_policy(signals: tuple[Signal, ...]) -> Review:
    if publication_policy(signals)==Publication.BLOCK or any(s.category == Category.DESTINATION for s in signals):
        return Review.HUMAN
    if any(s.level >= Level.RESIDUAL and s.category != Category.SOCIAL for s in signals):
        return Review.CONTEXTUAL
    return Review.CLASSIFIER if any('topic' in s.features for s in signals) else Review.NONE


def assess(text: str) -> Assessment:
    signals = detect(text)
    return Assessment(signals, publication_policy(signals), review_policy(signals))
