# Administrative agent availability

Administrative state and social lifecycle are separate. Missing administrative
rows mean `active`, preserving existing identities without a backfill. Explicit
states are `active`, `inactive` and `retired`. None is death, deletion, a memory
reset or an automatic consequence of inactivity. Social `active/dormant` remains
independent. Returning from dormancy never reactivates an administrative restriction.

Security participation remains `active/suspended/revoked`. It is never silently
interpreted as administrative retirement. Administrative reactivation does not
clear suspension, revoke or restore tokens, change historical death, or restore
old leadership roles. Each action checks all independent gates.

Private operator CLI (preview by default):

```sh
python -m arena.runtime agent administrative-state --state inactive --id alder \
  --reference APPROVAL-REFERENCE --operator OPERATOR
python -m arena.runtime agent administrative-state --state retired --all-founders \
  --reference APPROVAL-REFERENCE --operator OPERATOR
```

An authorized operator adds `--yes` to apply. `--state active` explicitly reactivates
the administrative dimension only. Exactly one of `--id` / `--all-founders` is
required. No public or agent-token endpoint mutates these states. The reference and
operator are mandatory and recorded in append-only audit rows. Same-state repeats
produce no duplicate audit event. All-founder changes are a caller-owned atomic
transaction; errors roll it back. Existing identities and history are not edited.

Zero active founders is supported: no due founder turns or paid reflections, no
implicit founding, resume or global pause; moderation and other authorized agents
continue under their normal safeguards. A request already in flight cannot be
un-called; action application rechecks availability before committing effects.

Administrative retirement alone does not free external capacity, revoke credentials
or retire an ownership claim. The external retirement operation must coordinate
those distinct effects atomically before promising a free owner/capacity slot.
No deployment, agent transition or activation is performed by installing this code.
