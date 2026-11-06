# GitHub security and repository settings

These settings cannot be changed by a pull request. A repository or organisation
owner must apply them. Tick each item when it is done, and record who applied it
and when in the pull request that follows.

## Organisation (`kladosworld`)

- [ ] **Require two-factor authentication** for all members: *Settings → Authentication security*.
- [ ] **Base permissions: Read** (or *No permission*); grant write through teams only: *Settings → Member privileges*.
- [ ] Only owners can create repositories, and members **cannot change repository visibility** or delete or transfer repositories.
- [ ] **Actions policy**: *Settings → Actions → General*:
  - allow only actions from GitHub, from verified creators, and from this allow-list:
    `github/codeql-action@*`, `actions/checkout@*`, `actions/setup-python@*`,
    `actions/dependency-review-action@*`, `actions/upload-artifact@*`, `ossf/scorecard-action@*`;
  - turn on **Require actions to be pinned to a full-length commit SHA**;
  - set **Workflow permissions: Read repository contents** (the default `GITHUB_TOKEN` is read-only);
  - turn off **Allow GitHub Actions to create and approve pull requests**;
  - set fork pull request workflows to **Require approval for all external contributors**.
- [ ] Review installed **GitHub Apps** (Claude, Codex and others) and limit each one to the repositories it needs.
- [ ] Create teams, for example `platform`, `application` and `maintainers`, with real members who have write access. Then activate `.github/CODEOWNERS` following `docs/codeowners-proposal.md`. This is a joint decision between application and platform; until then, CODEOWNERS stays an inactive placeholder.

## Repository `klados-world`: *Settings → General*

- [ ] Default branch: `main`.
- [ ] Pull requests: allow **squash merging** only, turn on **Always suggest updating pull request branches**, and turn on **Automatically delete head branches**.
- [ ] Turn off the **Wiki** unless it is needed. Turn on **Discussions** only if they will be moderated.

## Repository: *Settings → Code security*

- [ ] **Private vulnerability reporting**: on. `SECURITY.md` relies on this setting.
- [ ] **Dependency graph**: on. This is the default for public repositories, and dependency review needs it.
- [ ] **Dependabot alerts**: on.
- [ ] **Dependabot security updates**: on.
- [ ] **Grouped security updates**: on.
- [ ] **Secret scanning**: on. This is free for public repositories.
- [ ] **Push protection**: on, so pushes that contain detected secrets are blocked.
- [ ] **Non-provider patterns** and **validity checks**: on, where available.
- [ ] **Code scanning → CodeQL**: keep **Default setup turned OFF**. This repository uses the advanced setup in `.github/workflows/codeql.yml`, and both cannot run at the same time.
- [ ] **Copilot Autofix** for code scanning: optional.

## Repository: *Settings → Rules → Rulesets*

- [ ] Import `.github/rulesets/main.json` after this pull request is merged and its
  checks have each run at least once. The steps, and a caveat for repositories
  with a single maintainer, are in [rulesets.md](rulesets.md).

## Repository: *Settings → Environments*

No deployment environments exist in this repository, and none are needed yet. If
any workflow is ever given deployment rights, it must use a GitHub Environment
with required reviewers. It must use OIDC rather than long-lived secrets, and its
design must be reviewed in `klados-internal` first.

## Repository: *Settings → Secrets and variables*

- [ ] Confirm that **no repository or organisation secrets** are exposed to this
  repository. None of the current workflows uses one.

## Periodic review (quarterly)

- Review who has write and admin access, and which apps and deploy keys are installed.
- Review the open Dependabot, code scanning and secret scanning alerts. None
  should be older than the response targets in `SECURITY.md`.
- Check the OpenSSF Scorecard results and track any regressions.
