# Feishu Notifications for Script Requests — Design Spec

**Status: REMOVED in TASK-302 (THR-022).** Web UI + threads are sole control surface. DB tables dormant.

**Date:** 2026-05-25
**Status:** Draft, pending implementation plan.
**Relates to:**
- `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md` — the script-request primitive being notified.
- `docs/superpowers/specs/2026-05-08-feishu-notification-design.md` — the original Feishu escalation outbound + listener architecture being reused verbatim.
- `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md` — kind × verb listener dispatch shape (failure REVISIT, thread_addressed freeform) being extended with one more kind.

## 1. Goal

When an agent submits a script request (`SR-NNN`), the founder learns about it via a push channel they already watch — Feishu — and can act (run or reject) without hopping to the CLI or web. Today the founder must actively run `happyranch scripts list` or refresh `/scripts` in the web UI to discover new SRs. That defeats the purpose of an "escape hatch" primitive: the agent is now blocked, but the founder has no idea the wall was hit.

This spec wires SR submissions into the same Feishu outbound+listener rails the escalation and failure flows already use. After this lands:

1. Agent submits SR → daemon pushes a Feishu post to the configured chat with the SR's title, rationale, full script preview, and reply grammar.
2. Founder replies `APPROVE` → daemon runs the SR with stored defaults (`cwd_hint`, `timeout_seconds=300`).
3. Founder replies `REJECT\n<reason>` → daemon rejects the SR with that reason.
4. When the run terminates (completed or failed), daemon posts a threaded reply with status, exit code, duration, and head of stdout/stderr.

The CLI (`happyranch scripts run|reject`) and web (`/scripts` feature) remain authoritative and unchanged. Feishu is an additional surface.

## 2. Non-goals

- **No live stdout/stderr streaming into Feishu.** The web UI's SSE panel is the streaming surface; Feishu gets a single terminal-result post per run.
- **No `cwd_override` or `timeout_seconds` overrides via reply.** Founder uses CLI or web for those. The reply grammar accepts only the verb + optional rationale.
- **No re-run via reply.** If the founder wants to re-submit after a failure, they revisit the agent's task (existing primitive) or run `happyranch scripts run` manually.
- **No backfill notifications.** SRs submitted before this feature ships (or before the daemon starts) do not get a notification.
- **No completion notifications for runs initiated via CLI or web.** Only Feishu-initiated runs get the terminal-result follow-up. (CLI run shows output in its own stream; web run renders the live SSE panel — both surfaces already give terminal feedback in-context.)
- **No multi-chat / multi-recipient escalation.** Same chat per org as every other Feishu notification.
- **No card / interactive button format.** Reuses the existing `msg_type=post` text format.
- **No new auth surface.** Trust boundary is `chat_id` match, identical to the escalation pattern.

## 3. User-Facing Interface

### 3.1 What the founder sees

**Push on submit:**

```
[HappyRanch hk-macau-tourism] SR-019 submitted — review needed

Agent:        engineering_head
Task:         TASK-091
Interpreter:  bash
Cwd hint:     repos/web-app
Title:        Close PR #247 with approval comment

Rationale:
PR review is complete. My allow_rules cover `gh pr comment` but not
`gh pr close`. Need founder to merge-close so the auth-rewrite branch
can be deleted.

Script:
set -euo pipefail
gh pr close 247 --comment 'Approved and closed per review thread THR-014.'

To resolve, reply in this thread with one of:

  APPROVE
  <optional note>

  —or—

  REJECT
  <reason>

You can also resolve via CLI:
  happyranch scripts show SR-019
  happyranch scripts run SR-019
  happyranch scripts reject SR-019 --reason "..."
```

Script body is truncated to `_SCRIPT_PREVIEW_CAP = 1500` characters with `\n[truncated — see happyranch scripts show SR-019 for full script]` appended when cut.

**Founder reply (APPROVE branch):**

```
APPROVE
```

(Or `APPROVE` with any free-form text on the lines after — the text is captured in the audit log but does not influence run parameters.)

**Founder reply (REJECT branch):**

```
REJECT
gh pr close doesn't fit our PR hygiene policy; comment + leave open instead.
```

Rationale is required; if the founder posts just `REJECT` with no body, the audit captures `(no rationale provided via Feishu)`.

**Terminal-result follow-up** (threaded reply, posted by daemon when the run finishes):

```
[HappyRanch hk-macau-tourism] SR-019 completed (exit 0)

Duration: 1.4s

stdout:
✓ Closed pull request #247

stderr:
(empty)
```

stdout/stderr are capped at `_RESULT_OUTPUT_PREVIEW_CAP = 500` chars each, with truncation footer pointing at `happyranch scripts output SR-019` for the full read.

On failure:

```
[HappyRanch hk-macau-tourism] SR-019 failed (timeout)

Duration: 300.0s

stdout:
(empty)

stderr:
Error: connection timed out
[truncated — 12 KB more in happyranch scripts output SR-019]
```

### 3.2 What the founder sees on edge paths

- **Bad parse** (founder types `MAYBE` instead of `APPROVE`/`REJECT`) → threaded reply with the existing `_build_parse_hint_body` grammar hint. Notification row stays unconsumed; founder retries in the same thread. The existing parser already recognizes `APPROVE` / `REJECT` / `REVISIT`; we extend it to accept those for `script_request` kind too (REVISIT routes to `verb_mismatch`).
- **Verb mismatch** (founder types `REVISIT` on a script_request notification) → silently dropped with `verb_mismatch` audit reason. No follow-up post. (Matches the escalation+revisit and failure+approve behaviors today.)
- **Stale notification** (founder replies after `reply_ttl_hours` elapses) → silently dropped with `notification_expired` audit reason.
- **Already consumed** (founder double-replies) → silently dropped with `notification_consumed` audit reason.
- **Race with CLI/web** (founder runs/rejects via CLI while a Feishu reply is in flight) → the in-process helper raises a status-mismatch error from the underlying transition; listener consumes the notification row with `consumed_by="feishu-reply"` only on success, so a lost race leaves the row unconsumed and audits `script_reply_rejected reason=not_pending`.

### 3.3 New / changed CLI surface

**None.** CLI is unchanged. Feishu integration is server-side only.

### 3.4 Configuration

Reuses the existing `feishu_notifications` block in `<runtime>/orgs/<slug>/org/config.yaml`. No new fields. Master switch (`enabled: false`) disables script notifications along with escalation/failure/thread notifications.

## 4. Architecture

### 4.1 Modules touched

```
src/infrastructure/feishu/notifier.py
  + _build_script_request_body()        — new body builder for the submit push
  + _build_script_result_body()         — new body builder for the terminal-result follow-up
  + EscalationNotifier.send_script_request(...)         — outbound on submit
  + EscalationNotifier.send_script_run_result(...)      — outbound on terminal

src/daemon/feishu_listener.py
  + _dispatch_reply_action: new branch for kind="script_request"

src/daemon/routes/scripts.py
  + run_script_from_notification(org, state, sr_id, actor)      — in-process helper
  + reject_script_from_notification(org, state, sr_id, reason)  — in-process helper
  + Hook from submit_script success: schedule notify_script_submitted
  + Hook from _run_and_persist terminal: schedule send_script_run_result

src/orchestrator/orchestrator.py
  + notify_script_submitted(sr_id, ...)  — fire-and-forget bridge (matches notify_escalated shape)

src/infrastructure/database.py
  + mint_escalation_notification: accept kind="script_request"

src/infrastructure/audit_logger.py
  + log_script_notify_sent(sr_id, task_id, feishu_message_id)
  + log_script_notify_failed(sr_id, task_id, error)
  + log_script_reply_processed(sr_id, task_id, decision, rationale)
  + log_script_reply_rejected(sr_id, task_id, reason, feishu_event_id?)
  + log_script_run_result_notify_sent(sr_id, parent_message_id, follow_up_message_id)
  + log_script_run_result_notify_failed(sr_id, error)
```

### 4.2 Outbound — submit push

After `submit_script` route persists the SR row and audits `script_submitted`:

```python
# inside submit_script, after audit:
if hasattr(org, "orchestrator") and org.orchestrator is not None:
    org.orchestrator.notify_script_submitted(
        sr_id=sr_id, agent=agent, task_id=body.task_id,
        title=title, rationale=rationale, script_text=body.script,
        interpreter=body.interpreter, cwd_hint=cwd_hint,
    )
```

`Orchestrator.notify_script_submitted` is a fire-and-forget bridge that mirrors `notify_escalated` — from a thread-pool context it spawns a daemon thread running `asyncio.run(notifier.send_script_request(...))`; from an async context it uses `loop.create_task(...)`. No-op when `notifier is None`.

`EscalationNotifier.send_script_request`:

1. Build `(title, body_lines)` via `_build_script_request_body(slug, sr_id, agent, task_id, title, rationale, script_text, interpreter, cwd_hint)`.
2. Truncate `script_text` to `_SCRIPT_PREVIEW_CAP = 1500` chars; append truncation footer if cut.
3. `message_id = self._client.send_post_message(chat_id, title, body_lines)`.
4. `self._db.mint_escalation_notification(message_id, slug, sr_id, chat_id, expires_at, kind="script_request")` — `task_id` column carries the SR-NNN (matches `thread_addressed`'s reuse for thread_id).
5. `self._audit.log_script_notify_sent(sr_id, task_id, message_id)`.

Errors: swallow + `log_script_notify_failed`. Order is send-then-mint (matches `notify_escalated`).

### 4.3 Inbound — listener dispatch

`_dispatch_reply_action` in `src/daemon/feishu_listener.py` already routes by `kind × verb`. New branch:

```python
# Branch 3 (insert before verb-mismatch fallback):
if kind == "script_request" and decision in ("approve", "reject"):
    try:
        if decision == "approve":
            await self._run_script_from_notification(
                sr_id=task_id,          # task_id column carries SR-NNN
                actor="feishu-reply",
                founder_note=parsed.rationale,
            )
        else:
            await self._reject_script_from_notification(
                sr_id=task_id,
                reason=parsed.rationale or "(no rationale provided via Feishu)",
            )
    except Exception as exc:
        reason = "handler_exception"
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict) and detail.get("code") in ("not_pending", "cwd_missing", "interpreter_unavailable"):
            reason = detail["code"]
        self._audit.log_script_reply_rejected(
            sr_id=task_id, task_id=task_id,
            reason=reason, feishu_event_id=event_id,
        )
        _close("rejected", reason)
        return
    self._db.consume_escalation_notification(msg.root_id, consumed_by="feishu-reply")
    self._audit.log_script_reply_processed(
        sr_id=task_id, task_id=task_id,
        decision=decision, rationale=parsed.rationale,
    )
    _close("consumed", None)
    return
```

The listener gains two new injected callables (constructed in `maybe_start_feishu_listener_for_org` the same way `_resolve_for_listener` etc. are):

```python
async def _run_script_for_listener(*, sr_id, actor, founder_note):
    return await run_script_from_notification(
        org, state, sr_id=sr_id, actor=actor, founder_note=founder_note,
    )

async def _reject_script_for_listener(*, sr_id, reason):
    return await reject_script_from_notification(
        org, state, sr_id=sr_id, reason=reason,
    )
```

### 4.4 In-process route helpers

Today `run_script_route` and `reject_script` are tightly coupled to the HTTP request shape (`OrgDep`, `RunBody`, `RejectBody`). We extract the core logic into helpers that take `(org, state, ...)` arguments — identical pattern to `resolve_escalation_in_process` and `revisit_from_notification`.

```python
# src/daemon/routes/scripts.py

async def reject_script_from_notification(
    org: OrgState, state, *, sr_id: str, reason: str,
) -> ScriptRequestRecord: ...

async def run_script_from_notification(
    org: OrgState, state, *, sr_id: str,
    actor: str, founder_note: str,
) -> dict: ...
```

Both helpers:
- Read the SR row; raise `HTTPException(404)` if missing.
- Check status == `pending`; raise `HTTPException(409 not_pending)` otherwise.
- Run the same validation + transitions the HTTP routes do.
- `run_script_from_notification` uses stored defaults (`cwd_hint`, `timeout_seconds=300`) — no overrides exposed.

The HTTP routes (`run_script_route`, `reject_script`) are refactored to call these helpers internally, so behavior is identical across surfaces.

### 4.5 Outbound — terminal result follow-up

Inside `_run_and_persist` (in `src/daemon/routes/scripts.py`), after the terminal transition + audit:

```python
# After audit.log_script_run_completed / log_script_run_failed:
parent = org.db.get_open_notification_for_sr(sr_id, kind="script_request")
if parent is not None:
    org.orchestrator.notify_script_run_result(
        sr_id=sr_id,
        parent_message_id=parent["feishu_message_id"],
        status=result.status,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        stdout_head=result.stdout_head,
        stderr_head=result.stderr_head,
        reason=result.reason,
    )
```

`get_open_notification_for_sr(sr_id, kind)` is a new DB helper: `SELECT * FROM escalation_notifications WHERE task_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1`. We accept consumed notifications too — the notification was consumed when the run was triggered; we still want the parent message_id for the threaded reply. The check exists to skip the follow-up entirely when no Feishu notification was ever sent (e.g., CLI-initiated run).

`Orchestrator.notify_script_run_result` is a fire-and-forget bridge → `EscalationNotifier.send_script_run_result(parent_message_id, ...)`.

`send_script_run_result`:
1. Build `(title, body_lines)` via `_build_script_result_body(...)`.
2. `self._client.send_thread_reply(parent_message_id, title, body_lines)`.
3. Audit `log_script_run_result_notify_sent`.

Errors: swallow + `log_script_run_result_notify_failed`. No DB row minted (the follow-up is a leaf in the conversation — no reply expected, no correlation key needed).

### 4.6 Race & ordering

- **Submit-then-no-notifier:** If `org.orchestrator.notifier is None` (Feishu disabled), `notify_script_submitted` returns immediately. SR submission still succeeds; CLI/web still works.
- **Send failure:** If the post fails (network, Feishu down), no notification row is minted (send-then-mint discipline). SR remains `pending`; founder uses CLI/web to discover and act. Audit captures the failure.
- **Founder uses CLI before reply lands:** The CLI run/reject transitions the SR from `pending` to terminal. When the Feishu reply arrives, the in-process helper sees `status != pending` and raises 409. The listener audits `script_reply_rejected reason=not_pending` and leaves the notification row unconsumed (matches the existing escalation+CLI race behavior — the founder's CLI action wins, the Feishu reply is a silent no-op).
- **Two replies in quick succession:** First reply consumes the notification atomically (`consume_escalation_notification` uses `WHERE consumed_at IS NULL`); second hits `notification_consumed` and is dropped.
- **Run completes before reply path commits:** Not applicable — APPROVE → `run_script_from_notification` returns synchronously after kicking off the async runner (matches the HTTP route shape). The terminal-result follow-up is fired from inside the runner task, not the reply handler.

## 5. Data Model

### 5.1 Existing tables — extensions only

`escalation_notifications.kind` allowlist: add `script_request` to the existing `{escalation, failure, thread_addressed}` set in `mint_escalation_notification`.

Row contract for `kind="script_request"`:

| Column | Value |
|---|---|
| `feishu_message_id` | Returned by `im.message.create` |
| `org_slug` | Org slug |
| `task_id` | **SR-NNN** (not the originating task — same overloading as `thread_addressed`'s thread_id) |
| `chat_id` | Configured chat_id |
| `created_at` | Now (UTC ISO8601) |
| `expires_at` | Now + `reply_ttl_hours` |
| `consumed_at` | NULL on mint, set on first APPROVE/REJECT reply |
| `consumed_by` | `feishu-reply` or `cli-fallback` |
| `kind` | `script_request` |

`processed_event_ids` is unchanged — it dedupes by event_id regardless of notification kind.

### 5.2 Lifecycle

```
mint:    INSERT after a successful im.message.create from send_script_request
match:   SELECT WHERE feishu_message_id = root_id AND consumed_at IS NULL AND expires_at > now()
consume: UPDATE … SET consumed_at = now(), consumed_by = 'feishu-reply' WHERE feishu_message_id = ?
result:  on terminal run, SELECT … WHERE task_id = sr_id AND kind = 'script_request' → use feishu_message_id as parent for threaded reply
```

No GC / cleanup in v1 (consistent with existing tables).

### 5.3 Audit events

| Action | Scope | Payload |
|---|---|---|
| `script_notify_sent`             | `task_id=originating-task-id`, `script_request_id=SR-NNN` | `{feishu_message_id}` |
| `script_notify_failed`           | same | `{error}` |
| `script_reply_processed`         | `script_request_id` | `{decision, rationale, feishu_event_id}` |
| `script_reply_rejected`          | `script_request_id` | `{reason, feishu_event_id, text_preview?}` |
| `script_run_result_notify_sent`  | `script_request_id` | `{parent_message_id, follow_up_message_id, status}` |
| `script_run_result_notify_failed`| `script_request_id` | `{error, status}` |

All six log via `AuditLogger.log_*` with the same shape as existing `log_escalation_*` / `log_failure_*` methods. Scope dual-keying (task_id + script_request_id) matches `log_script_submitted` etc. introduced by the original SR spec.

## 6. Reply Parsing

The existing `parse_reply` (in `src/infrastructure/feishu/reply_parser.py`) already accepts `APPROVE` / `REJECT` / `REVISIT` (case-insensitive). No grammar changes needed.

Dispatch logic in `_dispatch_reply_action` decides what is valid for each kind:

| Kind | Valid verbs |
|---|---|
| `escalation`        | APPROVE, REJECT |
| `failure`           | REVISIT |
| `thread_addressed`  | freeform (no verb extraction) |
| `script_request`    | APPROVE, REJECT |  ← NEW

Any verb outside the kind's valid set hits the `verb_mismatch` fallback (audited with `script_reply_rejected reason=verb_mismatch`, notification unconsumed).

## 7. Body Builders

### 7.1 Submit-push body

```python
_SCRIPT_PREVIEW_CAP = 1500

def _build_script_request_body(
    *, slug: str, sr_id: str, agent: str, task_id: str,
    title: str, rationale: str, script_text: str,
    interpreter: str, cwd_hint: str | None,
) -> tuple[str, list[str]]:
    header = f"[HappyRanch {slug}] {sr_id} submitted — review needed"
    script_preview = script_text
    if len(script_preview) > _SCRIPT_PREVIEW_CAP:
        script_preview = (
            script_preview[:_SCRIPT_PREVIEW_CAP]
            + f"\n[truncated — see happyranch scripts show {sr_id} for full script]"
        )
    lines = [
        f"Agent:        {agent}",
        f"Task:         {task_id}",
        f"Interpreter:  {interpreter}",
        f"Cwd hint:     {cwd_hint or '(workspace root)'}",
        f"Title:        {title}",
        "",
        "Rationale:",
        rationale,
        "",
        "Script:",
        script_preview,
        "",
        "To resolve, reply in this thread with one of:",
        "",
        "  APPROVE",
        "  <optional note>",
        "",
        "  —or—",
        "",
        "  REJECT",
        "  <reason>",
        "",
        "You can also resolve via CLI:",
        f"  happyranch scripts show {sr_id}",
        f"  happyranch scripts run {sr_id}",
        f"  happyranch scripts reject {sr_id} --reason \"...\"",
    ]
    return header, lines
```

### 7.2 Result follow-up body

```python
_RESULT_OUTPUT_PREVIEW_CAP = 500

def _build_script_result_body(
    *, slug: str, sr_id: str, status: str, exit_code: int | None,
    duration_ms: int, stdout_head: str | None, stderr_head: str | None,
    reason: str | None,
) -> tuple[str, list[str]]:
    if status == "completed":
        descriptor = f"completed (exit {exit_code if exit_code is not None else '?'})"
    else:
        descriptor = f"failed ({reason or 'unknown'})"
    header = f"[HappyRanch {slug}] {sr_id} {descriptor}"

    def _preview(s: str | None) -> list[str]:
        if not s:
            return ["(empty)"]
        s = s.rstrip("\n")
        if len(s) <= _RESULT_OUTPUT_PREVIEW_CAP:
            return s.split("\n")
        return (
            s[:_RESULT_OUTPUT_PREVIEW_CAP].split("\n")
            + [f"[truncated — full output in happyranch scripts output {sr_id}]"]
        )

    duration_s = duration_ms / 1000.0
    lines = [
        f"Duration: {duration_s:.1f}s",
        "",
        "stdout:",
        *_preview(stdout_head),
        "",
        "stderr:",
        *_preview(stderr_head),
    ]
    return header, lines
```

Both functions are pure (no I/O), unit-testable as table-driven cases.

## 8. Hook Points (surgical changes)

| File | Change |
|---|---|
| `src/daemon/routes/scripts.py` | `submit_script`: after audit, call `org.orchestrator.notify_script_submitted(...)`. Extract `run_script_from_notification` + `reject_script_from_notification` helpers. Inside `_run_and_persist`, after terminal audit, look up open notification and call `notify_script_run_result(...)` if found. |
| `src/orchestrator/orchestrator.py` | Add `notify_script_submitted(...)` and `notify_script_run_result(...)` — fire-and-forget bridges identical to `notify_escalated` / `send_failure`. |
| `src/infrastructure/feishu/notifier.py` | Add `_build_script_request_body`, `_build_script_result_body`, `send_script_request`, `send_script_run_result` methods. |
| `src/daemon/feishu_listener.py` | Add `_run_script_for_listener` + `_reject_script_for_listener` closures in `maybe_start_feishu_listener_for_org`. Add new branch in `_dispatch_reply_action` for `kind="script_request"`. Pass the two new callables into `FeishuEventListener.__init__`. |
| `src/infrastructure/database.py` | `mint_escalation_notification`: accept `"script_request"`. Add `get_open_notification_for_sr(sr_id, kind) -> dict \| None`. |
| `src/infrastructure/audit_logger.py` | Six new `log_script_*` methods (see §5.3). |

No schema migration: `kind` is a TEXT column, allowlist is enforced in code only.

## 9. Auth & Security

| Threat | Defense |
|---|---|
| Spoofed reply | Same chat_id filter as existing replies; bot only receives from 1:1 chat with founder. |
| Replay of reply | `processed_event_ids` dedup — first event wins. |
| Double-APPROVE (race) | `consume_escalation_notification` atomic; second consumer hits rowcount=0 silently. |
| APPROVE bypassing TTY-confirm requirement | Accepted in v1 — symmetric with escalation APPROVE today. The script body is shown in full (or up to 1500 chars) in the message, so the founder's eyes have been on the script. Founder discipline: don't auto-respond to Feishu without reading. |
| Founder approves before reading script | Same as above. The reply-grammar puts the script preview ABOVE the verb instructions, mirroring escalation body shape. |
| Founder hits APPROVE then realizes it was wrong | Mitigation paths: (a) reject via CLI is too late — SR is now running; (b) wait for terminal-result follow-up to assess damage; (c) `kill <pid>` out-of-band if needed. v1 accepts this risk; not worse than the existing escalation APPROVE flow. |

No new auth surface, no new credentials, no new chat. Trust boundary unchanged.

## 10. Failure Modes

| Scenario | Behavior |
|---|---|
| Daemon down during agent submit | `submit_script` route fails before `notify_script_submitted` is reached; agent's `happyranch scripts submit` callback returns non-zero. Standard CLI failure path. |
| Daemon up but Feishu disabled | `Orchestrator.notifier is None` → `notify_script_submitted` is a no-op. SR proceeds via CLI/web only. |
| Feishu send fails (network, 429, etc.) | Audit `script_notify_failed`; SR remains pending; CLI/web still works. |
| Daemon crashes between send and mint | Founder sees a Feishu post but the notification row is missing. Reply hits `notification_not_found`. Founder falls back to CLI. (Same trade-off as the existing escalation send-then-mint discipline; acceptable because mint follows send by µseconds.) |
| Founder APPROVES via Feishu, run takes longer than reply_ttl_hours | Terminal-result follow-up is sent regardless of TTL — the follow-up doesn't consume a notification; it just posts to the parent thread. TTL only gates *new* reply ingest. |
| Founder REJECTs after CLI ran it | Listener calls `reject_script_from_notification`, which raises 409 not_pending. Listener audits `script_reply_rejected reason=not_pending`. Notification stays unconsumed. The founder's Feishu reply is silently lost (same as escalation-then-CLI race). |
| Run completes before the reply handler returns | Not possible — the reply handler awaits `run_script_from_notification`, which returns after kicking off the runner task (matches the HTTP route's 202-style return). The actual subprocess runs in `_run_and_persist`. |
| Two SRs from the same agent, in flight simultaneously | Each gets its own notification row (different `feishu_message_id`, same `task_id` column but different SR-NNN). `get_open_notification_for_sr(sr_id, kind)` filters by SR-NNN, so the follow-up posts to the right thread. |
| Notification expired but run already happened (CLI) | Terminal-result follow-up: `get_open_notification_for_sr` returns the expired row (we don't filter on `expires_at` for the parent lookup; the follow-up is a leaf, not a new ingest point). Founder sees the result in the original thread even days later. |
| Founder replies to an old SR thread for a different SR | `root_id` correlates to the original SR's notification; new SR has a different `feishu_message_id`. Founder's reply applies to the original SR, which by now is in some other state. Standard race handling as above. |

## 11. Testing

### 11.1 Unit tests

- **`_build_script_request_body`** — happy path; script preview truncation; missing cwd_hint defaults to `(workspace root)`; multiline rationale preserved.
- **`_build_script_result_body`** — completed/failed branches; empty stdout/stderr → `(empty)`; truncation footer; duration formatting.
- **`EscalationNotifier.send_script_request`** — happy path mints row with `kind="script_request"` and `task_id=sr_id`; send failure audits `script_notify_failed` and does NOT mint.
- **`EscalationNotifier.send_script_run_result`** — happy path calls `send_thread_reply` with built body and audits sent; send failure audits `script_run_result_notify_failed`.
- **`run_script_from_notification`** — happy path transitions pending→running with stored defaults; not_pending raises 409; cwd_missing raises 409; interpreter_unavailable raises 422.
- **`reject_script_from_notification`** — happy path transitions pending→rejected with reason; not_pending raises 409.
- **`mint_escalation_notification(..., kind="script_request")`** — accepted; unknown kind still rejected.
- **`get_open_notification_for_sr`** — returns most recent row for (sr_id, kind), regardless of consumed/expired state; returns None when no row.
- **`_dispatch_reply_action` (table-driven)** — script_request + APPROVE → run helper called + notification consumed + audit processed; script_request + REJECT → reject helper called + audit processed; script_request + REVISIT → audit reply_rejected reason=verb_mismatch, notification unconsumed; handler exception → audit reply_rejected, notification unconsumed.

### 11.2 Integration tests (`-m integration`)

- **Submit → push → APPROVE → result follow-up:** stand up a fake Feishu HTTP+WS server (matches existing pattern from `2026-05-08` spec). Drive an agent via `fake_claude.sh` to submit an SR; assert Feishu received the push (chat_id, body shape). Simulate a founder APPROVE reply event; assert the SR transitions running→completed; assert a threaded follow-up is sent with the terminal result.
- **Submit → push → REJECT:** same scaffolding, founder replies REJECT; assert SR transitions to rejected; assert no follow-up post.
- **Submit → push → verb_mismatch:** founder replies REVISIT; assert audit `script_reply_rejected reason=verb_mismatch`; notification unconsumed; SR still pending.
- **Disabled config:** `feishu_notifications.enabled=false`; assert no HTTP traffic to fake Feishu; SR proceeds via CLI only.
- **CLI-initiated run (no Feishu notification):** founder runs SR via `happyranch scripts run`; assert no follow-up post sent (because `get_open_notification_for_sr` returns None).

### 11.3 What we explicitly skip

- WebSocket reconnect (SDK responsibility — already covered by existing tests).
- Feishu signature verification (SDK responsibility).
- Real Feishu API in CI (no credentials).

## 12. Rollout Order

Each step is independently merge-safe:

1. **DB + audit** — `mint_escalation_notification` accepts `script_request`; `get_open_notification_for_sr` helper; six `log_script_*` methods. Tests for each.
2. **Body builders** — `_build_script_request_body`, `_build_script_result_body`. Unit tests.
3. **Notifier methods** — `send_script_request`, `send_script_run_result`. Unit tests with mocked client.
4. **In-process route helpers** — extract `run_script_from_notification` + `reject_script_from_notification` from existing HTTP routes; refactor HTTP routes to call helpers. Unit tests.
5. **Orchestrator bridges** — `notify_script_submitted`, `notify_script_run_result`. Unit tests (no-op when notifier unset).
6. **Submit-hook** — wire `notify_script_submitted` into `submit_script` route success path.
7. **Listener dispatch** — new `script_request` branch in `_dispatch_reply_action`; pass new callables through `maybe_start_feishu_listener_for_org`. Unit tests.
8. **Terminal-result hook** — call `notify_script_run_result` inside `_run_and_persist` after audit.
9. **Integration tests** — full end-to-end scenarios from §11.2.
10. **Setup runbook update** — append a "Script requests" section to `docs/setup/feishu-notifications.md` explaining the new verb grammar and result follow-up.
11. **CLAUDE.md / README.md** — bullet under the Feishu section noting script_request as a new notification kind.

## 13. Open Questions

None blocking implementation. Deferred:

- **Notify on CLI-initiated runs?** Currently only Feishu-initiated runs get a follow-up. A future polish: post a "by-the-way" follow-up to the original SR thread when the founder uses CLI/web instead, so the Feishu thread stays in sync. Not in v1 — adds complexity for marginal benefit.
- **Card / interactive button format.** A confirm-modal-equivalent in Feishu would address the TTY-bypass concern. Same scope-out as the original escalation spec — text reply is fine for v1.
- **Per-SR override fields in reply.** Allowing `APPROVE\ncwd=other/path\ntimeout=600\n` would let the founder customize from Feishu. Not in v1; founder hops to CLI for non-default runs.
