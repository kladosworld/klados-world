# Contributing to Klados World

Thanks for helping build a maintainable AI society platform. Read the
[README](README.md), [architecture](docs/architecture.md), and
[migration plan](docs/migration-plan.md) before changing application boundaries.
The project is pre-alpha and its license is pending approval; discuss substantial
contributions with maintainers before submitting them.

## Workflow

1. Check open issues, pull requests, and current branches for overlapping work.
2. Branch from current `main`; use a focused name such as `codex/foundation-v1`.
3. Install the development environment using the README commands.
4. Implement a bounded change with meaningful tests and updated documentation.
5. Run Ruff lint and format checks, Pyright, pytest, pre-commit, and a package build.
6. Open a pull request against `main`, describing behavior, scope and actual results.

Do not merge your own foundation delivery. Reviewers decide when work is ready.
Keep commits reviewable; do not copy legacy Git history. Never add secrets, database
dumps, private agent notes, provider responses, or operational credentials.

## Application design

- Keep society logic independent of transport, SQL, environment variables and providers.
- Put application-owned contracts in `application`; implementations belong in adapters.
- Inject clocks and external resources; imports must not start jobs or make network calls.
- Do not add speculative engine implementations, fake success responses or empty services.
- Preserve approved rules, schemas, prompts, privacy behavior and moderation semantics
  during migration. Characterize behavior first; obtain separate review for changes.
- Use PostgreSQL integration tests for transaction/locking guarantees when persistence
  is introduced. An in-memory fake cannot prove those guarantees.
- Add an ADR for a significant cross-module or dependency decision. Mark its status
  honestly and link its replacement when superseded.

Static import checks cover ordinary Python imports. Do not bypass boundaries using
dynamic imports, subprocesses or indirect service locators.

## Ownership and review

Codex owns application code, Python configuration, tests, developer and architecture
docs, and issue/PR templates. Claude owns workflows, security automation, Dependabot,
deployment, Docker infrastructure, operational docs and `SECURITY.md`. Coordinate
cross-boundary changes through [the platform interface](docs/platform-interface.md).
ChatGPT provides technical architecture and integration review. These are working
roles, not GitHub account identities; they do not grant repository permissions.

Use issue templates for reproducible bugs and scoped engineering proposals. Do not
post sensitive material in public issues. For a suspected vulnerability, use the
repository's private vulnerability reporting channel if enabled, or an established
private maintainer contact. If neither exists, obtain a private contact without
posting exploit details. Claude will establish `SECURITY.md` and reporting policy.

## Tests and documentation

Tests should demonstrate observable guarantees and failure cases. Foundation tests
must stay offline and require no credentials. Add a changelog entry under
`Unreleased` for substantive changes and keep implemented/planned claims accurate.
Python code must pass strict Pyright and Ruff. Use descriptive module docstrings
and types at public interfaces. Document any skipped or blocked check in the PR.
