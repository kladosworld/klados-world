"""HTTP failures and mode disclosure without a database or real credentials."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena.app import create_app


class HttpReadinessTests(unittest.TestCase):
    def test_database_failure_is_unready_and_does_not_expose_exception_text(self):
        def unavailable():
            raise RuntimeError("synthetic-private-dsn")

        with patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC="1"), TestClient(
                create_app(connection_factory=unavailable), raise_server_exceptions=False) as client:
            health = client.get("/healthz")
            self.assertEqual((health.status_code, health.json()), (503, {"ready": False}))
            response = client.get("/api/society/feed")
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("synthetic-private-dsn", response.text)

    def test_disabled_public_api_fails_closed_before_database_access(self):
        def forbidden():
            raise AssertionError("disabled API must not open the database")

        with patch.dict(os.environ, KLADOS_SOCIETY_PUBLIC="0"), TestClient(
                create_app(connection_factory=forbidden)) as client:
            for path in ("feed", "square", "stats", "agents/alder", "agents/missing"):
                self.assertEqual(client.get("/api/society/" + path).status_code, 404)
