"""Verify transactional DROP INDEX reader/writer locks on disposable data."""
import psycopg
from psycopg import sql

from arena import db
from tests.arena_support import DatabaseCase, DSN

MIGRATION = '0026_like_per_post.sql'


class Migration26Tests(DatabaseCase):
    def test_drop_index_blocks_readers_and_writers_until_commit(self):
        # Reconstruct only the old index in the disposable schema; no live DB.
        self.conn.execute('''CREATE UNIQUE INDEX society_likes_agent_pair_day
            ON society_likes(from_id, target_id, day) WHERE from_kind='agent' ''')
        self.conn.execute('DELETE FROM arena_migrations WHERE name=%s', (MIGRATION,))
        self.conn.commit()
        before = self.conn.execute('SELECT * FROM society_likes').fetchall()
        db.migrate(self.conn)
        locks = self.conn.execute('''SELECT l.mode FROM pg_locks l
            JOIN pg_class c ON c.oid=l.relation WHERE l.pid=pg_backend_pid()
            AND c.relname='society_likes' ''').fetchall()
        self.assertIn('AccessExclusiveLock', [r['mode'] for r in locks])
        with db.connect(DSN) as other:
            other.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            other.commit()
            for statement in ('SELECT * FROM society_likes',
                              'DELETE FROM society_likes WHERE false'):
                other.execute("SET LOCAL lock_timeout='150ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    other.execute(statement)
                other.rollback()
            self.conn.commit()
            self.assertEqual(other.execute('SELECT * FROM society_likes').fetchall(), before)
        db.migrate(self.conn)  # Registered exactly once, harmless on retry.
        self.assertEqual(self.conn.execute('SELECT * FROM society_likes').fetchall(), before)
