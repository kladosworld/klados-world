from datetime import timedelta
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.arena_support import DSN, DatabaseCase, psycopg
from tests.test_arena_society import RULES, START
from tests.test_arena_society_life import CLEAN, WorkerCase

if psycopg:
    from psycopg import sql
    from fastapi.testclient import TestClient
    from arena import db, society, society_death, society_moderation, society_public
    from arena.app import create_app
from tee_arena.society import prompts

MIGRATIONS = sorted(Path(__file__).resolve().parent.parent.joinpath("arena", "migrations").glob("*.sql"))


class MigrationTests(DatabaseCase):
    def setUp(self):  # build the schema as it was before 0015, with data in it
        self.conn = db.connect(DSN)
        self.schema = "test_" + uuid4().hex
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.conn.execute("CREATE TABLE arena_migrations (name text PRIMARY KEY)")
        for path in MIGRATIONS:
            if path.name >= "0015":
                break
            self.conn.execute(path.read_text(encoding="utf-8"))
            self.conn.execute("INSERT INTO arena_migrations VALUES (%s)", (path.name,))
        self.conn.execute("""INSERT INTO society_clock (tick, paused, rules_version, updated_at)
                             VALUES (5, false, 'rules-v1', %s)""", (START,))
        self.conn.execute("""INSERT INTO society_agents (id, name, kind, born_tick, turn_minute, balance, created_at)
                             VALUES ('alder', 'Alder', 'founder', 0, 0, 29700, %s)""", (START,))
        self.conn.execute("""INSERT INTO energy_entries (agent_id, key, kind, requested, amount, balance_after, tick,
                             rules_version, created_at) VALUES ('alder', 'birth:alder', 'birth', 30000, 30000, 30000, 0,
                             'rules-v1', %s), ('alder', 'living:alder:x', 'living', -300, -300, 29700, 5, 'rules-v1', %s)""",
                          (START, START))
        self.conn.execute("""INSERT INTO society_posts (author_id, tick, text, status, created_at)
                             VALUES ('alder', 3, 'An old post.', 'published', %s)""", (START,))
        self.conn.commit()
        self.addCleanup(self.cleanup_database)

    def test_0015_keeps_every_row_and_descriptions_are_filled_once(self):
        db.migrate(self.conn)  # applies only what is missing: 0015 and later
        self.conn.commit()
        alder = self.conn.execute("SELECT * FROM society_agents WHERE id = 'alder'").fetchone()
        self.assertEqual((alder["balance"], alder["public_description"]), (29700, None))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM energy_entries").fetchone()["n"], 2)
        self.assertEqual(self.conn.execute("SELECT text FROM society_posts").fetchone()["text"], "An old post.")
        self.assertEqual(society.clock(self.conn)["tick"], 5)
        self.assertEqual(society.audit(self.conn), [])
        self.assertEqual(society.sync_descriptions(self.conn, START), 1)
        self.assertEqual(society.sync_descriptions(self.conn, START), 0)  # never overwrites
        description = self.conn.execute("SELECT public_description FROM society_agents").fetchone()["public_description"]
        self.assertEqual(description, prompts.load_founders()["alder"]["personality"])


class FossilTests(WorkerCase):
    def starve(self, agent_id):
        society.apply_energy(self.conn, RULES, agent_id, "penalty", -self.agent(agent_id)["balance"] + 10,
                             f"drain:{agent_id}", self.now)
        self.next_hour()

    def fossil(self, agent_id):
        return self.conn.execute("SELECT * FROM society_fossils WHERE agent_id = %s", (agent_id,)).fetchone()

    def test_death_writes_a_fossil_at_once_with_the_public_identity(self):
        post = self.turn("hazel", action="post", text="Counting what earns likes.")["post_id"]
        society_moderation.apply_verdict(self.conn, RULES, post, CLEAN, self.now)
        self.turn("birch", action="like", post_id=post)
        self.starve("hazel")
        fossil = self.fossil("hazel")
        self.assertEqual((fossil["name"], fossil["kind"], fossil["death_cause"]), ("Hazel", "founder", "energy"))
        self.assertEqual(fossil["public_description"], prompts.load_founders()["hazel"]["personality"])
        self.assertEqual(fossil["last_words_status"], "pending")
        self.assertEqual(fossil["counts"]["posts"], 1)
        self.assertEqual(fossil["relations"]["liked_by"], {"birch": 1})
        self.assertFalse(society.die(self.conn, RULES, "hazel", 9, "age", self.now))  # once
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM society_fossils").fetchone()["n"], 1)

    def test_exile_also_writes_a_fossil(self):
        for text in ("First grave post about apples.", "Second one, entirely about the sea.", "Third: mountains and snow."):
            post = self.turn("birch", action="post", text=text)["post_id"]
            society_moderation.apply_verdict(self.conn, RULES, post, dict(CLEAN, codes=["OUT_OF_BAND"], severity="grave"),
                                             self.now)
            society_moderation.decide(self.conn, RULES, society_moderation.review_queue(self.conn)[0]["id"],
                                      "confirm", self.now)
            self.next_hour()
        self.assertEqual(self.fossil("birch")["death_cause"], "exile")

    def last_words_script(self, words):
        script = {name: '{"action": "rest"}' for name in ("Alder", "Birch", "Cedar", "Hazel")}
        script["Hazel"] = lambda request: (words() if callable(words) else words) \
            if "Your last words as JSON" in request.messages[0]["content"] else '{"action": "rest"}'
        return self.make(script)

    def test_last_words_are_published_after_the_observer(self):
        run = self.last_words_script('{"text": "Measure what you value, friends."}')
        self.starve("hazel")
        run.run()
        fossil = self.fossil("hazel")
        self.assertEqual((fossil["last_words_status"], fossil["last_words"]),
                         ("published", "Measure what you value, friends."))
        self.assertEqual(len(self.events("last_words")), 1)
        prompt = [r for r in self.provider.requests if "Your last words as JSON" in r.messages[0]["content"]][0]
        self.assertIn("your energy ran out", prompt.messages[0]["content"])

    def test_withheld_last_words_never_reach_the_fossil_or_the_public(self):
        run = self.last_words_script('{"text": "Find me at https://example.com"}')
        self.starve("hazel")
        run.run()
        fossil = self.fossil("hazel")
        self.assertEqual((fossil["last_words_status"], fossil["last_words"]), ("withheld", None))
        self.conn.commit()
        with patch.dict(os.environ, {"ARENA_DEV_AUTH": "1", "KLADOS_SOCIETY_PUBLIC": "1"}):
            client = TestClient(create_app(connection_factory=self.connection, now=lambda: self.now))
            body = client.get("/api/society/agents/hazel").json()
        self.assertIsNone(body["last_words"])
        self.assertEqual(body["fossil"]["death_cause"], "out of energy")
        self.assertNotIn("example.com", json.dumps(body))

    def test_a_fossil_without_last_words_after_three_unusable_replies(self):
        run = self.last_words_script("I am thinking about it...")
        self.starve("hazel")
        for expected in ("pending", "pending", "missing"):
            run.run()
            self.assertEqual(self.fossil("hazel")["last_words_status"], expected)
        self.assertEqual(self.fossil("hazel")["counts"]["posts"], 0)  # the fossil itself is complete

    def connection(self):
        conn = db.connect(DSN)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return conn


class GenericApiTests(WorkerCase):
    def test_the_api_speaks_of_agents_with_their_kind(self):
        post = self.turn("alder", action="post", text="Hello.", note="SECRET-NOTE")["post_id"]
        society_moderation.apply_verdict(self.conn, RULES, post, CLEAN, self.now)
        self.turn("cedar", action="like", post_id=post)
        feed = society_public.feed(self.conn, RULES)
        self.assertNotIn("founders", feed)
        self.assertEqual({a["kind"] for a in feed["agents"]}, {"founder"})
        profile = society_public.profile(self.conn, RULES, "alder")
        self.assertEqual(profile["description"], prompts.load_founders()["alder"]["personality"])
        self.assertIsNone(profile["fossil"])
        self.assertIsNone(society_public.profile(self.conn, RULES, "nobody"))
        stats = society_public.stats(self.conn, RULES)
        self.assertEqual(stats["population"]["alive"], 4)
        self.assertIn({"from": "cedar", "to": "alder", "type": "like", "count": 1}, stats["interactions"])
        self.assertEqual(stats["published"], {"posts": 1, "replies": 0})
        self.assertNotIn("SECRET-NOTE", json.dumps(stats, default=str))
