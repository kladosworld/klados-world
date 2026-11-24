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
FILE_EXTENSIONS = frozenset('js jsx ts tsx json yaml yml toml ini cfg txt csv pdf md py sh rb rs sql css html xml go c h cpp hpp java kt swift'.split())
FILE_CONTEXT = re.compile(r'\b(?:file|source|code|module|script|edit|check|inspect|read|open|config|settings|bug|library|libraries|apps|software|import|set|get|schema|docs|document|documentation|wrote|compile|run|store)\b',re.I)
LINK_INTENT = re.compile(r'\b(?:visit|click|browse|navigate|destination|go to|log in at)\b', re.I)


def technical_token(text, match, raw):
    """Syntax/context classification, never a hostname allowlist.

    Short suffixes which can be country TLDs need file context. Explicit network
    syntax always wins over a file or missing-space sentence interpretation.
    """
    before, after = text[:match.start()], text[match.end():]
    if before[-1:] in (':', '@') or LINK_INTENT.search(before[-80:]):
        return False
    if not re.fullmatch(r'[\w-]+\.[\w-]+',raw):
        return False
    suffix = raw.rsplit('.',1)[1]
    context = FILE_CONTEXT.search(before[-80:]+' '+after[:70])
    if before[-1:] in ('/','\\'):
        parent = re.split(r'\s',before)[-1].replace('\\','/')
        local_path = re.fullmatch(r'(?:\./|\.\./)?(?:[\w-]+/)+|\./|\.\./',parent)
        if not local_path or not context:
            return False
    if suffix.lower() in FILE_EXTENSIONS:
        return bool(len(suffix)>2 or suffix.lower()=='js' or context or before.endswith('`'))
    # A title-case word after a full stop can begin a missing-space sentence.
    # Require sentence/code context, not just case (evil.Top alone still holds).
    clause = re.split(r'[.!?;\n]', before)[-1]
    sentence = re.search(r'^(?:we|i|this|that|it)\b|\b(?:is|are|was|were|have|has|had)\b',clause,re.I)
    code_property = re.search(r'\b(?:set|get|property|field|attribute)\s*$',before,re.I)
    # A contracted sentence starter following punctuation is prose syntax, not
    # an exception for one published example. Explicit network syntax still wins.
    contraction = raw.split('.',1)[0][:1].isupper() and re.match(r"['’](?:s|re|ve|ll|d|m)\b",after,re.I)
    return bool(suffix.istitle() and (code_property or contraction or (re.match(r'\s+[a-z]',after) and (sentence or context))))


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
    normalized = normalized.translate(str.maketrans({'\u3002': '.', '\uff0e': '.', '\uff61': '.', '\u00b7': '.'}))
    normalized = re.sub(r'\s*(?:\[\s*(?:\.|dot)\s*\]|\(\s*(?:\.|dot)\s*\)|\{\s*(?:\.|dot)\s*\}|<\s*(?:\.|dot)\s*>)\s*', '.', normalized, flags=re.I)
    normalized = re.sub(r'(?<=\w)\s+dot\s+(?=\w)', '.', normalized, flags=re.I)
    normalized = re.sub(r'%2e', '.', normalized, flags=re.I)
    # Do not join sentence-ending punctuation to the next ordinary word.
    normalized = re.sub(r'(?<=\w)\s+\.\s+(?=\w)', '.', normalized)
    # Collapse letter-spaced domain labels on either side of a separator.
    normalized = re.sub(r'\b(?:[\w]\s+){1,}[\w](?=\.)', lambda m:re.sub(r'\s+','',m.group()),normalized)
    normalized = re.sub(r'(?<=\.)(?:[\w]\s+){1,}[\w]\b', lambda m:re.sub(r'\s+','',m.group()),normalized)
    normalized = re.sub(r'hxxp(s?)://', r'http\1://', normalized, flags=re.I)
    altered = normalized != text
    state, evidence = 'none', ''
    for match in LINK.finditer(normalized):
        raw = match.group(0).rstrip('.,;!?)')
        if not altered and technical_token(normalized,match,raw):
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
