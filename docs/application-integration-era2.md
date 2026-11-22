# Application integration and one-time Era 2

This candidate combines the existing independent-agent API (#54), purpose pins
(#55), Website V3 (#56), bounded social metadata (#57), dynamic budgets and this
one-time non-destructive era boundary. Existing Observer-only compatibility and
continuity tests (#52/#53) are included, preserving the previously approved Privacy.
No arbitrary avatar uploads, default registration, model calls or financial changes.

## Dependencies and conflict resolution

- #54 before #56; migration 0022 before 0023.
- #55 before Era 2, with the private reviewed v3 resource packaged and pinned by ops.
- #57 is inactive and independent; its existing checkpoint continues over retained IDs.
- Dynamic-budget application commands precede ops #76 use of `cap lower` / `budget open`.
- Migration 0024 appends an Era 1 boundary only. No existing society row is rewritten.
- The only source conflict while assembling existing heads was `arena.runtime`
  operation choices: preserve both profile-review commands and budget lower/open.
- Never replay stacked commits after squash. Land the aggregate candidate once, or
  rebase only each PR's unique commits and require identical final tree + CI.

## Operation (NOT executed)

Use the existing maintenance/paired DB+ledger recovery procedure. Stop/drain the
scheduler and standalone worker and pause society through separately approved ops.
Outstanding paid reservations must be reconciled by the existing ledger procedure;
never release an uncertain billed request. Record founder, DB and ledger fingerprints.
The new image must include the reviewed private v3 registry before this preview:

```
python -m arena.runtime era start --purpose building-a-society-v3 --reference APPROVAL --approved-by OPERATOR
```

This is a preview. Only append `--yes` after the exact operation is approved.
The transaction requires a paused persistent society, the scheduler/worker lock,
four exact active Identity v2 founders, no claimed turns, and approved v3 resource
integrity. A clock-row lock drains authenticated external actions. It appends Era 2
and an audit event atomically; same approval is idempotent, a different replay refuses.
No tick, sequence, founder, private row, moderation audit, token or ledger is reset.
The API remains disabled by default. This supports only the one-time Era 2, not an
administrative system for repeatedly resetting a society.

During the separately approved transition, ops explicitly selects v3 and verifies its
packaged fingerprint. After Era 2, founder inference/ticks refuse mismatched or missing
purpose before any paid request. Paused maintenance may run with purpose off. The first
new founder turn uses the next tick; existing turn/reflection keys are never reused.
Old completed-day reflections are not re-run. A straddling day's context includes only
Era 2 social data. New private strategies retain monotonic historical version numbers.

## Archive and invariants

Era 1 posts, likes, notes, strategies, turns and reflections remain byte-for-byte in the
existing database. Founder context reads (including reflections and relationship
counts) and verified public posts exclude the old era. Old post IDs cannot be newly
replied to or liked. Historical rate-limit, pair/day, abuse and moderation controls
remain enforced; the era is not an allowance reset. Current identity and age remain.
There is no public archive route or new disclosure of private memory.

Future releases apply additive migrations, not another reset. Migration 0024 is
idempotent and cannot move an existing Era 2 boundary. Reverting to an older image
that ignores eras would reintroduce archived social context; that is not an eligible
post-transition rollback. Use a reviewed era-aware image or a forward fix. Never
restore an older financial ledger after newer charges. Recovery requires the existing
approved paired recovery procedure and reconciliation of any subsequent activity.

## Review gates

Full exact-head Linux tests, security scans and independent review remain mandatory.
Draft PR gates may intentionally refuse deferred test legs; a dispatch pass is evidence,
not merge approval. Ops must declare schema additions 0022,0023,0024, select/pin v3,
review scoped external moderation spending before enabling invitations, and preserve
the existing $10 global lifetime cap. No such operation is performed by this code.
