"""Durable mutation identity without persisting bearer credentials or request bodies."""
import hashlib
import json
import re

from fastapi import HTTPException
from psycopg.types.json import Jsonb

LOCK_NAMESPACE = 73423302


def request_identity(request, body):
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise HTTPException(400, 'Invalid request')
    supplied = parsed.get('request_id')
    header = request.headers.get('idempotency-key')
    if header is not None and supplied is not None and header != supplied:
        raise HTTPException(400, 'Conflicting request id')
    key = header if header is not None else supplied
    if not isinstance(key, str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', key) is None:
        raise HTTPException(400, 'Idempotency-Key required')
    parsed.pop('request_id', None)
    digest = hashlib.sha256(json.dumps(parsed, sort_keys=True, separators=(',', ':'),
                                        ensure_ascii=False).encode('utf-8')).hexdigest()
    if request.url.path.endswith('/actions'):
        parsed['request_id'] = key
    return key, digest, parsed


def begin(conn, scope, key, endpoint, digest):
    # Take before domain/object locks. Authentication's token lock remains first.
    conn.execute('SELECT pg_advisory_xact_lock(%s,hashtext(%s))',
                 (LOCK_NAMESPACE, scope + ':' + key))
    row = conn.execute('SELECT * FROM society_mutation_receipts WHERE scope=%s AND request_id=%s',
                        (scope, key)).fetchone()
    if row is not None:
        if row['endpoint'] != endpoint or row['body_sha256'] != digest:
            raise HTTPException(409, 'Request id already used')
        if row['credential_once']:
            # Never retain/reissue a bearer token to make a lost response convenient.
            raise HTTPException(409, 'Credential already issued; verified owner recovery required')
        return row['outcome']
    return None


def finish(conn, scope, key, endpoint, digest, result, now):
    once = endpoint.endswith('/register') or endpoint.endswith('/credentials/rotate')
    safe = {k: v for k, v in result.items() if k != 'token'}
    conn.execute('''INSERT INTO society_mutation_receipts
        (scope,request_id,endpoint,body_sha256,outcome,credential_once,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''',
        (scope, key, endpoint, digest, Jsonb(safe), once, now))
