"""Regular-index transaction/lock behavior on disposable PostgreSQL only."""
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from arena import db, society
from tee_arena.society import rules
from tests.arena_support import DatabaseCase, DSN

MIGRATION='0027_moderation_queue_indexes.sql'
INDEXES=('society_pending_review','society_optional_review','society_moderation_attempts')
NOW=datetime(2026,11,23,tzinfo=timezone.utc)


class Migration27Tests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn,rules.load(),NOW)
        for name in INDEXES:
            self.conn.execute(sql.SQL('DROP INDEX {}').format(sql.Identifier(name)))
        self.conn.execute('DELETE FROM arena_migrations WHERE name=%s',(MIGRATION,))
        self.conn.commit()

    def connection(self):
        conn=db.connect(DSN)
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        conn.commit()  # Keep the disposable namespace across rollback/timeout probes.
        return conn

    def insert(self,conn):
        conn.execute("""INSERT INTO society_posts(author_id,tick,text,created_at)
            VALUES ('alder',0,'Synthetic writer probe',%s)""",(NOW,))

    def indexes(self):
        return {row['indexname'] for row in self.conn.execute(
            'SELECT indexname FROM pg_indexes WHERE schemaname=%s',(self.schema,))} & set(INDEXES)

    def test_active_writer_causes_bounded_lock_timeout_and_no_partial_migration(self):
        with self.connection() as writer:
            self.insert(writer)
            self.conn.execute("SET LOCAL lock_timeout='150ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                db.migrate(self.conn)
            self.conn.rollback()
            self.assertEqual(self.indexes(),set())
            self.assertIsNone(self.conn.execute('SELECT 1 FROM arena_migrations WHERE name=%s',(MIGRATION,)).fetchone())
            writer.rollback()

    def test_index_locks_allow_reads_block_writes_until_transaction_commit(self):
        db.migrate(self.conn)
        locks=self.conn.execute("""SELECT c.relname,l.mode FROM pg_locks l JOIN pg_class c ON c.oid=l.relation
            WHERE l.pid=pg_backend_pid() AND c.relname IN ('society_posts','society_findings','society_events')""").fetchall()
        self.assertTrue(all(any(r['relname']==table and r['mode']=='ShareLock' for r in locks)
                            for table in ('society_posts','society_findings','society_events')))
        with self.connection() as other:
            self.assertEqual(other.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'],0)
            other.execute("SET LOCAL lock_timeout='150ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                self.insert(other)
            other.rollback()
            self.conn.commit()
            self.insert(other)  # Same writer succeeds after migration commit.
            other.rollback()
        self.assertEqual(self.indexes(),set(INDEXES))
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'],0)

    def test_precommit_interruption_rolls_back_indexes_and_registry_then_retries(self):
        before=self.conn.execute('SELECT * FROM society_agents ORDER BY id').fetchall()
        db.migrate(self.conn)
        self.conn.rollback()  # Simulated interruption before outer bootstrap transaction commit.
        self.assertEqual(self.indexes(),set())
        self.assertIsNone(self.conn.execute('SELECT 1 FROM arena_migrations WHERE name=%s',(MIGRATION,)).fetchone())
        db.migrate(self.conn)
        self.conn.commit()
        db.migrate(self.conn)
        self.assertEqual(self.indexes(),set(INDEXES))
        self.assertEqual(self.conn.execute('SELECT * FROM society_agents ORDER BY id').fetchall(),before)
