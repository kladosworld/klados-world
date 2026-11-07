from datetime import datetime, timedelta, timezone
import unittest

from tests.arena_support import DatabaseCase

try:
    from arena import society
except ImportError:  # psycopg missing: DatabaseCase is skipped anyway
    society = None
from tee_arena.society import rules as rules_module

RULES = rules_module.load()
START = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


class SocietyCase(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.now = START
        society.found(self.conn, RULES, self.now)
        society.set_paused(self.conn, RULES, False, self.now, "test")

    def hours(self, count):
        """Run ``count`` hourly ticks and return the summaries."""
        results = []
        for _ in range(count):
            self.now += timedelta(hours=1)
            results.append(society.tick(self.conn, RULES, self.now))
        return results

    def agent(self, agent_id):
        return self.conn.execute("SELECT * FROM society_agents WHERE id = %s", (agent_id,)).fetchone()

    def events(self, kind):
        return self.conn.execute("SELECT * FROM society_events WHERE kind = %s ORDER BY id", (kind,)).fetchall()


class FoundingTests(SocietyCase):
    def test_founders_start_with_energy_and_stone_is_disabled(self):
        status = society.status(self.conn, RULES)
        founders = [a for a in status["agents"] if a["kind"] == "founder"]
        self.assertEqual([a["name"] for a in founders], ["Alder", "Birch", "Cedar", "Hazel"])
        self.assertTrue(all(a["energy"] == 300 for a in founders))
        self.assertEqual(len(status["agents"]), 4)  # MVP v1: no Stone
        self.assertEqual(sorted(r["turn_minute"] for r in self.conn.execute(
            "SELECT turn_minute FROM society_agents WHERE kind = 'founder'")), [0, 15, 30, 45])
        with self.assertRaises(society.SocietyError):
            society.found(self.conn, RULES, self.now)
        self.assertEqual(society.audit(self.conn), [])

    def test_unknown_agents_cannot_hold_energy_or_turns(self):
        with self.assertRaises(society.SocietyError):
            society.apply_energy(self.conn, RULES, "stone", "like", 100, "like:stone:1", self.now)
        self.assertFalse(society.claim_turn(self.conn, "stone", 1, self.now))

    def test_likes_are_a_valid_journal_kind_and_old_kinds_are_not(self):
        society.apply_energy(self.conn, RULES, "alder", "like", 100, "like:alder:x", self.now)
        with self.assertRaises(society.SocietyError):
            society.apply_energy(self.conn, RULES, "alder", "prize", 100, "prize:alder:x", self.now)


class EnergyTests(SocietyCase):
    def test_a_key_applies_once(self):
        first = society.apply_energy(self.conn, RULES, "alder", "action", -50, "post:alder:1", self.now)
        again = society.apply_energy(self.conn, RULES, "alder", "action", -50, "post:alder:1", self.now)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(self.agent("alder")["balance"], 30_000 - 50)
        with self.assertRaises(society.SocietyError):  # same key, different change
            society.apply_energy(self.conn, RULES, "alder", "action", -100, "post:alder:1", self.now)

    def test_credits_stop_at_the_cap(self):
        row = society.apply_energy(self.conn, RULES, "birch", "like", 2000, "like:birch:1", self.now)
        self.assertEqual((row["requested"], row["amount"]), (2000, 0))
        society.spend(self.conn, RULES, "birch", "post", "post:birch:x", self.now)
        society.spend(self.conn, RULES, "birch", "reply", "reply:birch:x", self.now)
        row = society.apply_energy(self.conn, RULES, "birch", "like", 2000, "like:birch:2", self.now)
        self.assertEqual(row["amount"], 100)
        self.assertEqual(self.agent("birch")["balance"], RULES.cap)

    def test_actions_need_enough_energy_and_rest_is_free(self):
        society.apply_energy(self.conn, RULES, "cedar", "penalty", -29_980, "drain:cedar", self.now)
        with self.assertRaises(society.InsufficientEnergy):
            society.spend(self.conn, RULES, "cedar", "post", "post:cedar:1", self.now)
        self.assertIsNone(society.spend(self.conn, RULES, "cedar", "rest", "rest:cedar:1", self.now))
        self.assertEqual(self.agent("cedar")["balance"], 20)
        self.assertEqual(society.audit(self.conn), [])

    def test_falling_below_twenty_is_announced_once(self):
        society.apply_energy(self.conn, RULES, "hazel", "penalty", -28_100, "drain:hazel", self.now)
        society.spend(self.conn, RULES, "hazel", "post", "post:hazel:1", self.now)
        self.assertEqual([e["agent_id"] for e in self.events("danger")], ["hazel"])


class ClockTests(SocietyCase):
    def test_a_day_costs_twelve_and_a_retry_cannot_tick_twice(self):
        self.hours(24)
        self.assertTrue(all(self.agent(a)["balance"] == 30_000 - 1200 for a in ("alder", "hazel")))
        retry = society.tick(self.conn, RULES, self.now + timedelta(minutes=5))
        self.assertEqual(retry["reason"], "too_soon")
        self.assertEqual(society.clock(self.conn)["tick"], 24)

    def test_pause_freezes_age_and_energy(self):
        self.hours(2)
        society.set_paused(self.conn, RULES, True, self.now, "deploy")
        self.now += timedelta(hours=5)
        self.assertEqual(society.tick(self.conn, RULES, self.now)["reason"], "paused")
        self.assertEqual(self.agent("alder")["balance"], 30_000 - 100)
        society.set_paused(self.conn, RULES, False, self.now, "deploy done")
        self.assertEqual(society.tick(self.conn, RULES, self.now + timedelta(minutes=1))["reason"], "too_soon")
        self.hours(1)
        self.assertEqual(society.clock(self.conn)["tick"], 3)
        self.assertEqual([e["kind"] for e in self.conn.execute(
            "SELECT kind FROM society_events WHERE kind IN ('paused', 'resumed') ORDER BY id")],
            ["resumed", "paused", "resumed"])

    def test_starving_agent_dies_and_cannot_be_revived(self):
        society.apply_energy(self.conn, RULES, "birch", "penalty", -29_960, "drain:birch", self.now)
        results = self.hours(1)
        self.assertEqual(results[0]["deaths"], [("birch", "energy")])
        birch = self.agent("birch")
        self.assertEqual((birch["status"], birch["death_cause"], birch["died_tick"]), ("dead", "energy", 1))
        with self.assertRaises(society.SocietyError):
            society.apply_energy(self.conn, RULES, "birch", "like", 100, "like:birch:1", self.now)
        self.hours(1)
        self.assertEqual(len(self.events("death")), 1)
        self.assertNotIn("birch", society.due_turns(self.conn, self.now.replace(minute=59)))

    def test_everyone_dies_of_age_after_thirty_days_if_fed(self):
        for day in range(30):
            for agent in ("alder", "birch", "cedar", "hazel"):
                society.apply_energy(self.conn, RULES, agent, "like", 2000, f"like:{agent}:{day}", self.now)
            results = self.hours(24)
        self.assertEqual(sorted(results[-1]["deaths"]), [(a, "age") for a in ("alder", "birch", "cedar", "hazel")])
        self.assertEqual([e["data"]["day"] for e in self.events("aged") if e["agent_id"] == "alder"], [10, 20])
        self.assertEqual(society.audit(self.conn), [])


class TurnTests(SocietyCase):
    def test_turns_follow_each_agents_minute_and_are_claimed_once(self):
        self.hours(1)
        tick = society.clock(self.conn)["tick"]
        at = self.now.replace(minute=20)
        self.assertEqual(society.due_turns(self.conn, at), ["alder", "birch"])
        self.assertTrue(society.claim_turn(self.conn, "alder", tick, at))
        self.assertFalse(society.claim_turn(self.conn, "alder", tick, at))
        society.finish_turn(self.conn, "alder", tick, "post")
        self.assertEqual(society.due_turns(self.conn, at), ["birch"])
        self.assertEqual(society.due_turns(self.conn, self.now.replace(minute=59)), ["birch", "cedar", "hazel"])


if __name__ == "__main__":
    unittest.main()
