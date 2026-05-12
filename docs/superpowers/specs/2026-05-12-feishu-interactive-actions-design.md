# Feishu Interactive Actions — Failure Notifications + Top-Level Dispatch

**Status:** Design approved, pending implementation
**Author:** Founder + Claude Opus
**Date:** 2026-05-12
**Supersedes:** —
**Builds on:** `2026-05-08-feishu-notification-design.md` (escalation notify + APPROVE/REJECT reply pipeline)

## 1. Problem

Today, the Feishu integration covers exactly one workflow: a task escalates → the founder gets a push card → the founder replies `APPROVE` or `REJECT` in-thread → the listener calls `resolve_escalation_in_process`.

Two adjacent founder workflows still require the CLI:

1. **Failed tasks.** When a task terminates as `FAILED` (not blocked-escalated), no notification fires unless the daemon happened to crash mid-task. The founder discovers the failure only via `opc tasks` or `opc details`. To re-run the work, they must `ssh` into the host and run `opc revisit <task_id>` — which is TTY-gated.
2. **Spawning new work.** Dispatching a new task is CLI-only (`opc run --org … --brief …`). The founder cannot kick off work from their phone.

This spec extends the existing Feishu surface to cover both: push notifications for terminal failures with a `REVISIT` reply verb, and top-level `DISPATCH` messages that spawn new tasks.

## 2. Non-Goals

- **No live progress streaming back to Feishu.** Once a task is dispatched or revisited from Feishu, status updates remain CLI/SSE-driven.
- **No reply-to-confirmation actions.** The founder cannot reply `CANCEL TASK-123` under the bot's dispatch-confirmation card. A real cancel is still `opc` CLI work.
- **No per-Feishu-user authorization.** The configured `chat_id` remains the trust boundary; anyone with write access to that chat can dispatch and revisit. Per-user policy is a v2 concern.
- **No new top-level verbs beyond `DISPATCH`.** `STATUS`, `LIST`, `AGENTS`, etc. are deferred.
- **No notifications for founder-cancelled tasks.** A `cancelled_at != NULL` failure was deliberate — silent.
- **No retries / DLQ for outbound send failures.** Same best-effort model as escalation notify: log + audit, founder falls back to CLI.
- **No replacement of `opc revisit` or `opc run`.** Both CLI commands remain authoritative and unchanged. Feishu is an additional surface.
- **No interactive cards in v1.** All outbound messages keep `msg_type: post`. The reply protocol stays text-only.
- **No backfill.** Tasks already `FAILED` when this feature ships do not retroactively get a notification.
- **No HTML/markdown card upgrade beyond the current post shape.**
- **No rate-limiting on dispatch.** The `chat_id` allowlist is the throttle.

## 3. User-Facing Interface

### 3.1 Failure notification (outbound)

When a task transitions to `FAILED` and the gate (§5.1) opens, the daemon sends:

```
[OPC hk-macau-tourism] TASK-204 FAILED — review needed

Agent:        dev_agent
Team:         engineering
Task:         TASK-204
Org:          hk-macau-tourism
Failed at:    2026-05-12 09:14:33 UTC
Failure kind: self_blocked

--- Brief ---
Update the Macau ferry-schedule scraper to handle the new TurboJet
timetable PDF format. Reference KB entry ferry-scraping.

--- Last manager summary ---
Delegated to dev_agent. First attempt parsed only the legacy two-column
layout; the new PDF has a three-column header row that the scraper
misclassifies as data.

--- Failure detail ---
self-blocked: I cannot determine whether the third column "Service Class"
should map to existing fare tiers or a new field. Need founder direction.

--- To revisit ---
Reply in this thread with:

  REVISIT
  <optional note that becomes founder_note on the new root>

(Or ignore this message — the task stays failed.)
```

### 3.2 Failure reply (inbound, in-thread)

The founder replies *in the message thread* of §3.1:

```
REVISIT
Tell dev_agent to add "Service Class" as a new optional field on
FerryDeparture; do not map to existing fare tiers.
```

The listener spawns a new root task linked via `revisit_of_task_id` to TASK-204. The body becomes `founder_note` in the `revisit_of` audit row. The new root inherits TASK-204's brief, team, and `session_timeout_seconds`.

### 3.3 Top-level dispatch (inbound, no thread)

The founder posts a **new top-level message** in the configured chat:

```
DISPATCH engineering
Investigate why the Octopus card-balance endpoint returns 503 on
weekday mornings. KB entry octopus-api has the contract.
```

The team name is optional. If omitted, the daemon falls back to standard team resolution (single-team orgs work, multi-team errors back into the chat).

### 3.4 Dispatch confirmation (outbound, top-level)

After accepting a dispatch:

```
[OPC hk-macau-tourism] Task TASK-217 dispatched

Team:  engineering
Brief: Investigate why the Octopus card-balance endpoint returns 503 on
       weekday mornings. KB entry octopus-api has the contract.

Track with:
  opc tail --org hk-macau-tourism TASK-217
```

After rejecting a dispatch:

```
[OPC hk-macau-tourism] Dispatch rejected

Reason: unknown team "engineerin"
Valid teams: engineering, customer-care
```

Both are top-level posts (not threaded). The bot's own confirmations are filtered out of the inbound pipeline by the existing `sender_type=app` rule, so no echo loops.

## 4. Architecture

The change folds into the existing `feishu_listener.py` 8-step pipeline by:

- **Bifurcating step 3** (the threading filter) into a reply branch (has `root_id`) and a top-level dispatch branch (no `root_id`).
- **Routing reply branches at step 7** by the `kind` column on `escalation_notifications` (new): `escalation` → existing `resolve_escalation_in_process`; `failure` → new `revisit_from_notification`.

No new tables. No new HTTP routes for the listener — both inbound branches call in-process helpers, mirroring the `resolve_escalation_in_process` pattern.

## 5. Failure Notification — Trigger and Flow

### 5.1 When notifications fire

A failure notification fires from inside `_fail()` in `run_step.py`, **after** the auto-revisit decision, gated by all of:

1. `feishu_notifications.enabled = true`
2. `feishu_notifications.notify_on_failure = true` (new config field, default `false`)
3. The failing task's `cancelled_at IS NULL` (founder-cancelled failures never notify)
4. No auto-revisit was spawned for this task

The fourth condition matters because `_maybe_spawn_auto_revisit` already covers three of the seven `_fail()` paths (exception, session-failed, opaque-no-callback) and the system self-heals for those — but only up to `_AUTO_REVISIT_CAP = 2` attempts per chain. When the cap is hit, no auto-revisit spawns, and the founder needs to know.

The table below shows the auto-revisit gate per `_fail()` call site. All entries are additionally gated by conditions 1–3 (enabled, `notify_on_failure=true`, not cancelled) — those gates are not repeated per row.

| `_fail()` call site | auto-revisit attempted? | When does failure notify fire? |
|---|---|---|
| `run_step.py:103` agent invocation exception | yes | only when cap is hit |
| `run_step.py:127` session non-success | yes | only when cap is hit |
| (opaque no-completion-callback path) | yes | only when cap is hit |
| `run_step.py:138` self-blocked | no | every time |
| `run_step.py:187` invalid delegate JSON | no | every time |
| `run_step.py:261` unknown manager action | no | every time |
| `run_step.py:616` cascade-fail from child | no | every time |
| `daemon/__main__.py:48` daemon-restart sweep | no | every time (replaces today's mis-routed `notify_escalated` call) |
| founder-cancelled (any path with `cancelled_at`) | no | never |

### 5.2 Detecting "auto-revisit did not spawn"

`_maybe_spawn_auto_revisit` is updated to return `bool` (`True` if a revisit row was inserted, `False` otherwise — includes the "no chain," "cap hit," and "not eligible" cases). Existing callers ignore the return; new callers in `_fail()` use it to gate the notify hook.

For paths that do not call `_maybe_spawn_auto_revisit` at all (self-blocked, invalid-delegate, unknown-action, cascade-fail, daemon-restart), the gate treats this as "did not spawn" by default — i.e., notify if the other conditions are met.

### 5.3 Notifier integration

A new `Orchestrator.notify_failed(task_id, failure_note, failure_kind)` method mirrors `notify_escalated`:

- Loop-aware fire-and-forget: `loop.create_task` if a running asyncio loop is detected, else daemon thread + `asyncio.run`.
- Calls `EscalationNotifier.send_failure(...)`.
- Never blocks the orchestration loop. Send failures audit `failure_notify_failed` and are swallowed.

`failure_kind` is one of: `agent_exception`, `session_failed`, `self_blocked`, `invalid_delegate`, `unknown_action`, `cascade_fail`, `daemon_restart`. Recorded on the audit row and rendered in the card.

`EscalationNotifier.send_failure(...)` builds the card via `_build_failure_body` (mirrors `_build_body_phase1`), sends via `FeishuClient.send_post_message`, then mints an `escalation_notifications` row with `kind='failure'`. Mint-after-send semantics are preserved.

### 5.4 Daemon-restart reclassification

The existing `_sweep_on_startup` call to `notify_escalated` is replaced with a `notify_failed(kind="daemon_restart")` call. This is a semantic fix: the task is set to `FAILED` (not `BLOCKED/ESCALATED`), and the founder cannot meaningfully APPROVE/REJECT a failed task — REVISIT is the correct action.

## 6. Reply Pipeline — Branching by `kind`

### 6.1 Storage

Add one column to `escalation_notifications`:

```sql
ALTER TABLE escalation_notifications ADD COLUMN kind TEXT NOT NULL DEFAULT 'escalation';
```

The default backfills existing rows. New escalation notifications continue to pass `kind='escalation'` implicitly via the default; new failure notifications pass `kind='failure'` explicitly through an additional parameter on `mint_escalation_notification(...)`.

The DDL is applied through the existing try/except `ALTER TABLE` ladder in `Database.__init__` — wrapped in a `try: ... except sqlite3.OperationalError: pass` block, idempotent across restarts.

### 6.2 Reply parser

Refactor `parse_reply` to share a `_split_verb_and_body(text) -> (verb, body) | None` helper. Then:

```python
def parse_reply(text: str) -> ReplyIntent | None:
    """Verbs: APPROVE, REJECT, REVISIT. For threaded replies to notifications."""

def parse_top_level_message(text: str) -> DispatchIntent | None:
    """Verbs: DISPATCH. For top-level messages in the configured chat."""
```

`ReplyIntent.decision` becomes `Literal["approve", "reject", "revisit"]`. `DispatchIntent` is `(team: str | None, brief: str)` where `team` is the rest of the verb line (`DISPATCH engineering` → `team="engineering"`) and `brief` is the remaining non-empty body, stripped.

### 6.3 Listener pipeline

Steps 1–2 unchanged. Step 3 bifurcates:

| `msg.root_id` | `allow_dispatch` config | Routed to |
|---|---|---|
| present | — | reply pipeline (steps 4r–8r) |
| absent | `true` | dispatch pipeline (steps 4d–8d) |
| absent | `false` | dropped, `processed_event_ids.outcome="ignored"`, no audit (matches today's behavior for non-threaded messages) |

**Reply pipeline (4r–8r), unchanged from today except for step 7r:**

- 4r: sender filter (drop `sender_type=app`)
- 5r: notification lookup by `root_id`; check `consumed_at IS NULL` and `expires_at` not passed
- 6r: parse via `parse_reply`; `None` → audit `reply_rejected (parse_failed)`, leave unconsumed
- 7r: dispatch by `(kind, decision)`:

  | `kind` | `decision` | Action |
  |---|---|---|
  | `escalation` | `approve` / `reject` | existing `resolve_escalation_in_process` |
  | `escalation` | `revisit` | audit `reply_rejected (verb_mismatch)`, leave unconsumed |
  | `failure` | `revisit` | new `revisit_from_notification(task_id, founder_note)` |
  | `failure` | `approve` / `reject` | audit `reply_rejected (verb_mismatch)`, leave unconsumed |

- 8r: on success, consume notification with `consumed_by="feishu-reply"`. On `cannot_revisit` (predecessor transitioned out of `{FAILED, COMPLETED}` since the card was sent — extremely rare), audit `reply_rejected (cannot_revisit)` and **leave the row unconsumed** so the founder's intent is preserved; they fall back to CLI.

**Dispatch pipeline (4d–8d):**

- 4d: sender filter (drop `sender_type=app`)
- 5d: parse via `parse_top_level_message`; `None` → audit `dispatch_via_feishu_rejected (parse_failed)`, mark dedup row `outcome="rejected"`
- 6d: in-process dispatch via `dispatch_via_feishu(slug, intent, sender_id, event_id)`. Returns `(task_id, team)` on success or raises `DispatchError(reason: str)` (see §6.5)
- 7d: on success → send confirmation post (§3.4 success card) via `FeishuClient.send_post_message`. Audit `dispatch_via_feishu_accepted`
- 8d: on failure → send error post (§3.4 rejection card). Audit `dispatch_via_feishu_rejected (<reason>)` with one of: `empty_brief`, `unknown_team`, `dispatch_failed`. Confirmation/error sends are best-effort — send failures are swallowed but a final `dispatch_send_confirmation_failed` audit row is written so the absence of a Feishu reply is debuggable

### 6.4 `revisit_from_notification` in-process helper

A thin helper extracted from the existing `POST /api/v1/orgs/<slug>/tasks/{task_id}/revisit` route handler in `daemon/routes/tasks.py`. Same eligibility check (`_REVISIT_ELIGIBLE_STATUSES = {FAILED, COMPLETED}`), same `db.insert_task(..., revisit_of_task_id=predecessor.id, session_timeout_seconds=predecessor.session_timeout_seconds)`, same `audit.log_revisit_of(...)` and `audit.log_revisit_spawned(...)`.

Two differences from the HTTP route:

- An `actor` parameter is recorded on the `revisit_of` audit row: `actor="feishu-reply"` for this path, `actor="cli"` for the existing route. (The route adds `actor="cli"` as part of this change.)
- No `session_timeout_seconds` override is exposed via Feishu in v1 — the predecessor's value inherits unconditionally.

The HTTP route is refactored to call the same helper, so the two paths cannot drift.

### 6.5 `dispatch_via_feishu` in-process helper

A thin helper extracted from the existing `POST /api/v1/orgs/<slug>/tasks` (i.e., `opc run`) handler. Reuses the same task-creation logic: `db.next_task_id()`, `db.insert_task(...)`, queue enqueue. Records an `actor="feishu-dispatch"` audit row alongside the existing task-creation audit. Returns either `(task_id, team)` or raises `DispatchError(reason: str)` where `reason` is one of the rejection codes above. The reason strings drive both the Feishu error card and the audit payload.

The HTTP route is refactored to call the same helper; route-only concerns (HTTP status, JSON shape) stay in the route module.

## 7. Configuration

The `feishu_notifications` block in `<runtime>/orgs/<slug>/org/config.yaml` gains two opt-in flags:

```yaml
feishu_notifications:
  enabled: true
  region: feishu
  chat_id: oc_xxxxxx
  app_id: cli_xxxxxx
  app_secret: xxxxxx
  reply_ttl_hours: 72
  notify_on_failure: true   # NEW — default false
  allow_dispatch: true      # NEW — default false
```

`_parse_feishu_notifications` in `src/orchestrator/org_config.py` validates both as booleans (`OrgConfigError` on type mismatch). Both default to `false`, so existing orgs see no behavior change after upgrade until they opt in.

## 8. Authorization Model

Unchanged from the v1 escalation feature: the configured `chat_id` is the trust boundary. The Feishu `sender_id` is recorded in audit rows for traceability but is **not used for permission decisions**.

This is acceptable because:

- The bot can only see messages in chats it has been added to.
- The chat_id is a per-org configuration the founder sets up manually.
- The cost of an unintended action (a spurious revisit or dispatch) is bounded — the founder sees it immediately on the dashboard and can `opc` it dead.

Per-Feishu-user authorization (e.g., "only `sender_id=ou_xyz` can dispatch") is a v2 concern.

## 9. Error Handling and Edge Cases

| Case | Handling |
|---|---|
| Failure notify send fails | audit `failure_notify_failed`; no row minted; orchestration unaffected |
| `cancel-revisit`: predecessor transitions out of `{FAILED, COMPLETED}` between send and reply | listener gets `cannot_revisit` from helper; audit `reply_rejected (cannot_revisit)`; row stays unconsumed; founder uses CLI |
| Founder sends `DISPATCH` inside a thread (has `root_id`) | routed to reply pipeline; `parse_reply` rejects unknown verb; audit `reply_rejected (parse_failed)` |
| Founder sends `REVISIT` outside any thread (top-level) | routed to dispatch pipeline; `parse_top_level_message` rejects unknown verb; audit `dispatch_via_feishu_rejected (parse_failed)` |
| `DISPATCH` with empty body | audit `dispatch_via_feishu_rejected (empty_brief)`; error card back |
| `DISPATCH <team>` with unknown team | audit `dispatch_via_feishu_rejected (unknown_team)`; error card lists valid teams |
| Daemon down when Feishu message arrives | Feishu SDK queues; on reconnect, re-delivery + `processed_event_ids` dedup prevents double-action |
| Two orgs share a `chat_id` | both listeners would act independently — duplicate tasks across orgs. Documented as misconfiguration. No code fix in v1 (pre-existing risk for escalation replies too) |
| CLI revisit happens first, then Feishu reply | existing `opc revisit` is extended to also `consume_escalation_notification(..., consumed_by="cli-fallback")` on any open `kind='failure'` row for that task — so the Feishu listener finds the row consumed and silently no-ops, matching today's escalation behavior |
| Confirmation/error send fails after a dispatch was accepted | task is already created; orphan audit `dispatch_send_confirmation_failed` written; founder sees no Feishu echo but `opc tasks` shows the new task. Out-of-band recovery |

## 10. Audit Surface

New action names:

| Action | Payload |
|---|---|
| `failure_notify_sent` | `task_id, feishu_message_id, failure_kind, expires_at` |
| `failure_notify_failed` | `task_id, failure_kind, error` |
| `failure_revisit_via_reply` | `predecessor_task_id, new_root, founder_note, feishu_message_id, feishu_event_id, sender_id` |
| `dispatch_via_feishu_accepted` | `task_id, team, sender_id, feishu_event_id` |
| `dispatch_via_feishu_rejected` | `reason, sender_id, feishu_event_id` (reasons: `parse_failed`, `empty_brief`, `unknown_team`, `dispatch_failed`) |
| `dispatch_send_confirmation_failed` | `task_id, error` |

Existing `reply_rejected` reason set extended with: `verb_mismatch`, `cannot_revisit`.

Existing `log_revisit_of` payload gains an `actor` field (`"cli" | "feishu-reply"`). Existing rows without the field remain valid (Pydantic ignores extras on read; the audit logger writes the new field unconditionally going forward).

## 11. Testing

### 11.1 Unit

- `parse_reply` accepts `REVISIT` with multi-line rationale; rejects `APPROVE` mixed with `REVISIT`, leading-whitespace pathologies, empty input.
- `parse_top_level_message` accepts `DISPATCH`, `DISPATCH <team>`, multi-line briefs; rejects unknown verbs, empty body, in-thread context (callers should not invoke it; tested via listener integration).
- `mint_escalation_notification` round-trips `kind` parameter; default is `'escalation'`.
- `_build_failure_body` renders all `failure_kind` values; truncates oversized briefs identically to escalation card.
- `org_config` parses `notify_on_failure` and `allow_dispatch` as booleans; raises `OrgConfigError` on type mismatch and accepts absent (default `false`).
- `_maybe_spawn_auto_revisit` returns `True` on spawn, `False` on cap hit / no chain / not eligible. Existing call sites continue to ignore the return value.

### 11.2 Listener pipeline matrix

Stub `revisit_from_notification`, `dispatch_via_feishu`, and `resolve_escalation_in_process`. Exercise all 8 cells of the routing matrix:

- `(root_id present, kind=escalation, verb=approve)` → resolve_escalation called
- `(root_id present, kind=escalation, verb=reject)` → resolve_escalation called
- `(root_id present, kind=escalation, verb=revisit)` → `reply_rejected (verb_mismatch)`, unconsumed
- `(root_id present, kind=failure, verb=approve)` → `reply_rejected (verb_mismatch)`, unconsumed
- `(root_id present, kind=failure, verb=reject)` → `reply_rejected (verb_mismatch)`, unconsumed
- `(root_id present, kind=failure, verb=revisit)` → revisit_from_notification called, consumed
- `(root_id absent, allow_dispatch=false)` → dropped, no audit
- `(root_id absent, allow_dispatch=true, valid DISPATCH)` → dispatch_via_feishu called, confirmation sent

Plus the `cannot_revisit` case (predecessor moved out of eligibility): `reply_rejected (cannot_revisit)`, row unconsumed.

### 11.3 Orchestrator `_fail()` hook

For each path in the §5.1 table:

- assert no notify when `notify_on_failure=false`
- assert no notify when `task.cancelled_at != NULL`
- assert no notify when auto-revisit spawned (for the three eligible paths, on attempt 1 or 2)
- assert notify fires when auto-revisit did not spawn AND the other gates are open

### 11.4 Integration

Spawn the daemon with a fake Feishu client + fake WebSocket listener stubs (existing test harness):

- **Failure → REVISIT round-trip:** dispatch a task that self-blocks → assert `escalation_notifications` row with `kind='failure'` → simulate inbound `REVISIT <note>` reply → assert new task exists with `revisit_of_task_id` pointing to the failed task; founder_note in `revisit_of` audit row; notification consumed.
- **Top-level DISPATCH round-trip:** simulate inbound top-level `DISPATCH engineering <brief>` → assert task row exists with the brief and team; assert confirmation card sent to chat.
- **Misconfig regression:** with `allow_dispatch=false`, simulate top-level `DISPATCH …` → assert no task created, `processed_event_ids.outcome='ignored'`.

## 12. Migration and Rollout

- DDL: `ALTER TABLE escalation_notifications ADD COLUMN kind TEXT NOT NULL DEFAULT 'escalation'` applied via the existing try/except ladder in `Database.__init__`. Idempotent across restarts.
- Config defaults preserve existing behavior: `notify_on_failure=false`, `allow_dispatch=false`. Behavior is purely additive on upgrade — no surprises for orgs that don't opt in.
- The daemon-restart-sweep change (escalation → failure classification) is a behavior change but only fires after a real daemon crash mid-task. The card content changes, the founder action (REVISIT vs APPROVE/REJECT) becomes semantically correct. Documented in the README runbook update.
- README gains a "Failed-task notifications" section and a "Top-level dispatch" section under the existing Feishu setup.
- `CLAUDE.md` "Feishu notifications" section is renamed "Feishu interactive actions" and gains paragraphs mirroring the §5 and §6 flows.

## 13. Change Surface (estimate)

Source files touched:

- `src/orchestrator/run_step.py` — return-`bool` on `_maybe_spawn_auto_revisit`; call `notify_failed` from `_fail` per §5.1 gate
- `src/orchestrator/orchestrator.py` — add `notify_failed` method (fire-and-forget wrapper)
- `src/orchestrator/org_config.py` — add `notify_on_failure` and `allow_dispatch` fields
- `src/infrastructure/database.py` — `kind` column DDL; `mint_escalation_notification` accepts optional `kind`
- `src/infrastructure/feishu/notifier.py` — `send_failure`, `_build_failure_body`, `send_dispatch_confirmation`, `send_dispatch_error`
- `src/infrastructure/feishu/reply_parser.py` — `_split_verb_and_body` helper; extend `parse_reply` with `REVISIT`; add `parse_top_level_message`
- `src/daemon/feishu_listener.py` — step-3 bifurcation; reply-pipeline `kind` branching; dispatch pipeline
- `src/daemon/routes/tasks.py` — extract `revisit_from_notification` and `dispatch_via_feishu` in-process helpers; HTTP routes call them
- `src/daemon/__main__.py` — replace `_sweep_on_startup`'s `notify_escalated` with `notify_failed(kind="daemon_restart")`
- `src/cli.py` — `opc revisit` also consumes any open `kind='failure'` row for the task with `consumed_by="cli-fallback"`
- Tests + README + CLAUDE.md updates per §11 and §12

No new tables. No new HTTP routes. One new schema column. Two new config fields.
