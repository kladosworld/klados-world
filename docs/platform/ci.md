# Continuous integration

## Workflows

| Workflow | File | Runs on | Check name(s) | Required |
| -------- | ---- | ------- | ------------- | -------- |
| CI | `ci.yml` | PR, push to `main`, merge queue | `lint`, `typecheck`, `test (py3.12)`, `test (py3.13)`, **`ci-required`** | `ci-required` |
| Secret scan | `secret-scan.yml` | PR, push, merge queue, weekly | **`gitleaks`** | Yes |
| Workflow lint | `workflow-lint.yml` | PR, push, merge queue | **`workflow-lint`** | Yes |
| Dependency review | `dependency-review.yml` | PR, merge queue | **`dependency-review`** | Yes |
| CodeQL | `codeql.yml` | PR, push, merge queue, weekly | `codeql (actions)`, `codeql (python)` | Through the ruleset's code scanning rule |
| Scorecard | `scorecard.yml` | push to `main`, weekly | `scorecard` | No (informational) |

`ci-required` combines the results of every CI job, so its name stays the same
when jobs or the test matrix change. It fails if any job fails or is cancelled.

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

> **Status: assumption, not yet validated.** When this was written, application
> engineering had not yet published a branch with `pyproject.toml`. The CI jobs
> are skipped, with a warning, until `pyproject.toml` exists. Once it lands, every
> failure blocks merging. The contract below must be confirmed against the real
> `pyproject.toml`. Platform will then run CI on the application branch and update
> this section.

CI runs exactly these commands. Their configuration lives in `pyproject.toml`.

| Step | Command | Expected configuration |
| ---- | ------- | ---------------------- |
| Install | `.github/scripts/install-python-deps.sh` | The project installs from the repository root. Dev tools are declared as a PEP 735 group `[dependency-groups] dev` (preferred) or as the extra `[project.optional-dependencies] dev`. |
| Lint | `python -m ruff check .` | `[tool.ruff]` / `[tool.ruff.lint]` |
| Format | `python -m ruff format --check .` | `[tool.ruff.format]` |
| Types | `python -m mypy` | `[tool.mypy]` with `files = [...]` or `packages = [...]`. Without targets, mypy exits with an error. |
| Tests | `python -m pytest` | `[tool.pytest.ini_options]` (testpaths, addopts, coverage thresholds if used) |

Other assumptions:

- **Python 3.12 and 3.13** are both tested, matching the legacy project's
  `requires-python = ">=3.12"`. If `requires-python` changes, update the test
  matrix in `ci.yml`.
- `ruff`, `mypy` and `pytest` (plus any plugins, such as `pytest-cov`) are listed
  in the `dev` group with pinned or bounded versions.
- Tests run **without network credentials or model API keys**. Tests that need
  a database or an external service must skip themselves, or be marked and run in
  a separate job that is added later.
- If the project adopts `uv` with a lockfile, platform will switch the install
  step to `uv sync --locked`. It will also change the Dependabot pip entry to
  `package-ecosystem: uv`.

To change the contract, for example to replace mypy with pyright, application
engineering and platform agree on it in a pull request that updates this document
and `ci.yml` together.

## Running the same checks locally

```bash
python -m pip install -e . --group dev
```

```bash
python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest
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
