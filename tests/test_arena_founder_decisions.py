"""Founder decision context uses existing private memory and observed interactions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena import society, society_life, society_moderation
from tee_arena.society import prompts
from tests.test_arena_world_feed import RULES, WorldFeedCase


class FounderDecisionContextTests(WorldFeedCase):
    def act(self, identifier, decision):
        tick = society.clock(self.conn)['tick']
        self.assertTrue(society.claim_turn(self.conn, identifier, tick, self.now))
        result = society_life.act(self.conn, RULES, identifier, tick, decision, self.now)
        self.conn.commit()
        return result

    def test_like_eligibility_tracks_own_posts_daily_limits_and_previous_days(self):
        self.tick()
        post = self.founder_post('A synthetic idea worth discussing.')
        def eligibility(identifier):
            return {row['post_id']: row['can_like'] for row in society_life.turn_context(self.conn, RULES, identifier)['public_square']}
        self.assertFalse(eligibility('alder')[post])
        self.assertTrue(eligibility('birch')[post])
        self.assertTrue(self.act('birch', {'action':'like', 'post_id':post})['paid'])
        self.assertFalse(eligibility('birch')[post])
        for _ in range(24):
            self.tick()
        self.assertFalse(eligibility('birch')[post])
        fresh = self.founder_post('A different question about shared responsibilities.')
        self.assertTrue(eligibility('birch')[fresh])
        self.assertEqual(self.act('birch', {'action':'like', 'post_id':post})['error'], 'already_liked_post')
        self.assertEqual(society.audit(self.conn), [])

    def test_observed_relationships_and_own_memory_enter_prompt_without_public_leakage(self):
        self.tick()
        root = self.founder_post('A synthetic starting point.')
        reply = self.act('birch', {'action':'reply', 'post_id':root, 'text':'A different perspective.', 'note':'OTHER_PRIVATE_CANARY'})['post_id']
        society_moderation.apply_verdict(self.conn,RULES,reply,dict(codes=[],severity='none',evidence='',by='model'),self.now)
        self.act('cedar', {'action':'like','post_id':root})
        self.conn.execute("INSERT INTO society_notes(agent_id,tick,text,created_at) VALUES ('alder',1,'OWN_NOTE_CANARY',%s)",(self.now,))
        self.conn.execute("INSERT INTO society_strategies(agent_id,version,text,tick,created_at) VALUES ('alder',1,'OWN_STRATEGY_CANARY',1,%s)",(self.now,))
        self.conn.commit()
        context = society_life.turn_context(self.conn,RULES,'alder')
        peers = {row['name']:row for row in context['relationships']}
        self.assertEqual(peers['Birch']['replies_from'],1)
        self.assertEqual(peers['Cedar']['liked_by'],1)
        self.assertNotIn('OTHER_PRIVATE_CANARY',json.dumps(context))
        system,messages = prompts.turn(context)
        self.assertIn('OWN_NOTE_CANARY',messages[0]['content'])
        self.assertIn('OWN_STRATEGY_CANARY',messages[0]['content'])
        self.assertNotIn('OWN_NOTE_CANARY',system)
        with patch.object(society_life,'RELATIONSHIPS_SHOWN',2):
            self.assertEqual(len(society_life.turn_context(self.conn,RULES,'alder')['relationships']),2)
        for route in ('/api/society/feed','/api/society/square','/api/society/agents/alder'):
            response = self.client.get(route)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('PRIVATE_CANARY',response.text)
            self.assertNotIn('OWN_NOTE_CANARY',response.text)
            self.assertNotIn('OWN_STRATEGY_CANARY',response.text)

    @unittest.skipUnless(os.name == 'posix', 'real worker and financial ledger require POSIX')
    def test_real_worker_uses_memory_and_context_with_synthetic_provider_only(self):
        from arena import worker
        from tee_arena.gateway import Gateway, ModelPrice
        from tests.test_arena_society_life import ScriptedProvider, MODEL, OBSERVER
        self.tick()
        root = self.founder_post('A synthetic question for the next turn.')
        self.tick()
        actions = dict(alder='post', birch='like', cedar='reply', hazel='rest')
        for identifier, action in actions.items():
            self.conn.execute('INSERT INTO society_strategies(agent_id,version,text,tick,created_at) VALUES (%s,1,%s,1,%s)',
                              (identifier,'SYNTHETIC_CHOICE_'+action,self.now))
        self.conn.commit()
        def respond(request):
            context = json.loads(request.messages[0]['content'].split('<klados_state>\n',1)[1].split('\n</klados_state>',1)[0])
            self.assertIn('There is no required action mix',request.system)
            self.assertIn('relationships',context)
            choice = context['you']['private_strategy'].removeprefix('SYNTHETIC_CHOICE_')
            decision = {'action':choice}
            if choice in ('reply','like'):
                decision['post_id'] = root
            if choice == 'like':
                self.assertTrue(next(post for post in context['public_square'] if post['post_id']==root)['can_like'])
            if choice in ('post','reply'):
                decision['text'] = 'A distinct synthetic contribution.' if choice=='post' else 'A synthetic response to this question.'
            return json.dumps(decision)
        provider = ScriptedProvider({name:respond for _,name in society.FOUNDERS})
        with tempfile.TemporaryDirectory() as directory:
            ledger = worker.open_ledger(Path(directory)/'ledger.json','1.00')
            gateway = Gateway(ledger=ledger,models={MODEL:ModelPrice('0.10','0.50'),OBSERVER:ModelPrice('0.10','0.50')},
                              providers={'openrouter':provider},stop_file=None)
            def run():
                return worker.Worker(self.conn,RULES,gateway,observer_gateway=gateway,
                                     now=lambda:self.now.replace(minute=59),sleep=lambda _:None).run()
            self.assertEqual(run()['turns'],4)
            actual = {row['agent_id']:row['action'] for row in self.conn.execute('SELECT agent_id,action FROM society_turns WHERE tick=2')}
            self.assertEqual(actual,actions)
            calls = len(provider.requests)
            self.assertEqual(calls,6)  # Four decisions and two observer verdicts.
            self.assertEqual(run()['turns'],0)
            self.assertEqual(len(provider.requests),calls)
            self.assertEqual(society.audit(self.conn),[])
