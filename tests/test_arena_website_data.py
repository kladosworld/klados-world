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
