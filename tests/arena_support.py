"""Isolated PostgreSQL fixtures; no external services or model calls."""
import os
import atexit
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from uuid import uuid4

try:
    import psycopg
    from psycopg import sql
    from arena import db
except ImportError:
    psycopg = None

def _ci_database():
    """Use an ephemeral, loopback-only DB from runner binaries; no service or package changes.

    Explicit test DSNs always win. CI must fail rather than silently skip DB regressions.
    Never runs on the deployment host or during ordinary application startup.
    """
    if os.getenv('ARENA_TEST_DSN') or os.getenv('GITHUB_ACTIONS') != 'true' or os.name != 'posix':
        return
    candidates = sorted(Path('/usr/lib/postgresql').glob('*/bin'), reverse=True)
    binary = next((p for p in candidates if (p / 'initdb').is_file() and (p / 'pg_ctl').is_file()), None)
    if binary is None or psycopg is None:
        raise RuntimeError('CI requires preinstalled PostgreSQL binaries and psycopg; no DB skips allowed')
    directory = tempfile.TemporaryDirectory(prefix='kw-pg-')
    root = Path(directory.name)
    data = root / 'data'
    started = False

    def stop():
        if started or (data / 'postmaster.pid').exists():
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        directory.cleanup()

    try:
        subprocess.run([str(binary / 'initdb'), '-D', str(data), '-U', 'kw_test_runner',
                        '-A', 'trust', '--no-sync'], check=True, capture_output=True, timeout=30)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'),
                        '-o', f'-h 127.0.0.1 -p {port} -k {root}', '-w', 'start'],
                       check=True, capture_output=True, timeout=30)
        started = True
        os.environ['ARENA_TEST_DSN'] = f'host=127.0.0.1 port={port} dbname=postgres user=kw_test_runner'
        os.environ['ARENA_REQUIRE_DB'] = '1'
    except Exception:
        stop()
        raise
    atexit.register(stop)


_ci_database()
DSN = os.getenv('ARENA_TEST_DSN')
if os.getenv('ARENA_REQUIRE_DB') == '1' and not (DSN and psycopg):
    raise RuntimeError('ARENA_REQUIRE_DB=1 requires psycopg and ARENA_TEST_DSN')
NOW = '2026-11-02T00:00:00+00:00'


@unittest.skipUnless(DSN and psycopg, 'requires psycopg and ARENA_TEST_DSN')
class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(DSN)
        self.schema = 'test_' + uuid4().hex
        self.conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        db.migrate(self.conn)
        self.conn.commit()
        self.addCleanup(self.cleanup_database)

    def cleanup_database(self):
        self.conn.rollback()
        self.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.conn.commit()
        self.conn.close()

    def seed(self):
        for identifier in ['voter-a', 'voter-b']:
            db.insert_voter(self.conn, dict(id=identifier, created_at=NOW))
        for identifier in ['ch-0001', 'ch-0002']:
            db.insert_challenge(self.conn, dict(id=identifier, kind='open', category='logic',
                prompt='Explain the result.', checker=None, max_output_tokens=300,
                language='en', source='public', status='approved', submitted_by='voter-b'))
        for identifier, agent, challenge in [('ans-a', 'agent-a', 'ch-0001'),
                ('ans-b', 'agent-b', 'ch-0001'), ('ans-c', 'agent-a', 'ch-0001'),
                ('ans-d', 'agent-d', 'ch-0002')]:
            db.insert_answer(self.conn, dict(id=identifier, challenge_id=challenge,
                agent_id=agent, generation=1, genome_version='b' * 64,
                constitution_fingerprint='a' * 64, text='Answer ' + identifier,
                charged_microusd=12, created_at=NOW))

    def pairing(self, identifier='pair-1', **changes):
        record = dict(id=identifier, voter_id='voter-a', challenge_id='ch-0001',
            left_answer_id='ans-a', right_answer_id='ans-b', left_agent_id='agent-a',
            right_agent_id='agent-b', created_at=NOW)
        record.update(changes)
        return record

    def vote(self, identifier='vote-1', **changes):
        record = dict(id=identifier, pairing_id='pair-1', voter_id='voter-a',
            challenge_id='ch-0001', left_answer_id='ans-a', right_answer_id='ans-b',
            winner='left', weight=1.0, created_at=NOW)
        record.update(changes)
        return record
