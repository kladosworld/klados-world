"""Allowlisted public example snapshots, independent of agent identities or kinds."""
import json
from pathlib import Path
from fastapi import HTTPException

# Compatibility for the older public-page gate. The World routes accept generic ids.
FOUNDERS = tuple(path.stem for path in Path(__file__).with_name('static').joinpath('fixtures/agents').glob('*.json'))
CAUSES = ('out of energy', 'old age', 'exiled')
EVENTS = ('founded', 'danger', 'aged', 'death', 'last_words', 'penalty', 'post_withheld', 'post_popular')
SUMMARY = ('id', 'name', 'kind', 'energy', 'age_days', 'status', 'death_cause')
RELATIONS = ('replied_to', 'replies_from', 'liked', 'liked_by')
COUNTS = ('posts', 'replies', 'likes_given', 'likes_from_agents', 'likes_from_people')


def is_world_public(request):
    path = request.url.path
    return request.method in ('GET', 'HEAD') and (
        path in ('/society', '/society/square', '/society/stats', '/society/config.json',
                 '/static/society.js', '/static/society-session.js', '/static/society.css',
                 '/static/fixtures/feed.json', '/static/fixtures/square.json', '/static/fixtures/stats.json')
        or (path.startswith('/society/agents/') and '/' not in path[len('/society/agents/'):])
        or path.startswith('/static/fixtures/agents/'))


def _fields(value, names):
    return {key: value[key] for key in names if key in value} if isinstance(value, dict) else {}


def _numbers(value):
    return {key: count for key, count in value.items() if isinstance(key, str) and type(count) is int and count >= 0}


def _relations(value):
    return {name: _numbers(value.get(name, {})) for name in RELATIONS}


def _history(value):
    return [_fields(row, ('tick', 'energy')) for row in value]


def _post(value):
    result = _fields(value, ('id', 'text', 'likes', 'hours_ago', 'reply_to', 'flagged'))
    result['author'] = _fields(value.get('author'), ('id', 'name'))
    return result


def _fossil(value):
    if value is None:
        return None
    result = _fields(value, ('name', 'kind', 'description', 'born_tick', 'died_tick', 'last_words'))
    result['death_cause'] = value.get('death_cause') if value.get('death_cause') in CAUSES else None
    result['counts'] = _fields(value.get('counts'), COUNTS)
    result['relations'] = _relations(value.get('relations', {}))
    result['energy_history'] = _history(value.get('energy_history', []))
    return result


def project(kind, value):
    """Project public v2 fields before serialization; examples always stay marked."""
    result = {'_example': True}
    if kind == 'feed':
        result.update(_fields(value, ('tick',)))
        result['agents'] = [_fields(row, SUMMARY) for row in value.get('agents', [])]
        result['events'] = []
        for row in value.get('events', []):
            if row.get('kind') not in EVENTS:
                continue
            event = _fields(row, ('id', 'kind', 'hours_ago', 'text'))
            event['agent'] = _fields(row['agent'], ('id', 'name')) if row.get('agent') else None
            event['source'] = _fields(row['source'], ('type', 'id')) if row.get('source') else None
            result['events'].append(event)
    elif kind == 'square':
        result['posts'] = [_post(row) for row in value.get('posts', [])]
    elif kind == 'agent':
        result.update(_fields(value, (*SUMMARY, 'description', 'last_words')))
        result['energy_history'] = _history(value.get('energy_history', []))
        result['posts'] = [_post(row) for row in value.get('posts', [])]
        result['relations'] = _relations(value.get('relations', {}))
        result['fossil'] = _fossil(value.get('fossil'))
    elif kind == 'stats':
        result.update(_fields(value, ('tick',)))
        population = value.get('population', {})
        result['population'] = _fields(population, ('alive', 'dead'))
        result['population']['by_kind'] = _numbers(population.get('by_kind', {}))
        result['population']['death_causes'] = {k: n for k, n in _numbers(population.get('death_causes', {})).items() if k in CAUSES}
        result['activity_by_day'] = [_fields(row, ('day', 'post', 'reply', 'like', 'rest', 'likes_from_people', 'likes_from_agents'))
                                     for row in value.get('activity_by_day', [])]
        result['interactions'] = [_fields(row, ('from', 'to', 'type', 'count')) for row in value.get('interactions', [])
                                  if row.get('type') in ('reply', 'like')]
        result['events'] = {k: n for k, n in _numbers(value.get('events', {})).items() if k in EVENTS}
        result['published'] = _fields(value.get('published'), ('posts', 'replies'))
    else:
        raise ValueError('Unknown public data')
    return result


def fixture(root, resource):
    root = Path(root).resolve()
    parts = resource.split('/')
    if resource in ('feed.json', 'square.json', 'stats.json'):
        kind = resource[:-5]
    elif (len(parts) == 2 and parts[0] == 'agents' and parts[1].endswith('.json')
          and parts[1][:-5] not in ('', '.', '..') and '\\' not in resource):
        kind = 'agent'
    else:
        raise HTTPException(404, 'Public example not found')
    path = (root / resource).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, 'Public example not found')
    try:
        return project(kind, json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, AttributeError):
        raise HTTPException(503, 'Public example is temporarily unavailable') from None
