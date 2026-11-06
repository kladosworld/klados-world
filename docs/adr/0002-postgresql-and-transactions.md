# 0002: PostgreSQL persistence and transaction ownership

- Status: Proposed
- Date: 2026-11-06
- Review: technical lead; application and platform engineering

## Context

Production persistence is constrained to PostgreSQL. Society consistency involves
idempotent changes, journal reconciliation, concurrency and pause-safe time. A
structural refactor must not weaken these guarantees.

## Decision

Keep production persistence in PostgreSQL adapters behind application-owned ports.
Application use cases own the logical transaction; adapter helpers must not commit
partial operations. Domain objects must not expose SQL or database-driver types.
Design concrete repository/unit-of-work contracts during behavior characterization.
Foundation adds only the package boundary, with no schema, driver, ORM or migration
runner. Later implementation must verify locking, uniqueness, retries, rollback
and migration serialization against real PostgreSQL.

## Alternatives

- SQLite as production or concurrency-test substitute: does not establish PostgreSQL guarantees.
- SQL in domain transitions: couples policy to driver and transaction details.
- A generic repository framework now: speculative before migration operations are known.

## Consequences

The production target is explicit without premature schema design. Platform and
application roles must agree on PostgreSQL versions and isolated test services.
Selecting a driver, pooling and migration tooling remains follow-up work. No
persistence capability or deployment readiness is claimed by this scaffold.
