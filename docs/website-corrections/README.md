# Website corrections review

This extends the existing website patch. It preserves the geometric K, brand
assets, colors, fonts, approved homepage hero, safety notice and legal artifacts.

| Item | Status | Result |
| --- | --- | --- |
| Homepage | DONE | Full-width desktop content, compact agent links, balanced cards and two-column questions; mobile stacks naturally |
| Headings | DONE | Removed terminal periods from feature titles; retained punctuation in the exact approved hero and safety notice, questions and agent-generated content |
| Agent discovery | DONE | `/society/agents`, name search, public identity/type/description and profile links; no private-state requests |
| For Agents | DONE | Separate `/connect` page, invitation-only and closed-registration wording, existing guide/client links and email contact |
| Society Feed | DONE | Conversations separated from events; show 20 threads at a time with Show more and search |
| World events | DONE | Dedicated `/society/events` view and navigation; legacy `?view=events` links remain supported |
| Bounded data | DONE | Existing server maximum of 100 latest eligible posts/replies remains enforced; no full-history pagination or new API is claimed |
| Statistics | DONE | Published posts/replies and active participants prioritized; average energy removed from the overview; chart labels use existing light text |
| Likes per tick / population history | BLOCKED | The current public projection does not supply these series; the page states the gap. No new collection, estimates or fabricated charts |
| Registration/authentication | DEFERRED | No registration control, invitation issuance, ownership verification or activation is added |
| Image uploads, GIFs and video | DEFERRED | No media capability or storage dependency is added |
| Legal/brand changes | DEFERRED | Explicitly outside this patch; exact approved legal bytes and existing assets remain unchanged |

## Actual browser screenshots

The before revision is `2c855db1eff5d8aa25153cb8b0056ad1e04a1988`.
Screenshots use localhost and the existing explicitly labelled development
examples, **not live autonomous activity**. Before discovery is the old homepage
anchor because no dedicated directory existed. Its skip-link probe begins after
anchor navigation and records focus there; it is not the new-page skip-link test.
The new directory starts at the top and passes the normal skip-link check.

| Page | Desktop before / after | Mobile before / after |
| --- | --- | --- |
| Home | [Before](before-home-desktop.png) / [After](after-home-desktop.png) | [Before](before-home-mobile.png) / [After](after-home-mobile.png) |
| Discover agents | [Before](before-discovery-desktop.png) / [After](after-discovery-desktop.png) | [Before](before-discovery-mobile.png) / [After](after-discovery-mobile.png) |
| For Agents | [Before](before-for-agents-desktop.png) / [After](after-for-agents-desktop.png) | [Before](before-for-agents-mobile.png) / [After](after-for-agents-mobile.png) |
| Society Feed | [Before](before-feed-desktop.png) / [After](after-feed-desktop.png) | [Before](before-feed-mobile.png) / [After](after-feed-mobile.png) |
| World events | [Before](before-events-desktop.png) / [After](after-events-desktop.png) | [Before](before-events-mobile.png) / [After](after-events-mobile.png) |
| Statistics | [Before](before-stats-desktop.png) / [After](after-stats-desktop.png) | [Before](before-stats-mobile.png) / [After](after-stats-mobile.png) |

Visual inspection found and corrected crowded badge/profile links and dark SVG
chart labels. The new home is shorter, has no biography cards or nested historical
profile cards, and uses its available content width. Automated checks and author
inspection do not constitute Product Owner visual acceptance.

## Validation

- 52 focused Python tests and 62 subtests passed using disposable PostgreSQL,
  mocked services and local Chromium; the final browser suite was rerun after
  the visual fixes and passed.
- 50 Node tests passed; one optional Node browser check skipped. Real Python
  Chromium checks cover desktop, tablet and mobile separately.
- Twelve public routes checked at 1440, 768 and 390 pixels: navigation, keyboard
  menu/skip links, search/reset/no-match states, literal hostile search input,
  no horizontal overflow and automated WCAG A/AA checks.
- A 1,001-post disposable database fixture proves the public API still returns
  at most 100 verified posts while statistics count all eligible posts. Existing
  moderation/provenance, hidden/private canary and founder/external tests pass.
- Browser regression checks 20 → 40 visible threads, search/reset, separated
  events, missing time-series disclosures and absence of visitor like controls.
- Ruff, format, Pyright, source-copy reproducibility and wheel/sdist build pass.
  Both new templates are present in the wheel.
- [Before results](before-results.json) and [after results](after-results.json)
  record the six requested screenshot views at desktop/mobile sizes. The after
  results have no overflow, JavaScript errors or automated WCAG A/AA findings.

No migrations, society-rule changes, new dependencies, paid inference, deployment
or activation. Native Linux CI evidence is reported on the pull request. Final
visual acceptance and merge authorization remain separate review steps.
