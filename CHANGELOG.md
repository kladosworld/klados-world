# Changelog

Substantive application changes are recorded here. No public release has been made.

## Unreleased

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
