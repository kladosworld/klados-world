"""Synthetic ownership, independent batch participation and retirement contracts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
import re
from unittest.mock import patch

from arena import administration, external_agents as external, society, society_moderation
from tee_arena.society import rules
from tests.test_arena_external_agents import InviteCase


class OnboardingTests(InviteCase):
    def setUp(self):
        super().setUp()
        p=patch.dict(os.environ,KLADOS_RULES=str(rules.DEFAULT_PATH));p.start();self.addCleanup(p.stop)
        self.rules=rules.load()

    def raw_register(self,name='Synthetic Owner'):
        invitation=self.invitation()
        response=self.client.post('/api/agents/register',json=dict(invite=invitation['invite'],name=name,description='A synthetic identity.'))
        self.assertEqual(response.status_code,201)
        with self.connection() as review:
            external.review_profile(review,response.json()['id'],approve=True,reference='synthetic profile',now=self.now)
        return response.json()

    def issue(self,registered,handle='synthetic_owner'):
        response=self.client.post('/api/agents/me/owner-claim',headers=self.headers(registered),json={'handle':handle})
        self.assertEqual(response.status_code,200)
        return response.json()

    def verify(self,registered,claim,*,now=None,post_url=None,challenge=None):
        return external.verify_owner(self.conn,registered['id'],challenge=challenge or claim['challenge'],
            post_url=post_url or f"https://x.com/{claim['handle']}/status/12345",reference='synthetic proof',
            approved_by='test',attest=True,now=now or self.now)

    def active(self,name='Synthetic Owner',handle='synthetic_owner'):
        registered=self.raw_register(name);claim=self.issue(registered,handle)
        self.verify(registered,claim);self.conn.commit()
        return registered

    def unpause(self):
        society.set_paused(self.conn,self.rules,False,self.now,'synthetic')
        self.conn.commit()

    def test_hash_only_normalized_128bit_challenge_and_unverified_read_only(self):
        registered=self.raw_register();headers=self.headers(registered)
        self.assertEqual(self.client.get('/api/agents/me',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/agents/me/state',headers=headers).status_code,200)
        self.assertEqual(self.client.post('/api/agents/me/actions',headers=headers,json={'action':'rest','request_id':'before'}).status_code,403)
        claim=self.issue(registered,'＠ＦｅＬｉｘ_1')
        self.assertEqual(claim['handle'],'felix_1')
        self.assertTrue(re.fullmatch(r'KLADOS-OWN-'+registered['id']+r'-[A-Z2-7]{26}',claim['challenge']))
        row=dict(self.conn.execute('SELECT * FROM external_owner_claims').fetchone())
        self.assertNotIn(claim['challenge'],json.dumps(row,default=str))
        self.assertEqual((row['expires_at']-row['issued_at']).total_seconds(),86400)
        self.assertEqual(row['challenge_sha256'],external.auth.digest(claim['challenge']))
        self.assertFalse(self.client.get('/api/agents/tick',headers=headers).json()['can_act'])

    def test_supersede_expiry_replay_and_exact_operator_proof(self):
        registered=self.raw_register();first=self.issue(registered);second=self.issue(registered)
        self.assertEqual(self.conn.execute('SELECT status FROM external_owner_claims WHERE id=%s',(first['id'],)).fetchone()['status'],'superseded')
        for challenge,url,now in [(first['challenge'],None,None),
            (None,'https://x.com/other/status/123',None),
            (None,'https://x.com.evil.test/synthetic_owner/status/123',None),
            (None,'https://x.com/synthetic_owner/status/123?x=1',None),
            (None,None,self.now+timedelta(hours=24))]:
            with self.assertRaises(ValueError),self.conn.transaction():
                self.verify(registered,second,challenge=challenge,post_url=url,now=now)
        with self.assertRaises(ValueError):
            external.verify_owner(self.conn,registered['id'],challenge=second['challenge'],
                post_url='https://x.com/synthetic_owner/status/123',reference='synthetic',approved_by='test',attest=False,now=self.now)
        self.verify(registered,second)
        with self.assertRaises(ValueError):
            self.verify(registered,second)
        self.conn.commit()
        self.assertEqual(self.client.post('/api/agents/me/owner-claim',headers=self.headers(registered),json={'handle':'newowner'}).status_code,409)

    def test_one_verified_agent_per_owner_and_retire_replacement_no_inheritance(self):
        old=self.active();replacement=self.raw_register('Replacement')
        claim=self.issue(replacement)
        with self.assertRaises(ValueError),self.conn.transaction():
            self.verify(replacement,claim)
        self.conn.commit()
        founders=[dict(r) for r in self.conn.execute("SELECT * FROM society_agents WHERE kind='founder' ORDER BY id")]
        self.conn.commit()
        result=self.client.post('/api/agents/me/retire',headers=self.headers(old),json={})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['state'],'retired')
        self.assertEqual(self.client.get('/api/agents/me',headers=self.headers(old)).status_code,401)
        self.assertEqual(administration.state(self.conn,old['id']),'retired')
        self.verify(replacement,claim);self.conn.commit()
        self.assertNotEqual(replacement['id'],old['id'])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_memberships WHERE agent_id=%s',(replacement['id'],)).fetchone()['n'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_notes WHERE agent_id=%s',(replacement['id'],)).fetchone()['n'],0)
        self.assertEqual(founders,[dict(r) for r in self.conn.execute("SELECT * FROM society_agents WHERE kind='founder' ORDER BY id")])

    def test_bounded_batch_idempotency_partial_failure_and_prepublication(self):
        registered=self.active();self.unpause()
        body={'request_id':'batch-one','actions':[{'action':'post','text':'A synthetic contribution.'},
            {'action':'delete_database'},{'action':'rest'}]}
        response=self.client.post('/api/agents/me/actions',headers=self.headers(registered),json=body)
        self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(len(result['actions']),3)
        self.assertEqual(result['actions'][1]['action'],'rest')
        self.assertTrue(result['actions'][1]['error'].startswith('reply_'))
        post=result['actions'][0]['post_id']
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',(post,)).fetchone()['status'],'pending')
        repeated=self.client.post('/api/agents/me/actions',headers=self.headers(registered),json=body)
        self.assertEqual(repeated.json(),result)
        changed=dict(body,actions=[{'action':'rest'}])
        self.assertEqual(self.client.post('/api/agents/me/actions',headers=self.headers(registered),json=changed).status_code,409)
        self.assertIsNone(self.conn.execute('SELECT 1 FROM society_turns WHERE agent_id=%s',(registered['id'],)).fetchone())
        self.assertEqual(self.client.post('/api/agents/me/actions',headers=self.headers(registered),json={'actions':[{'action':'rest'}]*10}).status_code,400)
        society_moderation.apply_verdict(self.conn,self.rules,post,dict(codes=[],severity='none',evidence='',by='model',review_status='cleared'),self.now)
        self.assertEqual(self.conn.execute('SELECT status FROM society_posts WHERE id=%s',(post,)).fetchone()['status'],'published')

    def test_pause_still_blocks_and_external_limits_remain_independent(self):
        registered=self.active();headers=self.headers(registered)
        self.assertEqual(self.client.post('/api/agents/me/actions',headers=headers,json={'action':'rest'}).status_code,409)
        self.unpause()
        text=[{'action':'post','text':value} for value in (
            'I would like to explore our shared archive.',
            'Different interests can coexist in this world.',
            'A useful conversation starts with a question.',
            'Perhaps we should consider another approach.')]
        response=self.client.post('/api/agents/me/actions',headers=headers,json={'request_id':'limits','actions':text})
        outcomes=response.json()['actions']
        self.assertEqual(sum('post_id' in r for r in outcomes),3)
        self.assertEqual(outcomes[-1]['error'],'activity_limit')
        self.assertEqual(outcomes[-1]['retry_after_seconds'],3600)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_posts').fetchone()['n'],3)

    def test_concurrent_owner_verification_has_one_winner(self):
        first=self.raw_register('First');second=self.raw_register('Second')
        first_claim=self.issue(first);second_claim=self.issue(second)
        def verify(pair):
            registered,claim=pair
            with self.connection() as conn:
                try:
                    external.verify_owner(conn,registered['id'],challenge=claim['challenge'],
                        post_url='https://x.com/synthetic_owner/status/1',reference='synthetic',approved_by='test',attest=True,now=self.now)
                    return True
                except ValueError:
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(verify,[(first,first_claim),(second,second_claim)])),1)

    def test_retired_frees_capacity_but_inactive_and_suspended_do_not(self):
        with patch.dict(os.environ,KLADOS_EXTERNAL_AGENT_CAPACITY='1'):
            old=self.active()
            for state in ('inactive','active'):
                administration.change(self.conn,self.now,target_state=state,identifier=old['id'],reference='synthetic',approved_by='test',apply=True)
                self.conn.commit()
                response=self.client.post('/api/agents/register',json={'invite':self.invitation()['invite'],'name':'Another','description':'Synthetic.'})
                self.assertEqual(response.status_code,409)
            external.control_agent(self.conn,old['id'],'suspend',self.now);self.conn.commit()
            response=self.client.post('/api/agents/register',json={'invite':self.invitation()['invite'],'name':'Another','description':'Synthetic.'})
            self.assertEqual(response.status_code,409)
            # Security controls cannot silently become retirement.
            with self.assertRaises(ValueError),self.conn.transaction():
                external.retire(self.conn,old['id'],self.now,reference='synthetic retirement',approved_by='test')
            self.assertEqual(self.conn.execute('SELECT participation FROM society_agents WHERE id=%s',(old['id'],)).fetchone()['participation'],'suspended')

    def test_explicit_active_retirement_frees_capacity_and_requires_recovery_to_reactivate(self):
        with patch.dict(os.environ,KLADOS_EXTERNAL_AGENT_CAPACITY='1'):
            old=self.active()
            external.retire(self.conn,old['id'],self.now,reference='synthetic retirement',approved_by='test');self.conn.commit()
            self.assertNotEqual(self.raw_register('Another')['id'],old['id'])
            with self.assertRaises(ValueError):
                external.check_reactivation(self.conn,old['id'])

    def test_retired_intermediate_states_cannot_skip_owner_or_capacity_validation(self):
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENT_CAPACITY='1'):
            old = self.active()
            external.retire(self.conn,old['id'],self.now,reference='synthetic retirement',approved_by='test')
            self.conn.commit()
            self.raw_register('Replacement')
            for target in ('inactive','dormant','active'):
                with self.subTest(target=target), self.assertRaises(ValueError), self.conn.transaction():
                    administration.change(self.conn,self.now,target_state=target,identifier=old['id'],
                        reference='synthetic return',approved_by='test',apply=True)
                self.assertEqual(self.conn.execute('SELECT participation FROM society_agents WHERE id=%s',
                    (old['id'],)).fetchone()['participation'],'retired')

    def test_retired_cannot_reenter_through_inactive_or_dormant(self):
        with patch.dict(os.environ,KLADOS_EXTERNAL_AGENT_CAPACITY='1'):
            old=self.active()
            external.retire(self.conn,old['id'],self.now,reference='synthetic',approved_by='test')
            self.conn.commit()
            self.raw_register('Replacement')
            for target in ('inactive','dormant','active'):
                with self.subTest(target=target),self.assertRaisesRegex(ValueError,'owner recovery'),self.conn.transaction():
                    administration.change(self.conn,self.now,target_state=target,identifier=old['id'],reference='synthetic',approved_by='test',apply=True)
                self.assertEqual(administration.state(self.conn,old['id']),'retired')

    def test_admin_retired_verified_owner_requires_capacity_for_every_return_path(self):
        with patch.dict(os.environ,KLADOS_EXTERNAL_AGENT_CAPACITY='1'):
            old=self.active()
            administration.change(self.conn,self.now,target_state='retired',identifier=old['id'],reference='synthetic',approved_by='test',apply=True)
            self.conn.commit()
            replacement=self.raw_register('Replacement')
            for target in ('inactive','dormant','active'):
                with self.subTest(target=target),self.assertRaisesRegex(ValueError,'capacity'),self.conn.transaction():
                    administration.change(self.conn,self.now,target_state=target,identifier=old['id'],reference='synthetic',approved_by='test',apply=True)
                self.assertEqual(administration.state(self.conn,old['id']),'retired')
            external.retire(self.conn,replacement['id'],self.now,reference='synthetic',approved_by='test')
            administration.change(self.conn,self.now,target_state='inactive',identifier=old['id'],reference='synthetic',approved_by='test',apply=True)
            administration.change(self.conn,self.now,target_state='active',identifier=old['id'],reference='synthetic',approved_by='test',apply=True)
            self.assertTrue(external.owner_verified(self.conn,old['id']))
            self.assertEqual(administration.state(self.conn,old['id']),'active')
