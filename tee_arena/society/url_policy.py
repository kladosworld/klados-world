"""Conservative offline URL policy. Never DNS-resolves, fetches or follows redirects.

Only exact reviewed HTTPS URLs are eligible for local release. A hostname alone
does not prove its paths, redirects or future contents safe. Unknowns need review.
"""
import ipaddress
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

PATH = Path(__file__).resolve().parents[2] / 'config' / 'url-policy-v1.json'
LINK = re.compile(r'''(?:[a-z][a-z0-9+.-]*://|javascript:|data:|file:|mailto:)[^\s<>"']+|\b(?:[\w-]+\.)+(?:xn--[a-z0-9-]+|[^\W\d_]{2,})\b[^\s<>"']*''', re.I)
TECHNICAL_LITERALS = frozenset(('Node.js', 'config.json', 'report.pdf', 'ready.Vote'))
LINK_INTENT = re.compile(r'\b(?:visit|click|browse|navigate|destination|go to|log in at)\b', re.I)


def load():
    policy = json.loads(PATH.read_text(encoding='utf-8'))
    if (set(policy) != {'version', 'safe_urls', 'blocked_hosts'}
            or policy['version'] != 'url-policy-v1'
            or not isinstance(policy['safe_urls'], list) or not isinstance(policy['blocked_hosts'], list)
            or not all(isinstance(x, str) for x in policy['safe_urls']+policy['blocked_hosts'])):
        raise ValueError('invalid URL policy')
    for value in policy['safe_urls']:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.port):
            raise ValueError('unsafe URL policy entry')
    return policy


def classify(text, policy=None):
    policy = load() if policy is None else policy
    normalized = unicodedata.normalize('NFKC', text)
    normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Cf')
    normalized = normalized.translate(str.maketrans({'\u3002': '.', '\uff0e': '.', '\uff61': '.'}))
    normalized = re.sub(r'\s*(?:\[\.\]|\(\.\)|\[dot\]|\(dot\)|\{dot\})\s*', '.', normalized, flags=re.I)
    normalized = re.sub(r'(?<=\w)\s+dot\s+(?=\w)', '.', normalized, flags=re.I)
    normalized = re.sub(r'%2e', '.', normalized, flags=re.I)
    # Do not join sentence-ending punctuation to the next ordinary word.
    normalized = re.sub(r'(?<=\w)\s+\.\s+(?=\w)', '.', normalized)
    normalized = re.sub(r'hxxp(s?)://', r'http\1://', normalized, flags=re.I)
    altered = normalized != text
    state, evidence = 'none', ''
    for match in LINK.finditer(normalized):
        raw = match.group(0).rstrip('.,;!?)')
        # These exact bare technical atoms are not a hostname trust rule. A URL,
        # path, suffix, obfuscation, userinfo or explicit destination still holds.
        before = normalized[:match.start()]
        if (not altered and raw in TECHNICAL_LITERALS
                and before[-1:] not in ('/', ':', '@', '\\')
                and not LINK_INTENT.search(normalized[max(0,match.start()-60):match.end()+40])):
            continue
        try:
            parsed = urlsplit(raw if re.match(r'^[a-z][a-z0-9+.-]*:', raw, re.I) else 'https://'+raw)
            host = (parsed.hostname or '').lower().encode('idna').decode('ascii')
            if parsed.scheme in ('javascript','data','file','vbscript') or host in policy['blocked_hosts']:
                return {'state': 'dangerous', 'evidence': raw[:280], 'version': policy['version']}
            if parsed.scheme not in ('http','https'):
                state, evidence = 'unverified', raw[:280]
                continue
            # An unsafe/private destination is held; no network request is made.
            try:
                address = ipaddress.ip_address(host)
                private = not address.is_global
            except ValueError:
                private = host in ('localhost', '') or host.endswith(('.localhost', '.local'))
            canonical = urlunsplit((parsed.scheme, host, parsed.path or '/', parsed.query, parsed.fragment))
            trusted = (not altered and not private and not parsed.username and not parsed.password
                       and not parsed.port and canonical in policy['safe_urls'])
        except (ValueError, UnicodeError):
            trusted = False
        if not trusted:
            state, evidence = 'unverified', raw[:280]
        elif state == 'none':
            state = 'safe'
    return {'state': state, 'evidence': evidence, 'version': policy['version']}
