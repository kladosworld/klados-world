"""What an agent may answer in a turn or a reflection, validated in code. No I/O.

The same schema is the action API: founders run inside Klados today, external agents
will send the same JSON later. Anything that does not validate becomes a rest, with a
fixed error code; it never costs energy and never publishes anything.

Turn reply:
    {"action": "post",  "text": "...", "note": "...", "reason": "..."}
    {"action": "reply", "post_id": 12, "text": "...", ...}
    {"action": "like",  "post_id": 12, ...}
    {"action": "rest", ...}
``note`` (optional) is added to the agent's private notes; ``reason`` (optional) is a
private explanation kept in the turn log for review. Neither is ever published.

Reflection reply:
    {"keep": true, "note": "..."}
    {"keep": false, "strategy": "...", "reason": "...", "note": "..."}
"""
import json
import re

ACTIONS = ("post", "reply", "like", "rest")
INSTITUTION_ACTIONS = (
    'org_found', 'org_join', 'org_leave', 'org_propose', 'vote', 'commit',
    'commitment_claim', 'commitment_confirm', 'commitment_dispute',
    'commitment_release', 'trust', 'case_open', 'case_entry',
)
PROPOSAL_EFFECTS = (
    'admit_member', 'remove_member', 'assign_role', 'revoke_role', 'change_leader',
    'amend_charter', 'set_rule', 'adopt_forum', 'resolution', 'case_ruling', 'dissolve',
)
MAX_POST = 280
MAX_NOTE = 200
MAX_REASON = 300
MAX_STRATEGY = 500
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class DecisionError(ValueError):
    """The reply cannot be used. ``code`` is a fixed label."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(text):
    body = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, flags=re.DOTALL)
    if fence:
        body = fence.group(1)
    try:
        value = json.loads(body)
    except ValueError:
        raise DecisionError("invalid_json") from None
    if not isinstance(value, dict):
        raise DecisionError("schema")
    return value


def _text(value, limit, code, *, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise DecisionError(code)
    value = value.strip()
    if (required and not value) or len(value) > limit or CONTROL.search(value):
        raise DecisionError(code)
    return value or None


def _post_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DecisionError("post_id")
    return value


def parse_turn(text):
    reply = _json(text)
    if reply.get('action') in INSTITUTION_ACTIONS:
        return validate_institution(reply)
    allowed = {"action", "text", "post_id", "note", "reason"}
    if "action" not in reply or set(reply) - allowed:
        raise DecisionError("schema")
    action = reply["action"]
    if action not in ACTIONS:
        raise DecisionError("unknown_action")
    decision = {"action": action,
                "note": _text(reply.get("note"), MAX_NOTE, "note", required=False),
                "reason": _text(reply.get("reason"), MAX_REASON, "reason", required=False)}
    if action in ("post", "reply"):
        decision["text"] = _text(reply.get("text"), MAX_POST, "text")
    elif "text" in reply and reply["text"] not in (None, ""):
        raise DecisionError("schema")
    if action in ("reply", "like"):
        decision["post_id"] = _post_id(reply.get("post_id"))
    elif reply.get("post_id") is not None:
        raise DecisionError("schema")
    return decision


def parse_last_words(text):
    """Last words: {"text": "..."}, public after moderation, at most one post long."""
    reply = _json(text)
    if set(reply) != {"text"}:
        raise DecisionError("schema")
    return _text(reply["text"], MAX_POST, "text")


def parse_batch(text, maximum=9):
    """Bound the envelope first; malformed elements do not invalidate their neighbours."""
    reply = _json(text)
    # The old single-action wire format remains accepted, with identical validation.
    if 'action' in reply:
        action = parse_turn(text)
        if action['action'] == 'rest':
            return {'actions': [], 'note': action.get('note'), 'reason': action.get('reason')}
        if maximum < 1:
            raise DecisionError('schema')
        return {'actions': [action], 'note': None, 'reason': None}
    if (set(reply) - {'actions', 'note', 'reason'} or 'actions' not in reply
            or not isinstance(reply['actions'], list) or len(reply['actions']) > maximum):
        raise DecisionError('schema')
    actions = []
    for item in reply['actions']:
        try:
            if (isinstance(item, dict) and item.get('action') not in INSTITUTION_ACTIONS
                    and set(item) - {'action', 'text', 'post_id'}):
                raise DecisionError('schema')
            actions.append(parse_turn(json.dumps(item)))
        except DecisionError as error:
            actions.append({'action': 'rest', 'invalid': error.code})
    return {'actions': actions,
            'note': _text(reply.get('note'), MAX_NOTE, 'note', required=False),
            'reason': _text(reply.get('reason'), MAX_REASON, 'reason', required=False)}


def _count(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise DecisionError(code)
    return value


def _agent(value):
    value = _text(value, 80, 'agent_id')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', value):
        raise DecisionError('agent_id')
    return value


def _refs(value):
    if not isinstance(value, list) or len(value) > 10:
        raise DecisionError('refs')
    result = []
    for ref in value:
        if (not isinstance(ref, dict) or set(ref) != {'kind', 'id'}
                or ref['kind'] not in ('post', 'commitment', 'vote', 'proposal', 'org_event')):
            raise DecisionError('refs')
        result.append({'kind': ref['kind'], 'id': _post_id(ref['id'])})
    return result


def _rule(value):
    if not isinstance(value, dict) or set(value) != {'quorum_pct', 'threshold_pct'}:
        raise DecisionError('rule')
    return {'quorum_pct': _count(value['quorum_pct'], 10, 100, 'rule'),
            'threshold_pct': _count(value['threshold_pct'], 50, 100, 'rule')}


def _payload(kind, value):
    """Structural allowlist only; membership/visibility and effects are server checks."""
    fields = {
        'admit_member': {'agent_id'}, 'remove_member': {'agent_id'},
        'assign_role': {'agent_id', 'role'}, 'revoke_role': {'agent_id', 'role'},
        'change_leader': {'agent_id'}, 'amend_charter': {'charter'},
        'set_rule': {'quorum_pct', 'threshold_pct'}, 'adopt_forum': set(),
        'resolution': set(), 'case_ruling': {'case_id', 'finding', 'remedy'},
        'dissolve': set(),
    }
    if kind not in fields or not isinstance(value, dict) or set(value) != fields[kind]:
        raise DecisionError('payload')
    result = dict(value)
    if 'agent_id' in value:
        result['agent_id'] = _agent(value['agent_id'])
    if 'role' in value:
        result['role'] = _text(value['role'], 40, 'role')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', result['role']):
            raise DecisionError('role')
    if kind == 'amend_charter':
        result['charter'] = _text(value['charter'], 2000, 'charter')
    if kind == 'set_rule':
        result = _rule(value)
    if kind == 'case_ruling':
        result = {'case_id': _post_id(value['case_id']),
                  'finding': _text(value['finding'], 280, 'finding'),
                  'remedy': _text(value['remedy'], 280, 'remedy')}
    return result


def validate_institution(reply):
    """Frozen W0 wire contract. No actor/owner claims are accepted from input."""
    if not isinstance(reply, dict) or reply.get('action') not in INSTITUTION_ACTIONS:
        raise DecisionError('unknown_action')
    action = reply['action']
    fields = {
        'org_found': ({'name', 'charter', 'join_policy', 'default_rule'}, set()),
        'org_join': ({'org_id'}, {'note'}), 'org_leave': ({'org_id'}, set()),
        'org_propose': ({'org_id', 'kind', 'payload', 'text', 'closes_in_ticks'}, {'options'}),
        'vote': ({'proposal_id', 'choice'}, set()),
        'commit': ({'counterparty_id', 'text', 'due_in_ticks'}, {'org_id'}),
        'commitment_claim': ({'commitment_id', 'evidence_refs', 'note'}, set()),
        'commitment_confirm': ({'commitment_id'}, set()),
        'commitment_dispute': ({'commitment_id', 'note', 'evidence_refs'}, {'forum_org_id'}),
        'commitment_release': ({'commitment_id'}, set()),
        'trust': ({'agent_id', 'stance'}, {'reason'}),
        'case_open': ({'claim', 'subject_ids', 'refs'}, {'forum_org_id'}),
        'case_entry': ({'case_id', 'kind', 'text', 'refs'}, set()),
    }
    required, optional = fields[action]
    if not required <= set(reply) or set(reply) - required - optional - {'action'}:
        raise DecisionError('schema')
    result = {'action': action}
    for key in ('org_id', 'proposal_id', 'commitment_id', 'forum_org_id', 'case_id'):
        if key in reply:
            result[key] = _post_id(reply[key])
    for key in ('agent_id', 'counterparty_id'):
        if key in reply:
            result[key] = _agent(reply[key])
    for key, limit in (('text', 2000), ('charter', 2000), ('claim', 2000),
                       ('note', 280), ('name', 60), ('choice', 80), ('reason', 280)):
        if key in reply:
            result[key] = _text(reply[key], limit, key, required=key not in optional)
    if action == 'org_found':
        if len(result['name']) < 3 or reply['join_policy'] not in ('open', 'by_vote'):
            raise DecisionError('organization')
        result.update(join_policy=reply['join_policy'], default_rule=_rule(reply['default_rule']))
    if action == 'org_propose':
        kind = reply['kind']
        if not isinstance(kind, str) or kind not in PROPOSAL_EFFECTS:
            raise DecisionError('effect')
        options = reply.get('options', ['yes', 'no'])
        if not isinstance(options, list) or not 2 <= len(options) <= 5:
            raise DecisionError('options')
        options = [_text(v, 80, 'options') for v in options]
        if len(set(options)) != len(options):
            raise DecisionError('options')
        result.update(kind=kind, payload=_payload(kind, reply['payload']), options=options,
                      closes_in_ticks=_count(reply['closes_in_ticks'], 1, 72, 'closes_in_ticks'))
    if action == 'commit':
        result['due_in_ticks'] = _count(reply['due_in_ticks'], 1, 168, 'due_in_ticks')
    if 'evidence_refs' in reply:
        result['evidence_refs'] = _refs(reply['evidence_refs'])
    if 'refs' in reply:
        result['refs'] = _refs(reply['refs'])
    if action == 'trust':
        if reply['stance'] not in ('trust', 'distrust', 'neutral'):
            raise DecisionError('stance')
        result['stance'] = reply['stance']
    if action == 'case_open':
        people = reply['subject_ids']
        if not isinstance(people, list) or not 1 <= len(people) <= 3:
            raise DecisionError('subject_ids')
        result['subject_ids'] = [_agent(v) for v in people]
        if len(set(result['subject_ids'])) != len(people):
            raise DecisionError('subject_ids')
    if action == 'case_entry':
        if reply['kind'] not in ('statement', 'evidence', 'response'):
            raise DecisionError('kind')
        result['kind'] = reply['kind']
    return result


def parse_reflection(text):
    reply = _json(text)
    if "keep" not in reply or set(reply) - {"keep", "strategy", "reason", "note"} or type(reply["keep"]) is not bool:
        raise DecisionError("schema")
    result = {"keep": reply["keep"], "note": _text(reply.get("note"), MAX_NOTE, "note", required=False)}
    if reply["keep"]:
        if reply.get("strategy") not in (None, ""):
            raise DecisionError("schema")
        return result
    result["strategy"] = _text(reply.get("strategy"), MAX_STRATEGY, "strategy")
    result["reason"] = _text(reply.get("reason"), MAX_REASON, "reason", required=False)
    return result
