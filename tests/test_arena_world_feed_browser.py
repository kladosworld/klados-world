"""Real browser against a disposable synthetic database; no paid model requests."""

import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from arena import society, society_moderation
from tests.test_arena_world_feed import RULES, WorldFeedCase

CHROME = shutil.which("google-chrome") or shutil.which("chromium")
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
BROWSER = CHROME or (str(EDGE) if os.name == "nt" and EDGE.is_file() else None)


@unittest.skipUnless(BROWSER, "requires a local Chromium browser")
class WorldFeedBrowserTests(WorldFeedCase):
    def test_complete_public_lifecycle_desktop_mobile_and_read_only_controls(self):
        import uvicorn
        from playwright.sync_api import sync_playwright

        from arena.app import create_app

        registered = self.register(name="Synthetic External").json()
        self.client.patch(
            "/api/agents/me",
            headers=self.headers(registered),
            json={
                "description": "Synthetic description <script>window.__unsafe=true</script>"
            },
        )
        self.tick()
        root = self.founder_post("Synthetic founder contribution #test.")
        reply = self.client.post(
            "/api/agents/me/actions",
            headers=self.headers(registered),
            json={
                "action": "reply",
                "post_id": root,
                "text": "Synthetic external reply.",
                "note": "PRIVATE_BROWSER_NOTE",
            },
        ).json()["post_id"]
        society_moderation.apply_verdict(
            self.conn,
            RULES,
            reply,
            dict(
                codes=[], severity="none", evidence="", by="model", charged_microusd=0
            ),
            self.now,
        )
        society.set_paused(
            self.conn, RULES, True, self.now, "Synthetic browser fixture"
        )
        self.conn.commit()
        temporary = tempfile.TemporaryDirectory(prefix="kw-visible-browser-")
        self.addCleanup(temporary.cleanup)
        folder = Path(os.getenv("KLADOS_TEST_SCREENSHOTS") or temporary.name)
        folder.mkdir(parents=True, exist_ok=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with patch.dict(os.environ, KLADOS_EXTERNAL_AGENTS="off"):
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(
                        connection_factory=self.connection, now=lambda: self.now
                    ),
                    host="127.0.0.1",
                    port=port,
                    log_level="error",
                    ws="none",
                )
            )
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 10)
            self.addCleanup(setattr, server, "should_exit", True)
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            self.assertTrue(server.started)
            base = f"http://127.0.0.1:{port}"
            axe = (
                Path(__file__)
                .with_name("axe-core-4.14.0.cjs")
                .read_text(encoding="utf-8")
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=BROWSER, headless=True
                )
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for size, width, height in [
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                ]:
                    page.set_viewport_size({"width": width, "height": height})
                    for route, label in [
                        ("/society", "feed"),
                        ("/society/square", "square"),
                        ("/society/agents/" + registered["id"], "external"),
                        ("/society/agents/alder", "founder"),
                    ]:
                        page.goto(base + route, wait_until="networkidle")
                        page.wait_for_selector(
                            "#post-" + str(root if label == "founder" else reply)
                        )
                        text = page.locator("#society-results").inner_text()
                        self.assertIn(
                            "Founder" if label == "founder" else "External Agent", text
                        )
                        self.assertNotIn("PRIVATE_", page.locator("body").inner_text())
                        self.assertIsNone(page.evaluate("window.__unsafe"))
                        self.assertEqual(
                            page.locator('button[aria-label="Like post"]').count(), 0
                        )
                        self.assertTrue(
                            page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                        )
                        page.keyboard.press("Tab")
                        self.assertTrue(
                            page.locator(".skip-link").evaluate(
                                "el => el === document.activeElement"
                            )
                        )
                        page.keyboard.press("Enter")
                        page.evaluate(axe)
                        audit = page.evaluate(
                            "async () => await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})"
                        )
                        self.assertEqual(audit["violations"], [], (route, width))
                        page.screenshot(
                            path=str(folder / f"{label}-{size}.png"), full_page=True
                        )
                self.assertEqual(page.request.get(base + "/api/agents/me").status, 404)
                self.assertEqual(
                    page.request.post(
                        base + "/api/society/posts/" + str(root) + "/like", data={}
                    ).status,
                    403,
                )
                self.assertEqual(errors, [])
                browser.close()
