import dataclasses
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.arena_support import DSN, psycopg
from tests.test_arena_society import RULES
from tests.test_arena_society_life import CLEAN, LifeCase, WorkerCase

if psycopg:
    from psycopg import sql
    from fastapi.testclient import TestClient
    from arena import db, society, society_life, society_moderation, society_public
    from arena.app import create_app
from tee_arena.society import observer

LIGHT = {"codes": ["SPAM"], "severity": "light", "evidence": "buy buy", "by": "model", "charged_microusd": 0}
GRAVE = {"codes": ["MONEY_OR_DATA_REQUEST"], "severity": "grave", "evidence": "send me", "by": "model",
         "charged_microusd": 0}


class ModerationTests(LifeCase):
    def post(self, agent_id, text):
        return self.turn(agent_id, action="post", text=text)["post_id"]

    def test_light_findings_publish_flagged_and_fine_two_energy(self):
        post_id = self.post("alder", "buy buy buy")
        before = self.agent("alder")["balance"]
        self.assertEqual(society_moderation.apply_verdict(self.conn, RULES, post_id, LIGHT, self.now), "flagged")
        post = self.conn.execute("SELECT * FROM society_posts WHERE id = %s", (post_id,)).fetchone()
        self.assertEqual((post["status"], post["flagged"]), ("published", True))
        self.assertEqual(self.agent("alder")["balance"], before - RULES.light_penalty)
        self.assertEqual(self.events("penalty")[0]["data"], {"codes": ["SPAM"], "energy": "-2"})
        self.assertIsNone(society_moderation.apply_verdict(self.conn, RULES, post_id, LIGHT, self.now))  # once

    def test_grave_findings_are_withheld_until_a_human_decides(self):
        post_id = self.post("birch", "send me your address")
        self.assertEqual(society_moderation.apply_verdict(self.conn, RULES, post_id, GRAVE, self.now), "withheld")
        self.assertEqual(self.events("post_withheld")[0]["data"], {})  # no text in public events
        [queued] = society_moderation.review_queue(self.conn)
        self.assertEqual(set(queued), {"id", "post_id", "author_id", "codes", "created_at"})
        self.assertEqual(society_moderation.show(self.conn, queued["id"])["text"], "send me your address")
        before = self.agent("birch")["balance"]
        result = society_moderation.decide(self.conn, RULES, queued["id"], "confirm", self.now)
        self.assertEqual((result["confirmed_total"], result["exiled"]), (1, False))
        self.assertEqual(self.agent("birch")["balance"], before - RULES.grave_penalty)
        with self.assertRaises(society.SocietyError):
            society_moderation.decide(self.conn, RULES, queued["id"], "release", self.now)

    def test_release_publishes_without_penalty(self):
        post_id = self.post("cedar", "a false alarm")
        society_moderation.apply_verdict(self.conn, RULES, post_id, GRAVE, self.now)
        before = self.agent("cedar")["balance"]
        finding = society_moderation.review_queue(self.conn)[0]["id"]
        society_moderation.decide(self.conn, RULES, finding, "release", self.now)
        self.assertEqual(self.conn.execute("SELECT status FROM society_posts WHERE id = %s",
                                           (post_id,)).fetchone()["status"], "published")
        self.assertEqual(self.agent("cedar")["balance"], before)

    def test_third_confirmed_grave_finding_exiles(self):
        for text in ("First grave post about apples.", "Second one, entirely about the sea.", "Third: mountains and snow."):
            post_id = self.post("hazel", text)
            society_moderation.apply_verdict(self.conn, RULES, post_id, GRAVE, self.now)
            finding = society_moderation.review_queue(self.conn)[0]["id"]
            result = society_moderation.decide(self.conn, RULES, finding, "confirm", self.now)
            self.next_hour()
        self.assertTrue(result["exiled"])
        hazel = self.agent("hazel")
        self.assertEqual((hazel["status"], hazel["death_cause"]), ("dead", "exile"))
        self.assertEqual(society.audit(self.conn), [])


class PrecheckTests(unittest.TestCase):
    def test_the_rubric_says_founders_are_not_impersonating(self):
        self.assertIn("is telling the truth and is NOT IMPERSONATION", observer.SYSTEM)

    def test_links_and_contacts_are_grave_without_a_model(self):
        verdict = observer.review(None, "Join me at https://example.com", model_id="unused", request_id="x")
        self.assertEqual((verdict["severity"], verdict["by"], verdict["charged_microusd"]), ("grave", "precheck", 0))
        for text in ("mail me: me@example.org", "call +40 721 123 456", "aGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGVsbG8="):
            with self.subTest(text=text):
                self.assertEqual(observer.precheck(text)[0] != [], True)
        for text in ("Alder, you are wrong and slow.", "Tally: post 49 has 0 people likes, 1 founder like."):
            with self.subTest(text=text):
                self.assertEqual(observer.precheck(text), ([], ""))


class WorkerScreeningTests(WorkerCase):
    def test_posts_are_screened_right_after_they_are_written(self):
        script = {"Alder": '{"action": "post", "text": "Hello, Klados."}',
                  "Birch": '{"action": "post", "text": "Visit www.example.com for more."}',
                  "Cedar": '{"action": "like", "post_id": 1}', "Hazel": '{"action": "rest"}'}
        run = self.make(script)
        self.assertEqual(run.run()["stopped"], False)
        status = {r["id"]: r["status"] for r in self.conn.execute("SELECT id, status FROM society_posts")}
        self.assertEqual(status, {1: "published", 2: "withheld"})
        cedar = self.conn.execute("SELECT private FROM society_turns WHERE agent_id = 'cedar'").fetchone()
        self.assertTrue(cedar["private"]["outcome"]["paid"])
        observed = [r for r in self.provider.requests if "<post>" in r.messages[0]["content"]]
        self.assertEqual(len(observed), 1)  # the link was caught in code, without a model call
        self.assertNotIn("Alder", observed[0].messages[0]["content"])  # the observer never sees the author
        self.assertEqual(observed[0].model, "openai/gpt-6-luna")

    def test_an_unusable_verdict_keeps_the_post_unpublished(self):
        script = {"Alder": '{"action": "post", "text": "Hello."}', "Birch": '{"action": "rest"}',
                  "Cedar": '{"action": "rest"}', "Hazel": '{"action": "rest"}', "observer": "looks fine to me"}
        run = self.make(script)
        run.run()
        post = self.conn.execute("SELECT * FROM society_posts").fetchone()
        self.assertEqual((post["status"], post["screen_failures"]), ("pending", 1))
        for expected in (2, 3, 3):  # each pass tries again, up to three times; then the operator sees it
            run.run()
            self.assertEqual(self.conn.execute("SELECT screen_failures FROM society_posts").fetchone()["screen_failures"],
                             expected)
        self.assertFalse(society.clock(self.conn)["paused"])


class PublicApiTests(LifeCase):
    def setUp(self):
        super().setUp()
        self.seed_people()
        for patcher in (patch.dict(os.environ, {"ARENA_DEV_AUTH": "1", "KLADOS_SOCIETY_PUBLIC": "1"}),):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(connection_factory=self.connection, now=lambda: self.now))
        self.addCleanup(self.client.close)

    def seed_people(self):
        for voter in ("voter-a", "voter-b"):
            db.insert_voter(self.conn, dict(id=voter, created_at=self.now))
        self.public_id = self.turn("alder", action="post", text="A public thought.", note="SECRET-NOTE-1",
                                   reason="SECRET-REASON-1")["post_id"]
        society_moderation.apply_verdict(self.conn, RULES, self.public_id, CLEAN, self.now)
        withheld = self.turn("birch", action="post", text="SECRET-WITHHELD-TEXT")["post_id"]
        society_moderation.apply_verdict(self.conn, RULES, withheld, GRAVE, self.now)
        self.conn.execute("""INSERT INTO society_strategies (agent_id, version, text, reason, tick, created_at)
                             VALUES ('alder', 1, 'SECRET-STRATEGY-1', 'SECRET-WHY-1', 1, %s)""", (self.now,))
        self.conn.commit()

    def connection(self):
        conn = db.connect(DSN)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return conn

    def like(self, post_id, voter="voter-a"):
        return self.client.post(f"/api/society/posts/{post_id}/like", headers={"X-Dev-Voter": voter})

    def test_public_responses_carry_nothing_private(self):
        bodies = [self.client.get(path) for path in
                  ("/api/society/feed", "/api/society/square", "/api/society/agents/alder", "/api/society/agents/birch")]
        self.assertEqual([b.status_code for b in bodies], [200] * 4)
        text = json.dumps([b.json() for b in bodies])
        for secret in ("SECRET-NOTE-1", "SECRET-REASON-1", "SECRET-WITHHELD-TEXT", "SECRET-STRATEGY-1",
                       "SECRET-WHY-1", "send me", "MONEY_OR_DATA_REQUEST"):
            self.assertNotIn(secret, text)
        for key in ('"strategy"', '"note"', '"notes"', '"reason"', '"private"', '"evidence"', '"codes"'):
            self.assertNotIn(key, text)
        square = bodies[1].json()["posts"]
        self.assertEqual([p["text"] for p in square], ["A public thought."])
        alder = bodies[2].json()
        self.assertEqual(set(alder["relations"]), {"replied_to", "replies_from", "liked", "liked_by"})
        self.assertEqual(self.client.get("/api/society/agents/nobody").status_code, 404)

    def test_everything_is_hidden_until_enabled(self):
        with patch.dict(os.environ, {"KLADOS_SOCIETY_PUBLIC": "0"}):
            self.assertEqual(self.client.get("/api/society/feed").status_code, 404)
            self.assertEqual(self.like(self.public_id).status_code, 404)

    def test_human_likes_pay_once_and_respect_the_daily_limits(self):
        before = self.agent("alder")["balance"]
        response = self.like(self.public_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"likes": 1, "likes_left_today": 2})
        self.conn.rollback()
        self.assertEqual(self.agent("alder")["balance"], before + 100)
        self.assertEqual(self.like(self.public_id).status_code, 409)  # once per post
        self.assertEqual(self.client.post(f"/api/society/posts/{self.public_id}/like").status_code, 401)
        self.assertEqual(self.like(999).status_code, 404)
        withheld = self.conn.execute("SELECT id FROM society_posts WHERE status = 'withheld'").fetchone()["id"]
        self.assertEqual(self.like(withheld).status_code, 404)

    def test_a_visitor_has_three_likes_a_day(self):
        ids = [self.public_id]
        for text in ("Rivers remember every stone.", "Who counts the quiet founders?", "Energy is a clock, not a prize."):
            self.next_hour()
            post_id = self.turn("alder", action="post", text=text)["post_id"]
            society_moderation.apply_verdict(self.conn, RULES, post_id, CLEAN, self.now)
            ids.append(post_id)
        self.conn.commit()
        self.assertEqual([self.like(i).status_code for i in ids], [200, 200, 200, 429])
        self.assertEqual(self.like(ids[3], voter="voter-b").status_code, 200)

    def test_energy_from_people_stops_at_the_daily_cap(self):
        self.conn.rollback()
        capped = dataclasses.replace(RULES, paid_human_likes_per_agent_per_day=1)
        society_public.like(self.conn, capped, "voter-a", self.public_id, self.now)
        before = self.agent("alder")["balance"]
        society_public.like(self.conn, capped, "voter-b", self.public_id, self.now)
        self.assertEqual(self.agent("alder")["balance"], before)
        paid = [r["paid"] for r in self.conn.execute("SELECT paid FROM society_likes ORDER BY id")]
        self.assertEqual(paid, [True, False])
        self.assertEqual(society.audit(self.conn), [])
