# Agent Todos — Recurrence v2 (Flexible / Google-Calendar-style) — Design Spike

**Date:** 2026-08-10
**Status:** DESIGN-ONLY. No code, no build, no PR beyond this document. Founder
authorized the bounded scope + this spec's path at THR-105 seq226 ("ok go
ahead"); product_lead framed scope at seq224; engineering read the shipped
runtime at seq225. **Implementation does not start until BOTH this spike and
product_lead's parallel v2 PRD are founder-signed-off** (§9).
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
/ `BYSETPOS` / `UNTIL` / `COUNT` — rather than inventing a bespoke grammar.
Rationale: it is a well-understood, testable standard; "skip nonexistent
monthly dates" (§5.1) is RRULE's own documented behavior, not a HappyRanch
invention; and it gives a natural, auditable string form (`FREQ=MONTHLY;
INTERVAL=1;BYDAY=MO;BYSETPOS=2`) for the founder-visible list (§8) even
though the **stored** representation stays a JSON dict (consistent with v1's
`recurrence` column, which is already JSON — see §6).

### 3.1 Grammar (stored/wire shape)

```json
{
  "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
  "interval": 1,
  "byday": ["MO", "WE", "FR"],
  "bymonthday": [15],
  "bysetpos": [2],
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
| `byday` | list[weekday token] | `WEEKLY` (1+ tokens, required) or `MONTHLY`-ordinal (exactly 1 token, paired with `bysetpos`) | Forbidden for `DAILY`/`YEARLY`. Tokens: `MO TU WE TH FR SA SU`. |
| `bymonthday` | list[int, 1–31 or -31..-1] | `MONTHLY`-by-date only | Mutually exclusive with `byday`/`bysetpos`. Negative = counted from month end (`-1` = last day). |
| `bysetpos` | list[int, 1–5 or -5..-1] | `MONTHLY`-ordinal only | Requires exactly one `byday` token. `2` = 2nd, `-1` = last. |
| `time` | `HH:MM` | all | Same shape as v1's `recurrence.time`. |
| `tz` | IANA string | all | Same validation as v1's `recurrence.tz` (`ZoneInfo(tz)` must construct). |
| `until` | `YYYY-MM-DD` \| null | all | "Ends: On date" — a **local calendar date** in `tz`, inclusive (§5.4). Mutually exclusive with `count`. |
| `count` | int ≥ 1 \| null | all | "Ends: After N occurrences." Mutually exclusive with `until`. Both null = "Ends: Never" (§7.1). |

`YEARLY` carries no `byday`/`bymonthday`/`bysetpos` — the occurrence day is
always the anchor `fire_at`'s own month/day (§3.1); this keeps yearly inside
the bounded scope instead of growing a second by-month grammar nobody asked
for.

### 3.2 Validation surface (additive, unit-testable, no I/O)

A new `validate_recurring_rule(rule: dict) -> str | None` function living
beside `validate_weekly_recurrence` in `schedule_rules.py`
(`runtime/orchestrator/schedule_rules.py:27`, same file, same no-I/O
contract as the existing v1 validators at lines 27–66). It rejects, per
`freq`:

- `DAILY`: `byday`/`bymonthday`/`bysetpos` must all be absent.
- `WEEKLY`: `byday` required, 1–7 distinct tokens; `bymonthday`/`bysetpos`
  absent.
- `MONTHLY`: **exactly one** of (`bymonthday` present, 1+ ints, each
  nonzero and in `[-31,-1]∪[1,31]`) **or** (`byday` present with exactly
  one token **and** `bysetpos` present with exactly one int in
  `[-5,-1]∪[1,5]`). Neither-or-both is rejected — an "actionable" 422 per
  MEM-246, naming which combination is missing/conflicting.
- `YEARLY`: `byday`/`bymonthday`/`bysetpos` must all be absent.
- `interval`: any `freq`, must be a positive int; the existing per-kind
  minimum-interval concept in the v1 spec (§9 item 3, "weekly is inherently
  ≥7-day") generalizes cleanly: `interval` has no independent floor beyond
  ≥1, because the floor is already enforced by `freq` excluding sub-daily
  units — a `DAILY` rule with `interval=1` (fires every day) is the fastest
  legal cadence in v2, same floor as today's implicit weekly-only floor,
  just now reachable directly instead of only via `weekly`.
- `until`/`count`: at most one set; `until` must parse as `YYYY-MM-DD` and
  not be in the past (compared in `tz`); `count` must be ≥ 1.
- `time`/`tz`: byte-identical validation to `validate_weekly_recurrence`
  (`schedule_rules.py:47-64`) — reused, not reimplemented.

## 4. Evaluation: library vs. hand-rolled

### 4.1 Recommendation: `python-dateutil`'s `rrule`

`python-dateutil.rrule` implements RFC 5545 `RRULE` semantics directly —
`FREQ`, `INTERVAL`, `BYDAY` (including ordinal-prefixed tokens),
`BYMONTHDAY`, `BYSETPOS`, `UNTIL`, `COUNT` map onto its constructor
almost verbatim. It is mature (used by, among others, Google's own RFC 5545
tooling lineage), has correct month-skip and leap-day behavior out of the
box, and removes an entire class of calendar-arithmetic bugs (day-31-in-Feb,
5th-Monday-doesn't-exist, BYSETPOS counting) that a hand-rolled walker would
have to reimplement and re-prove correct.

**⚠️ FOUNDER-GATED: this is a new top-level Python dependency.**
`pyproject.toml` today declares exactly nine runtime dependencies — pydantic,
pydantic-settings, pyyaml, fastapi, uvicorn, sse-starlette, httpx,
websockets, python-multipart (`pyproject.toml:5-15`) — no calendar/RRULE
library. Per CLAUDE.md ("Add a top-level Python or npm dependency without
founder approval" is outside EM authority), adding `python-dateutil` is
**sign-off item #1 (§9)**, not an assumption baked into this design.

### 4.2 Fallback: hand-rolled bounded walker

If the founder declines the dependency, a hand-rolled `next_recurring_
occurrence(rule, after) -> datetime | None` is feasible **because the
grammar is already bounded** (§3): it is the same "walk forward day-by-day,
capped at N iterations so a misconfigured rule can never loop forever"
shape as the existing `next_weekly_occurrence` (`schedule_rules.py:136-173`,
366-day cap), generalized to also step by month/year and to evaluate
`BYMONTHDAY`/`BYSETPOS` against each candidate month. It is more code
(month-length tables, ordinal-weekday counting, leap-year Feb-29 handling)
and each of those becomes a hand-written edge case to prove correct in
tests, versus RFC-conformance already proven upstream. **Recommendation:
`python-dateutil` unless the founder has a standing no-new-deps posture**;
either way, the DST wrapper in §4.3 is required regardless of which engine
supplies the calendar series, so the fallback is not "avoid all new
surface area," only "avoid the one dependency line."

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
path (§7.5), returning `None` (skip this one occurrence, keep walking) when
the candidate falls in a spring-forward gap, and using Python's default
`fold=0` (PEP 495 "first occurrence") for a fall-back ambiguous hour.

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

### 5.4 "Ends: On date" / "Ends: After N"

- **On date:** the series' last occurrence is the last one whose **local
  date** (in the rule's `tz`) is `<= until`. This mirrors RRULE `UNTIL`
  semantics with the local-date framing the founder-facing "Ends on
  \<date\>" control implies (not a UTC-instant cutoff, which would silently
  clip the last occurrence early/late depending on tz offset).
- **After N:** exactly `count` fires, then terminal. Implemented by reusing
  the **existing** `fire_count` column (`database.py:775`, already
  incremented on every fire — `routes/schedules.py:434`) — no new column.
  Terminal check: `fire_count >= count` after incrementing → transition to
  `FIRED` (§6.3 — a deliberate, explicitly-declared broadening of what
  `FIRED` means for a bounded recurring rule, not a silent overload; see §6.3).

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
**policy change to flag explicitly at sign-off (§9 item 5)**, not something
this spike assumes.

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
alongside it — additive, not a rewrite of the weekly path.

### 6.2 v0/v1 runtime-compat flag (rollback safety, not schema risk)

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
forward gap). Two mitigations, both small and additive, recommended as part
of the build (not blocking sign-off, but worth founder awareness):
1. `_row_to_model` (`schedule_store.py:70-93`) wraps the `ScheduleKind(...)`
   parse and surfaces a named, catchable error per-row instead of crashing
   the whole `list`/`list_due` call — so one bad-kind row degrades
   gracefully rather than taking down the founder-visible list.
2. No code writes `kind="recurring"` until v2 ships — so the exposure
   window only opens post-deploy, by construction.

### 6.3 `FIRED` status broadening (declared, not silent)

`ScheduleStatus.FIRED` currently means "one-shot terminal"
(`models.py:648-651` docstring). §5.4's "Ends: After N" reaches the same
`FIRED` value when `count` is exhausted. This is a **declared broadening**
of an existing enum value's *meaning* ("no more fires will ever happen"),
not a scope-prefix-style overload like `audit_log.task_id` (MEM-075) — the
enum stays a small closed set consumed only by this module, and the new
usage is consistent with the old ("terminal, no further action") rather
than contradicting it. Flagged here per the discipline of naming every
status/column reinterpretation explicitly rather than letting it ride in
silently; **not** requesting it be treated as founder-gated schema/overload
territory, since it changes no column shape and no cross-module contract.

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
- **90-day *review/expiry* checkpoint — unchanged, reused as-is:**
  `default_expires_at`/`_RECURRING_EXPIRY_DAYS=90` (`schedule_rules.py:86-104`)
  already sets `expires_at = created_at + 90 days` for any non-`one_shot`
  kind unless `indefinite=1` (founder-only), and the spawn callback
  (`routes/schedules.py:497-501`) already expires-instead-of-re-arms when
  the next occurrence would exceed it. **v2 generalizes this from
  `kind==WEEKLY` to `kind in (WEEKLY, RECURRING)` verbatim — no behavior
  change beyond widening the `kind` check.** This *is* the "Ends: Never
  retains a periodic founder review/expiry checkpoint" safety envelope: an
  `Ends: Never` v2 rule still expires every 90 days absent
  `indefinite=1`, forcing a conscious founder re-arm (today: creating a
  fresh Todo — `EXPIRED` has no reactivate path, matching v1's existing
  terminal-state design; a `renew` verb is a plausible follow-up but is
  **not** requested by this brief and is called out only as a non-blocking
  future option, not part of this spike's scope).
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
`rule["tz"]` (same rule as `schedule_service.py:119-124`). Caps (§6.4),
mandatory-field checks (`schedule_service.py:76-84`), and the audit row
(`schedule_created`, `schedule_service.py:163-175`) are unchanged — the
`recurrence` payload is just richer for this `kind`.

### 7.2 Fire (on-time path)

`spawn_schedule` (`routes/schedules.py:371-...`, the weekly re-arm logic at
lines 457-505) gains a `RECURRING` sibling to its `else:` (today
weekly-only) branch: compute `next_recurring_occurrence(rule, after=now)`;
if `None` → `EXPIRED` (mirrors the existing "could not compute next
occurrence" guard, lines 473-496); if past `expires_at` and not
`indefinite` → `EXPIRED` (mirrors lines 497-...); else check `until`/`count`
termination (§5.4) → `FIRED` if exhausted, else re-arm `ARMED` with the new
`fire_at`. `fire_count`/`spawned_task_ids`/`last_fired_at`/audit rows
(`schedule_spawned`, `schedule_completed`/`schedule_expired`) are unchanged
plumbing — same fields, same audit actions, richer decision tree.

### 7.3 Missed-fire / stale path

`schedule_due_schedules`'s stale branch (`schedule_scheduler.py:69-114`,
today `if record.kind == ScheduleKind.WEEKLY and ...`) gains a
`kind in (ScheduleKind.WEEKLY, ScheduleKind.RECURRING)` condition, calling
`next_weekly_occurrence` or `next_recurring_occurrence` respectively — same
skip-and-advance-or-expire policy for both (§5.5), same
`_WEEKLY_STALE_TOLERANCE` reused (possibly renamed
`_RECURRENCE_STALE_TOLERANCE` for clarity; a rename-in-place with no
behavior change, not a new constant).

### 7.4 Pause / cancel — unchanged

Both operate purely on `status`; nothing about `kind` matters
(`schedule_service.py:195-253`). No change needed.

### 7.5 Edit

`ScheduleService.edit` (`schedule_service.py:257-357`) gains a `RECURRING`
branch structurally identical to the `WEEKLY` one (lines 308-343): merge
edited fields onto the stored record, re-validate the **whole merged rule**
atomically (§3.2), recompute `fire_at` and reject a mismatch — "edit
re-normalizes + re-validates," exactly as the brief requires, reusing the
existing atomic-merge-then-validate shape rather than inventing a new edit
protocol.

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
mandatory normalization) — those are kind-agnostic already.

### 8.2 CLI / API + OpenAPI/TS parity

`ScheduleCreateBody`/`ScheduleEditBody` (`routes/schedules.py:59-77,
263-273`) need no new Pydantic fields (`recurrence: dict` already accepts
the richer shape; `kind: str` already accepts a new string value) — but
`kind`'s implicit value set (today informally `"one_shot"|"weekly"`) should
gain an explicit note/enum in the OpenAPI schema so `"recurring"` isn't
silently undocumented. Any daemon-route-adjacent change here drifts **two**
contract surfaces per MEM-094/MEM-148 — the Python OpenAPI snapshot
(`tests/contract/openapi.json`) and the `web` mirror/coverage test
(`web/src/test/openapi-coverage.test.ts`, `web/src/lib/api/schedules.ts`,
`web/src/lib/api/types.ts:819`) — both must be regenerated in the
implementing PR. CLI (`cli/commands/schedules.py`) needs no new
subcommands, only richer `--from-file` payload documentation (§8.1) and,
for `todos show` (`cli/commands/schedules.py:63-86`), a human-readable
rendering of the `recurring` shape (e.g. "every 2 weeks on Mon, Wed ·
09:00 Asia/Shanghai · ends after 10").

### 8.3 Founder edit + create UI controls

Out of scope for this spike's build sequencing (backend ships first, per
the brief) — design intent only: "Repeat every [N] [day/week/month/year]"
+ conditional "Repeat on" (weekday multi-select for weekly; date-vs-
ordinal-weekday radio for monthly) + "Ends" (Never / On \<date\> / After
\<N\> occurrences radio), mapping 1:1 onto §3.1's grammar — **not** a
calendar-grid picker (§2 "Out"). The existing founder-visible list
(v1 spec §10, unbuilt-UI portion) gains a human-readable rule string
rendering, same computation as §8.2's CLI rendering, shared between
surfaces rather than duplicated.

### 8.4 `todos` system skill update

Covered in §8.1 — the skill's failure-handling table (§"Failure handling —
do not guess") gains one row: invalid `freq`/`byday`/`bymonthday`/
`bysetpos` combination → 422 with the specific missing/conflicting field
named (§3.2), same "do not guess, ask" discipline as every other row.

## 9. Migration — clean, additive, no backfill required

### 9.1 v1 → v2 representation: a read-time projection, not a data rewrite

**Recommendation: do not rewrite existing `kind="weekly"` rows at all.**
The brief's "existing v1 weekly rows map to
`FREQ=WEEKLY;INTERVAL=1;BYDAY=...`" is satisfied as a **display/API-contract
projection**, computed at the read/serialization boundary
(`_schedule_to_dict`-equivalent in `routes/schedules.py`, and the CLI's
`todos show`/`todos list` rendering, §8.2/§8.3) — a pure function
`{day,time,tz} → {freq:"WEEKLY",interval:1,byday:[DAY],time,tz,until:
<derived from expires_at or null>,count:null}` used **only** for founder-
facing display, so the Todos list reads consistently in one vocabulary.
The **stored** row and the **evaluation** path (`next_weekly_occurrence`,
the `WEEKLY` branches in §7.2/§7.3) stay byte-for-byte unchanged. This is
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

## 10. Founder sign-off gate (nothing below is assumed; all require explicit rulings)

1. **Add `python-dateutil` as a new top-level runtime dependency (§4.1)?**
   EM recommendation: yes — mature, RFC-conformant, removes a class of
   hand-rolled calendar bugs; the DST wrapper (§4.3) is required either way,
   so the dependency only removes calendar-arithmetic risk, not all risk.
   If declined, engineering builds the hand-rolled walker (§4.2) instead —
   more code, more edge-case tests, same external behavior.
2. **New `ScheduleKind.RECURRING` enum member + reusing the existing
   `recurrence` JSON column for the richer v2 shape (§6.1)?** EM
   recommendation: yes — this is the minimal additive schema footprint;
   the alternative (a new `recurrence_rule` column) adds a second parallel
   store for no behavioral gain.
3. **v0/v1 rollback-safety note (§6.2) — acceptable as an operational
   constraint ("no daemon-binary rollback once a `recurring` row exists"),
   with the two named defensive mitigations built as part of v2?** EM
   recommendation: yes, both mitigations are small and additive; this is
   not a request to change the schema-migration authority boundary, only
   to confirm the founder is comfortable with the rollback-direction
   constraint every additive enum member already carries.
4. **Migration: read-time display projection only, no physical backfill of
   existing `weekly` rows (§9.1)?** EM recommendation: yes, as the default;
   flagged for explicit confirmation because it means the `WEEKLY`-kind
   code path stays permanently, not just transitionally.
5. **Missed-fire policy: generalize the shipped skip-only behavior (§5.5)
   to all `freq` values, rather than introducing the brief's originally
   assumed "fire once on recovery"?** EM recommendation: yes — lowest risk,
   already proven, avoids new `count`/`until` interaction edge cases a
   catch-up-fire policy would introduce.
6. **`FIRED` status broadening to also mean "count exhausted" (§6.3) —
   acceptable as declared reuse, not requiring a new terminal status
   value?** EM recommendation: yes.
7. **`Ends: On date` semantics as a local-calendar-date cutoff (in the
   rule's own `tz`), inclusive of the exact date (§5.4)?** EM
   recommendation: yes — matches founder-facing intuition for a
   date-picker control better than a UTC-instant cutoff would.
8. **Bounded-scope confirmation:** no sub-daily frequency, no cross-agent
   scheduling, no general backlog, no complex calendar UI, no yearly
   by-month/by-day grammar beyond anchor-day preservation (§2, §3.1) — all
   as stated in the brief, restated here for one consolidated approval.
9. **Cross-check against product_lead's parallel v2 PRD:** this spike was
   authored in parallel with product_lead's PRD per the brief; engineering
   has not seen the PRD's final text as of this writing. **Any divergence
   between this spec and the PRD (scope, terminology, default values, or
   the "Ends" UI framing) must be reconciled before either is signed off**
   — flagging this explicitly rather than assuming alignment, consistent
   with the "flag divergence, don't silently pick a side" discipline from
   the THR-152 experience (do not treat this spec as implicitly overriding
   product framing, or vice versa).

**Build gate:** implementation does not start until every item above has an
explicit founder ruling **and** product_lead's v2 PRD is separately
signed off, per the brief. The build then lands as the normal phased
dev → code_reviewer → qa merge gate, with `protocol/05b-agent-runtime.md`
and `protocol/skills/todos/SKILL.md` updated in the same PR(s) as the
behavior they describe (doc parity), and any step that appears to require
touching an existing schema column, the `audit_log` scope convention,
auth/notification routing, or a permission-generation surface must STOP
and escalate (v1 spec §4 boundary, unchanged for v2).

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
- No new or altered permission-generation surface, no schema-column
  drop/alter, no `audit_log.task_id` scope-semantics change (§6, §10 item
  2 — additive `kind` enum member + reused JSON column only).
