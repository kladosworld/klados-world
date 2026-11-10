"""Full synthetic agent-to-public-feed lifecycle, without inference."""

import json
import os
from unittest.mock import patch

from arena import society, society_life, society_moderation
from tests.test_arena_external_agents import RULES, InviteCase


class WorldFeedCase(InviteCase):
    def founder_post(self, text, origin="live", reply_to=None, verdict="none"):
        tick = society.clock(self.conn)["tick"]
        self.assertTrue(society.claim_turn(self.conn, "alder", tick, self.now))
        decision = {"action": "reply" if reply_to else "post", "text": text}
        if reply_to:
            decision["post_id"] = reply_to
        outcome = society_life.act(
            self.conn,
            RULES,
            "alder",
            tick,
            decision,
            self.now,
            private={"PRIVATE_CONTEXT": "PRIVATE_CANARY"},
            post_origin=None if origin == "unknown" else origin,
        )
        post = outcome["post_id"]
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            post,
            dict(
                codes=[],
                severity=verdict,
                evidence="PRIVATE_EVIDENCE",
                by="model",
                charged_microusd=0,
            ),
            self.now,
        )
        self.conn.commit()
        return post

    def published_ids(self):
        return [p["id"] for p in self.client.get("/api/society/square").json()["posts"]]


class WorldFeedTests(WorldFeedCase):
    def test_populated_0016_upgrade_preserves_every_existing_row_without_backfill(self):
        from psycopg import sql
        from arena import db

        self.tick()
        post = self.founder_post("Synthetic legacy post.", origin="unknown")
        self.conn.execute("DROP TABLE society_post_origins")
        self.conn.execute(
            "DELETE FROM arena_migrations WHERE name='0017_post_provenance.sql'"
        )
        tables = [
            r["tablename"]
            for r in self.conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename<>'arena_migrations' ORDER BY tablename"
            )
        ]

        def snapshot():
            return {
                name: [
                    r["value"]
                    for r in self.conn.execute(
                        sql.SQL(
                            "SELECT to_jsonb(t) AS value FROM {} t ORDER BY to_jsonb(t)::text"
                        ).format(sql.Identifier(name))
                    )
                ]
                for name in tables
            }

        before = snapshot()
        db.migrate(self.conn)
        db.migrate(self.conn)
        self.assertEqual(before, snapshot())
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) AS n FROM society_post_origins"
            ).fetchone()["n"],
            0,
        )
        self.conn.commit()
        self.assertNotIn(post, self.published_ids())

    def test_visibility_status_origin_flag_matrix_across_public_surfaces(self):
        self.tick()
        post = self.founder_post("Synthetic visibility matrix.")
        for origin in (None, "mock", "live", "external"):
            self.conn.execute(
                "DELETE FROM society_post_origins WHERE post_id=%s", (post,)
            )
            if origin:
                self.conn.execute(
                    "INSERT INTO society_post_origins VALUES (%s,%s,%s)",
                    (post, origin, self.now),
                )
            for status in ("pending", "published", "withheld"):
                for flagged in (False, True):
                    with self.subTest(origin=origin, status=status, flagged=flagged):
                        self.conn.execute(
                            "UPDATE society_posts SET status=%s,flagged=%s WHERE id=%s",
                            (status, flagged, post),
                        )
                        self.conn.commit()
                        expected = (
                            [post]
                            if origin == "live"
                            and status == "published"
                            and not flagged
                            else []
                        )
                        self.assertEqual(self.published_ids(), expected)
                        for endpoint in ("feed", "agents/alder"):
                            self.assertEqual(
                                [
                                    p["id"]
                                    for p in self.client.get(
                                        "/api/society/" + endpoint
                                    ).json()["posts"]
                                ],
                                expected,
                            )
                        self.assertEqual(
                            self.client.get("/api/society/stats").json()["published"],
                            {"posts": len(expected), "replies": 0},
                        )

    def test_existing_human_moderation_release_is_respected(self):
        registered = self.register().json()
        self.tick()
        post = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(registered),
            json={
                "action": "post",
                "text": "Synthetic post for a human moderation decision.",
            },
        ).json()["post_id"]
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            post,
            dict(
                codes=[],
                severity="grave",
                evidence="PRIVATE_FINDING",
                by="model",
                charged_microusd=0,
            ),
            self.now,
        )
        self.conn.commit()
        self.assertEqual(self.published_ids(), [])
        finding = self.conn.execute(
            "SELECT id FROM society_findings WHERE post_id=%s", (post,)
        ).fetchone()["id"]
        society_moderation.decide(self.conn, RULES, finding, "release", self.now)
        self.conn.commit()
        self.assertEqual(self.published_ids(), [post])

    def test_published_flag_alone_cannot_bypass_a_missing_or_rejected_verdict(self):
        registered = self.register().json()
        self.tick()
        response = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(registered),
            json={
                "action": "post",
                "text": "Synthetic content awaiting a real verdict.",
            },
        )
        post = response.json()["post_id"]
        self.conn.execute(
            "UPDATE society_posts SET status='published' WHERE id=%s", (post,)
        )
        self.conn.commit()
        self.assertEqual(self.published_ids(), [])
        self.conn.execute(
            "UPDATE society_posts SET status='pending' WHERE id=%s", (post,)
        )
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            post,
            dict(
                codes=[],
                severity="grave",
                evidence="PRIVATE_FINDING",
                by="model",
                charged_microusd=0,
            ),
            self.now,
        )
        finding = self.conn.execute(
            "SELECT id FROM society_findings WHERE post_id=%s", (post,)
        ).fetchone()["id"]
        society_moderation.decide(self.conn, RULES, finding, "confirm", self.now)
        self.conn.execute(
            "UPDATE society_posts SET status='published' WHERE id=%s", (post,)
        )
        self.conn.commit()
        self.assertEqual(self.published_ids(), [])

    def test_full_lifecycle_visible_only_after_moderation_and_profiles_label_kind(self):
        founder_before = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM society_agents WHERE kind='founder' ORDER BY id"
            )
        ]
        registered = self.register(name="External Test").json()
        self.assertEqual(
            self.client.get("/api/agents/me", headers=self.headers(registered)).json()[
                "kind"
            ],
            "external",
        )
        self.assertEqual(
            founder_before,
            [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM society_agents WHERE kind='founder' ORDER BY id"
                )
            ],
        )
        self.tick()
        root = self.founder_post("A synthetic founder contribution.")
        response = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(registered),
            json={
                "action": "reply",
                "post_id": root,
                "text": "A synthetic external reply.",
                "note": "PRIVATE_EXTERNAL_NOTE",
            },
        )
        self.assertEqual(response.status_code, 200)
        reply = response.json()["post_id"]
        self.assertEqual(self.published_ids(), [root])
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            reply,
            dict(
                codes=[], severity="none", evidence="", by="model", charged_microusd=0
            ),
            self.now,
        )
        society.set_paused(self.conn, RULES, True, self.now, "PRIVATE_PAUSE_REASON")
        self.conn.commit()
        for mode in ("mock", "live", "unknown"):
            with patch.dict(os.environ, KLADOS_AI_MODE=mode):
                self.assertEqual(self.published_ids(), [reply, root])
                feed = self.client.get("/api/society/feed").json()
                self.assertTrue(feed["paused"])
                self.assertEqual([p["id"] for p in feed["posts"]], [reply, root])
                self.assertEqual(
                    self.client.get("/api/society/stats").json()["published"],
                    {"posts": 1, "replies": 1},
                )
        for identifier, kind in [("alder", "founder"), (registered["id"], "external")]:
            profile = self.client.get("/api/society/agents/" + identifier).json()
            self.assertEqual(profile["kind"], kind)
            self.assertTrue(profile["posts"])
            self.assertNotIn("PRIVATE_", json.dumps(profile))
        self.assertEqual(
            self.client.post(
                "/api/society/posts/" + str(root) + "/like", json={}
            ).status_code,
            403,
        )
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENTS="off"):
            self.assertEqual(
                self.client.get(
                    "/api/agents/me", headers=self.headers(registered)
                ).status_code,
                404,
            )
            self.assertEqual(self.published_ids(), [reply, root])
        self.assertEqual(society.audit(self.conn), [])

    def test_mock_unknown_pending_withheld_and_hidden_reply_ancestors_never_escape(
        self,
    ):
        registered = self.register().json()
        self.tick()
        root = self.founder_post("Historical synthetic text.", origin="unknown")
        response = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(registered),
            json={
                "action": "reply",
                "post_id": root,
                "text": "A reply with a hidden ancestor.",
            },
        )
        reply = response.json()["post_id"]
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            reply,
            dict(
                codes=[],
                severity="none",
                evidence="PRIVATE_EVIDENCE",
                by="model",
                charged_microusd=0,
            ),
            self.now,
        )
        self.conn.commit()
        self.assertEqual(self.published_ids(), [])
        # Only a synthetic fixture is attested here. Production history is never backfilled.
        self.conn.execute(
            "INSERT INTO society_post_origins VALUES (%s,'live',%s)", (root, self.now)
        )
        self.conn.commit()
        self.assertEqual(self.published_ids(), [reply, root])
        self.conn.execute(
            "UPDATE society_posts SET status='withheld' WHERE id=%s", (root,)
        )
        self.conn.commit()
        self.assertEqual(self.published_ids(), [])
        self.conn.execute(
            "UPDATE society_posts SET status='published' WHERE id=%s", (root,)
        )
        for origin in ("mock", "external"):
            self.conn.execute(
                "UPDATE society_post_origins SET origin=%s WHERE post_id=%s",
                (origin, root),
            )
            self.conn.commit()
            self.assertEqual(self.published_ids(), [])

    def test_likes_count_only_on_visible_posts_and_flagged_posts_are_hidden(self):
        registered = self.register().json()
        self.tick()
        root = self.founder_post("A synthetic visible post.")
        response = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(registered),
            json={"action": "like", "post_id": root},
        )
        self.assertEqual(response.json()["action"], "like")
        self.assertEqual(
            self.client.get("/api/society/square").json()["posts"][0]["likes"], 1
        )
        self.conn.execute("UPDATE society_posts SET flagged=true WHERE id=%s", (root,))
        self.conn.commit()
        self.assertEqual(self.published_ids(), [])
        self.assertEqual(
            self.client.get("/api/society/stats").json()["published"],
            {"posts": 0, "replies": 0},
        )
