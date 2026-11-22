"""One explicit, non-destructive chapter boundary. No ledger or clock mutation."""
from datetime import datetime, timezone
import os

from arena import society
from tee_arena.society import prompts, shared_purpose

SINCE = "COALESCE((SELECT started_at FROM society_eras ORDER BY id DESC LIMIT 1), TIMESTAMPTZ '0001-01-01T00:00:00Z')"
START_TICK = 'COALESCE((SELECT started_tick FROM society_eras ORDER BY id DESC LIMIT 1), 0)'
SCHEDULER_LOCK = 73423189


def current(conn):
    row=conn.execute('SELECT * FROM society_eras ORDER BY id DESC LIMIT 1').fetchone()
    # Empty new table is the existing era, never a reason to seed or rewrite data.
    return row if row is not None else {
        'id':1, 'started_at':datetime.min.replace(tzinfo=timezone.utc), 'started_tick':0,
        'purpose':None, 'purpose_sha256':None, 'reference':None, 'approved_by':None}


def require_purpose(conn, purpose):
    era=current(conn)
    if era['id']==2 and (purpose is None or purpose.version!=era['purpose']
                         or purpose.fingerprint()!=era['purpose_sha256']):
        raise society.SocietyError('era_purpose_mismatch')


def start(conn, rules, now, *, purpose, reference, approved_by, apply=False):
    """Preview by default. Caller owns commit. Only the one-time Era 2 is supported."""
    if type(apply) is not bool or purpose!=shared_purpose.EVOLUTION_VERSION:
        raise society.SocietyError('invalid_era_request')
    for value in (reference,approved_by):
        if not isinstance(value,str) or not value.strip() or len(value)>200 or any(ord(c)<32 for c in value):
            raise society.SocietyError('era_approval_required')
    if os.environ.get('KLADOS_EXTERNAL_AGENTS','off')!='off':
        raise society.SocietyError('external_registration_must_be_off')
    registry=shared_purpose.validate_reviewed_registry(shared_purpose.load_registry())
    selected=registry.get(purpose)
    if selected is None:
        raise society.SocietyError('era_purpose_unavailable')
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout='2s'")
        conn.execute("SET LOCAL statement_timeout='5s'")
        if not conn.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(SCHEDULER_LOCK,)).fetchone()['ok']:
            raise society.SocietyError('worker_must_be_stopped')
        state=society.clock(conn,lock=True)
        if not state['paused'] or not rules.persistent:
            raise society.SocietyError('paused_persistent_society_required')
        before=current(conn)
        if before['id']==2:
            if (before['purpose'],before['purpose_sha256'],before['reference'],before['approved_by'])!=(purpose,selected.fingerprint(),reference,approved_by):
                raise society.SocietyError('era_already_started_with_different_approval')
            return {'era':2,'changed':False,'preview':not apply,'started_tick':before['started_tick']}
        expected=prompts.load_founders()
        rows=conn.execute("SELECT id,name,kind,status,participation,public_description FROM society_agents WHERE kind='founder' ORDER BY id FOR UPDATE").fetchall()
        if len(rows)!=4 or any(r['id'] not in expected or r['name']!=expected[r['id']]['name']
                or r['public_description']!=expected[r['id']]['personality']
                or r['status']!='alive' or r['participation']!='active' for r in rows):
            raise society.SocietyError('active_identity_v2_founders_required')
        if conn.execute("SELECT 1 FROM society_turns WHERE status='claimed' LIMIT 1").fetchone():
            raise society.SocietyError('claimed_turns_require_reconciliation')
        result={'era':2,'changed':False,'preview':not apply,'started_tick':state['tick'],
                'purpose':purpose,'purpose_sha256':selected.fingerprint()}
        if apply:
            # Server time avoids an operator-supplied backdate including old social data.
            conn.execute('''INSERT INTO society_eras(id,started_at,started_tick,purpose,purpose_sha256,reference,approved_by)
                VALUES (2,clock_timestamp(),%s,%s,%s,%s,%s)''',
                (state['tick'],purpose,selected.fingerprint(),reference,approved_by))
            society.event(conn,rules,state['tick'],'era_started',now,era=2,purpose=purpose,
                          purpose_sha256=selected.fingerprint(),reference=reference,approved_by=approved_by)
            result['changed']=True
        return result
