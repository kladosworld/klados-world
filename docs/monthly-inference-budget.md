# Monthly inference accounting

The application keeps one append-only ledger. Monthly policy is opt-in and adds a
calendar-month ceiling to the existing lifetime/account ceilings. A release does
not activate monthly accounting or raise a lifetime cap. The strictest applicable
ceiling governs each maximum-cost reservation.

## Accounting semantics

All amounts are integer microdollars. A reservation belongs to its creation month
in UTC. Settlement and release retain that attribution across month boundaries.
Unused allowance never accumulates. An approved monthly ceiling continues to apply
each month without a rollover job. Existing charges in the current month count
when a new policy is introduced. Decreasing below committed spending blocks new
requests. Open prior-month reservations stay recorded in their original periods
and remain included in lifetime reserved totals.

A backward clock relative to any recorded event, invalid timestamps, missing files
or malformed accounting prevents new requests. Unknown usage retains the existing
maximum-cost charge. A failed settlement retains the reservation and produces the
safe `accounting_unavailable` error, which pauses the worker immediately. Model
retries, founder turns, reflections and paid observer calls share the ledger.

The separate classification API introduced by tiered moderation records request
counts and has no founder inference amount. External operators' own model calls
remain outside this ledger.

## Operator interface

These examples describe separately authorized operations. Running a release does
not run them. `--reference` must identify the approval without including secrets.
`--approved-by` is an operator-selected audit label and requires no personal data.

Preview the monthly policy:

```sh
python -m arena.runtime budget set --ledger /data/ledger.json --usd 10 --warning-usd 7 --reference APPROVAL_REFERENCE
```

Only after explicit approval, append the policy using the same command with `--yes`.
The existing $1 lifetime cap and all account caps remain unchanged. The limited
pilot retains its existing authorization and receives no automatic monthly budget.

A lifetime-cap increase is a separate operation, previewed by default:

```sh
python -m arena.runtime cap raise --ledger /data/ledger.json --usd AMOUNT --reference SEPARATE_APPROVAL_REFERENCE
```

`--account society-agents` or `--account society-observer` selects a particular
account instead of the global lifetime cap. Increasing any cap requires explicit
`--yes` and a nonempty reference. Budget policy changes append one event each and
preserve every previous event, reservation and request ID. Library callers also
must supply explicit approval for an increase. A lower monthly ceiling may be
applied without increase approval, while the CLI continues to preview by default.

## Guard reporting contract

```sh
python -m arena.runtime budget show --ledger /data/ledger.json
```

This reads the ledger without database or provider access. The existing `status`
command also includes the report as `inference_budget`. A failed report exits
nonzero with sanitized diagnostics. Report schema 1 contains:

- `as_of`, `month` in UTC;
- `monthly_cap_microusd`, `warning_microusd`, `warning`;
- `charged_microusd`, `reserved_microusd`, `available_microusd` for the current month;
- `lifetime_cap_microusd`, `lifetime_charged_microusd`,
  `lifetime_reserved_microusd`, `lifetime_available_microusd`;
- `pending_by_month` for outstanding reservations, including prior periods;
- `last_budget` and `last_cap` with amount, timestamp, actor label and reference.

A null monthly ceiling means no monthly policy has been activated. The monthly
remaining amount does not override a tighter lifetime or account ceiling.
The additive schema-1 fields `accounts`, `events` and `last_event_id` support guard
reconciliation. `accounts` maps each account ID to its effective `cap_microusd`,
`charged_microusd` and `reserved_microusd`, including approved cap events. `events`
counts all validated ledger events. `last_event_id` is the final event ID, or null
for empty history. These cursors describe the ledger snapshot. `as_of` is report
generation time and does not establish provider-data freshness.

Only settlement events add actual charges. Reserve events hold maximum cost;
settlement or release closes the reservation. Budget/cap events change policy or
effective ceilings and add no spending. Open events establish account ceilings.
Unknown events or malformed accounting fail closed before emitting a report.
Consumers must use this validated report instead of summing arbitrary event amounts.
The guard must separately verify provider-data freshness, unexplained differences
and history continuity. Event count and final ID alone do not prove history integrity.

Raw `global_cap` in the file is the immutable initial cap. Consumers must use the
derived report to account for approved later `cap` events.

The operations guard owns provider reconciliation, freshness requirements,
continuous-operation authorization and alerts. It must account for provider billing
latency, reservation creation month, conservative unknown-usage charges and charges
outside this ledger. Missing or unexplained evidence stops continuous operation.
Provider auto top-up remains an operational control. This change installs no
credentials, calls no providers and changes no deployed budget.

## Independent validation

Native Linux must validate process-level file locks, concurrent reservations,
UTC month/year rollover, pending reservations, restart recovery, provider outages,
malformed accounting and approval gates. Local Windows unit runs may mock `flock`;
they do not establish native Linux locking behavior. No database migration is needed.
