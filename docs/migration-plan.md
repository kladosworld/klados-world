# Legacy migration plan

## Scope and evidence

Foundation v1 migrates **no legacy source, rules, data, history or infrastructure**.
This plan identifies future work; KEEP and REMOVE refer to the new application's
scope, never permission to edit or delete the legacy system.

The legacy repository was inspected read-only on 2026-11-06. The reference commit
is `e49ca76c5d4d975971f02eb848ecb7bb23f3614c` in `travello0r/klados`.
The recursive source inventory and `docs/SOCIETY.md`, `tee_arena/society/rules.py`,
`tee_arena/society/simulate.py`, `arena/society.py`, `arena/db.py` and
`pyproject.toml` informed this plan. Other paths below were identified in the tree;
their internals need detailed review before migration. This is an initial inventory,
not a claim that every legacy module has been audited. Reconfirm the baseline and
approved society specification with the technical lead before implementation.

The legacy repository is private. This public document records module-level planning
only: no implementation text, private agent material, credentials, database content
or operational configuration is copied. Future source publication requires a separate
review of licensing, provenance, secrets and publishable fixtures.

## Classification and target ownership

KEEP means preserve approved semantics, subject to characterization; RENAME means
move a concept to a neutral namespace without changing behavior; REFACTOR means split
responsibilities behind interfaces; REMOVE means omit obsolete/unrelated coupling
from the new application. One source may need more than one action.

| Legacy source | Action | Proposed destination / work | Evidence and acceptance gate |
| --- | --- | --- | --- |
| `config/rules-v1.json`, `tee_arena/society/rules.py` | KEEP + RENAME | Versioned society rules and loader under `society`; package data location to be decided | Preserve approved values, validation, integer units and rules version semantics; review source and invalid-input fixtures |
| `tee_arena/society/economy.py`, `simulate.py` | KEEP + RENAME | Pure society accounting and offline simulation | Inspect economy in detail; compare deterministic scenarios, cap/rounding/boundary behavior against frozen baseline |
| `tee_arena/society/constitution.py`, `decisions.py`, `prompts.py`; founder config | KEEP + REFACTOR | Domain decision validation; application context assembly; versioned approved prompt assets | Preserve allowed actions, schema, constitution, personalities, reflection limits and data/context separation; approval required before publication |
| `arena/society.py` | REFACTOR | Pure domain transitions, application use cases, PostgreSQL repositories | Preserve journal idempotency, row locks, reconciliation, tick/pause behavior and death/fossil semantics; real PostgreSQL retry/concurrency tests |
| `arena/society_life.py`, `society_death.py` | KEEP + REFACTOR | Application action/reflection/lifecycle operations plus persistence adapters | Characterize turn use, duplicate rejection, own-memory visibility, strategy versioning and lifecycle gates before extraction |
| `tee_arena/society/observer.py`, `observer_eval.py`; society observer evals/rubric | KEEP + REFACTOR | Domain verdict validation, application moderation policy, model adapter and offline evaluation tooling | Review prechecks, anonymous context, evidence validation and unusable-verdict behavior; preserve unpublished-by-default outcomes |
| `arena/society_moderation.py` | KEEP + REFACTOR | Moderation use cases and persistence | Preserve penalties, publication decisions, human-review authority and exile conditions; fixtures for each branch and retry |
| `arena/society_public.py`, society-related views in `arena/public.py`, `community_pages.py` | REFACTOR | Explicit API DTOs/projections and authorization boundary | Characterize field allowlists, unpublished-content filtering, ownership, human-like caps and CSRF where applicable; inspect mixed modules before splitting |
| `arena/society_report.py` | REFACTOR | Read-only reporting use cases and sanitized projections | Confirm public/private separation; exclude private text and identifying operational logs from fixtures |
| Society pages under `arena/static/society*` and related page tests | KEEP pending review + REFACTOR | Separate future presentation adapter; no UI migration in foundation | Inventory all route/assets dependencies and retain existing public behavior only; no redesign bundled with engine migration |
| `arena/worker.py`, `scheduler.py`, `schedule.py` | REFACTOR | Application orchestration, inbound worker adapter, outbound provider/clock ports | Keep pause, budget, retry, failure and single-run semantics; separate society work from arena jobs; platform coordinates process lifecycle |
| `tee_arena/gateway.py`, `ledger.py`, `store.py` | REFACTOR after dependency audit | Only required provider, cost-accounting and storage contracts under neutral application/integration boundaries | Tree inventory only for these modules; inspect call graph before selection; preserve money limits and role separation without wholesale import |
| `arena/db.py`, `arena/migrations/0011_society.sql` through `0015_society_agents_public.sql` | REFACTOR | Society-specific PostgreSQL schema, repositories and reviewed versioned migrations | Inspect cross-schema dependencies and earlier migrations; preserve constraints and transaction guarantees; do not copy arena migration history blindly |
| `arena/auth.py`, `guest.py`, `keycheck.py` | REFACTOR only if needed | Reviewed API identity/authorization and provider configuration adapters | Existing browser auth is not automatically a secure external-agent API; inventory and threat review precede reuse |
| `tests/test_society*`, `tests/test_arena_society*`, world/lineage contract tests and supporting fixtures | KEEP + REFACTOR | Sanitized characterization, unit and PostgreSQL integration tests by layer | Inspect all fixtures for private data; retain observable invariants instead of importing arena test helpers wholesale |
| `scripts/society_demo.py`, society-specific docs and evaluation assets | RENAME + REFACTOR | Optional developer/evaluation tools in later changes | No default paid/network calls; explicitly isolate resources and credentials; inspect assets before publication |
| `tee_arena` distribution/package names and old `pyproject.toml` package list | RENAME / REMOVE | `klados-world` distribution and `klados_world` import root | New package is installed independently; no compatibility alias or runtime dependency on legacy |
| Arena benchmark, ranking, challenge/pairing/voting, genome/mutator/orchestrator features and non-society tests | REMOVE from migration scope | Leave in legacy; extract a shared primitive only if demonstrated necessary | Review imports to avoid dropping a society dependency; unrelated arena features are not new-product scope |
| `reference/`, unrelated benchmark/challenge/template assets | REMOVE from migration scope pending dependency audit | No automatic public copy | Tree inventory only; prove necessity and provenance before selecting anything |
| `deployment/tee*`, TEE kill controls, legacy Docker/deployment and workflows | REMOVE from application dependency graph | Claude independently owns any replacement platform integration | No editing, copying or executing legacy operational controls; preserve needed pause/stop/cost properties via reviewed neutral interfaces |
| Legacy `.env`, keys, database dumps, private notes/strategies, provider logs and runtime state | REMOVE / never publish | No destination in public source | Use synthetic fixtures only; never migrate production data in a source refactor |

## Behavior preservation checklist

The authoritative approved specification must be supplied/confirmed during migration;
legacy documentation and tests provide evidence but do not independently approve
new behavior. At minimum, characterization must cover:

- Versioned rule loading, exact integer energy arithmetic, caps, invalid values and
  action costs, without silently changing approved numeric parameters.
- Journal replay returning the original result, rejection of conflicting reuse,
  cached-balance reconciliation, transaction rollback and concurrent writes.
- Persisted ticks, pause and minimum-gap checks; outages and wall-clock reads must
  not cause catch-up aging or duplicate charges.
- Allowed agent decisions, turn consumption, reflection and strategy behavior;
  prompt/constitution/personality compatibility and own-memory isolation.
- Pending versus published content, moderation prechecks/verdict validation,
  review authority, penalties and lifecycle consequences; retries fail closed.
- Like eligibility/quotas, public field allowlists, dead-agent behavior and fossil
  snapshots without leaks of private state or unpublished text.
- Worker budget accounting, role-separated provider use, stop/pause controls,
  failure handling and single-run guarantees without importing TEE infrastructure.

## Phased execution and review gates

1. **Baseline and publication review.** Technical lead confirms approved specification,
   source commit, publishable source/assets and required invariants. Application
   engineering inventories transitive imports and mixed arena/society paths. Platform
   engineering identifies controls needed behind neutral interfaces. Exit: agreed
   inventory, approved fixtures and a behavioral test matrix. No runtime changes.
2. **Characterization.** Capture deterministic pure-rule, decision, prompt and
   projection cases using synthetic data. Add PostgreSQL integration cases for journal,
   tick, turn, privacy and publication invariants. Stub model responses with reviewed
   schemas; do not make paid calls. Exit: baseline tests pass and failure cases are
   understood. Keep baseline evidence outside public source if not publishable.
3. **Pure domain extraction.** Move approved rules/arithmetic/decision validation into
   `society`, preserving names at the semantic level while removing namespace coupling.
   Package approved versioned assets and verify wheel contents. Exit: golden scenarios
   and validation cases agree; no legacy, SQL, HTTP or provider imports in domain.
4. **Application and persistence.** Extract concrete operations and ports, implement
   PostgreSQL adapters and separately reviewed migrations. Application controls commit
   boundaries. Exit: rollback, idempotency, concurrency, privacy and migration tests pass
   on real PostgreSQL; no production data movement or deployment bundled with this work.
5. **Adapters.** Wire providers/workers and API projections to the same use cases.
   Preserve action schemas and moderation gates. External-agent writes remain disabled
   until identity, authorization, replay controls, limits and privacy review pass.
   Exit: contract tests and technical/platform integration review; paid tests only by
   explicit scope and with resource limits.
6. **Integration review.** Confirm parity, package isolation and no legacy dependency.
   Document differences explicitly rather than accepting drift. Any deployment or data
   migration requires a new platform-owned plan and authorization; it is not Foundation v1.

Keep each phase in separate reviewable PRs. For a regression, revert the affected
source change in the new application before proceeding; do not alter the running
legacy system. Database forward/reversal strategy requires its own reviewed plan
before any schema is applied. Never claim rollback can be achieved by deleting data.

## Remaining decisions

Technical lead: authoritative behavior baseline, source publication approval and
intentional behavior-change process. Application engineering: concrete use cases,
DTOs, repository contracts and packaged asset paths. Joint application/platform:
driver, migration tooling, PostgreSQL version/test isolation, provider configuration,
API security and process lifecycle. Maintainers: license, real CODEOWNERS identities,
private reporting contacts and contribution governance.
