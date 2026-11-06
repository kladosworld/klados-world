# 0003: Preserve society behavior while removing legacy coupling

- Status: Proposed
- Date: 2026-11-06
- Review: technical lead; application engineering; platform review for infrastructure boundaries

## Context

The existing society has approved rules and agent behavior. The new public project
needs maintainable modules without unnecessary TEE dependencies. Moving code and
changing behavior simultaneously would make regressions difficult to identify.

## Decision

Do not migrate legacy code, data or Git history in Foundation v1. Inspect legacy
read-only and record a staged KEEP/RENAME/REFACTOR/REMOVE plan. Before later migration,
freeze an approved source baseline and create sanitized characterization tests for
rules, decisions, privacy, accounting and publication behavior. Separate intentional
behavior changes from structural refactors and obtain explicit review. Preserve
required cost/pause controls through neutral interfaces; remove only coupling and
unrelated features, not safety properties. External-agent exposure requires a
separate secure API design review.

## Alternatives

- Wholesale copy: moves obsolete features and dependencies, obscuring responsibility.
- Rewrite rules from memory: risks unreviewed semantic and agent-behavior drift.
- Keep legacy imports behind aliases: leaves the new application dependent on legacy layout.

## Consequences

Foundation stays small and honest. Later work takes characterization effort and
requires source publication review: the legacy repository is private. No private
operational material or agent state belongs in this public repository. Source paths
in the migration inventory are planning references, not copied implementations.

See [migration plan](../migration-plan.md). No legacy system is modified or removed.
