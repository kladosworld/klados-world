"""Maintenance DDL fails cleanly under readers and preserves existing rows."""
from psycopg.errors import LockNotAvailable
from psycopg import sql
from arena import db, lifecycle, society
from tee_arena.society import rules
from tests.arena_support import DatabaseCase, DSN
from datetime import datetime, timezone

NOW = datetime(2026,11,28,tzinfo=timezone.utc)


class MaintenanceMigrationTests(DatabaseCase):
    def test_lock_timeout_rolls_back_and_quiescent_retry_preserves_state(self):
        society.found(self.conn,rules.load(),NOW)
        lifecycle.refresh(self.conn,NOW)
        self.conn.execute('DROP TABLE society_maintenance_checks')
        self.conn.execute('DROP INDEX external_owner_claims_agent')
        self.conn.execute('DROP INDEX society_commitments_due')
        self.conn.execute("DELETE FROM arena_migrations WHERE name='0030_bounded_maintenance.sql'")
        self.conn.commit()
        before = self.conn.execute('SELECT * FROM society_agent_lifecycle ORDER BY agent_id').fetchall()
        with db.connect(DSN) as reader:
            reader.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            reader.execute('LOCK TABLE external_owner_claims IN ROW EXCLUSIVE MODE')
            with self.assertRaises(LockNotAvailable):
                with self.conn.transaction():
                    self.conn.execute("SET LOCAL lock_timeout='100ms'")
                    db.migrate(self.conn)
        self.assertIsNone(self.conn.execute("SELECT to_regclass('society_maintenance_checks') AS name").fetchone()['name'])
        db.migrate(self.conn)
        db.migrate(self.conn)
        after = self.conn.execute('SELECT * FROM society_agent_lifecycle ORDER BY agent_id').fetchall()
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_maintenance_checks').fetchone()['n'],0)
        self.assertEqual(before, after)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_agents WHERE kind='founder'").fetchone()['n'],4)
