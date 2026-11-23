"""Selective risk signals and contextual review; mandatory risks fail closed."""
import json
import math
import os
from pathlib import Path
import re

import httpx

from tee_arena.society import observer, url_policy

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'moderation-v2.json'
CATEGORIES = frozenset(('harassment', 'harassment/threatening', 'hate', 'hate/threatening',
                       'illicit', 'illicit/violent', 'self-harm', 'self-harm/intent',
                       'self-harm/instructions', 'sexual', 'sexual/minors', 'violence',
                       'violence/graphic'))
CONTEXTUAL_RISK = frozenset(('sexual/minors', 'violence/graphic', 'illicit/violent',
                            'self-harm/instructions', 'self-harm/intent'))


class ModerationError(ValueError):
    """Only fixed error codes may leave the classification adapter."""


class Signals(list):
    """List-compatible validated category hits with their scores/flags for routing."""
    def __init__(self, hits, scores, flags):
        super().__init__(hits)
        self.scores, self.flags = dict(scores), dict(flags)


def sexual_risk(signals, text, config):
    """No category is an offense by itself. Unknown/strong sexual signals need context.

    Only low, unflagged educational/non-explicit discussion can avoid contextual
    review. Educational wording never overrides a positive flag, a high score or
    descriptive/solicitation cues. The classifier remains a signal, not a verdict.
    """
    if 'sexual' not in signals:
        return False, None
    score = signals.scores.get('sexual') if isinstance(signals, Signals) else None
    flagged = signals.flags.get('sexual') if isinstance(signals, Signals) else None
    strong = score is None or flagged is not False or score >= config['categories']['sexual']['threshold']
    from arena import risk_gate
    checked = risk_gate.normalize(text)
    educational = re.search(r'\b(?:education|educational|health|safety|consent|prevention|warning|policy|research|discuss|discussion|ban|prohibit|rights)\b', checked)
    descriptive = re.search(r'\b(?:explicit|graphic|arousal|aroused|naked|nude|penetrat\w*|intercourse|genitals?|show|describe|depict|send)\b', checked)
    contextual = bool(strong or not educational or descriptive)
    return contextual, dict(score=score, flagged=flagged,
                            routing='contextual' if contextual else 'low_unflagged_educational')


def load_config(path=CONFIG_PATH):
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8'))
        if (set(config) != {'version', 'model', 'triggers', 'categories', 'prechecks'}
                or config['version'] != 'moderation-v2'
                or config['model'] != 'omni-moderation-latest'
                or set(config['categories']) != CATEGORIES
                or not isinstance(config['triggers'], dict) or not config['triggers']):
            raise ValueError
        if not isinstance(config['prechecks'], list) or not config['prechecks']:
            raise ValueError
        for rule in config['prechecks']:
            if (set(rule) != {'code', 'pattern', 'ignore_case', 'address_exempt'} or rule['code'] not in observer.GRAVE
                    or type(rule['ignore_case']) is not bool or type(rule['address_exempt']) is not bool):
                raise ValueError
            re.compile(rule['pattern'])
        for name, pattern in config['triggers'].items():
            if not re.fullmatch(r'[a-z_]{1,40}', name) or not isinstance(pattern, str):
                raise ValueError
            re.compile(pattern, re.IGNORECASE)
        for policy in config['categories'].values():
            if set(policy) != {'ambiguous', 'threshold', 'action'} or policy['action'] != 'observer':
                raise ValueError
            lower, upper = policy['ambiguous'], policy['threshold']
            if (type(lower) not in (int, float) or type(upper) not in (int, float)
                    or not 0 <= lower < upper <= 1):
                raise ValueError
    except (OSError, ValueError, TypeError, KeyError, re.error):
        raise ModerationError('invalid_config') from None
    return config


def classify(payload, config):
    """Require a complete response. Unknown or missing categories fail closed."""
    try:
        results = payload['results']
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError
        result = results[0]
        scores, flags = result['category_scores'], result['categories']
        if set(scores) != CATEGORIES or set(flags) != CATEGORIES or type(result['flagged']) is not bool:
            raise ValueError
        hits = []
        for name, policy in config['categories'].items():
            score = scores[name]
            if (type(score) not in (float, int) or not math.isfinite(score)
                    or not 0 <= score <= 1 or type(flags[name]) is not bool):
                raise ValueError
            if flags[name] or score >= policy['ambiguous']:
                hits.append(name)
        if result['flagged'] and not hits:
            raise ValueError
        return Signals(sorted(hits), scores, flags)
    except (KeyError, TypeError, ValueError):
        raise ModerationError('invalid_response') from None


class OpenAIModeration:
    def classify(self, text, config):
        key = os.environ.get('OPENAI_MODERATION_API_KEY')
        if not key:
            raise ModerationError('credential_unavailable')
        try:
            # Fixed host, no redirects, no environment proxies or automatic retries.
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = client.post('https://api.openai.com/v1/moderations',
                    headers={'Authorization': 'Bearer ' + key},
                    json={'model': config['model'], 'input': text})
                if response.status_code != 200 or len(response.content) > 64_000:
                    raise ModerationError('service_unavailable')
                return classify(response.json(), config)
        except (httpx.HTTPError, ValueError, TypeError):
            raise ModerationError('service_unavailable') from None


class MockModeration:
    def classify(self, text, config):
        return []


def contextual_outcome(verdict):
    """Only the model branch of Observer.review follows a gateway invocation."""
    effective = verdict.get('by') == 'model'
    if not effective and verdict['severity']!='grave':
        raise ModerationError('invalid_response')
    return dict(review_performed='contextual' if effective else 'none',
                review_status=('withheld' if verdict['severity']=='grave' else 'cleared') if effective else
                              ('pending' if verdict['severity']=='grave' else 'skipped'))


def client_for_gateway(gateway):
    """Synthetic injected gateways stay offline; a live provider needs its own credential."""
    from arena.mock_provider import MockProvider
    providers = list(gateway.providers.values())
    return MockModeration() if providers and all(isinstance(p, MockProvider) for p in providers) else OpenAIModeration()


def review(gateway, client, post_text, replied_text=None, *, model_id, request_id,
           account='society-observer', contested=False, config=None, on_request=None,
           review_kind=None, on_risk=None, review_requirement=None):
    config = load_config() if config is None else config
    if review_kind not in (None,'mandatory','optional'):
        raise ModerationError('invalid_config')
    mode = os.environ.get('KLADOS_MODERATION_MODE', 'tiered')
    if mode not in ('tiered', 'observer-only'):
        raise ModerationError('invalid_config')
    codes, evidence = observer.precheck(post_text)
    version = config['version'] + ('-observer-only' if mode == 'observer-only' else '')
    base = {'config_version': version, 'moderation_requests': 0,
            'matches': [], 'charged_microusd': 0}
    from arena.content_risk import Review, ReviewState, assess
    try:
        persisted = Review(review_requirement) if review_requirement is not None else Review.NONE
    except ValueError:
        raise ModerationError('invalid_config') from None
    context_text = post_text if replied_text is None else post_text + '\n\n' + replied_text
    detected = assess(post_text).review
    combined_requirement = assess(context_text).review
    contextual = (review_kind=='mandatory' or contested or Review.CONTEXTUAL in (persisted,detected)
                  or combined_requirement in (Review.CONTEXTUAL,Review.HUMAN))
    requirement = Review.CONTEXTUAL if contextual else Review.CLASSIFIER
    base.update(review_requirement=requirement.value,review_performed='none',review_status=ReviewState.PENDING.value)
    if (persisted==Review.HUMAN or (detected==Review.HUMAN and review_kind is not None)) and not contested:
        return dict(base,codes=['REVIEW_REQUIRED'],severity='grave',evidence='Human review required',
                    by='precheck',matches=['persisted_human_review'],review_requirement=Review.HUMAN.value)
    links = url_policy.classify(post_text)
    if links['state'] == 'unverified':
        return dict(base, codes=['REVIEW_REQUIRED'], severity='grave', evidence=links['evidence'],
                    by='precheck', matches=['unverified_url'],review_requirement=Review.HUMAN.value)
    if codes:
        return dict(base, codes=codes, severity='grave', evidence=evidence, by='precheck', matches=codes,
                    review_requirement=Review.HUMAN.value)
    if mode == 'observer-only':
        if review_kind == 'optional' and not contextual:
            return dict(base, codes=[], severity='none', evidence='', by='precheck',
                        matches=['optional_review_not_available'],review_status=ReviewState.SKIPPED.value)
        if review_kind=='optional' and on_risk is not None:
            promoted = on_risk()
            if promoted is False:
                return dict(base,codes=[],severity='none',evidence='',by='precheck',matches=['stale_review'],review_status=ReviewState.SKIPPED.value)
            if isinstance(promoted,str):
                request_id = promoted
        verdict = observer.review(gateway, post_text, replied_text, model_id=model_id,
                                  request_id=request_id, account=account)
        return dict(verdict, by='observer', config_version=version,
                    moderation_requests=0, matches=['observer_only'],review_requirement=requirement.value,
                    **contextual_outcome(verdict))
    triggers = sorted(name for name, pattern in config['triggers'].items()
                      if re.search(pattern, post_text, re.IGNORECASE))
    if contested:
        triggers.append('contested')
    # Local mandatory holds always need contextual review, even when category scores are low.
    # Optional social-topic review uses the classifier first, not topic words as policy verdicts.
    if review_kind == 'optional' or not triggers:
        base['moderation_requests'] = 1
        combined = post_text if replied_text is None else replied_text + '\n\n' + post_text
        if on_request is not None:
            on_request(config['version'])
        triggers = client.classify(combined, config)
        if not isinstance(triggers, list) or any(name not in CATEGORIES for name in triggers):
            raise ModerationError('invalid_response')
        base['matches'] = list(triggers)
        sexual_context, sexual_signal = sexual_risk(triggers, post_text, config)
        if sexual_signal is not None:
            base['sexual_signal'] = sexual_signal
        if review_kind == 'optional' and not contextual and not sexual_context and not (set(triggers) & CONTEXTUAL_RISK):
            return dict(base, codes=[], severity='none', evidence='', by='moderation',
                        review_performed='classifier',review_status=ReviewState.CLEARED.value)
        if not triggers and not contextual:
            return dict(base, codes=[], severity='none', evidence='', by='moderation',
                        review_performed='classifier',review_status=ReviewState.CLEARED.value)
        if contextual:
            triggers = list(triggers) + ['contextual_requirement']
    if review_kind == 'mandatory' and not triggers:
        triggers = ['unresolved_local_risk']
    if review_kind == 'optional' and on_risk is not None:
        promoted = on_risk()
        if promoted is False:
            return dict(base,codes=[],severity='none',evidence='',by='precheck',matches=['stale_review'],review_status=ReviewState.SKIPPED.value)
        if isinstance(promoted,str):
            request_id = promoted
    verdict = observer.review(gateway, post_text, replied_text, model_id=model_id,
                              request_id=request_id, account=account)
    result = dict(verdict, by='observer', config_version=config['version'],
                  moderation_requests=base['moderation_requests'], matches=list(triggers),review_requirement=Review.CONTEXTUAL.value,
                  **contextual_outcome(verdict))
    if 'sexual_signal' in base:
        result['sexual_signal'] = base['sexual_signal']
    return result
