# Architecture decision records

ADRs record a decision, its context, alternatives and consequences. They are not
product specifications. Number records sequentially as `NNNN-short-title.md`.
Use [the template](template.md); link decisions from implementation PRs.

Statuses: **Proposed**, **Accepted**, **Superseded** (link replacement), or
**Rejected**. Foundation records below are proposed for technical-lead review;
this branch implements their approach without claiming final approval. An accepted
record is not silently rewritten to reverse a decision: add a superseding record.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-python-package-boundaries.md) | Python `src` package and inward dependencies | Proposed |
| [0002](0002-postgresql-and-transactions.md) | PostgreSQL and application-owned transactions | Proposed |
| [0003](0003-behavior-preserving-migration.md) | Characterize behavior before removing legacy coupling | Proposed |
