# Application / platform handoff

This document proposes the working interface with Claude. It records boundaries;
it does not claim Claude has reviewed or accepted this branch. No direct message
or assignment to a GitHub identity is implied.

## File ownership

| Owner role | Paths / responsibilities |
| --- | --- |
| Codex — application engineering | `src/`, `tests/`, `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore`, `.editorconfig`, `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, application docs under `docs/`, `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` |
| Claude — platform engineering | `.github/workflows/`, `.github/dependabot.yml`, security automation, `SECURITY.md`, Docker files, deployment configuration, operational docs such as `docs/operations/` |
| ChatGPT — technical lead | Architecture coordination and integration review across these boundaries |

CODEOWNERS is a joint maintainer decision; see [the proposal](codeowners-proposal.md).
Application documentation must not replace operational runbooks. New platform files
must be coordinated by path before either role edits overlapping content. Changes
to a shared interface require review from both responsible roles.

## Interface supplied to platform engineering

- Distribution: `klados-world`; import root: `klados_world`; Python baseline: 3.12.
- Install: `python -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev]'`.
- Gates: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
  `.venv/bin/pyright`, `.venv/bin/python -m pytest`,
  `.venv/bin/pre-commit run --all-files`, `.venv/bin/python -m build`.
- Build output: wheel and source distribution under `dist/`.
- Tool versions live in `pyproject.toml`; local hooks reuse that environment to avoid
  a second version set. Hooks currently use POSIX `.venv/bin` paths.
- Foundation tests are offline and credential-free. No database service is needed.
- Runtime dependencies: none. No port, process command, health check, migration
  command, required environment variable or deployment artifact is defined yet.

Claude may wire these commands into CI in a separate platform change. This branch
does not add or modify workflows, Dependabot, Docker, deployment, security policy,
production configuration, VPS resources or operational documentation.

## Decisions needed before integration

1. Confirm the path ownership and real GitHub reviewers; enable branch protection
   and required checks through the appropriate platform review.
2. Review dependency locking and update policy, interpreter matrix and package
   artifact checks. Exact direct pins do not lock transitive dependencies.
3. Define PostgreSQL service versions and isolated integration-test setup when
   persistence arrives; do not substitute SQLite for concurrency tests.
4. Review process startup, shutdown, readiness, secrets loading and telemetry once
   runnable adapters exist. Do not deploy this scaffold as a functioning society.
5. Establish private security and conduct reporting contacts. License approval is
   a project-owner decision; no license file is supplied here.

## Isolated society runtime handoff to Claude

The execution task authorizes `deployment/local/` for this application/runtime PR.
Use its Compose contract: PostgreSQL 16; web port 8000 internally, loopback 8080
externally; user 10001; `/data` for the JSON spending ledger/reports; separate
project-owned `/etc/klados-control/KILLED`; web gets no provider keys or ledger.
Worker public flag remains off, web live projections on. Initialization is
idempotent and starts paused. Mock calls are the default; no key is needed.

No VPS inspection or `klados-audit` scripts are included or installed. No existing
host, TEE, Gen0, nginx, deployment, workflow or private repository is changed.
Existing workflow invocations remain supported. The optional Compose test runner
requires its own build and uses the disposable database only. Operational/platform
follow-up should review this concrete runtime rather than recreate it.
