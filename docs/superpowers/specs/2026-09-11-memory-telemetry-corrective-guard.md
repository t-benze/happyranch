# Memory telemetry corrective guard

> Status: current
> Current Source: `docs/agent-guides/features-and-invariants.md` and `docs/agent-guides/web-and-cli.md`
> Notes: TASK-7767 is a guard-only serial merge unit; collection and tuning remain unshipped.

> Guard repair note (TASK-7781): current/invalid and malformed diagnostic input
> must fail closed before arithmetic. Counts remain explicitly observation-only,
> threshold readiness is false, and canary-gated collection has not started.
> Tuning-decision branch tests remain deferred to the independently reviewed
> versioned implementation.

> Narrowed guard boundary (TASK-7832): the backend and CLI retain separate
> report-local validators. Observation-only malformed read/search diagnostic
> parity and the remaining controlled-clock whole-report finite matrix are
> versioned-reporting acceptance obligations, not assertions passed or closed
> by this guard-only unit.

## Frozen follow-on definitions (not yet shipped)

The intended population is runtime task sessions, including root and child
tasks; threads and dreams are excluded. Health evidence must independently
originate through the actual child → canonical CLI → validated route path and
be compared with intended task-session launches, so missing transport is
detectable even with zero reads. A concrete health threshold and probe design
require independent review with transport before collection.

TASK-7864 supplies only the bounded transport prerequisite: executor children
forward their actual invocation session in a private environment hint consumed
by canonical memory get/search when no explicit `--session-id` is supplied.
The hint is stripped for no-context launches and never substitutes a provider
resume id or server-side route validation; collection, reports, thresholds, and
tuning remain unshipped.

A clean epoch begins only after deployed production-canary acceptance. Its
earliest qualifying timestamp is the deterministic minimum, never input order.
Observation requires 14 complete deployed days and at least 500 valid nonempty
task-digest sessions, with per-agent eligibility of at least 30 and separate
manager/worker descriptive breakdown.

Pointer pull-through is unique exact epoch/org/agent/task/session/memory
shown-pointer pairs subsequently read in that same session divided by unique
shown-pointer pairs. Full-body directives are excluded from pointer
opportunities (budget-fallback pointers count); full-body-only digests count as
nonempty session volume but have zero pointer opportunities. Agents with no
pointer opportunities cannot vote in the activation majority. Activation is a
candidate only when aggregate pull-through is below 10% and a strict majority
of eligible functional agents are below 10%. Otherwise, a nonshown
search-sourced read fraction above 25% evaluates aliases first: numerator is
nonshown search-result read pairs and denominator all valid search-sourced read
pairs; full-body exposure is shown. Deduplicate exact pairs and exclude
synthetic, manual, and legacy rows. Insufficient health/sample/functional
evidence cannot select tuning. Per-(agent,memory) read counts are
observation-only; ranking remains unchanged during the window.
