"""Append-only founder allowances. No public administrator API or budget controls."""
from psycopg.types.json import Jsonb

from arena import society

DEFAULTS = {'post': 1, 'reply': 3, 'like': 5, 'max_actions': 9}
# Increases beyond the explicitly approved ceilings need another reviewed decision.
BOUNDS = DEFAULTS.copy()


def validate(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULTS):
        raise ValueError('invalid activity settings')
    if any(type(value) is not int or not 0 <= value <= BOUNDS[key]
           for key, value in values.items()):
        raise ValueError('activity settings outside approved bounds')
    return dict(values)


def effective(conn, tick):
    row = conn.execute('''SELECT id, values FROM society_settings
        WHERE scope='founders' AND effective_tick <= %s ORDER BY id DESC LIMIT 1''', (tick,)).fetchone()
    return {'version': row['id'] if row else 0,
            'values': validate(row['values'] if row else DEFAULTS)}


def history(conn):
    return [dict(row, created_at=row['created_at'].isoformat()) for row in conn.execute(
        "SELECT * FROM society_settings ORDER BY id")]


def change(conn, values, now, *, expected_version, reference, approved_by, apply=False):
    values = validate(values)
    if (not isinstance(reference, str) or not reference.strip() or len(reference) > 200
            or not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 100
            or type(expected_version) is not int or expected_version < 0):
        raise ValueError('approval reference and expected version required')
    # Clock first, same ordering as external actions and ticks. Prevent a mid-boundary race.
    clock = society.clock(conn, lock=True)
    last = conn.execute('SELECT id FROM society_settings ORDER BY id DESC LIMIT 1').fetchone()
    latest = last['id'] if last else 0
    if latest != expected_version:
        raise ValueError('activity settings version changed')
    result = {'applied': apply, 'expected_version': latest, 'values': values,
              'effective_tick': clock['tick'] + 1, 'reference': reference,
              'approved_by': approved_by}
    if apply:
        row = conn.execute('''INSERT INTO society_settings
            (scope, values, reference, approved_by, created_at, effective_tick, supersedes)
            VALUES ('founders',%s,%s,%s,%s,%s,%s) RETURNING id''',
            (Jsonb(values), reference, approved_by, now, result['effective_tick'], latest or None)).fetchone()
        result['version'] = row['id']
    return result


def revert(conn, version, now, **kwargs):
    row = conn.execute('SELECT values FROM society_settings WHERE id=%s', (version,)).fetchone()
    if version != 0 and row is None:
        raise ValueError('unknown settings version')
    return change(conn, DEFAULTS if version == 0 else row['values'], now, **kwargs)
