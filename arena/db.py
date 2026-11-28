"""Small psycopg 3 record helpers. Callers own transactions; helpers never commit."""
from datetime import datetime, timezone
from pathlib import Path
import os
import threading

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tee_arena.checkers import validate_challenge

FIELDS = {
    'challenges': ('id', 'kind', 'category', 'prompt', 'checker', 'max_output_tokens',
                   'language', 'source', 'status', 'notes', 'submitted_by'),
    'answers': ('id', 'challenge_id', 'agent_id', 'generation', 'genome_version',
                'constitution_fingerprint', 'text', 'charged_microusd', 'created_at', 'withheld_at'),
    'voters': ('id', 'created_at'),
    'pairings': ('id', 'voter_id', 'challenge_id', 'left_answer_id', 'right_answer_id',
                 'left_agent_id', 'right_agent_id', 'created_at', 'expired_at'),
    'votes': ('id', 'pairing_id', 'voter_id', 'challenge_id', 'left_answer_id',
              'right_answer_id', 'winner', 'weight', 'created_at'),
}


class DatabaseBusy(Exception):
    """Bounded local admission exhausted; caller should apply controlled backpressure."""


_ADMISSION_LOCK = threading.Lock()
_ADMISSION = None


def _admission():
    global _ADMISSION
    with _ADMISSION_LOCK:
        if _ADMISSION is None:
            raw = os.environ.get('KLADOS_DB_CONNECTION_LIMIT', '8')
            if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 64:
                raise DatabaseBusy('Invalid database admission configuration')
            _ADMISSION = threading.BoundedSemaphore(int(raw))
        return _ADMISSION


class AdmittedConnection(psycopg.Connection):
    """A permit follows the physical connection, including exceptional context exit."""

    _permit = None

    def close(self):
        try:
            super().close()
        finally:
            permit, self._permit = self._permit, None
            if permit is not None:
                permit.release()


def connect(dsn):
    """Bound connection count and handshake time; callers still own transactions.

    Admission is per process, initialized once. Deployment must allocate the sum of
    API process limits plus worker/admin headroom below PostgreSQL max_connections.
    A saturated process refuses quickly rather than accumulating waiting sockets.
    """
    permit = _admission()
    if not permit.acquire(timeout=0.25):
        raise DatabaseBusy('Database admission saturated')
    try:
        conn = AdmittedConnection.connect(dsn, row_factory=dict_row, connect_timeout=5)
    except BaseException:
        permit.release()
        raise
    conn._permit = permit
    return conn


def migrate(conn):
    """Apply versioned plain SQL migrations exactly once in the caller's transaction."""
    conn.execute('SELECT pg_advisory_xact_lock(73423188)')
    conn.execute('CREATE TABLE IF NOT EXISTS arena_migrations (name text PRIMARY KEY)')
    for path in sorted(Path(__file__).with_name('migrations').glob('*.sql')):
        if conn.execute('SELECT 1 FROM arena_migrations WHERE name = %s', (path.name,)).fetchone():
            continue
        conn.execute(path.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO arena_migrations VALUES (%s)', (path.name,))


def _record(row):
    if row is None:
        return None
    return {key: value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value
            for key, value in row.items() if not (key == 'notes' and value is None)}


def _insert(conn, table, record):
    columns = [name for name in FIELDS[table] if name in record]
    values = []
    for name in columns:
        value = record[name]
        if name == 'checker' and value is not None:
            value = Jsonb(value)
        if name == 'created_at' or (name in ('withheld_at', 'expired_at') and value is not None):
            value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
            if not isinstance(value, datetime) or value.utcoffset() != timezone.utc.utcoffset(value):
                raise ValueError('created_at must be UTC')
        values.append(value)
    query = sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING *').format(
        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
        sql.SQL(',').join(sql.Placeholder() for _ in columns))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, values)
        return _record(cur.fetchone())


def _get(conn, table, identifier):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql.SQL('SELECT * FROM {} WHERE id = %s').format(sql.Identifier(table)), (identifier,))
        return _record(cur.fetchone())


def _list(conn, table):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(table)))
        return [_record(row) for row in cur.fetchall()]


def insert_challenge(conn, record):
    validate_challenge(record)
    return _insert(conn, 'challenges', record)


def insert_answer(conn, record):
    return _insert(conn, 'answers', record)


def insert_voter(conn, record):
    return _insert(conn, 'voters', record)


def insert_pairing(conn, record):
    return _insert(conn, 'pairings', record)


def insert_vote(conn, record):
    return _insert(conn, 'votes', record)


def get_challenge(conn, identifier):
    return _get(conn, 'challenges', identifier)


def get_answer(conn, identifier):
    return _get(conn, 'answers', identifier)


def get_voter(conn, identifier):
    return _get(conn, 'voters', identifier)


def get_pairing(conn, identifier):
    return _get(conn, 'pairings', identifier)


def get_vote(conn, identifier):
    return _get(conn, 'votes', identifier)


def list_challenges(conn):
    return _list(conn, 'challenges')


def list_answers(conn):
    return _list(conn, 'answers')


def list_voters(conn):
    return _list(conn, 'voters')


def list_pairings(conn):
    return _list(conn, 'pairings')


def list_votes(conn):
    return _list(conn, 'votes')
