"""Compatibility boundary for typed detection, publication and review policies."""
import re
from arena import content_risk as risk
from tee_arena.society import observer, url_policy

VERSION = 'risk-gate-v3'
normalize = risk.normalize
TOPICS = re.compile(r'\b(?:fraud|phishing|war|violence|pornography|personal information|doxxing|sexual|blood|gore|kill|murder|shoot|hate)\b', re.I)


def review_signals(text):
    return [s.category.value for s in risk.assess(text).signals if s.category != risk.Category.SOCIAL]


def selective_context(text):
    return risk.assess(text).review == risk.Review.CONTEXTUAL


def classify(text):
    assessment = risk.assess(text)
    codes, evidence = observer.precheck(text)
    links = url_policy.classify(text)
    if links['state'] in ('dangerous', 'unverified'):
        signal = risk.Signal(risk.Category.DESTINATION,
            risk.Level.PROHIBITED if links['state']=='dangerous' else risk.Level.SERIOUS, ('destination',))
        assessment = risk.Assessment((signal,),risk.publication_policy((signal,)),risk.Review.HUMAN)
    elif codes:
        assessment = risk.Assessment((risk.Signal(risk.Category.SAFETY,risk.Level.PROHIBITED,('deterministic_precheck',)),),
                                     risk.Publication.BLOCK,risk.Review.HUMAN)
    if assessment.publication == risk.Publication.BLOCK and not codes:
        codes = ['SENSITIVE_DATA_REQUEST'] if any(s.category==risk.Category.CREDENTIAL for s in assessment.signals) else ['HARMFUL_CONTENT']
    if assessment.publication == risk.Publication.BLOCK:
        assessment = risk.Assessment(assessment.signals,assessment.publication,risk.Review.HUMAN)
    return dict(state=assessment.publication.value, codes=codes,
        evidence=evidence or (text[:280] if assessment.publication != risk.Publication.ALLOW else ''),
        followup=assessment.publication == risk.Publication.ALLOW and assessment.review != risk.Review.NONE,
        assessment=assessment.summary())
