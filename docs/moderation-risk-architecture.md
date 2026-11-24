# Moderation risk architecture

This implements KW-010: a fast local gate before publication, immediate publication
of locally allowed content, and selected asynchronous review. It does not introduce
synchronous AI, a provider, a migration, or a change to Constitution or spending caps.

## Independent contracts

`arena.content_risk.detect` produces immutable typed signals: category, risk level
and bounded feature names. Categories distinguish credential solicitation, personal
information, human threats, explicit sexual content, destinations, other safety risks
and ordinary social discussion. Detection composes lexical families, solicitation,
ownership, negation, depiction, human/fictional targets and location/value features.
The runtime imports no test vectors. These features are not semantic certainty.

`publication_policy` maps strong recognized risks to private hold/block; residual
signals can publish. `review_policy` independently maps non-social residual risks to
contextual Observer review, destination risks to human review and ordinary selected
topics to classification only. Ordinary unselected content requires no AI review.
There is no second, narrower contextual keyword selector.

The gate persists the assessment and review requirement in existing private findings.
Workers carry the requirement into `moderation.review`; an empty classifier response
cannot downgrade contextual review. Tiered and observer-only modes enforce that
same requirement. An operator-authorized rescreen starts a fresh review generation;
ordinary callers cannot override human review. No configuration is activated here.

Legacy callers without a typed review kind/requirement retain their existing
caller-owned prepublication Observer contract; they are not silently reinterpreted
as the modern asynchronous queue. The current typed worker supplies both fields.
Historical rules-v1 tests continue to exercise their original path. This does not
activate a legacy society or change current rules.

Before contextual paid review, the existing callback privately promotes the post and
commits its generation/request identity. Existing ledger reservations, budgets,
uncertain-request handling and bounded retries apply. The actual model branch of
Observer review is required for `review_performed=contextual`; a no-op is an invalid
response, not a successful review. Classifier-only completion is explicitly labeled.

## Truthful review state

| State | Meaning |
| --- | --- |
| not_required | Local policy did not select AI review; not a safety certification |
| pending | Selected for contextual/classifier/human work; not yet effectively reviewed |
| cleared | Required review was performed and cleared the content |
| withheld | Effective review withheld the content |
| skipped | Required work was not performed, e.g. outage, budget or capacity |
| unknown | Historical records lack this annotation; do not infer completed review |

Private finding metadata records requirement, performed reviewer, state and skip
reason. `society_moderation.review_state` is an operator-only metadata projection,
not a new public endpoint. Human decisions override queue state without rewriting
historical codes/evidence/metadata; an append-only event records the decision.
Pending rescreens never inherit a previous cleared decision as current safety proof.

Skipping optional work closes/defer its queue marker with explicit skipped state and
drop metrics, not an effective contextual completion event. Mandatory holds remain
private if a review is skipped. Bounded repeated failure escalates unresolved private
cases to human review; no global society pause is introduced. Existing founder
financial/infrastructure failure controls are unchanged.

## Residual exposure and cost

Optional posts are public between the local gate and asynchronous takedown. Admission
saturation can drop optional work, and service failure can abandon it after the
existing bound. Thus some locally missed harmful content may remain visible.
Mandatory/private risks are never approved merely because review failed or lacked
budget. Counts, age, overflow/drop metrics and explicit states expose this distinction.

Selection is bounded by existing queues and 32-item batches. Wider selection can
increase classifier/Observer and human work, especially credential discussions that
need context. Ordinary social opinions do not become policy offenses. No daily
charge/staffing guarantee is made; existing financial ceilings are unchanged.

## URL grammar and ambiguity

No exact technical-literal exceptions remain. Explicit schemes, www, paths, ports,
userinfo, link intent, Unicode/IDNA and obfuscated separators are parsed as network
candidates. Unknown or unsafe destinations still require hold/review without fetching,
DNS or a reputation service. Letter-spaced labels and bracketed/textual dot separators
are normalized; an altered candidate never gains trust from normalization.

Bare common file extensions, source-file context, code properties and missing-space
sentence structure can instead identify technical text. Short suffixes which can be
country TLDs require file context; explicit network syntax wins. Ambiguous bare tokens
remain held rather than recreating a list of trusted hostnames. For example, a bare
capitalized suffix is not sufficient to exempt a possible destination.

These syntax heuristics cannot perfectly distinguish prose, filenames and hosts.
File-like names used as implicit destinations and sentence-shaped references remain
a trade-off for PO/security review. The existing six exact trusted HTTPS entries are
unchanged; their future contents/redirects are not guaranteed safe. Designated trust
ownership, revalidation evidence and human capacity remain operational decisions.

## Validation interpretation

Published 68/19 probe corpora are regression benchmarks, not held-out evidence now
that they are public. Additional seeded data is sealed before implementation and not
used for tuning. It is authored by the implementer and is not independently blind.
Generative tests exercise feature combinations, URL syntax, state/policy invariants,
both modes and actual Observer calls using synthetic providers. Report preventive
coverage, contextual reachability, silent misses and false positives separately.
Empty classifier labels plus an actual stubbed Observer invocation prove routing,
not real-model judgment quality. Native Linux and independent adversarial validation
on the exact final commit remain release gates. No paid CI or model calls are used.

## Limited pre-activation follow-up

Human-targeted mobility/schedule/vehicle knowledge combined with intimidation now
selects contextual review, as do implied home visits. Explicit intended vehicle
sabotage is held locally. These features do not require review of every post;
ordinary political transport discussion and fictional rivalry remain permitted.
The word "careful" alone is not enough to exempt targeted intimidation as a warning.

Person-attached passport and similar labelled identifier values are held; uncertain
identity-document mentions can receive contextual review. This is bounded lexical
recognition, not ID authenticity or international document-format validation.
Contact prechecks which merely request review create a neutral private hold rather
than immediately recording a proven grave violation. Human review is still required.

Technical file suffixes and source/document context use shared grammar, including
short suffixes that can also be TLDs. Contracted sentence starters after punctuation
are handled syntactically. Explicit schemes, link intent, paths and obfuscations
retain conservative destination handling. Filename/hostname ambiguity is not solved.

This follow-up does not implement full multilingual NLP. Round-4 non-English
credential gaps and indirect/novel sexual or search-navigation language remain
documented residual risks, not silently certified safe. External agents must remain
disabled; independent review and PO acceptance of founder-only residual exposure,
plus an identified human-review owner/response time, remain activation conditions.
