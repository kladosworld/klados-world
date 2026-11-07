"""Offline Challenge validation and checkers from docs/CONTRACTS.md.

Structural validation cannot establish whether prose is self-contained or asks for
personal data/current events; challenge approval still requires human review.
Id uniqueness is a collection/database constraint, not a single-record property.
"""
import math
import json
import subprocess
import sys
import re
from collections.abc import Mapping

CATEGORIES = frozenset(('explanation', 'planning', 'writing', 'reasoning',
                        'logic', 'math', 'knowledge', 'code_reading'))
NUMBER = re.compile(r'[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and (isinstance(value, int) or math.isfinite(value)))


def _checker(checker):
    _require(isinstance(checker, Mapping), 'checker must be an object')
    kind = checker.get('type')
    _require(isinstance(kind, str), 'checker type must be a string')
    if kind == 'exact':
        _require(isinstance(checker.get('answer'), str), 'exact answer must be a string')
        _require(isinstance(checker.get('case_sensitive', False), bool), 'case_sensitive must be bool')
    elif kind == 'numeric':
        _require(_number(checker.get('answer')), 'numeric answer must be finite')
        tolerance = checker.get('tolerance', 0)
        _require(_number(tolerance) and tolerance >= 0, 'tolerance must be finite and nonnegative')
    elif kind == 'choice':
        options = checker.get('options')
        _require(isinstance(options, list) and bool(options), 'options must be a nonempty list')
        _require(all(isinstance(x, str) and len(x) == 1 and x.isascii() and x.isalpha()
                     for x in options), 'options must contain letters')
        _require(len(set(options)) == len(options), 'options must be unique')
        _require(checker.get('answer') in options, 'answer must be an option letter')
    elif kind == 'regex':
        pattern = checker.get('pattern')
        _require(isinstance(pattern, str) and len(pattern) <= 200, 'pattern exceeds 200 characters or is missing')
        try:
            re.compile(pattern)
        except (re.error, OverflowError, RecursionError) as exc:
            raise ValueError('invalid regex') from exc
    else:
        raise ValueError('unknown checker type')
    return kind


def validate_challenge(challenge):
    """Validate all machine-checkable Challenge fields; raise ValueError on failure."""
    _require(isinstance(challenge, Mapping), 'challenge must be an object')
    required = {'id', 'kind', 'category', 'prompt', 'checker', 'max_output_tokens',
                'language', 'source', 'status'}
    _require(required <= challenge.keys(), 'missing challenge field')
    identifier = challenge['id']
    _require(isinstance(identifier, str) and re.fullmatch(r'ch-[0-9]{4,}', identifier), 'invalid id')
    for field, allowed in [('kind', ('open', 'verifiable')), ('category', CATEGORIES),
                           ('language', ('en', 'ro')), ('source', ('team', 'public')),
                           ('status', ('draft', 'approved', 'retired'))]:
        _require(isinstance(challenge[field], str) and challenge[field] in allowed, 'invalid ' + field)
    prompt = challenge['prompt']
    _require(isinstance(prompt, str) and 1 <= len(prompt) <= 4000 and bool(prompt.strip()), 'invalid prompt')
    tokens = challenge['max_output_tokens']
    _require(type(tokens) is int and 100 <= tokens <= 2000, 'invalid max_output_tokens')
    _require('notes' not in challenge or isinstance(challenge['notes'], str), 'notes must be a string')
    if challenge['kind'] == 'open':
        _require(challenge['checker'] is None, 'open challenge must have null checker')
    else:
        _checker(challenge['checker'])


def check(challenge, answer_text):
    """Check a verifiable answer. Invalid checker/input raises ValueError.

    Choice letters are case-sensitive, as specified by their options. Numeric
    extraction accepts signs, decimals and scientific notation, including prose.
    """
    _require(isinstance(challenge, Mapping), 'challenge must be an object')
    kind = _checker(challenge.get('checker'))
    _require(isinstance(answer_text, str), 'answer_text must be a string')
    checker = challenge['checker']
    if kind == 'exact':
        actual, expected = answer_text.strip(), checker['answer']
        if not checker.get('case_sensitive', False):
            actual, expected = actual.casefold(), expected.casefold()
        return actual == expected
    if kind == 'numeric':
        found = NUMBER.search(answer_text)
        if found is None:
            return False
        value = float(found.group())
        return math.isfinite(value) and abs(value - checker['answer']) <= checker.get('tolerance', 0)
    if kind == 'choice':
        pattern = r'(?<!\w)[' + ''.join(checker['options']) + r'](?!\w)'
        found = re.search(pattern, answer_text)
        return found is not None and found.group() == checker['answer']
    _require(len(answer_text) <= 4000, 'regex answer exceeds 4000 characters')
    # Length limits alone do not bound Python backtracking (e.g. (a+)+$).
    # Isolate matching so an adversarial pattern can always be terminated.
    try:
        result = subprocess.run(
            [sys.executable, '-I', '-c',
             'import json,re,sys; p,a=json.load(sys.stdin); '
             'print(int(re.fullmatch(p,a) is not None))'],
            input=json.dumps([checker['pattern'], answer_text.strip()]),
            text=True, capture_output=True, timeout=1, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ValueError('regex exceeded execution budget or failed') from exc
    return result.stdout.strip() == '1'
