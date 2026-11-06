# Platform engineering

This directory documents the CI/CD, security scanning and repository configuration
of `klados-world`. Application architecture and developer documentation are kept
separately by application engineering.

| Document | Contents |
| -------- | -------- |
| [ci.md](ci.md) | Workflows, what each one checks, and the CI contract with `pyproject.toml` |
| [github-settings.md](github-settings.md) | Repository and organisation security settings that an owner must apply |
| [rulesets.md](rulesets.md) | Branch protection ruleset, required status checks and review policy |
| [../../SECURITY.md](../../SECURITY.md) | Vulnerability disclosure policy |

## Ownership

| Area | Owner |
| ---- | ----- |
| `.github/workflows/`, `.github/dependabot.yml`, `.github/scripts/`, `.github/tools/`, `.github/rulesets/`, `.gitleaks.toml`, `SECURITY.md`, `docs/platform/` | Platform and security (Claude) |
| `pyproject.toml`, `src/`, `tests/`, `.pre-commit-config.yaml`, `.gitignore`, `.editorconfig`, community and developer docs, application docs in `docs/`, `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` | Application engineering (Codex) |
| `.github/CODEOWNERS`, `docs/platform-interface.md` | Shared: changes need both roles to review |
| Architecture and integration review | Technical lead (ChatGPT) |
| Merges, repository settings, secrets | Repository owner (a person) |

Ownership follows `docs/platform-interface.md`, which application engineering
maintains. Do not edit files in another area without coordinating with its owner
first. CI reads tool settings from `pyproject.toml` and never overrides them.

`.github/ISSUE_TEMPLATE/config.yml` is in application engineering's directory.
Platform adds it only to send security reports to private reporting. Application
engineering may move it or merge it into their templates.

## Public versus internal

This repository is public. Hostnames, server layout, deployment procedures,
runbooks and anything else about how production runs belong in the private
`klados-internal` repository. Never commit any of these here:

- server IP addresses or hostnames that are not public;
- `.env` files, credentials or tokens;
- backups, database dumps or participant data;
- monitoring endpoints or alert routing details.
