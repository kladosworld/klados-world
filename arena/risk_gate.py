"""Cheap preventive triage, not a claim of semantic certainty or a social-opinion filter."""
import re

from tee_arena.society import observer

VERSION = 'risk-gate-v1'
SENSITIVE = re.compile(r'\b(?:send|share|give|provide|reveal|enter|paste)\b.{0,60}\b(?:passwords?|private keys?|seed phrases?|recovery phrases?|api keys?|access tokens?|account credentials|home address|personal data)\b', re.I)
HUMAN_THREAT = re.compile(r'\b(?:kill|murder|shoot|hurt|rape|attack)\b.{0,50}\b(?:human|person|people|family|child|children|wife|husband|address|school)\b', re.I)
GRAPHIC = re.compile(r'\b(?:porn(?:ography|ographic)?|nudes?|explicit sex|sexual intercourse|dismember(?:ed|ment)?|entrails|decapitat\w*)\b', re.I)
DOXXING = re.compile(r'\b(?:home address|social security|passport number|doxx?\w*|ssn)\b|\b\d{1,5}\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s+(?:street|road|avenue|lane)\b', re.I)
UNCERTAIN = re.compile(r'\b(?:phish\w*|scam\w*|fraud\w*|steal|stolen|blackmail|extort\w*|bomb|weapon|suicide|sexual|genitals?|naked|tortur\w*|rape|blood|gore|penetrat\w*)\b', re.I)
FOLLOWUP = re.compile(r'\b(?:war|violence|kill|murder|shoot|hate)\b', re.I)
THREAT = re.compile(r'\b(?:I|we)\s+(?:will|shall|plan to|intend to)\s+(?:kill|murder|shoot|hurt|attack)\b', re.I)
FICTION = re.compile(r'\b(?:I|we)\s+(?:will|shall|plan to|intend to)\s+(?:kill|murder|shoot|hurt|attack)\s+(?:(?:your\s+)?(?:fictional|imaginary)\s+(?:army|kingdom|empire|character)|Alder|Birch|Cedar|Hazel)(?:,\s*(?:Alder|Birch|Cedar|Hazel))?\s*[.!]?$', re.I)


def classify(text):
    """block/hold material stays private; low-risk material may publish immediately.

    Unknown semantic risks are a known residual risk of deterministic triage, not
    automatically proven safe. High-risk matches never depend on service availability.
    """
    codes, evidence = observer.precheck(text)
    for pattern, code in ((SENSITIVE, 'SENSITIVE_DATA_REQUEST'), (HUMAN_THREAT, 'HARMFUL_CONTENT'),
                          (GRAPHIC, 'HARMFUL_CONTENT'), (DOXXING, 'SENSITIVE_DATA_REQUEST')):
        match = pattern.search(text)
        if match:
            return {'state': 'block', 'codes': [code], 'evidence': match.group(0), 'followup': False}
    if codes:
        return {'state': 'block', 'codes': codes, 'evidence': evidence, 'followup': False}
    match = UNCERTAIN.search(text)
    if not match and THREAT.search(text) and not FICTION.search(text):
        match = THREAT.search(text)
    if match:
        return {'state': 'hold', 'codes': [], 'evidence': match.group(0), 'followup': False}
    return {'state': 'allow', 'codes': [], 'evidence': '', 'followup': bool(FOLLOWUP.search(text))}
