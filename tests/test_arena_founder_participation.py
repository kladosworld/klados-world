"""KW-028 participation on disposable PG17; all inference is mocked."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from psycopg import sql

from arena import activity_settings, administration, db, founder_batch, founder_status
from arena import lifecycle, society, society_life, worker
from tee_arena.society import decisions, rules
from tests.arena_support import DatabaseCase, DSN

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
RULES = rules.load()


class FounderParticipationTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn, RULES, NOW)
        self.conn.commit()

    def change(self, target, *, identifier='alder', all_founders=False, apply=True):
        return founder_status.change(self.conn, NOW, target_state=target,
            identifier=None if all_founders else identifier, all_founders=all_founders,
            reference='SYNTHETIC-PO-APPROVAL', approved_by='test-operator', apply=apply)

    def org(self):
        org = self.conn.execute('''INSERT INTO society_organizations
            (name,charter_text,join_policy,default_rule,created_by,created_tick,status)
            VALUES ('Synthetic','A charter','open','{}','alder',0,'active') RETURNING id''').fetchone()['id']
        self.conn.execute("INSERT INTO society_memberships(org_id,agent_id,role,joined_tick) VALUES (%s,'alder','leader',0)", (org,))
        return org

    def test_individual_all_transitions_preview_batch_and_idempotency(self):
        before = self.conn.execute('SELECT to_jsonb(a)-\'participation\' AS row FROM society_agents a ORDER BY id').fetchall()
        self.assertFalse(self.change('retired', apply=False)['applied'])
        self.assertEqual(founder_status.history(self.conn), [])
        for state in ('inactive', 'dormant', 'retired', 'active'):
            result = self.change(state)
            self.assertEqual(result['changed'], 1)
            self.assertEqual(administration.state(self.conn, 'alder'), state)
            self.assertEqual(self.change(state)['changed'], 0)
        all_result = self.change('inactive', all_founders=True)
        self.assertEqual(all_result['changed'], 4)
        rows = self.conn.execute("SELECT batch_id FROM society_participation_events WHERE to_state='inactive' ORDER BY id DESC LIMIT 4").fetchall()
        self.assertEqual({r['batch_id'] for r in rows}, {all_result['batch_id']})
        self.assertEqual(self.conn.execute('SELECT to_jsonb(a)-\'participation\' AS row FROM society_agents a ORDER BY id').fetchall(), before)

    def test_inactive_keeps_roles_dormant_retired_release_leadership_only(self):
        org = self.org()
        self.change('inactive')
        self.assertEqual(self.conn.execute('SELECT role FROM society_memberships WHERE org_id=%s', (org,)).fetchone()['role'], 'leader')
        self.change('dormant')
        self.assertEqual(self.conn.execute('SELECT role,left_tick FROM society_memberships WHERE org_id=%s', (org,)).fetchone(), {'role': 'member', 'left_tick': None})
        self.change('active')
        self.assertEqual(self.conn.execute('SELECT role FROM society_memberships WHERE org_id=%s', (org,)).fetchone()['role'], 'member')
        self.change('retired')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_memberships').fetchone()['n'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_org_events').fetchone()['n'], 1)

    def test_admin_lock_not_overridden_by_activity_or_refresh(self):
        for state in ('inactive', 'dormant', 'retired'):
            self.change(state)
            self.assertFalse(lifecycle.touch(self.conn, 'alder', NOW+timedelta(days=9)))
            lifecycle.refresh(self.conn, NOW+timedelta(days=10))
            self.assertEqual(administration.state(self.conn, 'alder'), state)
            self.assertFalse(society.claim_turn(self.conn, 'alder', 50, NOW))
            gateway = Mock()
            instance = worker.Worker(self.conn, RULES, gateway, now=lambda: NOW)
            self.assertIsNone(instance.reflect('alder', 0, 50))
            gateway.call.assert_not_called()

    def test_auto_dormant_founder_gets_turn_and_returns_without_role_restore(self):
        self.org()
        lifecycle.refresh(self.conn, NOW+timedelta(days=7))
        detail = administration.describe(self.conn, 'alder')
        self.assertEqual((detail['participation'], detail['source'], detail['can_participate']), ('dormant', 'auto', True))
        society.set_paused(self.conn, RULES, False, NOW+timedelta(days=7), 'synthetic')
        self.conn.execute('UPDATE society_clock SET tick=168')
        self.assertIn('alder', society.due_turns(self.conn, (NOW+timedelta(days=7)).replace(minute=59)))
        self.assertTrue(society.claim_turn(self.conn, 'alder', 168, NOW))
        self.assertTrue(lifecycle.touch(self.conn, 'alder', NOW+timedelta(days=7, seconds=1)))
        self.assertEqual(administration.state(self.conn, 'alder'), 'active')
        self.assertEqual(self.conn.execute('SELECT role FROM society_memberships').fetchone()['role'], 'member')

    def test_admin_dormant_conversion_locks_existing_auto_dormant(self):
        lifecycle.refresh(self.conn, NOW+timedelta(days=7))
        result = self.change('dormant')
        self.assertEqual(result['changed'], 1)
        self.assertEqual(administration.describe(self.conn, 'alder')['source'], 'admin')
        self.assertFalse(lifecycle.touch(self.conn, 'alder', NOW+timedelta(days=8)))

    def test_pending_paid_decision_expires_without_actions_or_new_call(self):
        society.set_paused(self.conn, RULES, False, NOW, 'synthetic')
        self.conn.execute('UPDATE society_clock SET tick=1')
        self.assertTrue(society.claim_turn(self.conn, 'alder', 1, NOW))
        founder_batch.save(self.conn, 'alder', 1,
            decisions.parse_batch('{"actions":[{"action":"post","text":"Do not publish"}]}'),
            activity_settings.effective(self.conn, 1), 123, 'mock')
        self.conn.commit()
        self.change('inactive')
        self.assertEqual(founder_batch.execute(self.conn, RULES, 'alder', 1, NOW), 'failed')
        row = self.conn.execute("SELECT private,charged_microusd FROM society_turns WHERE agent_id='alder'").fetchone()
        self.assertEqual(row['private']['error'], 'participation_unavailable')
        self.assertEqual(row['charged_microusd'], 123)
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_posts').fetchone())

    def test_clock_required_atomic_all_and_concurrent_idempotency(self):
        self.conn.commit()
        def run(_):
            with db.connect(DSN) as conn:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
                return founder_status.change(conn,NOW,target_state='inactive',identifier='alder',
                    reference='synthetic',approved_by='operator',apply=True)['changed']
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(sum(pool.map(run, range(3))), 1)
        self.assertEqual(len(founder_status.history(self.conn, 'alder')), 1)
        self.conn.execute("UPDATE society_agents SET participation='suspended' WHERE id='birch'")
        before = self.conn.execute('SELECT id,participation FROM society_agents ORDER BY id').fetchall()
        with self.assertRaises(society.SocietyError):
            self.change('retired', all_founders=True)
        self.assertEqual(self.conn.execute('SELECT id,participation FROM society_agents ORDER BY id').fetchall(), before)
        self.conn.execute('DELETE FROM society_clock')
        with self.assertRaises(society.SocietyError):
            self.change('active')

    def test_auto_dormant_reflections_are_due_admin_dormant_are_not(self):
        self.conn.execute('UPDATE society_clock SET tick=168')
        lifecycle.refresh(self.conn, NOW+timedelta(days=7))
        self.assertIn(('alder', 6), society_life.reflections_due(self.conn))
        self.change('dormant')
        self.assertFalse(any(a == 'alder' for a, _ in society_life.reflections_due(self.conn)))

