"""Website navigation and disclosure checks using explicitly labelled examples."""

import socket
import threading
import time
import unittest
from pathlib import Path

from tests.test_arena_world_feed_browser import BROWSER


@unittest.skipUnless(BROWSER, "requires a local Chromium browser")
class WebsiteBrowserTests(unittest.TestCase):
    def test_public_pages_navigation_and_mobile_keyboard_access(self):
        import uvicorn
        from playwright.sync_api import sync_playwright

        from arena.app import create_app

        def forbidden():
            raise AssertionError("Example pages must not open a database")

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            create_app(connection_factory=forbidden, examples=True),
            host="127.0.0.1", port=port, log_level="error", ws="none",
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(setattr, server, "should_exit", True)
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        self.assertTrue(server.started)
        base = f"http://127.0.0.1:{port}"
        axe = Path(__file__).with_name("axe-core-4.14.0.cjs").read_text(encoding="utf-8")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=BROWSER, headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for width in (1440, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for route in ("/", "/connect", "/society", "/society/events", "/society/agents", "/society/square",
                              "/society/agents/alder", "/society/stats", "/docs",
                              "/contact", "/terms", "/privacy"):
                    page.goto(base + route, wait_until="networkidle")
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"), (route, width))
                    self.assertEqual(page.locator("#primary-nav").count(), 1)
                    self.assertEqual(page.locator('#primary-nav a[href="/connect"]').inner_text(), "For Agents")
                    self.assertEqual(page.locator(".safety-notice").inner_text(),
                        "Stay safe. The Klados team will never ask you for passwords, private keys, recovery phrases, account credentials, or payments. Never share sensitive information with anyone claiming to represent Klados.")
                    page.keyboard.press("Tab")
                    self.assertTrue(page.locator(".skip-link").evaluate("e => e === document.activeElement"))
                    page.evaluate(axe)
                    violations = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations")
                    self.assertEqual(violations, [], (route, width))
                page.goto(base + "/society/agents", wait_until="networkidle")
                self.assertEqual(page.locator("#agent-directory article").count(), 4)
                page.locator("#agent-search").fill("ALDER")
                self.assertEqual(page.locator("#agent-directory article").count(), 1)
                self.assertIn("Alder", page.locator("#agent-directory").inner_text())
                page.locator("#agent-search").fill("<script>window.__unsafe = true</script>")
                self.assertEqual(page.locator("#agent-directory article").count(), 0)
                self.assertIsNone(page.evaluate("window.__unsafe"))
                page.locator("#agent-search").fill("")
                self.assertEqual(page.locator("#agent-directory article").count(), 4)
                page.goto(base + "/society/events", wait_until="networkidle")
                self.assertEqual(page.locator("#society-results > .thread:visible").count(), 0)
                self.assertEqual(page.locator(".feed-hero h1").inner_text(), "World events")
                page.goto(base + "/society", wait_until="networkidle")
                self.assertEqual(page.locator("#society-results > .world-event:visible").count(), 0)
                self.assertNotIn("Avg. energy", page.locator("#world-summary").inner_text())
                page.goto(base + "/connect", wait_until="networkidle")
                if page.locator("#menu-toggle").is_visible():
                    page.locator("#menu-toggle").focus()
                    page.keyboard.press("Enter")
                    self.assertEqual(page.locator("#menu-toggle").get_attribute("aria-expanded"), "true")
                    page.keyboard.press("Escape")
                    self.assertEqual(page.locator("#menu-toggle").get_attribute("aria-expanded"), "false")
                    self.assertTrue(page.locator("#menu-toggle").evaluate("e => e === document.activeElement"))
                for anchor in page.locator('#site-content a[href^="/"]').all():
                    self.assertEqual(page.request.get(base + anchor.get_attribute("href")).status, 200)
            # Model the existing bounded API response, without adding any public data.
            posts = [{"id": i, "text": f"Boundary item {i:03}", "author": {"id": "alder", "name": "Alder", "kind": "founder"}, "reply_to": None, "hours_ago": 100-i, "likes": 0} for i in range(1, 101)]
            page.route("**/static/fixtures/square.json", lambda route: route.fulfill(json={"_example": True, "posts": posts}))
            page.goto(base + "/society", wait_until="networkidle")
            self.assertEqual(page.locator(".thread:visible").count(), 20)
            page.locator("#feed-more").click()
            self.assertEqual(page.locator(".thread:visible").count(), 40)
            page.locator("#feed-search").fill("Boundary item 099")
            self.assertEqual(page.locator(".thread:visible").count(), 1)
            self.assertFalse(page.locator("#feed-more").is_visible())
            page.locator("#feed-search").fill("")
            self.assertEqual(page.locator(".thread:visible").count(), 20)
            self.assertEqual(page.locator('button[aria-label="Like post"]').count(), 0)
            self.assertEqual(errors, [])
            browser.close()
