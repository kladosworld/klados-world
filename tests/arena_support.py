"""Isolated PostgreSQL fixtures; no external services or model calls."""
import os
import unittest
from uuid import uuid4

try:
    import psycopg
    from psycopg import sql
    from arena import db
except ImportError:
    psycopg = None

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
