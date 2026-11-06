# CODEOWNERS proposal — inactive pending maintainer mapping

No real GitHub account or team has been supplied for the engineering roles. The
repository organization is not itself a CODEOWNERS reviewer, and AI role names are
not GitHub identities. Therefore this proposal does not invent handles or activate
review routing.

| Pattern | Proposed review responsibility |
| --- | --- |
| `/src/`, `/tests/`, `/pyproject.toml`, `/.pre-commit-config.yaml` | Application engineering |
| `/.gitignore`, `/.editorconfig`, root developer/community docs | Application engineering |
| `/docs/architecture.md`, `/docs/adr/`, `/docs/migration-plan.md` | Application engineering and technical lead |
| `/.github/ISSUE_TEMPLATE/`, `/.github/pull_request_template.md` | Application engineering |
| `/.github/workflows/`, `/.github/dependabot.yml`, `/SECURITY.md` | Platform/security engineering |
| Docker, deployment and `/docs/operations/` paths | Platform engineering |
| `/docs/platform-interface.md`, `/.github/CODEOWNERS` | Application and platform engineering |

Maintainers must map these roles to existing users/teams with repository write
access, verify notification availability, then activate actual owner lines in
`.github/CODEOWNERS`. Use specific paths after broader defaults: GitHub uses the
last matching pattern, not additive matches. Put all intended owners of one path
on the same line. Requiring owner approval is a separate branch-protection decision.
