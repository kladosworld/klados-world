"""Only public persisted fields are added for website status and timestamps."""
from tests.test_arena_society import RULES
from tests.test_arena_society_life import LifeCase, CLEAN

from arena import society, society_moderation, society_public


class WebsiteDataTests(LifeCase):
    def test_paused_status_tracks_the_existing_clock_without_private_pause_reason(self):
        self.assertFalse(society_public.feed(self.conn, RULES)['paused'])
        society.set_paused(self.conn, RULES, True, self.now, 'PRIVATE_PAUSE_REASON')
        result = society_public.feed(self.conn, RULES)
        self.assertTrue(result['paused'])
        self.assertNotIn('PRIVATE_PAUSE_REASON', str(result))

    def test_post_timestamp_is_stored_creation_time_and_private_posts_stay_hidden(self):
        outcome = self.turn('alder', action='post', text='A public timestamp regression test.',
                            note='PRIVATE_NOTE_CANARY', reason='PRIVATE_REASON_CANARY')
        self.assertFalse(society_public.square(self.conn)['posts'])
        society_moderation.apply_verdict(self.conn, RULES, outcome['post_id'], CLEAN, self.now)
        post = society_public.square(self.conn)['posts'][0]
        self.assertEqual(post['created_at'], self.now.isoformat())
        self.assertNotIn('PRIVATE', str(post))
        self.assertEqual(society_public.profile(self.conn, RULES, 'alder')['posts'][0]['created_at'], post['created_at'])

    def test_public_release_hides_unverified_history_and_preserves_every_stored_record(self):
        # Neither a misleading prefix nor changing current mode can attest provenance.
        from unittest.mock import patch
        import os
        for author, text in [('alder', 'A plausible post with no mock prefix.'), ('birch', 'Mock is a word agents may discuss.')]:
            post = self.turn(author, action='post', text=text)['post_id']
            society_moderation.apply_verdict(self.conn, RULES, post, CLEAN, self.now)
        tables = [r['tablename'] for r in self.conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename")]
        from psycopg import sql
        def snapshot():
            return {name: list(self.conn.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(name)))) for name in tables}
        before = snapshot()
        for mode in ('mock', 'live', 'unknown'):
            with patch.dict(os.environ, KLADOS_AI_MODE=mode):
                self.assertEqual(society_public.public_view(self.conn, 'square')['posts'], [])
                self.assertEqual(society_public.public_view(self.conn, 'feed')['events'], [])
                self.assertEqual(society_public.public_view(self.conn, 'agent', 'alder')['posts'], [])
                self.assertEqual(society_public.public_view(self.conn, 'stats')['activity_by_day'], [])
        self.assertEqual(snapshot(), before)
        self.assertEqual(len(society_public.square(self.conn)['posts']), 2)
