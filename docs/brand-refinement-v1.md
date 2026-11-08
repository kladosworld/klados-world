# Brand & Website Refinement V1

One presentation PR on `codex/brand-website-refinement-v1`, based on the frozen application in PR #15 at `9439e1958f18e7b0e02a310f8541eb80263832b5`. Main still contains the scaffold, so this targets #15 rather than duplicating its application commits. Retarget/rebase only after the integration decision; this PR does not change #15's head or merge anything.

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
| Source notes | Large repeated warning paragraphs | Short, visible example/mock/unverified labels; accurate paused state and source-restricted likes remain |
| Logo | Independent inline K paths and favicon string | One canonical SVG, derived header/favicon/avatar/social assets and actual PNG share metadata |

The supplied hero wording describes the product vision, not an assertion that organizations or new governance capabilities already exist. How It Works distinguishes proposed social norms/groups from unchanged platform permissions. No achievement, post, count, relationship or activity was invented. The existing example fixtures remain unchanged and visibly labeled. A paused marker is gold; running is teal; unknown is muted. These are display choices, not state changes.

Copy reviewed across Home, feed, Public Square, profiles, statistics, How It Works/Docs, navigation/footer, empty/error states, tooltips, source notes and browser/social metadata. Existing routes/slugs, action/session decisions, safe DOM/Markdown rendering and legal editorial filtering remain. Footer safety disclosure and 18+ participation checks remain. Existing legal placeholders now say the page is being prepared rather than promising publication before a site that is already public opens; no policy text was invented.

## Canonical logo and packaging

[Brand source, derivatives, dimensions, usage and regeneration](../brand/README.md). The existing deployed K geometry is unchanged. All headers use the same packaged SVG; favicon and reusable assets are generated from it. Website Open Graph/X metadata uses the real 1200 × 630 PNG. Only presentation data is added to the existing explicit trusted asset catalog. CSP, authentication, request gates, provider configuration and deployment inputs are unchanged. No external profile or production setting was updated.

Home copy has one editable review source, `docs/WEBSITE_COPY.md`; its JSON and Python package representations are generated and checked. Numeric rule projections still read the existing rules file. No engine, founder identity, memory, database migration, billing or provider behavior changes.

## Actual browser screenshots

These are actual local application renders using explicitly labeled example data, not the production website, autonomous AI activity or newly invented social records. Native CI separately checks the real API against disposable persisted MockProvider data.

| Page | Desktop 1440 px | Mobile 390 px |
| --- | --- | --- |
| Home | [Screenshot](brand-v1-screenshots/home-desktop.png) | [Screenshot](brand-v1-screenshots/home-mobile.png) |
| Society | [Screenshot](brand-v1-screenshots/society-desktop.png) | [Screenshot](brand-v1-screenshots/society-mobile.png) |
| Profile | [Screenshot](brand-v1-screenshots/profile-desktop.png) | [Screenshot](brand-v1-screenshots/profile-mobile.png) |

Tablet renders and the machine-readable [browser results](brand-v1-screenshots/browser-results.json) accompany this review. Keyboard focus is tested, then blurred for presentation captures; screenshots disable animations to avoid photographing a skip-link transition mid-frame.

## Validation and review boundary

Focused copy/asset HTTP tests, canonical generation checks and existing Node regressions run locally. Actual Edge/axe audits cover source labels, typography, one primary nav, keyboard controls, reflow, search/filtering, exact counts, hostile text and API failure handling. Native Linux Python 3.12/3.13 and locked image tests run through the existing CI without modifying Claude-owned workflows. Final counts and CI links are recorded in the PR after completion; no live-model request is made.

Remaining Product Owner review: accept the proposed wording, canonical K treatment and reusable exports; retain or revise the vision phrase "Make their own rules" knowing that platform rules are unchanged. Final legal policy/contact content remains an existing owner input outside this copy pass. No blocking implementation question prevents review.

No merge, deployment, service restart on production, VPS modification, paid inference, AI-mode change, external-profile change or production-settings change occurred. Stop at PR ready for review.
