"""Zero active founders never means society-wide suspension or implicit spending."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from arena import founder_status, scheduler, society, society_public, worker
from tee_arena.society import rules
from tests.arena_support import DatabaseCase

NOW = datetime(2026,11,26,tzinfo=timezone.utc)
RULES = rules.load()


class ZeroFoundersTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        society.found(self.conn,RULES,NOW)
        society.set_paused(self.conn,RULES,False,NOW,'synthetic')
        self.conn.commit()

    def test_worker_screens_without_founder_calls_and_scheduler_keeps_ticking(self):
        founder_status.change(self.conn,NOW,target_state='inactive',all_founders=True,
            reference='synthetic',approved_by='operator',apply=True)
        self.conn.commit()
        gateway=Mock()
        instance=worker.Worker(self.conn,RULES,gateway,observer_gateway=None,
                               now=lambda: NOW+timedelta(hours=1))
        with patch.object(instance,'screen',return_value=None) as screen, \
             patch.object(instance,'last_words',return_value=None):
            summary=instance.run()
        screen.assert_called_once()
        gateway.call.assert_not_called()
        self.assertEqual(summary['turns'],0)
        self.assertEqual(summary['reflections'],0)
        self.assertFalse(summary['stopped'])
        with patch('arena.worker.Worker',return_value=instance), \
             patch.object(instance,'screen',return_value=None), \
             patch('arena.institutions.close_due') as close, \
             patch('arena.lifecycle.refresh'):
            result=scheduler.step(self.conn,RULES,gateway,None,NOW+timedelta(hours=1))
        self.assertTrue(result['ticked'])
        self.assertFalse(society.clock(self.conn)['paused'])
        close.assert_called_once()
        gateway.call.assert_not_called()
        public=society_public.stats(self.conn,RULES)
        self.assertIsInstance(public,dict)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_agents WHERE kind='founder'").fetchone()['n'],4)
