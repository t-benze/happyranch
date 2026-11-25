---
name: todos
description: Use when you need to create a scheduled Todo for yourself from explicit founder or operator instruction. Never infer or proactively schedule future work. Scheduling is self-only and available to every valid in-org agent.
---


Payload paths use `${TMPDIR:-/tmp}`. Expand it to an absolute path before a
file-tool write, and keep the shell command path quoted.

## Current names and permanent automation IDs

Fresh prompts may show current Name · ID for this turn. Use the permanent ID
from the injected parameters for callback agent/composer/speaker, task/session/
invocation proofs, configuration and delegate/then/fanout targets. A name is
human-addressing/presentation data, never actor authority. Human destination
arguments and @mentions accept current names or IDs; former names refuse with
the current name and do not forward. Founder is a separate human; recipient
transport remains `@founder`. Metadata failure gives ID-only context.

Human Todo-list filters accept current names; `schedules create/spawn` payload agent and self/session checks remain permanent-ID-only. No scheduling authority changes.


# Todos — Agent-owned scheduled commitments (THR-105 v2)

Preparation and recovery refusals belong to the maintained
[runtime lifecycle contract](../../../../docs/agent-guides/contracts/runtime-lifecycle.md).
A failed create is not authority to repair ownership, invent catch-up, shift
recurrence or create a replacement schedule; report the actual refusal.

Agents may create `one_shot`, `weekly`, and `recurring` self-scheduled Todos via the
`schedules create` callback. This is a **self-only, explicit-instruction,
session-bound** operation available to every valid in-org agent. You cannot
schedule another agent, you cannot infer or proactively create future work, and
the skill body is discoverability only — it does not bypass server validation.

## Preconditions

1. **Founder/operator instruction only.**  Only create a Todo when the
   founder or an operator has explicitly told you to schedule something.
   Never infer, guess, or proactively arm a schedule.

2. **You must be a valid in-org caller.** Every agent with an active session
   and a resolvable in-org team may create a self-owned Todo. Legacy
   `scheduling.enabled_agents` configuration is accepted but has no effect.

3. **Self-target only.**  Agents must create Todos only for themselves
   and must never target another agent.  The `agent` field must match
   your own permanent agent ID — the server validates it against the active
   session.

4. **Normalize before arming.**  Before firing the callback, turn the
   founder/operator instruction into a concrete, founder-reviewable
   `normalized_brief` — a self-contained task brief that any engineer
   reading the Todo list would understand without extra context:
   "Run the weekly user-growth report and post it to #metrics" is good;
   "do the thing from yesterday" is not.  `source_instruction` records
   the original request with outer whitespace trimmed; it is immutable after creation.

## The single-line callback

```bash
happyranch schedules create --org {ORG_SLUG} --from-file "${TMPDIR:-/tmp}/schedule-<task_id>.json"
```

Callback writes MUST be single-line and use `--from-file` with a pre-written
JSON payload file. Multi-line bash is rejected by executor permission matchers.
Read-only commands such as `todos show --json` do not take `--from-file`.

## Payload shape (`--from-file` file)

```json
{
  "task_id":      "TASK-XXX",
  "session_id":   "sess-…",
  "agent":        "your_permanent_agent_id",
  "source_instruction": "original founder/operator instruction",
  "normalized_brief":   "self-contained, reviewable task brief",
  "kind":         "one_shot | weekly | recurring",
  "fire_at":      "2026-08-07T18:00:00+08:00",
  "recurrence":   null,
  "timezone":     "UTC"
}
```

| Field | Required | Notes |
| --- | :---: | --- |
| `task_id` | ✓ | Your current task id |
| `session_id` | ✓ | Your current session id |
| `agent` | ✓ | Your injected permanent agent ID (validated against the active session); never substitute a displayed name |
| `source_instruction` | ✓ | Original instruction with outer whitespace trimmed — kept for audit, immutable afterward |
| `normalized_brief` | ✓ | Self-contained, founder-reviewable brief — immutable after creation |
| `kind` | ✓ | `"one_shot"`, `"weekly"`, or `"recurring"` |
| `fire_at` | ✓ except native recurring with `start_date` | ISO-8601 with an **explicit timezone offset** (`+00:00`, `+08:00`, or `Z`); omit for the server-derived phase form below |
| `recurrence` | one_shot: omit/null; weekly/recurring: required | See recurrence section |
| `timezone` | default `"UTC"` | Must equal `recurrence.tz` for weekly and recurring schedules |
| `start_date` | native recurring only, optional | Canonical local `YYYY-MM-DD` phase; never send for one-shot or weekly |

**Extra fields are forbidden.**  The server responds 422 for unrecognized keys.

### One-shot (`kind: "one_shot"`)

- `recurrence` **must** be `null` or omitted.
- `fire_at` must be in the future, within 90 days from now.
- Format: `YYYY-MM-DDTHH:MM:SS±HH:MM` or `…T…Z` (UTC).

### Weekly (`kind: "weekly"`)

- `recurrence` must be exactly:
  ```json
  {"day": "mon", "time": "09:00", "tz": "Asia/Shanghai"}
  ```
  where `day` is a single lowercase weekday (`mon`–`sun`), `time` is
  24-hour `HH:MM`, and `tz` is a valid IANA timezone string.

- `timezone` **must equal** `recurrence.tz` — the server validates this.
- `fire_at` **must be the next computed occurrence** of the recurrence
  (server-validated; the server rejects mismatches with a diagnostic
  showing the expected ISO-8601 value).

### Recurring (`kind: "recurring"`)

Use this exact bounded recurrence object. The server computes and stores
`anchor_date`; do **not** send it in an agent-authored create payload.

For an explicitly instructed local phase, send top-level `start_date` and
**omit** `fire_at`. The daemon validates that date against the complete rule
(including DST), derives the first UTC instant, and persists only its managed
`anchor_date`. A supplied `fire_at` is assertion-only; agents should not
calculate it. Without `start_date`, retain the legacy rule: `fire_at` is
required and must exactly match the server's next candidate.

| `freq` | Required fields | Forbidden fields |
| --- | --- | --- |
| `DAILY` | `interval`, `time`, `tz` | `byday`, `bymonthday`, `ordinal` |
| `WEEKLY` | `interval`, non-empty distinct `byday` (`MO`–`SU`), `time`, `tz` | `bymonthday`, `ordinal` |
| `MONTHLY` | `interval`, `time`, `tz`, and exactly one selector below | See below |
| `YEARLY` | `interval`, `time`, `tz` | `byday`, `bymonthday`, `ordinal` |

For `MONTHLY`, choose exactly one selector:

- `bymonthday`: one integer from `1` through `31`; or
- `byday`: exactly one `MO`–`SU` token **and** `ordinal`: one of `first`,
  `second`, `third`, `fourth`, `fifth`, or `last`.

`interval` is a positive integer. `time` is `HH:MM`; `tz` is an IANA timezone.
The only end conditions are: omit both `until` and `count` for never; set
`until` to one inclusive local `YYYY-MM-DD` date; or set `count` to a positive
integer for that many **successful dispatches**. Never set both. `count` is
not a calendar-candidate count and must never be expressed as RRULE `COUNT`.

The agent-authored grammar has no lists of monthly dates, negative wire values,
cron/sub-daily fields, yearly selectors, or alternate field names. If the
instruction cannot be expressed exactly (for example, "every weekday except
the second Tuesday"), do not approximate it: ask for clarification before
arming.

`timezone` must equal `recurrence.tz`. For a native recurring payload with
`start_date`, omit `fire_at` and never calculate recurrence or DST. Without
`start_date`, the legacy `fire_at` remains required and must exactly match the
server's next computed occurrence.

- **Default expiry:** 90 days from creation.  The server does not
  accept `"indefinite": true` from the agent callback — indefinite
  expiry is founder-set only.

## Caps (v1)

| Cap | Limit |
| --- | --- |
| Armed Todos per agent | 20 |
| Armed Todos org-wide | 100 |

Cap violations return 409 with an actionable message — pause or cancel an
existing Todo to make room.

## Failure handling — do not guess

| Failure class | Server response | What you do |
| --- | --- | --- |
| Session mismatch / unknown | 409 `session_mismatch` / `unknown_session` | Correct a local transcription error only to the injected task/agent/SID. If that exact triple is refused, stop and report under the [start-task session contract](../start-task/SKILL.md#error-handling); never retry or adopt a returned active SID from another launch |
| Agent team unresolved | 409 `agent_team_unresolved` | Do not arm; report the in-org identity/configuration problem |
| Invalid `kind` | 422 `invalid_kind` | Only `one_shot`, `weekly`, and `recurring` are valid |
| Malformed `fire_at` or missing timezone offset | 422 `invalid_fire_at` | Correct the ISO-8601 timestamp; always include an offset |
| One-shot `fire_at` is past or more than 90 days ahead | 409 `create_failed` with the service diagnostic | Correct the timestamp only when the explicit instruction supports it; otherwise report the diagnostic and ask for clarification |
| Invalid recurring grammar or phase | 422 with one of `invalid_freq_fields`, `invalid_byday`, `monthly_selector_missing`, `monthly_selector_conflict`, `invalid_interval`, `anchor_date_not_settable`, `invalid_start_date`, `invalid_until`, `invalid_count`, `end_condition_conflict`, `invalid_time`, or `invalid_timezone` | Do not guess or retry a different grammar; correct only from the explicit instruction, or ask for clarification |
| Cap exceeded | 409 `create_failed` | Report which cap; ask the founder which Todo to pause/cancel |
| Ambiguous instruction | (your guard — don't call) | **Ask for clarification** — do NOT guess the date, timezone, cadence, or instruction. Escalate rather than arm nonsense. |

## On success

The create CLI prints only ID/kind/status/agent. After a successful create, read
the stored schedule before confirming it:

```bash
happyranch todos show <SCHEDULE-ID> --org {ORG_SLUG} --json
```

Use this server readback for `fire_at` and `timezone`, especially when
`start_date` lets the server derive the first instant. The response includes:

- `schedule_id` (e.g., `SCHEDULE-007`)
- `kind`, `normalized_brief`, `timezone`
- `fire_at` (the next fire, returned as server-validated ISO-8601)
- `status: "armed"`, `active: 1` (`active` is the integer 0/1 wire field)

Report to the founder: the `schedule_id`, the normalized commitment,
kind, timezone, and next fire time.  Let them know:

> ✅ **Armed.**  Founders can inspect all Todos through the Todos list
> surface and pause, cancel, or edit timing/recurrence/timezone through
> the Todos controls.  Original `source_instruction` and `normalized_brief`
> are immutable and cannot be edited through founder controls.

**Founder management (separate from agent create):** The Todos surface provides list, show/id detail, pause, cancel, and edit (timing/recurrence/timezone only) operations.  Do NOT use `todos create` — agent creation is `schedules create`.

Pause requires an armed Todo; re-pausing a paused Todo is idempotent. Cancel
and edit require armed or paused status. Pause/cancel refuse a concurrent status
change, and edit also refuses a changed occurrence (`fire_at`/`fire_count`), using
the existing 409 `state_conflict` response without a management audit entry.
Terminal transitions clear `active`, including one-shot failure/timeout and
daemon-restart recovery.

## Examples

### Example 1 — Follow up in 48 hours (one-shot)

**Context:** The founder says "follow up on TASK-4000 in 48 hours."

Write `${TMPDIR:-/tmp}/schedule-TASK-4317.json`:

```json
{
  "task_id": "TASK-4317",
  "session_id": "sess-b86b2a3404d740179c23871958c9c7db",
  "agent": "dev_agent",
  "source_instruction": "follow up on TASK-4000 in 48 hours",
  "normalized_brief": "Review TASK-4000 status and report any blockers or completion to the founder in #engineering",
  "kind": "one_shot",
  "fire_at": "2026-08-06T18:40:00+08:00",
  "recurrence": null,
  "timezone": "UTC"
}
```

> **Placeholders you MUST replace:** `fire_at` must be an actual
> future timestamp 48 hours from now with your own offset.  `task_id`,
> `session_id`, `agent` must match your active session.  The
> `normalized_brief` must reflect the real instruction.
> **Do NOT conduct a live schedule create** — this is a template.

```bash
happyranch schedules create --org happyranch --from-file "${TMPDIR:-/tmp}/schedule-TASK-4317.json"
```

### Example 2 — Saturday market update (weekly)

**Context:** The founder says "send me a market update every Saturday
at 09:00 Shanghai time."

Write `${TMPDIR:-/tmp}/schedule-weekly-market.json`:

```json
{
  "task_id": "TASK-4317",
  "session_id": "sess-b86b2a3404d740179c23871958c9c7db",
  "agent": "dev_agent",
  "source_instruction": "send me a market update every Saturday at 09:00 Shanghai time",
  "normalized_brief": "Compile and post a concise crypto/ai market update for the week, including major price moves and news, to the founder's thread",
  "kind": "weekly",
  "fire_at": "2026-08-08T01:00:00Z",
  "recurrence": {"day": "sat", "time": "09:00", "tz": "Asia/Shanghai"},
  "timezone": "Asia/Shanghai"
}
```

> **Placeholders you MUST replace:** `fire_at` must be the actual
> next Saturday 09:00 Asia/Shanghai occurrence computed from NOW and
> encoded as UTC (or with offset).  The example shows `2026-08-08T01:00:00Z`
> which is Saturday 2026-08-08 09:00 CST (UTC+8).  Replace with the
> real next occurrence.  `task_id`, `session_id`, `agent`, and
> `normalized_brief` must reflect reality.

```bash
happyranch schedules create --org happyranch --from-file "${TMPDIR:-/tmp}/schedule-weekly-market.json"
```

### Example 3 — fortnightly Tuesday/Thursday report, ending after six successful dispatches (recurring)

This is a template only. Use it only when the founder/operator explicitly gave
this exact commitment and phase. Let the daemon calculate the first instant.

```json
{
  "task_id": "TASK-4317",
  "session_id": "sess-abc123",
  "agent": "dev_agent",
  "source_instruction": "Every other Tuesday and Thursday at 09:00 Shanghai time, send the project report; stop after six successful reports.",
  "normalized_brief": "Send the project report to the founder's thread, covering progress, blockers, and the next planned steps.",
  "kind": "recurring",
  "start_date": "2026-08-18",
  "recurrence": {
    "freq": "WEEKLY",
    "interval": 2,
    "byday": ["TU", "TH"],
    "time": "09:00",
    "tz": "Asia/Shanghai",
    "count": 6
  },
  "timezone": "Asia/Shanghai"
}
```

```bash
happyranch schedules create --org happyranch --from-file "${TMPDIR:-/tmp}/schedule-fortnightly-report.json"
```

## v2 boundaries — what is NOT included

| Out of scope | Details |
| --- | --- |
| Cross-agent scheduling | Self-only; agents must never target another agent |
| Cron or unsupported recurrence | No sub-daily/cron, monthly lists or negative values, yearly selectors, or hidden alternate grammar |
| Hidden / silent schedules | All Todos are founder-visible |
| General unscheduled backlog | No "Todo inbox" — every Todo has a `fire_at` |
| Resume / re-arm from agent side | Re-arming after pause is founder-controlled |
| Edit `source_instruction` or `normalized_brief` | Immutable; founder can only edit timing/recurrence/timezone |
| Agent-driven `todos create` | Agent create is `schedules create`; the `todos` surface is founder management (list/show/pause/cancel/edit) |
| `indefinite: true` from agent callback | Indefinite expiry is founder-set only |
