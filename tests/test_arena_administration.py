"""Compatibility wrapper never creates a second administrative state projection."""
from datetime import datetime, timezone
from arena import administration, founder_status, society
from tee_arena.society import rules
from tests.arena_support import DatabaseCase

NOW=datetime(2026,11,26,tzinfo=timezone.utc)
RULES=rules.load()

class AdministrationTests(DatabaseCase):
    def setUp(self):
        super().setUp(); society.found(self.conn,RULES,NOW); self.conn.commit()

    def change(self,value):
        return administration.change(self.conn,NOW,target_state=value,identifier='alder',
            reference='SYNTHETIC',approved_by='operator',apply=True)

    def test_state_is_actual_participation_and_security_is_not_cleared(self):
        self.assertEqual(administration.state(self.conn,'alder'),'active')
        self.change('inactive')
        self.assertEqual(administration.state(self.conn,'alder'),'inactive')
        self.assertFalse(administration.is_active(self.conn,'alder'))
        self.change('active')
        self.conn.execute("UPDATE society_agents SET participation='suspended' WHERE id='alder'")
        self.assertEqual(administration.describe(self.conn,'alder')['participation_source'],'security')
        for target in ('active','inactive','dormant','retired'):
            with self.assertRaises(society.SocietyError): self.change(target)
        self.assertEqual(administration.state(self.conn,'alder'),'suspended')

    def test_preview_is_default_and_canonical_history_is_append_only(self):
        result=administration.change(self.conn,NOW,target_state='retired',identifier='alder',
            reference='SYNTHETIC',approved_by='operator')
        self.assertFalse(result['applied'])
        self.assertEqual(administration.state(self.conn,'alder'),'active')
        self.change('retired'); self.change('retired')
        self.assertEqual(len(founder_status.history(self.conn,'alder')),1)
        with self.assertRaises(Exception),self.conn.transaction():
            self.conn.execute('DELETE FROM society_participation_events')

    def test_operator_cli_matches_minimal_json_contract_without_private_data(self):
        from contextlib import contextmanager, redirect_stdout
        import io
        import json
        import os
        from unittest.mock import patch
        from arena import runtime
        @contextmanager
        def connection(_):
            yield self.conn
            self.conn.commit()
        def run(args):
            out = io.StringIO()
            with patch('arena.runtime.db.connect', side_effect=connection), \
                 patch.dict(os.environ, ARENA_DATABASE_URL='synthetic'), redirect_stdout(out):
                code = runtime.main(['founders', *args])
            self.assertEqual(code, 0)
            return json.loads(out.getvalue())
        initial = run(['status'])
        self.assertEqual([r['id'] for r in initial['founders']], ['alder','birch','cedar','hazel'])
        self.assertEqual(set(initial['founders'][0]), {'id','participation','source','since'})
        args = ['set','--all','--state','inactive','--reference','SYNTHETIC-PO','--approved-by','operator']
        preview = run(args)
        self.assertTrue(preview['preview'])
        self.assertTrue(all(r['participation']=='active' for r in run(['status'])['founders']))
        applied = run([*args,'--yes'])
        self.assertFalse(applied['preview'])
        self.assertIs(type(applied['batch_id']), int)
        events = run(['history'])['events']
        self.assertEqual(len(events), 4)
        self.assertEqual({r['batch_id'] for r in events}, {applied['batch_id']})
        self.assertEqual({r['to'] for r in events}, {'inactive'})

    def test_external_social_dormancy_is_visible_without_bypassing_admin_controls(self):
        from arena import lifecycle
        self.conn.execute("INSERT INTO society_agents(id,name,kind,born_tick,created_at) VALUES ('ext-synthetic','Synthetic external','external',0,%s)", (NOW,))
        self.conn.execute("INSERT INTO society_agent_lifecycle(agent_id,state,last_active_at,changed_at) VALUES ('ext-synthetic','dormant',%s,%s)",(NOW,NOW))
        value = administration.describe(self.conn,'ext-synthetic')
        self.assertEqual((value['participation'],value['participation_label'],value['participation_source']),('active','dormant','auto'))
        self.assertTrue(administration.is_active(self.conn,'ext-synthetic'))
        administration.change(self.conn,NOW,target_state='inactive',identifier='ext-synthetic',
            reference='synthetic hold',approved_by='operator',apply=True)
        self.assertFalse(lifecycle.touch(self.conn,'ext-synthetic',NOW))
        value = administration.describe(self.conn,'ext-synthetic')
        self.assertEqual((value['participation_label'],value['participation_source']),('inactive','admin'))
        self.assertFalse(value['can_participate'])
