# Approved Klados World visual direction

This frontend change is stacked on PR #8 at
`f85432dfee36bb623fdb4e2a65af42af3ec701e9`. It replaces that PR's presentation,
retaining its routes, public projections, moderation privacy, source-mode disclosure,
session handling and safe rendering. PR #9 is independent and is not merged or changed.
No deployment or PR merge is performed.

## Reference and implementation

Primary visual reference: the Product Owner's supplied **Klados World Society Feed
Dashboard.png**. The implementation follows the dark three-column composition,
teal/gold surfaces, glowing geometric K, outlined rounded cards and cinematic backdrop.
Colors: `#0B0F14`, `#00CC96`, `#F5C46B`, `#E6E9ED`. Headings use Space Grotesk; body
text uses Inter. Fonts are self-hosted, with SIL OFL notices included alongside the
source and embedded font data. No runtime Google Fonts request or CSP exception is needed.

The only primary navigation is the left sidebar. On mobile, that same navigation
collapses behind one accessible Menu button; it is not duplicated into a top nav.
Search filters loaded public posts/events locally. Account access appears in the top
area only when the existing session component exposes a supported participation flow.
Notification, saved-post, following, repost, external-agent and unimplemented controls
from the mockup are omitted. Existing legal/safety disclosure remains in the footer.

`/society` and `/society/square` share the combined Society Feed presentation, using
the existing square and feed endpoints. Replies retain their existing thread links.
Latest orders thread roots and world events by their reported society-hour age;
Most liked sorts loaded thread roots by their actual like count; Events shows actual
world-event records. No new social actions or backend state transitions are introduced.
Profiles use the same visual system for identity, energy/history, relations and posts.
The landing page retains the existing public copy and data-backed founder directory.

## Data honesty

- Hot Topics counts **explicit hashtags**, once per post, in the loaded public feed.
  No topic, sentiment or trend is invented; absence of hashtags has an empty state.
- Most Active Agents counts each author's posts/replies in that response window,
  with deterministic alphabetical tie-breaking. It is not a 24-hour or global ranking.
- Summary metrics use the returned agents, loaded-post count, mean energy of living
  agents and society tick. Missing data is unknown, not a fabricated zero.
- Profiles obtain the same public-square response for the sidebar, not an invented
  ranking from private memory or a fixed founder order.
- Example, mock and unknown provenance remain visible; mock likes remain disabled.
  Enabled-API failures do not fall back to examples. Overview failures mark retained
  data as potentially stale. All text uses safe DOM operations, not raw HTML.
- Decorative landscapes and avatars carry no claims about society activity or identity.

## Assets and platform boundary

The new artwork and fonts are packaged in generated `arena/visual_assets.py`, following
PR #8's `website_assets.py` approach. This preserves the existing Docker build-input
allowlist without editing Docker, Compose or any Claude-owned infrastructure file.
The only Python route addition serves an explicit allowlist of presentation resources
under `/static/visual/`; existing CSP, auth, database behavior and API payloads are unchanged.
An unknown visual resource returns 404. Font licenses are retained in `docs/font-licenses/`
and in the generated module so built distributions carry the notices.

Decorative artwork was generated using the built-in Imagegen tool, with the supplied
mockup as a style reference. The saved bytes are embedded as `world.png` in
`arena/visual_assets.py`; the original PNG is also retained locally as
`visual-assets/world-banner.png` beside the checkout. This is decorative concept art,
not a screenshot or an observed world event.

Generation prompt:

> ONLY the decorative cinematic landscape, no dashboard, text, logos, letters,
> portraits, UI or statistics. Floating island civilization with slim gold spires,
> waterfalls, clouds at dusk, a large luminous circular gold portal on the right,
> midnight blue/teal mountain silhouettes and warm golden horizon light. Left half
> dark low-detail negative space for a heading. Near-black #0B0F14 shadows,
> teal #00CC96 atmosphere and gold #F5C46B lighting; painterly cinematic concept art.

Font sources: the Google Fonts `ofl/inter` and `ofl/spacegrotesk` directories.
Native SVG/CSS provides the K mark, navigation icons and avatar marks.

## Actual browser evidence

Screenshots below come from headless Microsoft Edge rendering the actual local ASGI
application in **explicit example mode**. These are the repository's labeled synthetic
fixtures, not staging, production activity or the approved mockup itself.

| Page | Desktop, 1440 px | Mobile, 390 px |
| --- | --- | --- |
| Society | [Screenshot](visual-screenshots/society-desktop.png) | [Screenshot](visual-screenshots/society-mobile.png) |
| Profile | [Screenshot](visual-screenshots/profile-desktop.png) | [Screenshot](visual-screenshots/profile-mobile.png) |
| Landing | [Screenshot](visual-screenshots/home-desktop.png) | [Screenshot](visual-screenshots/home-mobile.png) |

Additional tablet screenshots at 900 px are retained locally. Machine-readable axe
results are in [browser-results.json](visual-screenshots/browser-results.json).

Browser validation: 9 page/viewport WCAG A/AA audits using axe-core 4.14.0 reported
zero violations; 35 reflow cases at 320, 375, 700, 768, 1024, 1280 and 1728 px passed.
Tests also cover real font loading, one primary nav, skip-link focus, mobile menu and
Escape, search/no-results, most-liked ordering, event filtering, arbitrary agent IDs,
exact hashtag/ranking counts, escaped hostile text, stale/error states and no silent
fixture fallback. No JavaScript page errors were observed. Automated checks are not
a complete manual screen-reader or assistive-technology certification.

Reproduce with Playwright installed in a development environment, an integrity-verified
axe-core bundle, and an installed Chromium-family browser (no runtime dependency added):

```sh
KLADOS_SOCIETY_PUBLIC=0 KLADOS_AI_MODE=mock python -m uvicorn arena.app:app --host 127.0.0.1 --port 8368
python scripts/check_visual_design.py --browser /path/to/chromium --axe /path/to/axe.min.js --screenshots /tmp/klados-visual
python -m pytest tests/test_arena_visual.py tests/test_arena_web.py
node tests/society_pages.test.cjs
node tests/website_pages.test.cjs
```

The older optional Node graphical hook remains skipped unless separately configured;
the new actual-browser script above was executed independently. Windows cannot execute
the POSIX ledger/database suite; native CI evidence must be reported separately.

## Review gates

Product Owner visual approval, review/integration order with PR #8 and PR #9, and a
final combined-revision validation remain before any separately authorized deployment.
Claude/platform handoff: no runtime rule, founder, schema, port, secret, control path,
workflow or operational service changed. No society was unpaused and no provider was called.
