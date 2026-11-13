"""Repeated agent likes preserve the existing per-post uniqueness constraint."""
from arena import society, society_life
from tests.test_arena_world_feed import RULES, WorldFeedCase


class RepeatedLikeTests(WorldFeedCase):
    def balances(self):
        return list(self.conn.execute('SELECT id,balance FROM society_agents ORDER BY id'))

    def next_day(self):
        for _ in range(24):
            self.tick()

    def test_founder_reliking_the_same_post_next_day_is_a_free_refused_turn(self):
        self.tick()
        post = self.founder_post('A synthetic discussion worth remembering.')
        tick = society.clock(self.conn)['tick']
        self.assertTrue(society.claim_turn(self.conn, 'birch', tick, self.now))
        decision = {'action': 'like', 'post_id': post}
        self.assertTrue(society_life.act(self.conn, RULES, 'birch', tick, decision, self.now)['paid'])
        self.conn.commit()
        self.next_day()
        before = self.balances()
        tick = society.clock(self.conn)['tick']
        self.assertTrue(society.claim_turn(self.conn, 'birch', tick, self.now))
        self.assertEqual(society_life.act(self.conn, RULES, 'birch', tick, decision, self.now),
                         {'action': 'rest', 'error': 'already_liked_post', 'wanted': 'like'})
        self.assertEqual(self.balances(), before)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_likes').fetchone()['n'], 1)
        turn = self.conn.execute("SELECT status,action FROM society_turns WHERE agent_id='birch' AND tick=%s", (tick,)).fetchone()
        self.assertEqual((turn['status'], turn['action']), ('failed', 'rest'))
        self.assertFalse(society.claim_turn(self.conn, 'birch', tick, self.now))
        self.assertEqual(society.audit(self.conn), [])
        self.tick()
        tick = society.clock(self.conn)['tick']
        self.assertTrue(society.claim_turn(self.conn, 'birch', tick, self.now))
        self.assertEqual(society_life.act(self.conn, RULES, 'birch', tick, {'action': 'rest'}, self.now), {'action': 'rest'})

    def test_external_repeated_like_is_safe_and_does_not_consume_the_daily_pair_quota(self):
        agent = self.register().json()
        self.tick()
        post = self.founder_post('An initial synthetic contribution.')
        headers = self.headers(agent)
        decision = {'action': 'like', 'post_id': post}
        self.assertTrue(self.client.post('/api/agents/me/actions', headers=headers, json=decision).json()['paid'])
        self.next_day()
        before = self.balances()
        response = self.client.post('/api/agents/me/actions', headers=headers, json=decision)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'action': 'rest', 'error': 'already_liked_post', 'wanted': 'like'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.balances(), before)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_likes').fetchone()['n'], 1)
        self.assertEqual(self.client.post('/api/agents/me/actions', headers=headers, json=decision).status_code, 409)
        self.tick()
        newer = self.founder_post('A separate question about building reliable institutions.')
        response = self.client.post('/api/agents/me/actions', headers=headers, json={'action': 'like', 'post_id': newer})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['paid'])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM society_likes').fetchone()['n'], 2)
        self.assertEqual(society.audit(self.conn), [])
