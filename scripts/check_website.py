"""Browser smoke checks and screenshots against a running, isolated application.

Requires Playwright Python and Chromium; makes no database or provider writes.
Run: python scripts/check_website.py --url http://127.0.0.1:8367 --screenshots /tmp/klados-shots
The application must use KLADOS_AI_MODE=mock: screenshots label test activity.
"""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--screenshots", required=True)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    output = Path(args.screenshots)
    output.mkdir(parents=True, exist_ok=True)
    with urlopen(base + "/society/config.json") as response:
        config = json.load(response)
    assert config["live"] and config["ai_mode"] == "mock", (
        "Use an explicitly mocked isolated society"
    )
    with urlopen(base + "/api/society/feed") as response:
        feed = json.load(response)
    with urlopen(base + "/api/society/square") as response:
        posts = json.load(response)["posts"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.chromium, headless=True, args=["--no-sandbox"]
        )
        errors = []
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        for label, width in [("desktop", 1440), ("mobile", 390)]:
            page.set_viewport_size({"width": width, "height": 1000 if width > 600 else 844})
            for path, name in [
                ("/", "home"),
                ("/society/square", "feed"),
                ("/society/agents/alder", "profile"),
                ("/docs", "docs"),
                ("/society/stats", "status"),
            ]:
                page.goto(base + path, wait_until="networkidle")
                assert page.locator("h1").count() == 1, path
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    path,
                    width,
                )
                assert (
                    page.get_by_role("navigation")
                    .first.get_by_role("link", name="Home", exact=True)
                    .count()
                    == 1
                )
                if name == "home":
                    assert page.locator("#agent-profiles .agents article").count() == len(
                        feed["agents"]
                    )
                    assert "Mock AI mode" in page.locator("#home-example").inner_text()
                    assert "Paused" in page.locator("#home-society-state").inner_text()
                    for agent in feed["agents"]:
                        assert agent["name"] in page.locator("#agent-profiles").inner_text()
                elif name in ("feed", "profile", "status"):
                    assert "Mock AI mode" in page.locator("#demo-notice").inner_text()
                    assert page.locator("#demo-notice").is_visible()
                if name == "feed":
                    assert page.locator('article[id^="post-"]').count() == len(posts)
                    assert page.locator("time[datetime]").count() == len(posts)
                    assert all(
                        not button.is_enabled()
                        for button in page.get_by_role("button", name="Like post").all()
                    )
                if name == "profile":
                    assert page.locator("h1").inner_text() == "Alder"
                    assert "Patient and methodical" in page.locator("#society-results").inner_text()
                    assert "SECRET" not in page.locator("body").inner_text()
                if name != "status":
                    page.screenshot(path=str(output / f"{name}-{label}.png"), full_page=True)
                print(f"PASS {label} {path}")
        # Navigation actually follows the profile link and returns to the feed.
        page.goto(base, wait_until="networkidle")
        page.locator("#agent-profiles").get_by_role("link", name="Alder", exact=True).click()
        page.wait_for_url("**/society/agents/alder")
        page.get_by_role("heading", name="Alder", exact=True).wait_for()
        page.get_by_role("navigation").first.get_by_role("link", name="Feed", exact=True).click()
        page.wait_for_url("**/society/square")
        page.wait_for_selector('article[id^="post-"]')
        # Failure and empty states never fall back silently to fictional activity.
        page.route("**/api/society/square", lambda route: route.fulfill(json={"posts": []}))
        page.reload(wait_until="networkidle")
        assert "No published posts" in page.locator("#society-results").inner_text()
        page.unroute("**/api/society/square")
        page.route("**/api/society/square", lambda route: route.fulfill(status=503, json={}))
        page.reload(wait_until="networkidle")
        assert "temporarily unavailable" in page.locator("#status").inner_text()
        assert page.locator('article[id^="post-"]').count() == 0
        page.unroute("**/api/society/square")
        page.route("**/api/society/feed", lambda route: route.fulfill(status=503, json={}))
        page.goto(base, wait_until="networkidle")
        assert "No current activity is shown" in page.locator("#site-content").inner_text()
        assert "No fossils yet" not in page.locator("#site-content").inner_text()
        page.unroute("**/api/society/feed")
        page.goto(base + "/society/agents/not-a-founder", wait_until="networkidle")
        assert page.locator("h1").inner_text() == "Profile not found"
        assert not errors, errors
        browser.close()
    print(
        "PASS navigation, persisted timestamps, source labels, empty/error/404 states; "
        "no browser errors"
    )


if __name__ == "__main__":
    main()
