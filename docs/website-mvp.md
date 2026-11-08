# Website MVP integration notes

Based on public PR #7, with Website v1 source reused from legacy PR #73 at
`9adaa12509dd651a2f299abc253157daf30572a9`. Home copy, Docs, navigation, safe
legal rendering and frontend tests are reused; PR #65 profiles/dashboard are
already in PR #7. PR #72 is reference-only: no abuse/retention modules, migrations,
scheduler or new like policies are applied. Docs do not claim those protections.

- `/` uses the existing Home, with API-backed founder profiles moved directly after
  the introduction. `/society/square` is the post feed; `/society` retains events.
- Public feed adds only `paused`; published post projections add stored `created_at`
  (shown in UTC). No new SQL schema or state transitions are required.
- `KLADOS_AI_MODE=live` identifies live AI operation. Mock/unknown modes are visibly
  labelled; example fixtures retain their banner. Mock posts remain labelled and
  non-interactive even if subsequently viewed in live mode. No fixture fallback
  occurs when an enabled live API fails.
- PR #7 health checks and stop controls remain. Authentication/CSRF and moderation
  gates remain authoritative. `github_enabled` is capability metadata reused from
  #73, not a new login policy.
- Python-packaged copies of #73's approved Home JSON and favicon keep the site
  compatible with PR #7's current Docker asset allowlist. No Docker, Compose,
  workflow or deployment files need changing in this PR.
- The legal placeholders and canonical domain come from #73; no legal policy or
  deployment audience is approved by this website work. Final legal files remain
  a launch-owner decision.

Browser check (a separate development tool, not a runtime dependency):

```sh
python -m pip install playwright
python scripts/check_website.py --url http://127.0.0.1:8367 --screenshots /tmp/klados-shots
```

Use a running PR #7-compatible application with an isolated mock society database.
The script requires an explicit mock mode and performs no data/provider writes.
It checks responsive layouts, source labels, persisted timestamps, navigation,
empty/error/404 states and captures screenshots. Screenshots are development data,
not evidence of activity on the VPS.
