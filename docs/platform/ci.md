# Continuous integration

## Workflows

| Workflow | File | Runs on | Check name(s) | Required |
| -------- | ---- | ------- | ------------- | -------- |
| CI | `ci.yml` | PR, push to `main`, merge queue | `lint`, `typecheck`, `test (py3.12)`, `test (py3.13)`, `package`, **`ci-required`** | `ci-required` |
| Secret scan | `secret-scan.yml` | PR, push, merge queue, weekly | **`gitleaks`** | Yes |
| Workflow lint | `workflow-lint.yml` | PR, push, merge queue | **`workflow-lint`** | Yes |
| Dependency review | `dependency-review.yml` | PR, merge queue | **`dependency-review`** | Yes |
| CodeQL | `codeql.yml` | PR, push, merge queue, weekly | `codeql (actions)`, `codeql (python)` | Through the ruleset's code scanning rule |
| Scorecard | `scorecard.yml` | push to `main`, weekly | `scorecard` | No (informational) |

`ci-required` combines the results of every CI job, so its name stays the same
when jobs or the test matrix change.

### Fail-closed Python gating

The Python jobs (`lint`, `typecheck`, `test`, `package`) **cannot be skipped
silently**:

| Situation | `detect` | Python jobs | `ci-required` |
| --------- | -------- | ----------- | ------------- |
| `pyproject.toml` is present in the tested revision | `python=true` | must all be `success` | fails if any of them is skipped, cancelled or failed |
| `pyproject.toml` is absent here but present on the base or parent revision (removed or renamed) | **fails** | not run | fails |
| The parent revision cannot be resolved | **fails** | not run | fails |
| Absent here **and** on the base (bootstrap only, before the application lands) | `python=false`, with a warning | must be `skipped` | passes |

`detect` (`.github/scripts/ci-detect.sh`) checks the tested revision and its
first parent (`fetch-depth: 2`). For pull requests, GitHub tests the merge ref,
whose first parent is the base branch tip. For merge-queue commits the first
parent is also the base. For a push to `main` (squash merges only), it is the
previous `main` commit. The gate (`.github/scripts/ci-gate.py`) requires
`success` whenever Python CI is required, and `skipped` only in the bootstrap
state. `.github/scripts/test-ci-gating.sh` covers 15 scenarios and runs in the
`workflow-lint` job.

Once `pyproject.toml` reaches `main`, the bootstrap row can never apply to a pull
request against `main` again.

## Workflow security baseline

Every workflow follows these rules. `workflow-lint` checks them with actionlint and
zizmor.

- **No default permissions.** Each workflow sets `permissions: {}` at the top, and
  each job grants only the scopes it needs, usually `contents: read`. Only CodeQL
  and Scorecard are granted `security-events: write`. Only Scorecard is granted
  `id-token: write`, which it needs to publish its results.
- **Actions pinned to a full commit SHA**, with the version in a comment.
  Dependabot updates both.
- **Downloaded tools are integrity-checked.** The gitleaks and actionlint
  binaries are checked against SHA-256 values. zizmor is installed with
  `pip --require-hashes`.
- **`persist-credentials: false`** on every checkout, so the token is not left in
  `.git/config`.
- **No `pull_request_target`, and no secrets in pull request jobs.** Workflows
  that run on forks get a read-only token and no secrets.
- **Untrusted input is never interpolated into scripts.** Values from events go
  through `env:`, never directly into `${{ }}` inside `run:`.
- **Timeouts and concurrency groups** on every job. Superseded pull request runs
  are cancelled.

## CI contract with `pyproject.toml`

> **Status: validated** against application engineering's branch `codex/foundation-v1`
> (commit `92e9096`) and its interface document `docs/platform-interface.md`. Every
> CI step below was replayed locally on that branch merged with this one, under
> Python 3.12 and 3.13: ruff, ruff format, pyright (0 errors), pytest (12 passed),
> the build, and a clean install of the wheel all pass. The jobs are skipped, with a
> warning, only while `pyproject.toml` does not exist.

CI runs exactly the commands that application engineering publishes in
`docs/platform-interface.md`. Their configuration lives in `pyproject.toml`.

| Step | Command | Configuration source |
| ---- | ------- | -------------------- |
| Install | `.github/scripts/install-python-deps.sh`, which runs `python -m venv .venv` and then `.venv/bin/python -m pip install -e '.[dev]'`. A PEP 735 `dev` group is also accepted | `[project.optional-dependencies] dev` |
| Lint | `.venv/bin/ruff check .` | `[tool.ruff]`, `[tool.ruff.lint]` |
| Format | `.venv/bin/ruff format --check .` | `[tool.ruff]` |
| Types | `.venv/bin/pyright` | `[tool.pyright]` (strict mode, which expects `venv = ".venv"`) |
| Tests | `.venv/bin/python -m pytest` | `[tool.pytest.ini_options]` |
| Package | `.venv/bin/python -m build`, then installs the wheel into a clean venv and imports `klados_world` | `[build-system]`, `[tool.hatch.*]` |

Notes:

- **Python 3.12 and 3.13** are tested. `requires-python = ">=3.12"`, and the
  classifiers list 3.12. Testing 3.13 as well catches forward-compatibility
  problems early. If application engineering prefers 3.12 only, change the
  matrix in `ci.yml`.
- `pre-commit run --all-files` is **not** run in CI. Its local hooks call the
  same ruff, pyright and pytest commands, so running it would only repeat them.
- `pyright` (the PyPI wheel) bundles the type checker and runs on the Node.js
  already installed on GitHub runners. It downloads nothing at run time.
- Direct dev dependencies are pinned exactly in `pyproject.toml`, but transitive
  dependencies are not locked. A lock file (for example `uv.lock`, or
  `requirements-dev.txt` with hashes) is an open decision for application
  engineering (`docs/platform-interface.md`, decision 2). If `uv` is adopted,
  platform will switch the install step to `uv sync --locked` and the Dependabot
  pip entry to `package-ecosystem: uv`.
- Tests run **without network credentials or model API keys**. When PostgreSQL
  integration tests arrive, platform will add a job with a `postgres` service
  container, pinned to the version used in deployment.

To change the contract, application engineering and platform agree on it in a
pull request that updates `docs/platform-interface.md`, this document and
`ci.yml` together.

## Running the same checks locally

```bash
python -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
```

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pyright && .venv/bin/python -m pytest
```

Workflow and secret checks:

```bash
actionlint
```

```bash
zizmor --offline .github/workflows
```

```bash
gitleaks git --config .gitleaks.toml --redact .
```
