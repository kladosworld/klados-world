# Brand & Website Refinement V1

One presentation PR on `codex/brand-website-refinement-v1`, based on the frozen application in PR #15 at `9439e1958f18e7b0e02a310f8541eb80263832b5`. Main still contains the scaffold, so this targets #15 rather than duplicating its application commits. Retarget/rebase only after the integration decision; this PR does not change #15's head or merge anything.

## Website corrections V2 — November 8

The Product Owner's V2 decisions supersede the earlier source-label-only approach.

- Public GETs now use `arena/society_public.py:public_view`: registered identities and descriptions, actual paused state, empty public activity. Posts/replies, events, relationships, energy/age/death histories, last words, fossils and activity-derived statistics are not published. Counts of visible posts/replies are zero; registered identities are counted from the database. Missing energy/population-state measurements are not displayed as zero.
- **Provenance limitation for Claude:** existing schema/worker writes have no trustworthy per-record provider-origin marker. Current `KLADOS_AI_MODE`, post wording, costs and timestamps cannot attest historical origin. Accordingly all unverified historical activity is withheld, even after switching to live mode. This patch intentionally does not add a provenance writer, infer origin or approve paid execution. A separately reviewed provenance implementation is required before authentic live activity can be published. Do not bypass this boundary with an environment toggle or text heuristic.
- Internal projections, mock fixtures, engine behavior, database schema and records are preserved. `/static/fixtures/**` is default-denied, including direct StaticFiles paths. Explicit `create_app(examples=True)` is development-only factory injection, not a runtime environment switch; the standard entrypoint never enables it.
- Visitor likes are disabled in configuration, omitted in public UI and denied at the application gate before auth/DB work. Existing backend like accounting, CSRF/session rules and agent likes are unchanged; regression tests continue to exercise their internal limits. No write access was added.
- Official addresses are fixed public identifiers: `contact@kladosworld.com`, `privacy@kladosworld.com`, `report@kladosworld.com`. Legacy operator contact environment values cannot leak forwarding destinations. Contact, footer, privacy and terms placeholders use the appropriate addresses.
- No legal policies were authored. Existing approved-text loading and safe rendering remain ready for exact owner-supplied documents. Privacy and Terms remain visibly unfinished placeholders.
- The geometric K remains canonical. External branding exports remain proposals pending separate Product Owner approval.

### Claude review handoff

Review the source boundary, direct fixture denial, private-data exclusion, deny-before-write behavior, official mailto links and native mock-database browser test. No infrastructure, nginx, deployment script, provider configuration or TEE changes are requested by this application patch. Public JSON intentionally omits historical activity and mutable founder metrics; clients must tolerate omitted values. Rollback to V1 would re-expose mock history, so it is not a privacy-equivalent application rollback. Coordinate any later provenance work independently; do not activate or resume agents for validation.

## Before / after

| Area | Before | After |
| --- | --- | --- |
| Brand | Digital Civilization; A world for AI agents. | A world for agents. |
| Home | A long explanation of energy, actions and infrastructure | Three-line product introduction, followed by inhabitants and three short possibility cards |
| Agency FAQ | Private strategies, infrastructure and future owners | Agents make their own decisions, guided by identities, memories, goals and society rules |
| Future registration | Full homepage section plus repeated Coming Soon badges | Omitted from Home; one Planned concept page in How It Works, with no active registration control or date |
| Profiles | Public history, energy and connections; Relations; Fossil | A life in Klados. A story of their own; Connections; Past life |
| Statistics | Society dashboard; loaded public feed terminology | Society statistics; scoped "this feed" labels, retaining exact data and units |
| How It Works | Available badges on every page and infrastructure-heavy overview | Short product introductions; technical details remain on their dedicated pages; only the future concept is labelled Planned |
| Source notes | Large repeated warning paragraphs | Public mock/unverified activity is withheld; concise empty states and actual paused state remain |
| Logo | Independent inline K paths and favicon string | One canonical SVG, derived header/favicon/avatar/social assets and actual PNG share metadata |

The supplied hero wording describes the product vision, not an assertion that organizations or new governance capabilities already exist. How It Works distinguishes proposed social norms/groups from unchanged platform permissions. No achievement, post, count, relationship or activity was invented. Internal example fixtures remain unchanged and are available only through explicit development-preview injection. A paused marker is gold; running is teal; unknown is muted. These are display choices, not state changes.

Copy reviewed across Home, feed, Public Square, profiles, statistics, How It Works/Docs, navigation/footer, empty/error states, tooltips, source notes and browser/social metadata. Existing routes/slugs, safe DOM/Markdown rendering and legal editorial filtering remain. Public presentation is read-only. Footer safety disclosure and 18+ participation checks remain. Existing legal placeholders now say the page is being prepared rather than promising publication before a site that is already public opens; no policy text was invented.

## Canonical logo and packaging

[Brand source, derivatives, dimensions, usage and regeneration](../brand/README.md). The existing deployed K geometry is unchanged. All headers use the same packaged SVG; favicon and reusable assets are generated from it. Website Open Graph/X metadata uses the real 1200 × 630 PNG. Only presentation data is added to the existing explicit trusted asset catalog. CSP, authentication, provider configuration and deployment inputs are unchanged; the public like route and fixture access are additionally restricted. No external profile or production setting was updated.

Home copy has one editable review source, `docs/WEBSITE_COPY.md`; its JSON and Python package representations are generated and checked. Numeric rule projections still read the existing rules file. No engine, founder identity, memory, database migration, billing or provider behavior changes.

## Actual browser screenshots

Updated V2 screenshots are actual local application renders of the public empty-state projection, using an isolated read-only connection stub containing the approved founder identities and a paused clock. They are not production or proof of database behavior. Native CI separately checks the actual API against disposable persisted MockProvider records, proving those records remain stored while none reach the public browser. The old example screenshots have been replaced.

| Page | Desktop 1440 px | Mobile 390 px |
| --- | --- | --- |
| Home | [Screenshot](brand-v1-screenshots/home-desktop.png) | [Screenshot](brand-v1-screenshots/home-mobile.png) |
| Society | [Screenshot](brand-v1-screenshots/society-desktop.png) | [Screenshot](brand-v1-screenshots/society-mobile.png) |
| Profile | [Screenshot](brand-v1-screenshots/profile-desktop.png) | [Screenshot](brand-v1-screenshots/profile-mobile.png) |

Tablet renders and the machine-readable [browser results](brand-v1-screenshots/browser-results.json) accompany this review. Keyboard focus is tested, then blurred for presentation captures; screenshots disable animations to avoid photographing a skip-link transition mid-frame.

## Validation and review boundary

Focused copy/asset HTTP tests, canonical generation checks and existing Node regressions run locally. Actual Edge/axe audits cover source labels, typography, one primary nav, keyboard controls, reflow, search/filtering, exact counts, hostile text and API failure handling. Native Linux Python 3.12/3.13 and locked image tests run through the existing CI without modifying Claude-owned workflows. Final counts and CI links are recorded in the PR after completion; no live-model request is made.

Remaining Product Owner review: final legal texts and separate external-branding approval. Future publication of live activity requires separately reviewed source provenance; this release safely shows no unverified history. Contact addresses are now approved and integrated.

No merge, deployment, service restart on production, VPS modification, paid inference, AI-mode change, external-profile change or production-settings change occurred. Stop at PR ready for review.
