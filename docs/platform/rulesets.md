# Branch protection: rulesets, required checks and review policy

## Recommended ruleset for `main`

The importable definition is in [`.github/rulesets/main.json`](../../.github/rulesets/main.json).

| Rule | Setting | Why |
| ---- | ------- | --- |
| Restrict deletions | on | `main` cannot be deleted |
| Block force pushes | on | History cannot be rewritten, so the audit trail is kept |
| Require linear history | on | One squash commit per pull request |
| Require a pull request | 1 approval, dismiss stale approvals, code-owner review, approval of the most recent push, all threads resolved | Every change is reviewed by someone other than its last author |
| Allowed merge methods | squash only | Matches the linear history rule |
| Required status checks (strict) | `ci-required`, `gitleaks`, `workflow-lint`, `dependency-review` | Branches must be up to date and pass every check |
| Status check source | GitHub Actions (`integration_id` 15368) | Another app cannot fake a passing status |
| Code scanning | CodeQL: block merges on *high or higher* security alerts and on *errors* | New high-severity findings cannot reach `main` |
| Bypass list | **empty** | Nobody, not even an admin, merges without passing the checks |

Possible later additions, each needing a separate decision:

- **Signed commits.** Turn this on once every contributor, including Claude and
  Codex through their GitHub Apps, produces verified commits.
- **Merge queue.** Every workflow already listens for `merge_group`, so it can be
  turned on without changing any workflow.
- **Tag ruleset** (`v*`). Restricts who can create or delete release tags once
  releases begin.

## Required status checks

| Check | Workflow | Blocks merging when |
| ----- | -------- | ------------------- |
| `ci-required` | CI | lint, format, type check or any test version fails |
| `gitleaks` | Secret scan | a secret appears anywhere in the history |
| `workflow-lint` | Workflow lint | actionlint or zizmor reports a workflow error or security issue |
| `dependency-review` | Dependency review | the pull request adds a dependency with a known vulnerability of moderate severity or higher |
| CodeQL (code scanning rule) | CodeQL | a high or critical security alert is introduced |

**Not required:** `scorecard` (informational only) and the individual CI jobs,
which `ci-required` already covers.

## Review policy

1. **Every change goes through a pull request** to `main`. Nobody pushes to it directly.
2. **AI-authored pull requests**, from Claude or Codex, must be approved by a person
   who has write access. AI agents never approve or merge pull requests, and the
   Actions setting *Allow GitHub Actions to create and approve pull requests*
   stays off.
3. **Code owners**: the ruleset requires code-owner review. It takes effect only
   once `.github/CODEOWNERS` holds active owner lines. That file is currently an
   inactive placeholder, maintained jointly with application engineering
   (`docs/codeowners-proposal.md`). When it is activated, platform paths are
   workflows, Dependabot, rulesets, `SECURITY.md`, `.gitleaks.toml` and
   `docs/platform/`.
4. **Approving the latest push**: approvals are dismissed when new commits are
   pushed, and the reviewer must approve the final state of the branch.
5. **Changes to workflows get an extra security review**. Look for added
   permissions, new triggers (never `pull_request_target`), new third-party
   actions (pinned to a SHA and on the organisation allow-list), and any secret
   use.
6. **Dependency updates** from Dependabot follow the same rules. Patch and minor
   update groups can be approved once CI passes. Major updates need the changelog
   reviewed.

### Caveat: a single maintainer

GitHub never counts the author's own approval. If the only person with write
access also opens the pull requests, which happens when Claude or Codex push
through that person's credentials, a rule requiring 1 approval makes merging
impossible. Choose one of these:

- **(Recommended)** Add a second person with write access who reviews pull
  requests. Keep `required_approving_review_count: 1`.
- **(Interim)** Until a second reviewer exists, set
  `required_approving_review_count` to `0` and set `require_code_owner_review` and
  `require_last_push_approval` to `false`. Keep the pull request requirement, all
  required checks, code scanning and the empty bypass list. Record this as a known
  risk and restore the full rule as soon as possible.

Do not solve this problem by adding a bypass actor.

## Applying the ruleset

Apply it **after** this pull request is merged, and after each required check has
run at least once on `main`. Until a check has run, GitHub cannot find it and the
ruleset cannot be satisfied.

Web interface: *Settings → Rules → Rulesets → New ruleset → Import a ruleset* →
select `.github/rulesets/main.json`.

Command line, run by an owner with an admin token:

```bash
gh api --method POST repos/kladosworld/klados-world/rulesets --input .github/rulesets/main.json
```

Check the result:

```bash
gh api repos/kladosworld/klados-world/rulesets
```
