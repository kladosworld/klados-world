"""Public institutional projections fail closed and never expose Personal Trust."""
import unittest
from pathlib import Path
from arena import society_public, society, institution_context, society_life, activity_settings
from tee_arena.society import rules
from tests.arena_support import DatabaseCase
from datetime import datetime, timezone
from unittest.mock import patch
from psycopg.types.json import Jsonb


class PublicInstitutionDatabaseTests(DatabaseCase):
    def test_empty_society_and_founder_profiles_are_read_only(self):
        for kind in ('organizations','proposals','cases'):
            self.assertEqual(society_public._institution_rows(self.conn,kind),[])
        society.found(self.conn,rules.load(),datetime(2026,11,26,tzinfo=timezone.utc))
        before = self.conn.execute('SELECT to_jsonb(a) AS record FROM society_agents a ORDER BY id').fetchall()
        for name in ('alder','birch','cedar','hazel'):
            view = society_public.public_view(self.conn,'profile',name)
            self.assertEqual(set(view['reputation']),{'credibility','social_reputation','influence'})
            self.assertNotIn('trust',view)
            self.assertNotIn('memory',view)
            self.assertEqual(view['memberships'],[])
            context=institution_context.build(self.conn,name)
            self.assertEqual(context['organizations'],[])
        self.assertEqual(before,self.conn.execute('SELECT to_jsonb(a) AS record FROM society_agents a ORDER BY id').fetchall())

    def test_verified_source_clearance_and_takedown_bound_public_objects(self):
        now=datetime(2026,11,26,tzinfo=timezone.utc)
        policy=rules.load()
        society.found(self.conn,policy,now)
        society.set_paused(self.conn,policy,False,now,'synthetic')
        action={'action':'org_found','name':'Commons','charter':'Voluntary cooperation.',
                'join_policy':'open','default_rule':{'quorum_pct':10,'threshold_pct':50}}
        result=society_life.apply_action(self.conn,policy,'alder',0,action,now,
            action_key='public:org',post_origin='live',limits=activity_settings.DEFAULTS)
        self.assertNotIn('error',result)
        rows=society_public._institution_rows(self.conn,'organizations')
        self.assertEqual([row['name'] for row in rows],['Commons'])
        source=self.conn.execute('SELECT post_id FROM society_institution_texts').fetchone()['post_id']
        self.conn.execute('''INSERT INTO society_cases(filed_by,subject_ids,claim_text,status,outcome,opened_tick)
            VALUES ('alder',ARRAY['birch'],'An advisory disagreement.','open',%s,0)''',
            (Jsonb({'source_post_id':source,'ruling':{'proposal_id':'not-an-id','finding':{'private':'never expose'}}}),))
        case=society_public._institution_rows(self.conn,'cases')[0]
        self.assertEqual(case['claim_text'],'An advisory disagreement.')
        self.assertEqual(case['ruling'],{})
        self.assertNotIn('outcome',case)
        self.conn.execute("UPDATE society_institution_texts SET clearance='withheld'")
        self.assertEqual(society_public._institution_rows(self.conn,'organizations'),[])
        self.assertEqual(society_public._institution_rows(self.conn,'cases'),[])
        self.conn.execute("UPDATE society_institution_texts SET clearance='cleared'")
        self.conn.execute("UPDATE society_post_origins SET origin='mock'")
        self.assertEqual(society_public._institution_rows(self.conn,'organizations'),[])
        self.conn.execute("UPDATE society_post_origins SET origin='live'")
        self.conn.execute("UPDATE society_posts SET flagged=TRUE")
        self.assertEqual(society_public._institution_rows(self.conn,'organizations'),[])


class PublicInstitutionSourceTests(unittest.TestCase):
    def test_safe_dom_and_read_only_requests(self):
        script=Path('arena/static/institutions.js').read_text()
        self.assertNotIn('innerHTML',script)
        self.assertNotIn('method:',script)
        self.assertIn('textContent',script)
        self.assertIn('encodeURIComponent(id)',script)
        for page in ('organizations','governance','cases'):
            html=Path(f'arena/static/{page}.html').read_text()
            self.assertIn('institutions.js',html)
            self.assertNotIn('<form',html)

    def test_canonical_participation_labels_exclude_private_references(self):
        for label,source in [('active','auto'),('inactive','admin'),('dormant','admin'),
                             ('dormant','auto'),('retired','admin'),('suspended','security')]:
            with patch('arena.administration.describe',return_value={
                    'participation_label':label,'participation_source':source,
                    'reference':'private approval','approved_by':'private operator'}):
                self.assertEqual(society_public._participation(None,'alder'),
                    {'participation_label':label,'participation_source':source})

    def test_unknown_projection_is_rejected(self):
        with self.assertRaises(society.SocietyError):
            society_public._institution_rows(None,'personal_trust')
