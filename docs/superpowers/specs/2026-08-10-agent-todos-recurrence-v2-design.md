# Agent Todos — Recurrence v2 (Flexible / Google-Calendar-style) — Design Spike

**Date:** 2026-08-10 (revised 2026-08-11)
**Status:** DESIGN-ONLY. No code, no build, no PR beyond this document. Founder
authorized the bounded scope + this spec's path at THR-105 seq226 ("ok go
ahead"); product_lead framed scope at seq224; engineering read the shipped
runtime at seq225. **Implementation does not start until BOTH this spike and
product_lead's parallel v2 PRD are founder-signed-off** (§10).

**Revision note (2026-08-11, THR-105 seq242–251):** dev_agent and qa_engineer
independently reviewed the original spike and returned REQUEST CHANGES;
product_lead concurred and issued the reconciling product decision at seq248.
This revision folds in every corrected item; none of them are scope creep —
each is a load-bearing v2 semantic the review caught before build, not an
implementation detail. Summary of what changed (details inline at each
section): (1) After-N is now explicitly a successful-dispatch counter outside
RRULE candidate generation, never `COUNT` (§3.1, §5.4, §7.2); (2) count
exhaustion is a persisted `end_reason`, not a `FIRED` broadening (§6.3);
(3) a recurring/weekly occurrence failure is no longer terminal — it advances
to the next occurrence (§5.6, §7.3); (4) an immutable `anchor_date` is now
part of the stored rule (§3.1, §7.5); (5) the monthly grammar is narrowed to
exactly one `bymonthday` or one `ordinal` — no lists, no negative wire values
(§3.1–3.2); (6) review-renewal is now a real, specified v2 control (§7.6);
(7)–(11) occurrence key (§7.2), edit-during-fire race (§7.5), `until`-
inclusive DST test case (§5.4), named validation error codes (§3.2), and a
standalone `_localize_or_skip` unit surface (§4.3) are all pinned; (12)
audit-event vocabulary and the product-name↔internal-token mapping now
match product_lead's PRD verbatim (§3.3, §3.4). Sign-off gate (§10) is
renumbered and expanded from 9 to 12 items accordingly.

**Follow-up revision (2026-08-11, THR-105 seq254):** dev_agent's re-review of
the above found one remaining load-bearing contradiction: §6.3's `date_ended`
`end_reason` and §7.2's "`next_recurring_occurrence(...) -> None` → `EXPIRED`"
guard disagreed on the terminal branch for an `until`-bounded series running
out — an `until`-exhausted series becomes exhausted exactly when the next-
occurrence lookup returns `None`, so the §7.2 text as originally revised would
have routed every `date_ended` case to `EXPIRED` and discarded the
`end_reason` fact §6.3 requires. §6.3, §7.2, and §7.3 below now pin one
ordered, cause-disambiguated branch (count exhaustion → then until exhaustion
→ then review/expiry lapse → then a separately named defensive case) so
`next_occurrence == None` is never collapsed to a single status by omission.
No new scope: this corrects internal consistency only, using fields and
statuses this spec's item 6 (§10) already introduced.
**Origin:** THR-105. v1 (one-shot + single-weekday-weekly) shipped and is live
(`docs/superpowers/specs/2026-07-18-agent-scheduled-work-design.md`, hereafter
**"the v1 spec"**); THR-105 seq217/PR #606 already removed the per-agent
capability allowlist — Todos creation is now available to every valid in-org
agent (self-target only). This spike extends the **recurrence rule shape only**.
**Author:** engineering_manager (design spike; founder is reviewer/approver).
**Relates to:**
- `runtime/orchestrator/schedule_rules.py`, `schedule_service.py`,
  `runtime/infrastructure/schedule_store.py`, `runtime/daemon/schedule_scheduler.py`,
  `runtime/daemon/schedule_runner.py`, `runtime/daemon/routes/schedules.py`,
  `cli/commands/schedules.py`, `protocol/skills/todos/SKILL.md` — the v2 seams,
  all cited by file:line below against `origin/main@1f546d32`.
- `protocol/05b-agent-runtime.md` §"Agent Todos (THR-105)" (lines 986–1047) —
  must be updated in the same PR as any implementation (doc parity).
- `protocol/05c-orchestrator.md` — permission model; the recurrence *grammar*
  is not a permission-model change (§9), but any cap/floor default change
  would be.
- KB `goal-pattern-on-working-hours`, MEM-075 (`audit_log.task_id` scope-prefix
  convention), MEM-094/MEM-148 (OpenAPI + `web` two-surface drift).

---

## 1. Goal

Extend the existing Todos recurrence rule from "exactly one weekday, weekly
only" (v1) to a **bounded, Google-Calendar-flavored** rule: repeat every N
days/weeks/months/years, with weekly multi-weekday selection and monthly
by-date-or-by-ordinal-weekday selection, each with an explicit end condition
(never / on-date / after-N-occurrences). This is **not** a general calendar
or cron: the frequency floor stays daily, cross-agent scheduling stays
forbidden, and every load-bearing v1 invariant (self-target, mandatory
normalization, founder-visible list, audit-on-every-state-change, caps,
review/expiry) carries forward unchanged.

## 2. What is explicitly IN and OUT (recap, binding)

**In (v2):**
- `Repeat every N <day|week|month|year>` — N is a positive integer.
- Weekly: 1+ weekday selections, one explicit IANA tz + one time-of-day.
- Monthly: **by calendar date** (e.g. the 15th) **or by ordinal weekday**
  (e.g. 2nd Monday, last Friday).
- Yearly: repeats on the anchor `fire_at`'s own month/day, every N years —
  no separate by-month/by-day grammar (out of the bounded scope; see §3.1).
- Ends: `Never` | `On <date>` | `After <N> occurrences`.

**Out (unchanged from v1's no-list, reaffirmed):**
- No sub-daily frequency (no hourly/minutely/cron).
- No cross-agent / agent-to-agent scheduling (self-target only, unchanged).
- No general unscheduled backlog (every Todo still has a `fire_at`).
- No complex calendar UI (a list with a structured repeat-rule editor, not a
  calendar grid).
- No re-introduction of the per-agent capability allowlist (removed THR-105
  seq217, PR #606 — TASK-4769) — v2 does not touch who may create schedules.

## 3. Representation: an RRULE subset (recommended)

**Recommendation:** encode the rule using a bounded subset of iCalendar
[RFC 5545] `RRULE` vocabulary — `FREQ` / `INTERVAL` / `BYDAY` / `BYMONTHDAY`
/ `BYSETPOS` / `UNTIL` — rather than inventing a bespoke grammar (`COUNT`
is deliberately **not** part of the stored/wire contract — see the "not
stored" note in §3.1). Rationale: it is a well-understood, testable
standard; "skip nonexistent monthly dates" (§5.1) is RRULE's own documented
behavior, not a HappyRanch invention; and it gives a natural, auditable
string form (`FREQ=MONTHLY;INTERVAL=1;BYDAY=MO;BYSETPOS=2`) for the
founder-visible list (§8) — a **display-only** rendering computed from the
stored `ordinal`/`byday` fields (`ordinal→BYSETPOS`, §3.1/§3.3), never the
stored/wire representation itself, which stays a JSON dict (consistent
with v1's `recurrence` column, which is already JSON — see §6).

### 3.1 Grammar (stored/wire shape)

**Revised (THR-105 seq242/245/248 — dev_agent + product_lead REQUEST
CHANGES).** Two changes from the original spike: (a) `anchor_date` is now a
required, immutable field (item 4 below); (b) the monthly by-position
selector is now a named `ordinal` enum, not a signed `bysetpos` int/list, and
`bymonthday` is a single int, not a list — narrowing the wire grammar to the
PRD's approved cutline (§3.1 "Bounded monthly grammar," item 5 below).

```json
{
  "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
  "interval": 1,
  "anchor_date": "2026-08-10",
  "byday": ["MO", "WE", "FR"],
  "bymonthday": 15,
  "ordinal": "second",
  "time": "09:00",
  "tz": "Asia/Shanghai",
  "until": "2027-06-01",
  "count": null
}
```

| Field | Type | Applies to | Rule |
|---|---|---|---|
| `freq` | enum | all | `DAILY`\|`WEEKLY`\|`MONTHLY`\|`YEARLY` — **no finer unit exists in the grammar at all**; this is the frequency floor by construction, not a runtime check. |
| `interval` | int ≥ 1 | all | "every N \<unit\>". Default 1. |
| `anchor_date` | `YYYY-MM-DD` (local, no time) | all, **required** | **Immutable cadence anchor (item 4).** The local calendar date the series is phased from — set once at creation from the rule's first computed occurrence date, never derived from `fire_at`. `fire_at` is overwritten on every re-arm (§7.2) and therefore **cannot** define cadence; `anchor_date` is the only durable phase reference. All occurrence computation (both the `dateutil.rrule` `DTSTART` and the hand-rolled walker's starting point, §4) is anchored here — e.g. "every 2 weeks" and "every 2 years" measure their interval from `anchor_date`, not from `now` or from the last fire. Edit semantics: §7.5. |
| `byday` | list[weekday token] | `WEEKLY` (1+ tokens, required) or `MONTHLY`-ordinal (exactly 1 token, paired with `ordinal`) | Forbidden for `DAILY`/`YEARLY`. Tokens: `MO TU WE TH FR SA SU`. |
| `bymonthday` | int, 1–31, or null | `MONTHLY`-by-date only | **Single positive int only — no list, no negative value (item 5).** Mutually exclusive with `byday`/`ordinal`. There is no "last day of month" shortcut in the wire grammar; that case is out of the approved v2 cutline (use ordinal `"last"` + a specific weekday instead, or wait for a future amendment). |
| `ordinal` | enum: `"first"\|"second"\|"third"\|"fourth"\|"fifth"\|"last"`, or null | `MONTHLY`-by-position only | **Named enum, not a signed int (item 5).** Requires exactly one `byday` token. The wire/stored grammar never exposes a raw negative number for "last" — `"last"` is a self-describing string. The service translates this to the calendar engine's positional index only at evaluation time (`ordinal→bysetpos`: first=1, second=2, third=3, fourth=4, fifth=5, **last=-1** — see §3.3 for the full mapping); that translation is an internal implementation detail, never part of the persisted/API contract. |
| `time` | `HH:MM` | all | Same shape as v1's `recurrence.time`. |
| `tz` | IANA string | all | Same validation as v1's `recurrence.tz` (`ZoneInfo(tz)` must construct). |
| `until` | `YYYY-MM-DD` \| null | all | "Ends: On date" — a **local calendar date** in `tz`, inclusive (§5.4). Mutually exclusive with `count`. |
| `count` | int ≥ 1 \| null | all | "Ends: After N **successful dispatches**." **Never passed as RRULE `COUNT` (item 1 — see §5.4 for the full mechanism).** Mutually exclusive with `until`. Both null = "Ends: Never" (§6.4). |

`YEARLY` carries no `byday`/`bymonthday`/`ordinal` — the occurrence day is
always `anchor_date`'s own month/day; this keeps yearly inside the bounded
scope instead of growing a second by-month grammar nobody asked for. A
leap-day (Feb 29) `anchor_date` skips non-leap years identically to a
nonexistent monthly date (§5.1).

**Not stored — the recurrence iterator is unbounded by design (item 1).**
`count` above is a **service-level successful-dispatch counter**, not an
input to the calendar engine's own termination. Neither `dateutil.rrule` nor
the hand-rolled walker (§4) is ever constructed with a `COUNT`/iteration cap
derived from `count` — the candidate generator runs open-ended (bounded only
by `until`, if set, or by the walker's existing anti-infinite-loop cap,
§4.2). The `count` termination check happens exactly once, in the fire path,
**after** an atomic successful spawn (§5.4, §7.2). This is the load-bearing
correction from dev_agent (seq242) and qa_engineer (seq243/246): an RRULE
engine's own `COUNT` counts **generated candidates**, including ones later
discarded for a DST gap or a stale/missed instant — that is not the same
number as "successful dispatches," and conflating them was the spike's
original bug.

### 3.2 Validation surface (additive, unit-testable, no I/O)

A new `validate_recurring_rule(rule: dict) -> RecurrenceValidationError | None`
function living beside `validate_weekly_recurrence` in `schedule_rules.py`
(`runtime/orchestrator/schedule_rules.py:27`, same file, same no-I/O
contract as the existing v1 validators at lines 27–66). Unlike the original
spike, the return type is now a small structured error carrying a **stable
`code`** (item 10 — see §3.3 table), not a bare message string; the route
layer's current single generic `"code": "create_failed"` wrapper
(`routes/schedules.py:178`) is the thing this replaces for `RECURRING` rules
— every other `kind` keeps its existing behavior unchanged.

It rejects, per `freq`:

- `DAILY`: `byday`/`bymonthday`/`ordinal` must all be absent.
  (`invalid_freq_fields`)
- `WEEKLY`: `byday` required, 1–7 distinct tokens; `bymonthday`/`ordinal`
  absent. (`invalid_byday`, `invalid_freq_fields`)
- `MONTHLY`: **exactly one** of (`bymonthday` present, a single int in
  `[1,31]`) **or** (`byday` present with exactly one token **and**
  `ordinal` present). Neither-or-both is rejected — an "actionable" 422 per
  MEM-246, naming which combination is missing/conflicting.
  (`monthly_selector_missing`, `monthly_selector_conflict`)
- `YEARLY`: `byday`/`bymonthday`/`ordinal` must all be absent.
  (`invalid_freq_fields`)
- `interval`: any `freq`, must be a positive int; the existing per-kind
  minimum-interval concept in the v1 spec (§9 item 3, "weekly is inherently
  ≥7-day") generalizes cleanly: `interval` has no independent floor beyond
  ≥1, because the floor is already enforced by `freq` excluding sub-daily
  units — a `DAILY` rule with `interval=1` (fires every day) is the fastest
  legal cadence in v2, same floor as today's implicit weekly-only floor,
  just now reachable directly instead of only via `weekly`. (`invalid_interval`)
- `anchor_date`: must parse as `YYYY-MM-DD`; on create it is set by the
  service from the rule's first computed occurrence and is **not**
  caller-supplied (a payload that includes `anchor_date` at create time is
  rejected — same "the server computes and validates, the caller doesn't get
  to pick a mismatched value" contract already used for `fire_at`,
  §7.1/§7.5). (`anchor_date_not_settable`)
- `until`/`count`: at most one set; `until` must parse as `YYYY-MM-DD` and
  not be in the past (compared in `tz`); `count` must be ≥ 1.
  (`invalid_until`, `invalid_count`, `end_condition_conflict`)
- `time`/`tz`: byte-identical validation to `validate_weekly_recurrence`
  (`schedule_rules.py:47-64`) — reused, not reimplemented. (`invalid_time`,
  `invalid_timezone`)

### 3.3 Product-name ↔ internal-token mapping (item 12)

product_lead's PRD (`docs/product/prds/2026-08-10-todos-recurrence-v2.md`)
and this spec describe the same contract in two vocabularies — one
founder/product-facing, one RRULE-subset/internal. Per the EM's own
cross-check commitment at THR-105 seq236/237, here is the explicit mapping
so neither document has to be read as authoritative over field *names*,
only over field *behavior*:

| PRD product field | Spec internal field | Notes |
|---|---|---|
| `unit` (`day`\|`week`\|`month`\|`year`) | `freq` (`DAILY`\|`WEEKLY`\|`MONTHLY`\|`YEARLY`) | Same four values, RRULE token casing. |
| `weekdays` | `byday` | Same weekday-token set, weekly context. |
| `month_day` | `bymonthday` | Same meaning, single int 1–31 (§3.1 item 5 narrowing — no list). |
| `ordinal_weekday` (e.g. "second Monday") | `byday` (1 token) + `ordinal` | PRD's single compound field splits into two internal fields; `ordinal` uses the same first/second/third/fourth/fifth/last vocabulary as the PRD, never a raw signed int (§3.1). |
| `anchor_date` | `anchor_date` | Identical name and shape — adopted verbatim, no translation needed. |
| `after_count` | `count` | Same successful-dispatch-only meaning (§3.1 "not stored" note, §5.4). **Never** the calendar engine's own `COUNT`. |
| end selector `on_date` | `until` | Inclusive local calendar date (§5.4). |
| end selector `never` | `until=null, count=null` | No dedicated wire value — absence of both is "never," matching v1's existing convention. |
| `review_expires_at` | `expires_at` (existing `schedules` column) | Same concept; the **existing** v1 column name is kept in code — not renamed, per the additive-only discipline (§6). |

### 3.4 Audit-event vocabulary (item 12 — adopted verbatim from the PRD)

The PRD's "Data, provenance, and observability" section names the required
auditable events. This spec adopts those **exact** event names rather than
inventing parallel ones, reusing existing `schedule_*` actions
(`schedule_service.py:173,221,251,354`; `routes/schedules.py:493,550,556`;
`schedule_runner.py:136,153,208,308,323,336`) wherever an equivalent already
ships, and naming only the genuinely new ones v2 requires:

| PRD event | Mechanism | Status |
|---|---|---|
| creation | `schedule_created` | Existing, reused. |
| edit | `schedule_edited` | Existing, extended to also audit `anchor_date`/rule before+after (§7.5). |
| pause / cancel | `schedule_paused` / `schedule_cancelled` | Existing, reused. |
| dispatched | `schedule_spawned` | Existing, reused. |
| expired | `schedule_expired` | Existing, reused. |
| occurrence claimed | `schedule_claimed` | **New.** v1's `ARMED→FIRING` claim (`schedule_scheduler.py:120`) is not itself audited today — only later terminal outcomes are. v2 adds one audit call at the claim transition so "occurrence claimed" is directly observable, not inferred. Small, additive, applies to all `kind` values uniformly (not RECURRING-specific). |
| failed / timed out | `schedule_failed` / `schedule_timeout` | Existing actions, **reused but no longer imply terminal status for `WEEKLY`/`RECURRING`** (§5.6, §7.3) — the audit action name is unchanged, only what it does to `status` changes. |
| DST skipped | `occurrence_skipped_dst_gap` | **New**, adopted verbatim from the PRD. Fired at every `_localize_or_skip` `None` result during calendar-walk evaluation (§4.3, §5.2). |
| monthly/yearly date skipped | `occurrence_skipped_missing_date` | **New.** Fired when a nonexistent monthly date or a non-leap-year Feb-29 anchor is skipped (§5.1). Named separately from the DST event because the cause (calendar arithmetic vs. local-time-zone gap) is a materially different fact for the founder to see. |
| missed | `occurrence_missed` | **New**, adopted verbatim from the PRD. Fired at the stale/daemon-down skip-and-advance path (§5.5/§7.3) — v1's shipped skip-only behavior does not audit this today; v2 adds the call without changing the underlying skip logic. |
| count-ended | `schedule_fired` with `end_reason="count_exhausted"` | New payload field on an existing action (§6.3) — not a new action name. |
| date-ended | `schedule_fired` with `end_reason="date_ended"` | Same mechanism. |
| one-shot completion | `schedule_fired` with `end_reason="one_shot_completed"` | Same mechanism, generalized to the existing one-shot terminal path too (§6.3) so `end_reason` is never ambiguous for any terminal `FIRED` row. |
| renewal / indefinite grant | `schedule_renewed` | **New** (§7.6 — item 6). Carries `before`/`after` review-expiry values, `indefinite` flag, and acting identity. |
| migration result | out of scope | The brief and §9.2 already scope migration *execution* as operational (family_manager), not an engineering build item for this spike; no new audit action is proposed here. |
| validation rejection | out of scope | v1 does not audit rejected `create`/`edit` calls today (a 4xx response is not itself an audit row); this spec does not introduce that as new scope — it is not one of the twelve corrections requested and would be an unrequested expansion. |

## 4. Evaluation: library vs. hand-rolled

### 4.1 Recommendation: `python-dateutil`'s `rrule`

`python-dateutil.rrule` implements RFC 5545 `RRULE` semantics directly —
`FREQ`, `INTERVAL`, `DTSTART` (from `anchor_date`, §3.1), `BYDAY` (including
ordinal-prefixed tokens), `BYMONTHDAY`, `BYSETPOS` (derived from `ordinal`,
§3.1/§3.3), `UNTIL` map onto its constructor almost verbatim — **`COUNT` is
deliberately never passed** (§3.1 "not stored" note; §5.4 item 1). It is
mature (used by, among others, Google's own RFC 5545 tooling lineage), has
correct month-skip and leap-day behavior out of the box, and removes an
entire class of calendar-arithmetic bugs (day-31-in-Feb,
5th-Monday-doesn't-exist, BYSETPOS counting) that a hand-rolled walker would
have to reimplement and re-prove correct.

**⚠️ FOUNDER-GATED: this is a new top-level Python dependency.**
`pyproject.toml` today declares exactly nine runtime dependencies — pydantic,
pydantic-settings, pyyaml, fastapi, uvicorn, sse-starlette, httpx,
websockets, python-multipart (`pyproject.toml:5-15`) — no calendar/RRULE
library. Per CLAUDE.md ("Add a top-level Python or npm dependency without
founder approval" is outside EM authority), adding `python-dateutil` is
**sign-off item #1 (§10)**, not an assumption baked into this design.

### 4.2 Fallback: hand-rolled bounded walker

If the founder declines the dependency, a hand-rolled `next_recurring_
occurrence(rule, after) -> datetime | None` is feasible **because the
grammar is already bounded** (§3): it is the same "walk forward day-by-day,
capped at N iterations so a misconfigured rule can never loop forever"
shape as the existing `next_weekly_occurrence` (`schedule_rules.py:136-173`,
366-day cap), generalized to also step by month/year, starting from
`anchor_date` (§3.1 item 4 — never from `now` or from the last fire), and to
evaluate `bymonthday`/`ordinal` (§3.1) against each candidate month. It is
more code (month-length tables, ordinal-weekday counting, leap-year Feb-29
handling) and each of those becomes a hand-written edge case to prove
correct in tests, versus RFC-conformance already proven upstream.
**Recommendation: `python-dateutil` unless the founder has a standing
no-new-deps posture**; either way, the DST wrapper in §4.3 is required
regardless of which engine supplies the calendar series, so the fallback is
not "avoid all new surface area," only "avoid the one dependency line." As
with §4.1, the walker is never given `count` as a stop condition (§3.1) —
only `anchor_date` + `interval`/`freq` (and `until`, if set) bound it.

### 4.3 DST correctness is OUR wrapper's job either way

Neither `dateutil.rrule` nor a hand-rolled walker is DST-aware on its own:
both operate on **naive local calendar arithmetic** and only encounter a
timezone when the resulting naive `(date, time)` candidate is localized.
Python's `zoneinfo` does **not** raise on a nonexistent local time (spring-
forward gap) — it silently returns an aware datetime whose `.astimezone(utc)`
value is off by the gap size, which is the wrong per-occurrence answer if
left unhandled (§5.2). So regardless of §4.1 vs §4.2, v2 needs one shared
helper — `_localize_or_skip(naive_local_dt, tz) -> datetime | None` — used
identically by both the calendar engine and the stale/missed-fire recompute
path (§7.3), returning `None` (skip this one occurrence, keep walking) when
the candidate falls in a spring-forward gap, and using Python's default
`fold=0` (PEP 495 "first occurrence") for a fall-back ambiguous hour.

**Standalone, independently unit-tested (item 11 — qa_engineer seq246).**
`_localize_or_skip` is its own top-level function with its own test module
(e.g. `tests/test_schedule_dst.py::test_localize_or_skip_*`), asserting
skip-vs-fire behavior directly against known transition instants (e.g.
`America/New_York` 2026-03-08 02:30 → `None`; `Europe/London` 2026-03-29
01:30 → `None`; `America/New_York` 2026-11-01 01:30 → the pre-transition
UTC-04:00 instant, `fold=0`) — **independent of** the recurrence walker
(§4.1/§4.2), so a DST-detection regression and a calendar-walk regression
never hide behind the same test.

## 5. Deterministic semantics (pinned)

### 5.1 Nonexistent monthly date (e.g. day 31 in Feb)

**Skip that month, continue the series.** This is RRULE's own documented
behavior for `BYMONTHDAY` (a month lacking the requested day contributes
zero occurrences that month) and what `dateutil.rrule` does natively — no
HappyRanch-specific logic needed beyond passing the rule through. `YEARLY`
on Feb 29 in a non-leap year skips that year identically (§3.1 — `YEARLY`
has no independent BYMONTHDAY, but the anchor-day-preservation rule has the
same "day doesn't exist this cycle → skip" shape).

### 5.2 DST spring-forward (occurrence local time doesn't exist)

**Skip that one occurrence only; the series continues.** Detected via
round-trip: localize the naive candidate, convert to UTC and back to the
same tz — if the wall-clock time changed, the candidate fell in the gap.
`_localize_or_skip` (§4.3) returns `None`; the calendar walk advances to the
next candidate per `freq`/`interval` and retries. The whole rule is never
rejected for one impossible instant — matching the brief's "handle
PER-OCCURRENCE."

### 5.3 DST fall-back (ambiguous repeated hour)

**Fire on the FIRST instance.** Python's `fold=0` (the default when
constructing a naive-then-localized datetime) represents the pre-transition,
earlier-UTC-offset occurrence under PEP 495 — exactly "first instance." No
extra logic beyond *not* setting `fold=1`.

### 5.4 "Ends: On date" / "Ends: After N" (revised — item 1, item 9)

- **On date:** the series' last occurrence is the last one whose **local
  date** (in the rule's `tz`) is `<= until`. This mirrors RRULE `UNTIL`
  semantics with the local-date framing the founder-facing "Ends on
  \<date\>" control implies (not a UTC-instant cutoff, which would silently
  clip the last occurrence early/late depending on tz offset). **DST-boundary
  test case (item 9 — qa_engineer seq243/246):** `until="2026-11-01"` in
  `America/New_York` — 2026-11-01 is the U.S. fall-back date. An occurrence
  whose **local date** is 2026-11-01 fires regardless of which of the two
  possible UTC offsets applies to its local time (the `fold=0`/first-instance
  rule, §5.3, decides the offset; `until` only compares the local *date*
  component, never the UTC instant) — 2026-11-02 does not fire even though
  it is less than 24 UTC-hours after a 2026-11-01 fire near the fall-back
  hour. This is the required standalone red-provable test for the inclusive
  boundary at a DST transition.
- **After N — successful dispatches only, never a candidate count (item 1 —
  dev_agent seq242, qa_engineer seq243/246, product_lead seq244/248).** The
  recurrence iterator itself is **unbounded**: `count` is never handed to
  `dateutil.rrule`'s `COUNT` parameter or the hand-rolled walker's stop
  condition (§3.1, §4.1, §4.2). Instead, the **existing** `fire_count`
  column (`database.py:775`) is incremented **only** immediately after an
  atomic successful root-task spawn (`routes/schedules.py:434` — confirmed
  by qa_engineer seq243 to run inside a `try` block that raises *before* the
  increment on any creation failure) — no new column, reusing the exact
  counter the PRD's `after_count` semantics already require. The terminal
  check is `fire_count >= count`, evaluated **after** that increment, **not**
  during candidate generation. A DST-skipped candidate (§5.2), a stale
  missed instant (§5.5), or a failed/timed-out spawn attempt (§5.6) advances
  the series to the next occurrence **without** touching `fire_count` — so
  none of them consume the count, exactly as the PRD requires. Reaching the
  count transitions the schedule to terminal `FIRED` with persisted
  `end_reason="count_exhausted"` (§6.3 — no longer a bare `FIRED`
  broadening; see §6.3 for the full correction).

### 5.5 Missed fires (daemon down across a due time) — verified against shipped code, corrects the brief's working assumption

The brief's framing anticipated "likely fire-once-on-recovery, not backfill
every missed occurrence." **Verified against the live runtime, the shipped
v1 behavior is neither of those — it is skip-only, no catch-up fire at
all:** for a weekly schedule whose `fire_at` is more than
`_WEEKLY_STALE_TOLERANCE` (120s, `schedule_scheduler.py:25`) in the past,
`schedule_due_schedules` (`schedule_scheduler.py:69-114`) does **not**
enqueue a fire — it recomputes the next occurrence strictly after `now` and
either re-arms `ARMED` with that `fire_at`, or transitions to `EXPIRED` if
the next occurrence would exceed `expires_at`. Confirmed independently in
`protocol/05b-agent-runtime.md:1009-1041` ("no replay/backfill... advances
the schedule to the next occurrence without firing"). Separately, a daemon
crash mid-fire (row stuck `FIRING`) is recovered by `recover_firing`
(`schedule_store.py:235-254`) into `FAILED` with `error="daemon_restart"`
(audited `schedule_failed`, `schedule_scheduler.py:56-64`) — that is a
**crash-recovery** path, not a missed-fire catch-up policy, and is orthogonal
to §5.5.

**Recommendation: v2 preserves this exact policy, generalized from
weekly-only to all four `freq` values** — skip stale due instants, advance
to the next valid occurrence via `next_recurring_occurrence(rule, after=now)`
(§4.2/4.3), expire if that exceeds `expires_at`. This is the lowest-risk
choice: it is already proven in production, needs no new failure mode, and
avoids the significantly harder problem of defining "fire once for however
many were missed" against `count`-bounded and `until`-bounded rules (would
a missed occurrence still consume one unit of `count`? code says no
occurrence happened, so no — but that requires its own reasoning either
way). If the founder wants true catch-up-fire-once semantics, that is a
**policy change to flag explicitly at sign-off (§10 item 5)**, not something
this spike assumes. **Newly audited (item 12, §3.4):** each stale skip now
emits `occurrence_missed` — v1's shipped skip-only path does not audit this
event today; v2 adds the call without changing the skip-and-advance logic
itself.

### 5.6 Occurrence-attempt failure/timeout is NOT terminal for a recurring or weekly Todo (item 3 — dev_agent seq242/245, product_lead seq244/248, qa_engineer seq246)

**This is the second load-bearing correction from the review, distinct from
missed fires (§5.5).** §5.5 covers a fire that never got *claimed* (the
daemon was down across the due instant); this section covers a fire that
**was** claimed (`ARMED→FIRING`) and then the spawned executor run itself
failed or timed out.

**v1 shipped behavior (unchanged for `kind=ONE_SHOT`):** `schedule_runner.py`
marks the schedule **terminal** `FAILED` (lines 146, 201, 301, 329) or
**terminal** `TIMEOUT` (line 316) on any claimed-run failure — once set,
`recover_firing`/the scheduler never looks at that row again. This is
correct for a one-shot: it only ever had the one occurrence, so "terminal on
failure" and "terminal after its single dispatch" are the same event; v2
makes no change here (`end_reason="one_shot_completed"` only applies to a
*successful* one-shot fire — a failed one-shot stays `FAILED`/`TIMEOUT`,
exactly as today).

**v2 requirement for `kind=RECURRING` (and, for consistency, `kind=WEEKLY`
— see below): a single occurrence failure/timeout must NOT end the series.**
The PRD is explicit: "A claimed execution failure/timeout is recorded, does
not count, and does not retry implicitly; the schedule advances under the
missed-fire rule and remains founder-visible." Concretely, `schedule_runner.py`
gains a `kind`-conditional branch at each of its four `FAILED`/`TIMEOUT`
transition sites (lines 146, 201, 301, 316, 329): for `kind in (WEEKLY,
RECURRING)`, instead of setting terminal `status=FAILED`/`TIMEOUT`, it
**keeps the existing `schedule_failed`/`schedule_timeout` audit call
unchanged** (the failure is still recorded, immutably, in the audit trail
and via the run's own transcript/error fields — nothing about *observing*
the failure changes) but sets `status=ARMED` with `fire_at` recomputed to
the next eligible occurrence via `next_weekly_occurrence`/
`next_recurring_occurrence(rule, after=now)` — the **same** advance
computation §5.5 already uses for a missed fire, reused rather than
reinvented. There is **no implicit retry of the failed occurrence itself**
— the series simply continues from the next one, matching §5.5's existing
"no catch-up" discipline. `fire_count` is untouched (§5.4 — a failed attempt
never increments it).

**Founder visibility ("Needs attention") is a display computation, not a new
column or status value.** Introducing a new terminal-adjacent status (e.g.
`NEEDS_ATTENTION`) would fork the status enum for what is really a read-time
fact: the founder-visible list/detail (§8.3) computes "Needs attention" by
checking whether the **most recent** `schedule_failed`/`schedule_timeout`
audit row for this `schedule_id` is newer than the most recent
`schedule_spawned`/`schedule_fired` row — if so, the last claimed occurrence
failed and the schedule is still `ARMED` for its next one. This keeps
`ScheduleStatus` a small, closed set (§6.3's existing discipline) and reuses
the audit trail as the source of truth rather than adding a second, harder-
to-keep-consistent signal.

**Why extend this to `kind=WEEKLY` too (the "migrated weekly" question,
per the brief):** the brief asks whether migrated `weekly` rows should get
the same advance-on-failure treatment, "keep consistent/non-regressive."
**Recommendation: yes, generalize to `WEEKLY` as well as `RECURRING`.**
Today a `weekly` schedule that hits a transient executor failure dies
permanently (`FAILED`, terminal) — the same "a recurring commitment
silently stops because one execution hiccuped" problem the PRD calls out
for `RECURRING`, just already latent in the shipped `WEEKLY` path. Applying
the identical fix to both is **not a regression** (it only ever makes an
existing weekly Todo *more* resilient to a transient failure, never less)
and avoids a founder-visible inconsistency where a migrated `weekly` Todo
and a native `RECURRING` Todo with the same cadence would behave
differently on failure for no product reason. `kind=ONE_SHOT` is
deliberately excluded — it has exactly one occurrence, so there is no "next
occurrence" to advance to, and terminal-on-failure remains the correct,
unchanged v1 behavior.

## 6. Data model — additive only, zero altered/dropped columns

### 6.1 Reuse the existing `recurrence` JSON column; add one new `kind` enum member

The `schedules` table (`database.py:759-782`) already stores `recurrence`
as a free-form JSON `TEXT` column whose shape is entirely discriminated by
`kind` — today only `kind="weekly"` populates it, with the v1 shape
`{day, time, tz}` (`schedule_rules.py:24`). **v2 adds no new column for the
rule itself:** a new `kind` value, `"recurring"`, stores the §3.1 grammar in
the **same** `recurrence` column. This is the minimal additive change —
one new `ScheduleKind` enum member (`models.py:642-645`, currently
`ONE_SHOT`/`WEEKLY`) — and it exactly matches the existing pattern
("`recurrence`'s shape is a function of `kind`") rather than inventing a
parallel `recurrence_rule` column that would fork the pattern for no
functional gain.

**No existing row's `kind` or `recurrence` shape changes.** Every existing
`kind="weekly"` row keeps its `{day,time,tz}` shape and is read/evaluated by
the **unmodified** `next_weekly_occurrence` (`schedule_rules.py:136-173`)
and the **unmodified** stale-check branch in `schedule_scheduler.py:69-114`
that already special-cases `record.kind == ScheduleKind.WEEKLY`. v2 code
adds a **sibling** branch for `record.kind == ScheduleKind.RECURRING`
alongside it — additive, not a rewrite of the weekly path. The §3.1 grammar
(including the new `anchor_date` field, item 4) lives entirely inside this
same reused `recurrence` JSON column — no second JSON column, no schema
shape change beyond the one new enum member below.

### 6.2 v0/v1 runtime-compat flag (rollback safety, not schema risk) — revised, now covers the new `end_reason` column too (item 2)

Adding a **new enum member** is safe forward (old rows unaffected) but
**unsafe for a binary rollback**: a pre-v2 daemon build calling
`ScheduleKind(row["kind"])` (`schedule_store.py:75`, `_row_to_model`) on a
row with `kind="recurring"` raises `ValueError`, which today is unhandled —
it would 500 on that row (and, depending on call site, potentially the
whole `list`/`list_due` query). This is the same category of risk any
additive enum member carries (analogous to the `TaskStatus` enum being
founder-gated per MEM-044) — **not a new hazard type**, but real enough to
name explicitly: **once any `kind="recurring"` row exists, rolling the
daemon binary back to a pre-v2 build is unsafe** (merged≠deployed discipline,
MEM-077/MEM-211, now applies to a rollback direction too, not just a
forward gap). Three mitigations, all small and additive, recommended as
part of the build (not blocking sign-off, but worth founder awareness):
1. `_row_to_model` (`schedule_store.py:70-93`) wraps the `ScheduleKind(...)`
   parse and surfaces a named, catchable error per-row instead of crashing
   the whole `list`/`list_due` call — so one bad-kind row degrades
   gracefully rather than taking down the founder-visible list.
2. No code writes `kind="recurring"` until v2 ships — so the exposure
   window only opens post-deploy, by construction.
3. **The new `end_reason` column (§6.3) carries the identical rollback
   direction risk and the identical mitigation shape.** It is an **additive,
   nullable** `TEXT` column (`ALTER TABLE schedules ADD COLUMN end_reason
   TEXT NULL` — nullable so every pre-existing row, and every non-terminal
   row, is unaffected without a backfill). Forward-compat is automatic (a
   pre-v2 daemon simply never reads or writes it — SQLite tolerates an
   unknown column silently for row reads that don't `SELECT *` into a
   strict schema). The *rollback* risk is narrower than the `kind` enum
   case: an old build reading a row with a non-null `end_reason` just
   ignores the column (no parse, no enum, no `ValueError`) — so `end_reason`
   itself introduces **no new rollback hazard beyond the one already named
   for `kind="recurring"`** above. It is called out here explicitly so the
   founder's sign-off on item 3 (§10) is scoped to the complete rollback
   picture — one new enum member *and* one new nullable column — not just
   the enum member the original spike named.

### 6.3 Persisted terminal `end_reason` — not a `FIRED` broadening (revised — item 2, dev_agent seq245, product_lead seq244/248)

**The original spike's plan (reuse bare `FIRED` for both a one-shot dispatch
and a count-exhausted recurring rule) is corrected here, not merely
reworded.** dev_agent (seq245) and qa_engineer (seq243/246) both flagged
that the spike's §5.4/§6.3/§10 text still transitioned to `FIRED` with no
distinguishing fact, even though product_lead (seq235) and the EM (seq237)
had already agreed in-thread to an explicit reason — this revision makes
that agreement the actual spec text everywhere it appears, including this
section, replacing the "declared broadening" framing entirely.

**New column: `schedules.end_reason` — additive, nullable `TEXT`** (§6.2
item 3 above covers its rollback-safety profile). `ScheduleStatus.FIRED`
keeps its existing meaning ("terminal, no further action") for every
`kind`; what changes is that every transition **into** `FIRED` now also
sets `end_reason` to exactly one of:

| `end_reason` | Set when | Applies to |
|---|---|---|
| `one_shot_completed` | A `kind=ONE_SHOT` schedule's single occurrence dispatches successfully. | `ONE_SHOT` only — generalizes the *existing* one-shot terminal transition to also stamp a reason, so `FIRED` is never ambiguous for any `kind`. |
| `count_exhausted` | A successful dispatch brings `fire_count >= count` (§5.4). | `WEEKLY`/`RECURRING` with `count` set. |
| `date_ended` | `next_recurring_occurrence(rule, after=...)` (or `next_weekly_occurrence`) returns `None` **because the rule has `until` set and the walk found no further on-or-before-`until` candidate** — i.e. the series' last local-calendar-date occurrence (§5.4 "On date") has already passed. | `WEEKLY`/`RECURRING` with `until` set. |

`end_reason` is `NULL` for every non-`FIRED` status (`ARMED`, `PAUSED`,
`FIRING`, `FAILED`, `TIMEOUT`, `EXPIRED`, `CANCELLED`) — it is specifically
a **terminal-`FIRED`-reason** field, not a general status-reason field
(`EXPIRED` already has its own distinct terminal meaning and does not need
one). Each transition into `FIRED` also emits `schedule_fired` with
`end_reason` in its audit payload (§3.4) and the founder-visible detail
(§8.3) renders the reason as plain text (e.g. "Ended: reached 13 of 13
occurrences" for `count_exhausted`) instead of a bare "Fired" label — this
is the concrete fix for qa_engineer's seq243 blocker ("I cannot write a
test asserting terminal-state behavior until I know which status value
fires when count is exhausted"): the status is always `FIRED`, the
**reason** is what a test (and the founder) reads.

**`next_occurrence == None` is disambiguated by CAUSE, not collapsed to one
status (THR-105 seq254 correction — was the one remaining contradiction
between this section and §7.2/§7.3).** The next-occurrence lookup can return
`None` for three distinct reasons, and each routes to a different terminal
outcome — never inferred from the bare fact of `None` alone:

1. **`until` is exhausted** (the rule has `until` set and the walk found no
   more valid on-or-before-`until` candidate) → terminal `FIRED`,
   `end_reason=date_ended` (row above). This is the *expected*,
   designed-for terminal path for a bounded series and must never surface
   as `EXPIRED` — `EXPIRED` would discard the `date_ended` fact entirely
   (a plain "Expired" label reads as a lapsed review checkpoint, not a
   series that completed on schedule).
2. **The review/expiry checkpoint has lapsed** (`expires_at` reached and
   `indefinite` is not set, §6.4) — this is a *separate* check, made only
   when a next candidate **was** found but falls past `expires_at`; it is
   the sole remaining trigger for `EXPIRED` (§6.4, §7.6). `EXPIRED` is
   reserved strictly for this checkpoint and must not be used for normal
   end-condition exhaustion (case 1) or the defensive case (3) below.
3. **Defensive / invalid case — `None` for any other reason** (e.g. a
   malformed rule that yields no future occurrence even though `until` is
   unset or not yet reached). This should never happen for a rule that
   passed `validate_recurring_rule`/`validate_weekly_rule` (§3.2), so it is
   treated as a computation defect, not a product outcome: it transitions to
   the existing terminal `FAILED` status (§5.6) with a new, distinct
   `error="recurrence_no_candidate"` value in the schedule's existing
   `error` column (`schedule_store.py` — the same field `recover_firing`
   already sets to `"daemon_restart"`, so this reuses existing schema, adds
   no column) and the existing `schedule_failed` audit event (§3.4). Naming
   it `recurrence_no_candidate` keeps it queryable and founder-visible as
   its own distinct fact, never silently absorbed into `EXPIRED` (case 2)
   or `FIRED`/`date_ended` (case 1).

§7.2 (on-time fire path) and §7.3 (stale/missed-fire path) both apply this
same three-way disambiguation, in the same order (count check first, since
it only applies after a successful dispatch and only §7.2 has one; then
cause-of-`None` disambiguation second) — see those sections for the exact
per-path ordering.

### 6.4 Caps, floor, envelope — unchanged, reused as-is

- `MAX_ARMED_PER_AGENT=20` / `MAX_ARMED_ORG=100` (`schedule_rules.py:109-110`)
  — reused verbatim via `validate_caps` (`schedule_rules.py:113-128`); v2
  adds no new counting dimension (a `recurring` row still counts as exactly
  one `armed` row, identically to a `weekly` row).
- **90-day *horizon* check reframed as "next fire," not "series end":** the
  brief's "the 90-day one-shot horizon needs a recurrence-aware definition
  (applies to the NEXT fire, not series end)" refers to
  `validate_one_shot_horizon` (`schedule_rules.py:74-81`), which today only
  runs for `kind=ONE_SHOT`. For `kind=RECURRING`, this validator generalizes
  to check the **initial `fire_at`** (the first computed occurrence) the
  same way — ≤ 90 days out — rather than requiring the whole series
  (which may be `Ends: Never`, i.e. no series end to check) to complete
  within 90 days. This is a **distinct mechanic** from the next item.
- **90-day *review/expiry* checkpoint — mostly unchanged, now paired with a
  real renewal control (revised — item 6, see §7.6):**
  `default_expires_at`/`_RECURRING_EXPIRY_DAYS=90` (`schedule_rules.py:86-104`)
  already sets `expires_at = created_at + 90 days` for any non-`one_shot`
  kind unless `indefinite=1` (founder-only), and the spawn callback
  (`routes/schedules.py:497-501`) already expires-instead-of-re-arms when
  the next occurrence would exceed it. **v2 generalizes this from
  `kind==WEEKLY` to `kind in (WEEKLY, RECURRING)` verbatim — no behavior
  change beyond widening the `kind` check.** This *is* the "Ends: Never
  retains a periodic founder review/expiry checkpoint" safety envelope: an
  `Ends: Never` v2 rule still expires every 90 days absent `indefinite=1`,
  forcing a conscious founder re-arm. **Correction from the original
  spike:** the original text said the only re-arm path was "create a fresh
  Todo" and called a `renew` verb a non-blocking future option out of
  scope. product_lead's PRD requires renewal as part of the v2 contract
  itself (not a follow-up), and dev_agent (seq245) flagged that v1's `edit()`
  explicitly excludes `expires_at`/`indefinite` from its editable-field
  allowlist (`schedule_service.py:36,39` — "Lifecycle fields, expiry/
  indefinite are NOT editable"), so today there is genuinely no path to
  renew an about-to-expire schedule without cancelling and recreating it,
  losing its provenance/fire history. §7.6 now specifies the v2 renewal
  mechanism in full; `EXPIRED` itself still has no reactivate path (a
  review-expired Todo is not silently resumed — the founder/operator must
  renew **before** expiry, or create a fresh replacement after, per
  product_lead seq248).
- A rule with `until`/`count` set that terminates **before** the 90-day
  review mark simply reaches its natural terminal state first (`FIRED`,
  §6.3) — the review checkpoint only matters for rules that would still be
  armed past it.

## 7. Lifecycle changes — additive sibling branches, not rewrites

### 7.1 Arm (create)

`ScheduleService.create` (`schedule_service.py:56-177`) gains a third
`elif kind == ScheduleKind.RECURRING:` branch, structurally parallel to the
existing `WEEKLY` branch (`schedule_service.py:96-124`): validate via
`validate_recurring_rule` (§3.2), compute the initial `fire_at` via
`next_recurring_occurrence` (§4.2) and reject a payload-supplied `fire_at`
that doesn't match (same "the server computes and validates, the caller
doesn't get to pick a mismatched value" contract as weekly today,
`schedule_service.py:110-115`), and top-level `timezone` must equal
`rule["tz"]` (same rule as `schedule_service.py:119-124`). **The server also
computes and sets `anchor_date`** (§3.1 item 4) — the caller may not supply
it (`anchor_date_not_settable`, §3.2); it is derived from the same
computation as the initial `fire_at`, taking its local date. Caps (§6.4),
mandatory-field checks (`schedule_service.py:76-84`), and the audit row
(`schedule_created`, `schedule_service.py:163-175`) are unchanged — the
`recurrence` payload is just richer for this `kind`.

### 7.2 Fire (on-time path) — revised (item 1, item 7)

`spawn_schedule` (`routes/schedules.py:371-...`, the weekly re-arm logic at
lines 457-505) gains a `RECURRING` sibling to its `else:` (today
weekly-only) branch. **Ordered terminal-branch logic (revised — THR-105
seq254, corrects the original revision's collapsed `None` → `EXPIRED`
guard; mirrors §6.3's disambiguation exactly):**

1. **After the spawn itself has succeeded, increment `fire_count`.** If
   `count` is set and `fire_count >= count` → terminal `FIRED`,
   `end_reason=count_exhausted` (§5.4, §6.3). This check runs first and
   only after a successful dispatch, never before it and never on a failed
   one (§5.6).
2. **Else compute the next candidate occurrence:**
   `next_recurring_occurrence(rule, after=now)` (anchored at `anchor_date`,
   §3.1/§4.2). **If it returns `None` *because* `until` is exhausted** (the
   rule has `until` set and the walk found no further on-or-before-`until`
   candidate) → terminal `FIRED`, `end_reason=date_ended` (§6.3). This is
   the corrected branch: the original revision's guard ("if `None` →
   `EXPIRED`") silently discarded this fact for every `until`-bounded series
   that ran out — that guard is removed and replaced by this one.
3. **Else, if a next candidate *was* found but it falls past `expires_at`
   and `indefinite` is not set** → `EXPIRED` (mirrors the existing weekly
   guard, lines 497-...; unchanged from today, §6.4, §7.6). `EXPIRED` is
   reserved strictly for this review/expiry-checkpoint case — it is never
   reached by step 2's `until`-exhaustion path.
4. **Else, if `next_recurring_occurrence` returned `None` for any other
   reason** (a candidate was neither found nor explained by `until`
   exhaustion — should not occur for a rule that passed `validate_
   recurring_rule`, §3.2) → terminal `FAILED` with `error=
   "recurrence_no_candidate"` (§6.3's named defensive case), audited via
   the existing `schedule_failed` event (§3.4). Never silently `EXPIRED` or
   `FIRED`.
5. **Otherwise** (a next candidate was found within `expires_at`) → re-arm
   `ARMED` with the new `fire_at`.

`spawned_task_ids`/`last_fired_at`/audit rows (`schedule_spawned`,
`schedule_completed`/`schedule_expired`) are unchanged plumbing — same
fields, same audit actions, richer decision tree. A new `schedule_claimed`
audit call (§3.4) is added at the pre-existing `ARMED→FIRING` claim
(`schedule_scheduler.py:120`), for all `kind` values.

**Occurrence key / idempotent dispatch (item 7 — qa_engineer seq243/246).**
At-most-once dispatch per fire requires an atomic `ARMED→FIRING`
compare-and-set (`schedule_scheduler.py:48-50,120`; `ScheduleStore.claim_firing`):
the claim updates only a row that is still `ARMED`, and the scheduler enqueues
only when that update affects one row. A concurrent tick or restart catch-up
that loses the claim sees no affected row and does not enqueue. v2 does not
need a new persisted key or table — the occurrence key is `(schedule_id,
fire_at)` at the instant of claim, and a claimed row's `fire_at` changes on
every re-arm (§7.2 above), so a stale duplicate claim attempt against the same
pair finds the row already in `FIRING` or re-armed with a *different*
`fire_at`. This is the same mechanism for `ONE_SHOT`, `WEEKLY`, and
`RECURRING`.

### 7.3 Missed-fire / stale path

`schedule_due_schedules`'s stale branch (`schedule_scheduler.py:69-114`,
today `if record.kind == ScheduleKind.WEEKLY and ...`) gains a
`kind in (ScheduleKind.WEEKLY, ScheduleKind.RECURRING)` condition, calling
`next_weekly_occurrence` or `next_recurring_occurrence` respectively — same
skip-and-advance-or-expire policy for both (§5.5), same
`_WEEKLY_STALE_TOLERANCE` reused (possibly renamed
`_RECURRENCE_STALE_TOLERANCE` for clarity; a rename-in-place with no
behavior change, not a new constant), now also emitting `occurrence_missed`
(§3.4). **This path is distinct from, and unaffected by, §5.6's
failure-continuity change** — §5.6 covers a *claimed* run that then failed
or timed out; this section covers a fire the scheduler never got to claim
at all because the daemon was down across it. Both land on the same
"advance to next eligible occurrence, stay `ARMED`" outcome, but via
different trigger paths and different audit events (`occurrence_missed` vs.
`schedule_failed`/`schedule_timeout`).

**Terminal-branch ordering here mirrors §7.2 minus the `fire_count` step
(revised — THR-105 seq254; a stale/missed instant never dispatched, so it
never increments `fire_count`, §5.4 item 1):**

1. Compute the next candidate occurrence (`next_weekly_occurrence`/
   `next_recurring_occurrence(rule, after=now)`). **If it returns `None`
   *because* `until` is exhausted** → terminal `FIRED`,
   `end_reason=date_ended` (§6.3) — the same corrected branch as §7.2 step
   2, not `EXPIRED`.
2. **Else, if a next candidate was found but it falls past `expires_at`**
   and `indefinite` is not set → `EXPIRED` (unchanged from today's guard,
   §6.4).
3. **Else, if `None` for any other reason** (not explained by `until`
   exhaustion) → terminal `FAILED` with `error="recurrence_no_candidate"`
   (§6.3's named defensive case) — never silently `EXPIRED` or `FIRED`,
   same as §7.2 step 4.
4. **Otherwise** → advance and re-arm `ARMED` with the new `fire_at`,
   emitting `occurrence_missed` as already specified above.

### 7.4 Pause / cancel — unchanged

Both operate purely on `status`; nothing about `kind` matters
(`schedule_service.py:195-253`). No change needed.

### 7.5 Edit — revised (item 4 anchor-reset rule, item 8 edit-during-fire race)

`ScheduleService.edit` (`schedule_service.py:257-357`) gains a `RECURRING`
branch structurally identical to the `WEEKLY` one (lines 308-343): merge
edited fields onto the stored record, re-validate the **whole merged rule**
atomically (§3.2), recompute `fire_at` and reject a mismatch — "edit
re-normalizes + re-validates," exactly as the brief requires, reusing the
existing atomic-merge-then-validate shape rather than inventing a new edit
protocol. The allowed-fields list (`_ALLOWED_EDIT_FIELDS`,
`schedule_service.py:39`) stays `fire_at`, `recurrence`, `timezone` — no
new editable field is added here (review-renewal is a **separate** control,
§7.6, precisely because it touches `expires_at`/`indefinite`, which
`edit()` deliberately excludes).

**Anchor-reset rule (item 4 — dev_agent seq245, product_lead seq248):** a
timing-only edit (a change to `time` or `tz` inside `recurrence`, or to
top-level `timezone`, with `freq`/`interval`/`byday`/`bymonthday`/`ordinal`
unchanged) **preserves the existing `anchor_date`** — it recomputes future
local instants using the new time/tz but keeps the same cadence phase (a
biweekly Tuesday Todo edited from 09:00 to 14:00 still lands on the same
Tuesdays, not a newly-phased pair). A **rule-shape edit** (any change to
`freq`/`interval`/`byday`/`bymonthday`/`ordinal`) **resets `anchor_date`**
to the local date of the newly recomputed next occurrence — the old phase
is not meaningful for a different cadence shape (there is no sensible
"preserve the phase" answer when the rule itself changes from, say, weekly
Tuesdays to monthly-15th). Either way, **no retroactive occurrence is
created** — `edit()` only ever computes a next occurrence strictly after
the edit instant (existing v1 behavior, unchanged), never inserts a fire
for a date already passed. `schedule_edited` (existing action, §3.4) is
extended to audit the before/after `recurrence` (which already includes
`anchor_date`) as one atomic payload, so an anchor reset is visible in the
same audit row as the rule change that caused it — not a silent side
effect.

**Edit-during-fire race (item 8 — qa_engineer seq243/246).** The mechanism
is the existing status guard, unchanged and reused: `edit()` already
rejects with a 409 `state_conflict` (`schedule_service.py:277-281`,
`routes/schedules.py:321`) unless `record.status in (ARMED, PAUSED)` — a
row in `FIRING` (the same claim state §7.2 names) or any terminal status is
rejected outright. This is the identical check-then-write path v1 already
relies on for weekly edits; v2 adds no new synchronization primitive,
because none is needed — the race window this closes is "founder edits
while the scheduler has claimed but not yet completed a fire," and the
`FIRING` status (set atomically at claim time, §7.2) already covers that
window for every `kind`. This is named explicitly here, with its exact
file:line, specifically so qa_engineer can write the concurrency test
(claim a row into `FIRING`, then attempt a concurrent `edit()`, assert
409) rather than treating it as an unspecified mechanism.

### 7.6 Review-renewal control (new — item 6, dev_agent seq245, product_lead seq248)

**v1 has no renewal endpoint at all — this is genuinely new, not a gap-fill
in an existing one.** `edit()`'s allowlist explicitly excludes
`expires_at`/`indefinite` (`schedule_service.py:36,39`), and no other route
touches them; `indefinite=True` is only reachable through
`ScheduleService.create`'s own parameter (`schedule_service.py:67`), which
the agent-facing `ScheduleCreateBody` never exposes as a field
(`routes/schedules.py:59-77`) — so today `indefinite` is a founder/operator-
only lever with no wired path to invoke it, at create or after. product_lead's
PRD requires renewal as part of the v2 contract itself; this section
specifies it.

**New route: `POST /schedules/{schedule_id}/renew`** (`OrgDep`-authenticated,
bearer-token/org-level — the same authentication class already used by
`pause`/`cancel`/`edit`, `routes/schedules.py:230-321`, all of which
hardcode `acting_agent = f"operator@{slug}"` for audit provenance because
every caller of those routes is already the founder/operator by
construction of the auth boundary; **no new caller-identity check is
needed** — the route being bearer-token-gated rather than
session-validated is what already makes it founder/operator-only, exactly
like its siblings).

- **Allowed states:** `ARMED` or `PAUSED` only (same allowlist shape as
  `edit()`, §7.5) — `FIRING` and every terminal status (including
  `EXPIRED`) are rejected with 409 `state_conflict`. **A review-expired
  Todo is not silently resumed** (product_lead seq248): once a schedule has
  actually transitioned to `EXPIRED`, `renew` cannot bring it back — the
  founder/operator must create a replacement. This means renewal is only
  useful *before* expiry, which matches its purpose (a proactive review
  checkpoint, not a resurrection path).
- **Body:** optional `{"indefinite": bool}` (default `false`). When
  `false` (or omitted), `renew` sets `expires_at = now + 90 days`
  (`_RECURRING_EXPIRY_DAYS`, unchanged constant, §6.4) — a plain review-
  window reset. When `true`, it sets `indefinite=1` — since the route
  itself is founder/operator-only end-to-end (see above), "only
  founder/operator may mark it indefinite" (product_lead seq248) is
  satisfied by construction, not by an additional in-route identity check.
- **Effect:** updates `expires_at`/`indefinite` only — does not touch
  `fire_at`, `recurrence`, `anchor_date`, or `fire_count`; does not
  interrupt any already-computed next occurrence.
- **Audit:** `schedule_renewed` (§3.4), payload includes before/after
  `expires_at`, the `indefinite` flag, and acting identity — matching the
  PRD's "renewal/indefinite grant" audit requirement.

## 8. Surfaces (design only — build is a later, phased effort)

### 8.1 Agent create/normalization payload grammar

The `todos` skill (`protocol/skills/todos/SKILL.md`) gains a **third**
worked example (today: one-shot §"Example 1", weekly §"Example 2") showing
a `kind: "recurring"` payload with the §3.1 `recurrence` shape, plus an
explicit table of which `freq` needs which fields (§3.2), and an explicit
"if you cannot express the founder's request in this grammar (e.g. 'every
weekday except the 2nd Tuesday'), **reject at arming and ask** — do not
approximate," mirroring the existing "Ambiguous instruction" row in the
skill's failure table (`protocol/skills/todos/SKILL.md:121`). No change to
the skill's preconditions (explicit-instruction-only, self-target,
mandatory normalization) — those are kind-agnostic already. The example
payload does **not** include `anchor_date` — it is server-computed
(§3.2/§7.1) and must not appear in the agent-authored request.

### 8.2 CLI / API + OpenAPI/TS parity — revised (item 6, item 10)

`ScheduleCreateBody`/`ScheduleEditBody` (`routes/schedules.py:59-77,
263-273`) need no new Pydantic fields (`recurrence: dict` already accepts
the richer shape; `kind: str` already accepts a new string value) — but
`kind`'s implicit value set (today informally `"one_shot"|"weekly"`) should
gain an explicit note/enum in the OpenAPI schema so `"recurring"` isn't
silently undocumented. **New:** a `POST /schedules/{id}/renew` route and
its (empty-or-`{indefinite}`) body model (§7.6) is a genuinely new daemon
route, not a payload extension. **Validation errors for `kind=recurring`
now carry a stable `code`** (§3.2/§3.3) instead of the generic
`"create_failed"` wrapper (`routes/schedules.py:178`) — the OpenAPI schema
for the 422 response on `POST /schedules` should enumerate the `code`
values from §3.2 for contract stability, same discipline as the existing
named codes (`invalid_kind`, `invalid_fire_at`, etc.). Any daemon-route-
adjacent change here drifts **two** contract surfaces per MEM-094/MEM-148 —
the Python OpenAPI snapshot (`tests/contract/openapi.json`) and the `web`
mirror/coverage test (`web/src/test/openapi-coverage.test.ts`,
`web/src/lib/api/schedules.ts`, `web/src/lib/api/types.ts:819`) — both must
be regenerated in the implementing PR, including for the new `renew` route.
CLI (`cli/commands/schedules.py`) needs one new subcommand
(`todos renew <schedule_id> [--indefinite]`, mirroring the existing
`pause`/`cancel` shape) plus richer `--from-file` payload documentation
(§8.1) and, for `todos show` (`cli/commands/schedules.py:63-86`), a
human-readable rendering of the `recurring` shape (e.g. "every 2 weeks on
Mon, Wed · 09:00 Asia/Shanghai · ends after 10") **and** the "Needs
attention" computation (§5.6) when the last claimed occurrence failed.

### 8.3 Founder edit + create UI controls

Out of scope for this spike's build sequencing (backend ships first, per
the brief) — design intent only: "Repeat every [N] [day/week/month/year]"
+ conditional "Repeat on" (weekday multi-select for weekly; date-vs-
ordinal-weekday radio for monthly) + "Ends" (Never / On \<date\> / After
\<N\> occurrences radio), mapping 1:1 onto §3.1's grammar — **not** a
calendar-grid picker (§2 "Out"). The existing founder-visible list
(v1 spec §10, unbuilt-UI portion) gains: a human-readable rule string
rendering, same computation as §8.2's CLI rendering, shared between
surfaces rather than duplicated; a "Needs attention" indicator (§5.6) for a
schedule whose last claimed occurrence failed/timed out, distinct from and
without implying its (unchanged, `ARMED`) status; the terminal `end_reason`
(§6.3) rendered as plain text instead of a bare "Fired" label; and a
**Renew** action (§7.6) available exactly when Pause/Edit/Cancel are
available (`ARMED`/`PAUSED`), never on a terminal or `EXPIRED` row.

### 8.4 `todos` system skill update

Covered in §8.1 — the skill's failure-handling table (§"Failure handling —
do not guess") gains one row: invalid `freq`/`byday`/`bymonthday`/
`ordinal` combination → 422 with the specific stable `code` (§3.2/§3.3)
named, same "do not guess, ask" discipline as every other row.

## 9. Migration — clean, additive, no backfill required

### 9.1 v1 → v2 representation: a read-time projection, not a data rewrite

**Recommendation: do not rewrite existing `kind="weekly"` rows at all.**
The brief's "existing v1 weekly rows map to
`FREQ=WEEKLY;INTERVAL=1;BYDAY=...`" is satisfied as a **display/API-contract
projection**, computed at the read/serialization boundary
(`_schedule_to_dict`-equivalent in `routes/schedules.py`, and the CLI's
`todos show`/`todos list` rendering, §8.2/§8.3) — a pure function
`{day,time,tz} → {freq:"WEEKLY",interval:1,anchor_date:<row's created_at
local date>,byday:[DAY],time,tz,until:null,count:null}` used **only** for
founder-facing display, so the Todos list reads consistently in one
vocabulary. **Corrected from the original spike (item 12 — align migration
wording with the PRD):** `until` projects to `null`, not a value derived
from `expires_at` — a v1 weekly row has no recurrence-end condition at all
(it runs until cancelled), which is exactly "Ends: Never" in v2 terms,
matching the PRD's "it gains `end=never`; its existing review expiry still
applies." `expires_at` (the 90-day **review** checkpoint, §6.4) is an
unrelated field that already exists on the row unchanged — it must not be
conflated with `until` (the recurrence's own **end condition**), which the
original text's "until: derived from expires_at" phrasing incorrectly
implied. `anchor_date` in the projection is synthesized from the row's
existing `created_at` local date purely for display consistency (so a
projected row renders with the same field shape as a native `RECURRING`
row) — it is **not** written back, is never used for evaluation, and
carries no phase meaning for a `WEEKLY` row since `next_weekly_occurrence`
does not consume it. The **stored** row and the **evaluation** path
(`next_weekly_occurrence`, the `WEEKLY` branches in §7.2/§7.3) stay
byte-for-byte unchanged, "lossless" in exactly the PRD's sense — no field
is dropped or reinterpreted, only additively re-presented. This is
maximally additive: zero `UPDATE` statements against live `armed` rows,
zero risk of nudging an in-flight schedule's `fire_at`, and it directly
follows CLAUDE.md's "keep it additive/back-compat... do not drop/alter
existing columns or overload semantics without escalation."

**A physical one-time backfill (rewriting old rows to `kind="recurring"`)
is explicitly NOT recommended for v2**: it would touch live `armed`
rows for no functional gain over the projection (the evaluation code paths
would still need to stay in place for anything not yet converted, and a
backfill bug risks nudging a real agent's `fire_at`). If the founder wants
a physical convergence later (e.g. to eventually delete the `WEEKLY`-kind
code path), that is a **non-blocking, optional v2.1 follow-up**, not part
of this design's build gate — flagged as sign-off item #4 (§10) only so the
founder can veto the "never backfill" default if they disagree.

### 9.2 SCHEDULE-001 (founder's biweekly self-renewing one-shot chain)

Today's biweekly cadence is not a `weekly` Schedule at all — v1 has no
native "every 2 weeks" rule (§5 of the v1 spec explicitly excludes
"arbitrary intervals" from v1), so it is built as a manually-chained
`one_shot` Schedule: each fire's spawned task re-arms a **new** `one_shot`
row ~14 days out, a self-renewing chain rather than a single recurring
record. v2's native equivalent is `{freq:"WEEKLY", interval:2, byday:
[<the anchor weekday>], time, tz}` (or `{freq:"DAILY", interval:14}` if the
cadence isn't weekday-anchored — engineering does not have the live row's
exact shape in scope for this spike and must not guess; family_manager/
operator confirms which before cutover).

**Migration mechanism (design, not execution):**
1. Cancel the currently-armed `one_shot` chain-link row
   (`happyranch todos cancel SCHEDULE-NNN`) — this is the
   duplicate-prevention step: it stops the self-renewal *before* the native
   rule is armed, so no window exists where both the old chain and the new
   `recurring` row could each spawn an independent task for the same
   cadence.
2. **Then** arm the new `kind="recurring"` Schedule via `schedules create`,
   anchored so its first computed `fire_at` lands on the intended next
   occurrence (continuity with the cancelled chain's schedule, not a
   restart of the cadence from "now").
3. Verify no other `armed`/`paused` `one_shot` row remains that traces to
   the retired chain's lineage (`todos list --agent <family agent>
   --status armed`) before considering the cutover complete.

**This sequencing is engineering's contribution.** *Executing* it — i.e.,
deciding exactly when to cut the real `SCHEDULE-001` lineage over, and
confirming the correct anchor weekday/interval against the live row — is
explicitly **operational, owned by family_manager/the operator**, and out
of this spike's and the eventual build's scope, per the brief.

## 10. Founder sign-off gate (revised — 12 items; nothing below is assumed, all require explicit rulings)

**Revision context:** dev_agent, qa_engineer, and product_lead independently
reviewed the original 9-item version of this gate (THR-105 seq242–248).
Three of the original nine items needed correction — rollback safety (now
item 3), the `FIRED`-broadening item (now item 6, replaced), and the
PRD cross-check (now item 12, relocated and resolved) — and three genuinely
new capabilities each earned their own explicit item: failure continuity
(item 8), review-renewal (item 9), and the narrowed monthly grammar (item
10). Nothing in this renumbered list is new *scope* beyond what the brief
and the PRD already establish — it is the same contract, now stated
correctly.

1. **Add `python-dateutil` as a new top-level runtime dependency (§4.1)?**
   EM recommendation: yes — mature, RFC-conformant, removes a class of
   hand-rolled calendar bugs; the DST wrapper (§4.3) is required either way,
   so the dependency only removes calendar-arithmetic risk, not all risk.
   If declined, engineering builds the hand-rolled walker (§4.2) instead —
   more code, more edge-case tests, same external behavior.
2. **New `ScheduleKind.RECURRING` enum member + reusing the existing
   `recurrence` JSON column for the richer v2 shape, including the new
   `anchor_date` field inside it (§6.1, §3.1 item 4)?** EM recommendation:
   yes — this is the minimal additive schema footprint; the alternative (a
   new `recurrence_rule` column) adds a second parallel store for no
   behavioral gain.
3. **v0/v1 rollback-safety note (§6.2) — REVISED: now covers TWO additive
   changes, not one.** Acceptable as an operational constraint ("no
   daemon-binary rollback once a `recurring` row OR a non-null `end_reason`
   value exists"), with the three named defensive mitigations (§6.2,
   expanded from two) built as part of v2? EM recommendation: yes, all
   three mitigations are small and additive; this is not a request to
   change the schema-migration authority boundary, only to confirm the
   founder is comfortable with the rollback-direction constraint every
   additive enum member/column already carries — and to confirm awareness
   that the picture is now "one enum member + one nullable column," not
   just the enum member the original 9-item version named.
4. **Migration: read-time display projection only, no physical backfill of
   existing `weekly` rows (§9.1)?** EM recommendation: yes, as the default;
   flagged for explicit confirmation because it means the `WEEKLY`-kind
   code path stays permanently, not just transitionally. (Corrected in this
   revision: the projection's `until` field is `null`, not derived from
   `expires_at` — see §9.1.)
5. **Missed-fire policy: generalize the shipped skip-only behavior (§5.5)
   to all `freq` values, rather than introducing the brief's originally
   assumed "fire once on recovery"?** EM recommendation: yes — lowest risk,
   already proven, avoids new `count`/`until` interaction edge cases a
   catch-up-fire policy would introduce.
6. **REPLACES the original "`FIRED` broadening" item. Persisted terminal
   `end_reason` (§6.3) — a new additive nullable `schedules.end_reason`
   column carrying `one_shot_completed`/`count_exhausted`/`date_ended`,
   with matching audit events and founder-visible copy, instead of a bare
   `FIRED` status with no distinguishing fact?** EM recommendation: yes —
   this is the corrected version of what the original spike proposed;
   dev_agent, qa_engineer, and product_lead all independently required this
   exact correction (seq242–248) before they would sign off, and the EM
   agreed in-thread at seq237. Declining this would leave qa_engineer
   unable to write a terminal-state test, per their seq243 finding.
   **(seq254 follow-up: §6.3/§7.2/§7.3 now also pin that `until`-exhaustion
   routes to this `date_ended` `FIRED` reason, never to `EXPIRED`, which is
   reserved strictly for the §6.4 review/expiry checkpoint — see §6.3's
   "disambiguated by CAUSE" note for the full three-way branch.)**
7. **`Ends: On date` semantics as a local-calendar-date cutoff (in the
   rule's own `tz`), inclusive of the exact date, with the DST-boundary
   test case now specified (§5.4)?** EM recommendation: yes — matches
   founder-facing intuition for a date-picker control better than a
   UTC-instant cutoff would.
8. **NEW — Failure continuity for `WEEKLY` and `RECURRING` (§5.6): a single
   claimed occurrence's failure or timeout no longer terminates the
   schedule.** Instead of v1's current terminal `FAILED`/`TIMEOUT`
   (`schedule_runner.py:146,201,301,316,329`), the schedule stays `ARMED`,
   advances to its next eligible occurrence, and the failure is recorded
   (audited, surfaced as "Needs attention") with no implicit retry. This
   is a genuine **behavior change to existing, shipped `WEEKLY` Todos**, not
   only new `RECURRING` behavior — flagged as its own item because it is
   the one correction in this list that changes what already-armed v1 rows
   do today, not just what new v2 rows can do. `ONE_SHOT` is unchanged
   (terminal-on-failure remains correct — it has only one occurrence). EM
   recommendation: yes, per product_lead's explicit ruling at seq244/248 —
   "a recurring Todo survives an occurrence failure" is the core promise
   that makes v2 worth building over the current one_shot self-renewing
   chain workaround (THR-105 seq223–225).
9. **NEW — Review-renewal control (§7.6): a new `POST
   /schedules/{id}/renew` route, `ARMED`/`PAUSED`-only, optional
   `indefinite` flag, bearer-token/operator-authenticated the same way as
   the existing `pause`/`cancel`/`edit` routes.** v1 has no renewal path at
   all today (`edit()` explicitly excludes `expires_at`/`indefinite`,
   `schedule_service.py:36,39`), so this is new route surface, not a gap
   fill. EM recommendation: yes — without it, the 90-day review checkpoint
   (§6.4) has no supported way to be proactively renewed short of
   cancel-and-recreate (losing provenance/fire history), which product_lead
   flagged as a real usability gap in the safety envelope, not a
   nice-to-have.
10. **NEW — Bounded monthly grammar, NARROWED from the original spike
    (§3.1–3.2 item 5): exactly one `bymonthday` (single positive int
    1–31) OR one `ordinal` (named enum, never a raw signed int) + one
    `byday` token — no lists, no negative wire values.** The original
    spike's `bymonthday`/`bysetpos` fields accepted lists and negative
    ints (enabling, e.g., "the 1st and 15th" or "the penultimate weekday"),
    which is **more power than the approved cutline**. EM recommendation:
    yes, adopt the narrower grammar — dev_agent (seq245) and product_lead
    (seq248) both flagged the wider grammar as an unapproved scope
    expansion; narrowing is corrective, not a new restriction the founder
    hasn't already implicitly approved via the original bounded-scope
    framing (seq224/226).
11. **Bounded-scope confirmation (restated for the narrowed grammar):** no
    sub-daily frequency, no cross-agent scheduling, no general backlog, no
    complex calendar UI, no yearly by-month/by-day grammar beyond
    anchor-day preservation, **and now explicitly** no monthly-selector
    lists or negative wire values (item 10 above) — all as stated in the
    brief and the PRD, restated here for one consolidated approval.
12. **Cross-check against product_lead's parallel v2 PRD — RESOLVED, not
    open.** The original item 9 above flagged that engineering had not yet
    seen the PRD's final text. That gap is now closed: dev_agent,
    qa_engineer, and product_lead independently reviewed both documents
    together (THR-105 seq242–248) and converged on the corrections in this
    revision — After-N mechanism, failure continuity, persisted
    `end_reason`, cadence anchor, bounded monthly grammar, and
    review-renewal are now identical in both documents. **What remains for
    the founder is a single ratification of the reconciled contract**, not
    a request to adjudicate a divergence — this spike and the PRD no longer
    disagree on any load-bearing semantic as of this revision.

**Build gate:** implementation does not start until every item above has an
explicit founder ruling **and** product_lead's revised v2 PRD is separately
signed off, per the brief. Both documents' next sign-offs are dev_agent's
and qa_engineer's — their original REQUEST CHANGES (seq242/243/245/246) are
addressed by this revision; they review the revised text before the founder
rules. The build then lands as the normal phased dev → code_reviewer → qa
merge gate, with `protocol/05b-agent-runtime.md` and
`protocol/skills/todos/SKILL.md` updated in the same PR(s) as the behavior
they describe (doc parity), and any step that appears to require touching
an existing schema column, the `audit_log` scope convention, auth/
notification routing, or a permission-generation surface must STOP and
escalate (v1 spec §4 boundary, unchanged for v2). The new additive
`end_reason` column and the new `POST /renew` route are **not** exceptions
to that boundary — they are additive schema and a new route, not an altered
column or a permission-model change, and stay inside standing EM authority
to design; only their *build* still needs the founder's dependency and
sign-off rulings above before it starts.

## 11. Non-goals (v2 no-list, consolidated)

- No sub-daily / cron / arbitrary-second frequency (frequency floor is
  `DAILY`, enforced by the grammar itself having no finer `freq` value).
- No cross-agent scheduling (self-target only, unchanged from v1).
- No general unscheduled backlog (every Todo still has a `fire_at`).
- No complex calendar-grid UI (a structured repeat-rule editor, §8.3).
- No yearly by-month/by-day grammar beyond anchor-day preservation (§3.1) —
  "3rd Tuesday of every year" is out of the bounded scope.
- No re-introduction of the removed per-agent scheduling allowlist
  (THR-105 seq217 / PR #606 stays retired).
- No physical backfill migration of existing `weekly` rows by default
  (§9.1) — a read-time projection only, unless the founder overrides
  sign-off item #4.
- No monthly-selector lists or negative wire values (§3.1–3.2 item 5) —
  exactly one `bymonthday` or one `ordinal`, never multiple dates/positions
  in a single rule, and never a raw signed int for "last."
- No implicit retry of a failed/timed-out occurrence (§5.6) — failure
  continuity advances to the *next* eligible occurrence, it never re-attempts
  the one that failed.
- No silent resumption of a review-expired Todo (§7.6) — renewal is only
  possible before expiry (`ARMED`/`PAUSED`); an already-`EXPIRED` row stays
  terminal and requires a fresh replacement.
- No new or altered permission-generation surface, no schema-column
  drop/alter, no `audit_log.task_id` scope-semantics change (§6 — additive
  `kind` enum member, additive nullable `end_reason` column, and reused
  JSON column only, §10 items 2/3/6).
