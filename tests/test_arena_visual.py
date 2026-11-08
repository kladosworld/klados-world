"""The visual redesign changes only public assets and presentation contracts."""
import re
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from arena.app import create_app
from arena.visual_assets import RESOURCES

STATIC = Path(__file__).resolve().parent.parent / "arena/static"


class VisualTests(unittest.TestCase):
    def test_only_one_primary_navigation_in_every_template(self):
        for path in STATIC.glob("*.html"):
            with self.subTest(page=path.name):
                html = path.read_text(encoding="utf-8")
                self.assertEqual(html.count('aria-label="Main"'), 1)
                self.assertIn('id="primary-nav"', html)
                self.assertIn('id="menu-toggle"', html)
                self.assertIn('aria-controls="primary-nav"', html)
                self.assertNotIn("Following", html)
                self.assertNotIn("Saved", html)

    def test_self_hosted_assets_are_explicitly_allowlisted_and_do_not_connect_to_database(self):
        def forbidden():
            raise AssertionError("Visual resources must not query the database")

        with TestClient(create_app(connection_factory=forbidden)) as client:
            for name, (mime, content) in RESOURCES.items():
                response = client.get("/static/visual/" + name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, content)
                self.assertIn(mime, response.headers["content-type"])
                self.assertEqual(response.headers["content-security-policy"], "default-src 'self'")
            self.assertEqual(client.get("/static/visual/unapproved.key").status_code, 404)
            self.assertEqual(client.get("/static/visual.js").status_code, 200)

    def test_design_tokens_and_local_fonts(self):
        css = (STATIC / "society.css").read_text(encoding="utf-8")
        for color in ("#0B0F14", "#00CC96", "#F5C46B", "#E6E9ED"):
            self.assertIn(color, css)
        self.assertIn("Space Grotesk", css)
        self.assertIn("font-family:Inter", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertFalse(re.search(r"https?://", css))
