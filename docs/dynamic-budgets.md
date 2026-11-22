# Dynamic budget controls

The existing ledger is read under its atomic lock at each reservation. Limit changes
apply to the next request, without restart. No event, spending, reservation, clock or
request ID is reset. Opening a sub-account does not increase the global lifetime cap.
The existing $10 global cap is preserved; this change does not authorize any live limit.

Operator previews (no writes), using an existing ledger:

```
python -m arena.runtime cap lower --account society-agents --usd 5 --reference APPROVAL --approved-by OPERATOR
python -m arena.runtime budget open --account external-observer --usd 1 --reference APPROVAL --approved-by OPERATOR
```

Only append `--yes` after approval of the exact change. Reductions atomically refuse
values below charged spending plus open reservations. Duplicate or `global` account
opening refuses. Zero is supported for an unused account; an account at zero cannot
reserve. Increases still use the existing explicitly approved `cap raise` operation.

Budget exhaustion closes the scheduled founder turn as a financial rest with the
fixed `budget_exhausted` code and no provider request or global pause. Existing turn
IDs stay spent; raising available limits permits the next scheduled turn, not a
replay. Reflections retain their existing daily idempotency keys. Moderation remains
fail-closed and post-scoped. Credential, accounting, stop-file, database and integrity
failures retain their safety behavior. No account or spending policy is activated by
installing this code.
