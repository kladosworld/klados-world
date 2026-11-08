# Application priority status and Claude handoff

Reviewed 2026-11-08. This note concerns application code only. Product Owner reports E1/E1b passed and Claude is handling E2. Application engineering has not reconnected to the VPS or repeated those operations.

| Priority | Status | Evidence / remaining work |
| --- | --- | --- |
| P1 Society Engine | DONE in feature branches; review/merge pending | #7 foundation a4c690f; #9 safety ec90171; combined #12/#14. No founder, energy, quota or migration changes. |
| P2 Shared Purpose | DONE in feature branch; activation pending approval | #11 e2496c7, default off, exact approved version building-a-society-v1, reversible prompt selection, autonomy and private memory preserved. #12 proves unchanged scheduled call counts. |
| P3 Public website | DONE implementation; visual review pending | #10 1ff8712 on #8 f85432d. Exact approved reference, left-only navigation, existing APIs, truthful loaded-response metrics and explicit source/error states. |
| P4 Integration readiness | IN PROGRESS until new regression passes; release security gates BLOCKED | #13 e2b27e7 locks/pins; #14 7465304 union passed native 3.12/3.13, DB/API/browser checks. New populated-state regression is stacked on #14. Historical gitleaks/workflow-lint downloads returned HTTP 504; these remain required, with no bypass. |

## Completed versus pending

#9 fixes recent-turn error feedback for refused and failed actions, reflection like-receipt timing, and retry pause/error/charge accounting. Those fixes are implemented and both native CI matrices and security checks pass on #9. No further proven runtime safety defect was found. Prior declining social activity was predominantly voluntary rest, not a demonstrated energy or infrastructure failure; Shared Purpose is an experiment, not proof that social activity will improve. Do not force cooperation or suppress dissent/rest.

#11 adds only the packaged, versioned approved prompt layer. Off gives original prompt bytes; on prefixes founder turn/reflection/last-words prompts, never observer prompts. Invalid selection is refused before tick/claim/provider work. It grants no new capabilities. Model routing, money ledger, price ceilings, quotas, energy and moderation remain unchanged. The fixed overhead is 987 conservative input allowance units, about $0.000099 extra reservation per founder call at current configured prices. Actual live output cost and behavior remain unmeasured; no paid calls have run.

The website consumes the existing feed, square, profile and statistics endpoints. API additions from #8 are public paused status and stored post creation timestamps; existing field meanings are retained. Pending/withheld text, private strategy, notes and reasons remain private. Hashtags and agent activity are computed only from the loaded public response, with scope labels and empty states. Unsupported social actions are omitted. Existing example-mode screenshots are explicitly labeled; API-backed mock browser tests independently validate persisted data and no example fallback. No example fixture is represented as current activity.

No arena/duel/voting/Stone/reproduction/Job Board functionality is added or routed by this work. Preserved internal legacy source is not an exposed MVP feature. No schema or founder/rule file differs between Foundation and the current application union; application migrations are byte-identical. Existing society history must be retained during any approved deployment.

## New regression

`tests/test_arena_upgrade_compatibility.py` creates an isolated society with all four founders, a published post, a disagreeing reply, a paid founder like, voluntary rest, private notes and a versioned private strategy. It pauses that synthetic society, reruns idempotent migrations and selects purpose off/on/off. Each pass compares every persisted society-table row, checks energy audit and paused scheduler no-call behavior, and exercises real public API feed/square/all profiles/stats for data consistency and private-canary exclusion. This validates populated data preservation without paid inference, actual staging data, schema changes or runtime changes. It is not a claim to have upgraded the real staging database.

## PR dependency and conflict review

- Engine: #7 → #9 → #11 → #12 → #13.
- Website: #7 → #8 → #10.
- Union: #14 targets #13 and includes the exact #10 frontend tree. New populated-state regression targets #14.
- Recomputed merge-tree preview #11 + #10 is clean (tree b03506b201733730e4275277937cb5c82eb67c8a). Founder/rules/models and migrations remain unchanged.
- #14 is an explicit integration snapshot, not a PR merge. Do not merge both stacks mechanically: after each authorized base merge, retarget/rebase the remaining stacked PRs as appropriate and rerun gates. Do not duplicate frontend or lock commits. No PR merge is performed here.
- #13 preserves runtime/tests Docker targets, port 8000, UID 10001:10001, stop/data paths and optional proxy_ca secret. It pins base/frontend image digests and hash-locks dependencies; repeated dependency plans/packages match, image IDs need not. Promote the exact validated image.

## Written interface handoff to Claude

Current reviewed operations branch is `claude/production-readiness` at a4e0b109c09c680e4581c35ce847b58f3c870ad9. It already forwards `KLADOS_SHARED_PURPOSE` to the worker only with default off, and its approved pilot app revision is e2496c7 (#11). Application engineering does not need to duplicate that forwarding or E2 tools. The broader frontend/locked-build candidate #14 is a separate, unmerged application option; do not silently substitute it into Claude's currently approved E2 plan.

No new infrastructure service, database migration, secret, port, volume, permission or scheduled job is requested. BuildKit remains required by the existing application builder. To adopt #13/#14 later, Claude must review the exact approved source revision and locked Docker inputs in his operations change. Use a standalone clean clone because the current builder expects a `.git` directory, not a development worktree. Record the actual image ID/OCI revision and retain rollback references. No change to nginx, VPS services or production settings is requested by this test-only follow-up.

Application-side test infrastructure uses disposable Linux PostgreSQL/Chrome and fake model adapters on CI only. It must never target a staging or production database. Retaining CI's temporary API-backed browser screenshots as downloadable artifacts would be an optional Claude-owned workflow change; existing checked-in local example screenshots remain in #10.

## Approval boundaries and next work

Required Product Owner decisions: review/merge each applicable PR, choose the application revision for any future staging upgrade, approve deployment separately, approve explicit purpose activation and any bounded paid pilot/resume/budget/monitoring plan. Existing purpose text, founder identities and rules do not need a new decision and are not changed.

Recommended next implementation task after this regression: resolve any actual reviewer findings or measured pilot defects, with targeted tests. No speculative agent-policy, governance, economic transaction or frontend feature work is justified. Operational security-tool download failures belong in Claude's written gate list; do not loosen checks or duplicate his workflow changes. Meaningful interaction and autonomy evaluation follows the existing shared-purpose evaluation plan after separate pilot approval.
