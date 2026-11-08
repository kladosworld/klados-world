"""Read-only browser QA against a local example-mode application; no society writes."""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--public-release",
        action="store_true",
        help="Validate the read-only public empty-state projection",
    )
    parser.add_argument("--url", default="http://127.0.0.1:8368")
    parser.add_argument("--browser", required=True)
    parser.add_argument("--axe", required=True, help="Integrity-verified axe-core JS bundle")
    parser.add_argument("--screenshots", required=True)
    args = parser.parse_args()
    base = args.url.rstrip("/")
    assert base.startswith("http://127.0.0.1:"), "Only the local test application is allowed"
    with urlopen(base + "/society/config.json") as response:
        config = json.load(response)
    if args.public_release:
        assert (
            config["live"] is True
            and config["examples"] is False
            and config["likes_enabled"] is False
        )
    else:
        assert config["live"] is False and config["examples"] is True, (
            "Use an explicit local development preview"
        )
    output = Path(args.screenshots)
    output.mkdir(parents=True, exist_ok=True)
    axe = Path(args.axe).read_text(encoding="utf-8")
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for label, width, height in [
            ("desktop", 1440, 1000),
            ("tablet", 900, 1000),
            ("mobile", 390, 844),
        ]:
            page.set_viewport_size({"width": width, "height": height})
            for route, name in [
                ("/society", "society"),
                ("/society/agents/alder", "profile"),
                ("/", "home"),
            ]:
                page.goto(base + route, wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                assert page.locator("h1").count() == 1, route
                assert page.locator("nav[aria-label='Main']").count() == 1, route
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    route,
                    width,
                )
                assert page.evaluate(
                    "document.fonts.check('16px Inter') && "
                    "document.fonts.check('16px \"Space Grotesk\"')"
                )
                if args.public_release:
                    assert "Development preview" not in page.locator("body").inner_text(), route
                else:
                    assert "Development preview" in page.locator("body").inner_text(), route
                page.keyboard.press("Tab")
                assert page.locator(".skip-link").evaluate("el => el === document.activeElement")
                page.keyboard.press("Enter")
                if width <= 700:
                    page.get_by_role("button", name="Menu", exact=True).click()
                    assert page.locator("#menu-toggle").get_attribute("aria-expanded") == "true"
                    assert page.locator("#primary-nav").is_visible()
                    page.keyboard.press("Escape")
                    assert page.locator("#menu-toggle").get_attribute("aria-expanded") == "false"
                page.evaluate(axe)
                audit = page.evaluate(
                    "async () => await axe.run(document, {runOnly:{type:'tag',"
                    "values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})"
                )
                violations = [
                    {"id": v["id"], "nodes": [n["target"] for n in v["nodes"]]}
                    for v in audit["violations"]
                ]
                results.append({"page": name, "viewport": label, "axe_violations": violations})
                assert not violations, (route, width, violations)
                page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
                page.screenshot(
                    path=str(output / f"{name}-{label}.png"), full_page=True, animations="disabled"
                )
                if name == "society":
                    if args.public_release:
                        assert page.locator(".thread").count() == 0
                        assert page.locator("#demo-notice:visible").count() == 0
                        assert page.locator("#society-auth:visible").count() == 0
                        assert page.locator('button[aria-label="Like post"]').count() == 0
                        assert "Paused" in page.locator("#society-state").inner_text()
                    else:
                        assert page.locator(".thread").count() > 0
                        assert (
                            "Development preview" in page.locator("#overview-source").inner_text()
                        )
                    search = page.get_by_role("searchbox")
                    search.fill("no-such-published-text-987654")
                    assert page.locator("#feed-empty").is_visible()
                    search.fill("")
                    page.get_by_role("button", name="Events", exact=True).click()
                    assert page.locator(".thread:visible").count() == 0
                    page.get_by_role("button", name="Most liked", exact=True).click()
                    likes = page.locator(".thread:visible").evaluate_all(
                        "els => els.map(el => Number(el.dataset.likes))"
                    )
                    assert likes == sorted(likes, reverse=True)
        # Reflow checks at narrow and intermediate widths, independent of screenshot sizes.
        for width in (320, 375, 700, 768, 1024, 1280, 1728):
            page.set_viewport_size({"width": width, "height": 900})
            for route in ("/", "/society", "/society/agents/alder", "/society/stats", "/docs"):
                page.goto(base + route, wait_until="networkidle")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    route,
                    width,
                )
        # Synthetic component contracts: arbitrary identities, exact counts, escaped text.
        page.goto(base + "/society", wait_until="networkidle")
        page.evaluate("""() => kladosVisual.update({feed:{tick:7,
          agents:[{id:'new-id',name:'New agent',status:'alive',energy:17}],events:[]},
          posts:[{author:{id:'new-id',name:'New <script> agent'},text:'#Trust #Trust #Other'},
                 {author:{id:'new-id',name:'New <script> agent'},text:'#Trust'}],
          configuredLive:false,aiMode:'mock'})""")
        assert "#trust" in page.locator("#world-topics").inner_text()
        assert "2 posts" in page.locator("#world-topics").inner_text()
        assert page.locator("#world-agents .agent-rank").count() == 1
        assert "New <script> agent" in page.locator("#world-agents").inner_text()
        assert page.locator("#world-agents script").count() == 0
        page.evaluate("kladosVisual.unavailable()")
        assert "may be stale" in page.locator("#overview-source").inner_text()
        # Enabled API errors must not silently fall back to example fixtures.
        await_error_config = {"live": True, "ai_mode": "live"}
        page.route("**/society/config.json", lambda route: route.fulfill(json=await_error_config))
        page.route(
            "**/api/society/**",
            lambda route: route.fulfill(status=503, json={"detail": "Unavailable"}),
        )
        page.goto(base + "/society", wait_until="networkidle")
        assert "Couldn't load the world" in page.locator("#status").inner_text()
        assert page.locator(".thread").count() == 0
        assert not errors, errors
        browser.close()
    (output / "browser-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(
        f"PASS: {len(results)} page/viewport accessibility audits, 35 reflow checks; no JS errors."
    )


if __name__ == "__main__":
    main()
