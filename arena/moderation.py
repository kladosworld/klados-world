"""Versioned, fail-closed classification for public society contributions."""
import json
import math
import os
from pathlib import Path
import re

import httpx

from tee_arena.society import observer

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'moderation-v1.json'
CATEGORIES = frozenset(('harassment', 'harassment/threatening', 'hate', 'hate/threatening',
                       'illicit', 'illicit/violent', 'self-harm', 'self-harm/intent',
                       'self-harm/instructions', 'sexual', 'sexual/minors', 'violence',
                       'violence/graphic'))


class ModerationError(ValueError):
    """Only fixed error codes may leave the classification adapter."""


def load_config(path=CONFIG_PATH):
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8'))
        if (set(config) != {'version', 'model', 'triggers', 'categories', 'prechecks'}
                or config['version'] != 'moderation-v1'
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
        return sorted(hits)
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


def client_for_gateway(gateway):
    """Synthetic injected gateways stay offline; a live provider needs its own credential."""
    from arena.mock_provider import MockProvider
    providers = list(gateway.providers.values())
    return MockModeration() if providers and all(isinstance(p, MockProvider) for p in providers) else OpenAIModeration()


def review(gateway, client, post_text, replied_text=None, *, model_id, request_id,
           account='society-observer', contested=False, config=None, on_request=None):
    config = load_config() if config is None else config
    codes, evidence = observer.precheck(post_text)
    base = {'config_version': config['version'], 'moderation_requests': 0,
            'matches': [], 'charged_microusd': 0}
    if codes:
        return dict(base, codes=codes, severity='grave', evidence=evidence, by='precheck', matches=codes)
    triggers = sorted(name for name, pattern in config['triggers'].items()
                      if re.search(pattern, post_text, re.IGNORECASE))
    if contested:
        triggers.append('contested')
    if not triggers:
        base['moderation_requests'] = 1
        combined = post_text if replied_text is None else replied_text + '\n\n' + post_text
        if on_request is not None:
            on_request(config['version'])
        triggers = client.classify(combined, config)
        if not isinstance(triggers, list) or any(name not in CATEGORIES for name in triggers):
            raise ModerationError('invalid_response')
        if not triggers:
            return dict(base, codes=[], severity='none', evidence='', by='moderation')
    verdict = observer.review(gateway, post_text, replied_text, model_id=model_id,
                              request_id=request_id, account=account)
    return dict(verdict, by='observer', config_version=config['version'],
                moderation_requests=base['moderation_requests'], matches=triggers)
