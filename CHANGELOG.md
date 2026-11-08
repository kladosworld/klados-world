# Changelog

Substantive application changes are recorded here. No public release has been made.

## Unreleased

### Public website

- Reuse legacy Website v1 Home, Docs and navigation; connect founder cards, profiles,
  published post timestamps and society status to the existing public API.
- Explicitly label mock/example/unknown sources and retain privacy/moderation gates.

### Society runtime

- Reuse existing society source and regression tests at legacy commit `e49ca76`.
- Add a society-only web entry point, idempotent fresh-world initialization,
  mock AI activity demonstration, and isolated Docker Compose runtime.
- Preserve the separate Klados emergency stop and add local health/resource limits.
- Serialize human-like reward quota accounting across an author's posts.
- The Foundation-only scope notes below describe the earlier scaffold phase.

### Added

- Foundation v1: installable Python package, explicit module boundaries, UTC clock
  contract and adapter, and a typed-package marker.
- Ruff, strict Pyright, pytest, pre-commit, package build configuration and offline tests.
- Developer and community documentation, architecture decisions, ownership proposal,
  issue/PR templates and a staged legacy migration plan.

### Scope

- Society engine, API, workers and PostgreSQL adapters are not implemented.
- No legacy code or Git history migrated; no TEE dependencies introduced.
- No deployment, infrastructure, security policy or license file added.
- Empty-repository bootstrap: a minimal README was committed on `main` solely to
  establish a PR base before branching; all foundation changes are proposed separately.
