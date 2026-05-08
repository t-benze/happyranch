# Feishu-Driven Escalation Notifications

**Status:** Design approved, pending implementation
**Author:** Founder + Claude Opus
**Date:** 2026-05-08
**Supersedes:** —
**Earlier-iteration scrap:** an alimail/email draft was explored and discarded; preserved at git tag `wip-alimail-2026-05-08` for reference only.

## 1. Problem

When a task transitions to `BLOCKED/ESCALATED`, the only signal to the founder today is a database row visible via `opc tasks` / `opc details`. The founder must actively poll the system to discover that work is waiting. There is no push notification.

This spec wires up the Feishu (Lark) Open Platform so that:

- The daemon sends a Feishu message to a configured 1:1 chat between the founder and a self-built bot when an escalation transition commits.
- The founder replies *in the message thread* with `APPROVE` or `REJECT` plus a free-form rationale.
- A long-lived event listener parses the reply and calls the existing `resolve_escalation` route in-process — same code path as `opc resolve-escalation`.

The CLI command remains authoritative and unchanged. Feishu is an additional surface.

The chat identifier (`chat_id`) is configured per org. Feishu's native message threading is the correlation key — no body-embedded token is needed because the bot only receives messages in chats it's a member of, and replies are matched by `root_id`.

## 2. Non-Goals

- **No notifications for non-escalation events.** Talks, completions, agent enrollments, performance-tier flips remain CLI/SSE-driven.
- **No replacement of the CLI.** `opc resolve-escalation` continues to work with identical semantics. If Feishu is disabled or fails, the founder uses the CLI.
- **No multi-recipient escalation.** One chat per org. Group escalation chains, on-call rotations, multi-founder setups are deferred.
- **No interactive cards in v1.** All messages use `msg_type: post` (rich-text Feishu post format). Card-with-buttons is a future tightening; until then, the reply protocol is text-only.
- **No HTTP callback mode for events.** We use Feishu's WebSocket event subscription, which works from a localhost daemon without exposing a public URL or running ngrok.
- **No retries / DLQ for outbound send failures.** Best-effort: log + audit. Founder can resolve via CLI if Feishu never delivered.
- **No additional channels in v1.** A `provider:` field exists in config to keep the seam clean (`feishu` is the only valid value), but Slack/Discord/email/etc. are out of scope.
- **No reply parsing outside the configured chat.** Messages in any other chat the bot is added to are silently dropped.
- **No backfill of pre-existing escalations.** Tasks already `BLOCKED/ESCALATED` when the feature ships do not get a notification.

## 3. User-Facing Interface

### 3.1 What the founder sees

**Outbound message** (sent by daemon when a task escalates):

```
[OPC hk-macau-tourism] TASK-152 escalated — action required

Agent:        engineering_head
Team:         engineering
Task:         TASK-152
Org:          hk-macau-tourism
Escalated at: 2026-05-08 14:22:11 UTC

--- Brief ---
Add Alipay support to the booking module.

--- Last manager summary ---
Tried two delegation rounds with dev_agent; both produced PRs that failed
QA on the same race condition in the payment-confirmation webhook. I do
not have authority to disable the webhook for testing — escalating.

--- Escalation reason ---
Manager requested founder authority to disable production webhook for a
controlled repro session.

--- To resolve ---
Reply in this thread with one of:

  APPROVE
  <your rationale>

  —or—

  REJECT
  <your rationale>

You can also resolve via CLI:
  opc resolve-escalation --org hk-macau-tourism --task-id TASK-152 \
    --decision approve|reject --rationale "..."
```

The message is sent as `msg_type: post` with a single `zh_cn` locale. Title is the first bracketed line; body is the rest as a sequence of plain-text lines.

**Reply (founder).** Founder taps the message in Feishu, picks "Reply in thread", types:

```
APPROVE
Wave the webhook for one repro session, audit the fix afterward.
```

The listener parses the first non-empty line as decision, the rest as rationale.

**On success:** the task transitions exactly as if the founder had run `opc resolve-escalation` — re-enqueued to `PENDING` on approve, set to `FAILED` on reject. An `escalation_reply_processed` audit event is logged. No confirmation message is sent (matches the silent CLI behavior).

**On failure** (parse error, expired notification, double-consume): silent drop + audit log entry. Founder sees no acknowledgement; falls back to CLI if needed. We deliberately do not echo error replies into the chat.

### 3.2 New / changed CLI surface

**None.** The CLI is unchanged. The Feishu integration is server-side only.

### 3.3 Configuration

Per-org `<runtime>/orgs/<slug>/org/config.yaml`:

```yaml
feishu_notifications:
  enabled: true
  provider: feishu                          # only "feishu" supported in v1
  region: feishu                            # feishu (CN) | lark (intl)
  chat_id: oc_xxxxxxxxxxxxxxxxxxxxxx        # 1:1 group between bot and founder
  reply_ttl_hours: 72                       # window during which a reply can resolve
```

Field semantics:

| Field | Required | Notes |
|---|---|---|
| `enabled` | yes | Master switch. `false` or block missing → notifier and listener are no-ops for this org. |
| `provider` | yes when enabled | Must be `feishu`. Reserved for future channels. |
| `region` | yes when enabled | `feishu` → CN domain (`open.feishu.cn`), `lark` → intl (`open.larksuite.com`). Maps to `lark_oapi.FEISHU_DOMAIN` / `LARK_DOMAIN`. |
| `chat_id` | yes when enabled | The chat where notifications are posted and replies are read from. Found via Feishu's `chat list` API or the bot's "joined groups" list during setup. |
| `reply_ttl_hours` | no | Default `72`. Min `1`, max `720` (30 days). After this window, replies are ignored. |

Secrets via env, never on disk:

- `OPC_FEISHU_APP_ID`
- `OPC_FEISHU_APP_SECRET`

Per-org override pattern (when one runtime hosts orgs with distinct Feishu apps):

- `OPC_FEISHU_APP_ID__<UPPER_SLUG_WITH_UNDERSCORES>` falls back to the unsuffixed env var when missing. Same convention used elsewhere in OPC.

If `enabled: true` but credentials are missing at daemon start, the daemon logs an error and skips the Feishu subsystem for that org. Other orgs unaffected; daemon does not crash.

### 3.4 Founder one-time setup

Out-of-band, before enabling per-org:

1. Create a "self-built app" at https://open.feishu.cn (or .larksuite.com for intl). Note the `App ID` and `App Secret`.
2. **Permissions** — add the following scopes:
   - `im:message` (read messages — required for the event listener)
   - `im:message:send_as_bot` (send messages)
   - `im:resource` (download attachments — used only if the founder ever sends one; harmless otherwise)
3. **Event subscription** — enable WebSocket mode (not HTTP callback). Subscribe to event `im.message.receive_v1`.
4. Add the bot to a 1:1 group chat with the founder (or just DM the bot). Note the resulting `chat_id`.
5. `export OPC_FEISHU_APP_ID=cli_xxxxx`, `export OPC_FEISHU_APP_SECRET=yyyyy`.
6. Edit `<runtime>/orgs/<slug>/org/config.yaml` to add the `feishu_notifications` block.
7. Restart daemon.

The setup steps are documented as a runbook in `docs/setup/feishu-notifications.md` (the implementation plan creates this).

## 4. Architecture

### 4.1 Modules added

```
src/infrastructure/feishu/
  __init__.py
  client.py             # FeishuClient: thin wrapper around lark-oapi for send + auth caching
  notifier.py           # EscalationNotifier — assembles message body, sends, persists correlation row
  reply_parser.py       # Pure functions: extract decision + rationale from message text

src/daemon/
  feishu_listener.py    # FeishuEventListener — per-org WS thread + handler bridging to asyncio
```

`lark_oapi` (Python SDK 1.6+, MIT, by ByteDance) is added to `pyproject.toml`. It handles tenant_access_token caching, REST request signing, and WebSocket event delivery.

### 4.2 Outbound flow (Phase 1)

```
run_step.py:
  ... existing escalation classification ...
  db.update_task(task_id, status=BLOCKED, block_kind=ESCALATED, note=reason)
  audit.log_escalation(task_id, agent, reason)
  + orch.notify_escalated(task_id, agent, reason, last_summary)   # NEW — fire-and-forget
```

`Orchestrator.notify_escalated` schedules `EscalationNotifier.notify_escalated(...)` so the orchestration loop never blocks on the network round-trip. From a thread-pool worker (the typical caller), it spawns a daemon thread that runs `asyncio.run(...)`; from an async context, it uses `loop.create_task(...)`.

`EscalationNotifier.notify_escalated`:

1. Reads `feishu_notifications` from `OrgConfig`. If disabled or missing → return immediately (no-op).
2. Builds the `post`-format content body via `_build_body_phase1`.
3. Calls `FeishuClient.send_post_message(chat_id, title, body_lines)`.
4. On success → row inserted into `escalation_notifications(feishu_message_id, org_slug, task_id, chat_id, created_at, expires_at, consumed_at=NULL, consumed_by=NULL)`. Audit `escalation_notify_sent(task_id, feishu_message_id)`.
5. On failure → audit `escalation_notify_failed(task_id, error)`. Swallow the exception.

Order is **send first, then insert**. The DB row is keyed by `feishu_message_id`, which we don't know until the send succeeds. If the send fails, no orphan row is left.

### 4.3 Inbound flow (Phase 2)

A `FeishuEventListener` is constructed per org with feishu enabled, started in the daemon's FastAPI lifespan. Each listener owns:

- A `lark_oapi.ws.Client` configured with the org's `app_id`, `app_secret`, and event handler.
- A daemon `threading.Thread` that runs `ws_client.start()` (blocking call inside the SDK).
- A reference to the daemon's asyncio event loop (captured at lifespan startup).
- The org's `OrgState` (DB, queue, etc.).

The event handler (registered via `lark_oapi.EventDispatcherHandler.builder().register_p2_im_message_receive_v1(...)`) runs in the WS thread. It bridges to async via `asyncio.run_coroutine_threadsafe(self._handle_event_async(data), self._loop)` and returns immediately. The actual processing happens on the daemon's event loop.

`_handle_event_async`:

1. **Dedup**: `INSERT OR IGNORE INTO processed_event_ids(org_slug, feishu_event_id, ...)`. If `rowcount == 0`, this is a redelivery — return.
2. **Chat filter**: `event.message.chat_id != configured chat_id` → return; outcome `wrong_chat`.
3. **Threading filter**: `event.message.root_id` must be set. Otherwise → return; outcome `not_threaded`.
4. **Sender filter**: `event.sender.sender_type` must be `user` (not `app` — the bot itself). Otherwise → return; outcome `not_user`.
5. **Notification lookup**: SELECT from `escalation_notifications` WHERE `feishu_message_id = root_id`. If missing or `consumed_at IS NOT NULL` or `expires_at < now` → return; outcome `not_found` / `consumed` / `expired`.
6. **Parse text**: `reply_parser.parse_reply(message_content)` → `ParseResult(decision, rationale)` or `None`. If None → outcome `bad_decision`.
7. **Apply**: call the in-process `resolve_escalation(slug, task_id, decision, rationale)` route function (defined in `src/daemon/routes/tasks.py`). Mark notification row `consumed_at=now`, `consumed_by="feishu-reply"`. Audit `escalation_reply_processed(task_id, decision, rationale)`. Update `processed_event_ids` outcome to `consumed`.
8. Errors at any step → audit `escalation_reply_rejected(task_id, reason)` and continue. Listener never crashes the daemon.

### 4.4 Hook points (surgical changes)

| File | Change |
|---|---|
| `src/orchestrator/run_step.py` | Add `orch.notify_escalated(...)` after each `audit.log_escalation(...)` (max-steps and manager-escalate paths). |
| `src/daemon/__main__.py` | `_sweep_on_startup` accepts an optional `orchestrator` arg; on IN_PROGRESS→FAILED transitions calls `orchestrator.notify_escalated`. |
| `src/daemon/app.py` | Lifespan: start each org's `FeishuEventListener` if configured; signal stop on shutdown. |
| `src/daemon/state.py` / `org_state.py` | Each `OrgState` carries `notifier: EscalationNotifier \| None` and `feishu_listener: FeishuEventListener \| None`. |
| `src/orchestrator/orchestrator.py` | `attach_notifier(notifier)` + `notify_escalated(...)` — fire-and-forget bridge. |
| `src/orchestrator/org_config.py` | New `FeishuNotificationsConfig` dataclass + parser; `resolve_feishu_credentials(slug)` env helper. |
| `src/infrastructure/database.py` | `escalation_notifications` and `processed_event_ids` tables + CRUD. |
| `src/infrastructure/audit_logger.py` | `log_escalation_notify_sent`, `log_escalation_notify_failed`, `log_escalation_reply_processed`, `log_escalation_reply_rejected`. |
| `pyproject.toml` | Add `lark-oapi>=1.6,<2`. |

Existing escalation logic, transition rules, parent-cascade behavior — none of it changes.

## 5. Data Model

### 5.1 New tables

```sql
CREATE TABLE escalation_notifications (
    feishu_message_id TEXT PRIMARY KEY,    -- message_id from im.message.create
    org_slug          TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    chat_id           TEXT NOT NULL,        -- redundant with org config but useful for audit
    created_at        TEXT NOT NULL,        -- UTC ISO8601
    expires_at        TEXT NOT NULL,        -- UTC ISO8601; reply window closes after this
    consumed_at       TEXT,                 -- NULL until reply or CLI fallback consumes
    consumed_by       TEXT                  -- 'feishu-reply' | 'cli-fallback' | NULL
);
CREATE INDEX idx_escalation_notifications_task ON escalation_notifications(task_id);

CREATE TABLE processed_event_ids (
    org_slug          TEXT NOT NULL,
    feishu_event_id   TEXT NOT NULL,        -- header.event_id from inbound payload
    processed_at      TEXT NOT NULL,        -- UTC ISO8601
    outcome           TEXT NOT NULL,        -- 'consumed' | 'rejected' | 'ignored'
    reason            TEXT,                 -- short tag for non-consumed paths
    PRIMARY KEY (org_slug, feishu_event_id)
);
```

`processed_event_ids` dedupes against Feishu's at-least-once event delivery. INSERT OR IGNORE on the composite PK is the lock — we never act twice on the same event.

### 5.2 Lifecycle

```
mint:    INSERT escalation_notifications after a successful im.message.create
match:   SELECT WHERE feishu_message_id = ? AND consumed_at IS NULL AND expires_at > now()
consume: UPDATE escalation_notifications SET consumed_at = now(), consumed_by = 'feishu-reply' WHERE feishu_message_id = ?
expire:  no cron; rows stay for audit. Cleanup is a future tidiness PR.
```

## 6. Reply Parsing

Pure-function module `src/infrastructure/feishu/reply_parser.py`. Input: a Feishu inbound message's text content (already extracted from the `content` JSON envelope). Output: `ParseResult(decision: Literal["approve","reject"], rationale: str)` or `None`.

Pipeline:

1. **Strip leading/trailing whitespace.**
2. **Decision extraction.** First non-empty line, uppercased, must be exactly `APPROVE` or `REJECT`. Anything else → `None`, parse_outcome `bad_decision`.
3. **Rationale extraction.** Lines after the decision line, joined with `\n`, stripped. If empty, default to `"(no rationale provided)"` (the `resolve_escalation` route requires a non-empty rationale).

Extraction handles Feishu's content envelopes:

- `msg_type=text`: content is `{"text": "..."}` → use `text` directly.
- `msg_type=post`: content is `{"zh_cn": {"title": "...", "content": [[{"tag": "text", "text": "..."}, ...], ...]}}` → flatten line-by-line into a string, joining segments with empty string and lines with `\n`.
- `msg_type=interactive`, `image`, `file`, etc. → `None`, parse_outcome `unsupported_msg_type`.

The parser is fully unit-testable with no I/O.

## 7. Feishu Integration Details

### 7.1 SDK choice

`lark-oapi` (PyPI `lark-oapi>=1.6,<2`, https://github.com/larksuite/oapi-sdk-python). MIT licensed, official, supports Python 3.8+.

### 7.2 Auth

```python
from lark_oapi.api.im.v1 import *
import lark_oapi as lark

client = lark.Client.builder() \
    .app_id(app_id) \
    .app_secret(app_secret) \
    .domain(lark.FEISHU_DOMAIN)  # or LARK_DOMAIN \
    .log_level(lark.LogLevel.INFO) \
    .build()
```

The SDK manages `tenant_access_token` lifecycle internally (fetch + refresh). We don't.

### 7.3 Send (Phase 1)

```python
content_obj = {
    "zh_cn": {
        "title": title,
        "content": [
            [{"tag": "text", "text": line}] for line in body_lines
        ]
    }
}
req = (
    CreateMessageRequest.builder()
    .receive_id_type("chat_id")
    .request_body(
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("post")
        .content(json.dumps(content_obj, ensure_ascii=False))
        .build()
    )
    .build()
)
resp = client.im.v1.message.create(req)
if not resp.success():
    raise FeishuSendError(resp.code, resp.msg)
return resp.data.message_id
```

### 7.4 Listen (Phase 2)

```python
def _on_message(data: P2ImMessageReceiveV1) -> None:
    asyncio.run_coroutine_threadsafe(
        listener._handle_event_async(data),
        listener._loop,
    )

handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(_on_message)
    .build()
)

ws_client = lark.ws.Client(
    app_id, app_secret,
    domain=lark.FEISHU_DOMAIN,
    event_handler=handler,
)

# In a daemon thread
threading.Thread(target=ws_client.start, daemon=True).start()
```

The SDK handles WebSocket connect, heartbeat, auto-reconnect, and event ack internally.

### 7.5 Region mapping

| `region` | SDK domain |
|---|---|
| `feishu` | `lark.FEISHU_DOMAIN` (CN, `open.feishu.cn`) |
| `lark` | `lark.LARK_DOMAIN` (intl, `open.larksuite.com`) |

Unknown region → config validation error.

## 8. Security Model

| Threat | Defense |
|---|---|
| Spoofed reply with attacker-controlled sender | The bot only receives messages from chats it's a member of. The configured `chat_id` is a 1:1 group between bot and founder. Only the founder can post in that chat. |
| Replay of an old reply (Feishu redelivery) | `processed_event_ids` dedup table — first event_id wins. |
| Unauthorized chat reuse | `chat_id` filter in handler. Other chats the bot may be in (e.g., test groups) are silently ignored. |
| Wrong-thread reply targeting | `root_id` must match a `feishu_message_id` from our notifications table. Stray messages in the chat (not threaded) are dropped. |
| Token consumption race | `UPDATE escalation_notifications SET consumed_at = ? WHERE feishu_message_id = ? AND consumed_at IS NULL` — atomic in SQLite; double-reply hits rowcount=0 and silently no-ops. |
| Subprocess injection via reply text | Reply text is passed as a string to the typed `resolve_escalation` route; route writes via parameterized queries; no shell. |
| App credential leak | Stored only in env vars, never written to disk. SDK does not log the secret. |

Audit log entries for every decision point (sent / failed / reply_processed / reply_rejected with reason) — queryable via `opc audit`.

## 9. Configuration Resolution

`OrgConfig` extends with `feishu_notifications: FeishuNotificationsConfig | None`.

Resolution rule:

- File missing or block missing → `feishu_notifications = None` → notifier and listener skip this org.
- Block present with `enabled: false` → same.
- Block present with `enabled: true` but secrets missing in env → log error at daemon start, skip Feishu subsystem for this org only.
- Block present with `enabled: true` and secrets present → fully active.

Hot-reload not supported (consistent with other config); founder restarts daemon to pick up changes.

## 10. Testing

### 10.1 Unit tests

- **DB CRUD** (`tests/test_database.py` extensions) — mint, get, consume escalation_notifications; insert dedup processed_event_ids.
- **`OrgConfig`/`FeishuNotificationsConfig` parser** — happy path, missing block, partial block, invalid region, invalid TTL bounds, missing required field.
- **`resolve_feishu_credentials`** env helper — per-org override, default fallback, missing returns None tuple.
- **`reply_parser.parse_reply`** — table-driven:
  - APPROVE clean
  - REJECT clean
  - lower/mixed-case decision accepted
  - multi-line rationale preserved
  - empty body → None
  - first line `MAYBE` → None (bad_decision)
  - text content extracted from `msg_type=text` envelope
  - text content extracted from `msg_type=post` envelope (zh_cn)
  - `msg_type=interactive` → None (unsupported_msg_type)
- **`FeishuClient.send_post_message`** — using a stub SDK client (mocked `client.im.v1.message.create`); verifies receive_id_type, content JSON shape, error propagation.
- **`EscalationNotifier`** — happy path; send failure → audit failed; missing task → no-op.
- **`Orchestrator.notify_escalated`** — fire-and-forget non-blocking from sync caller; no-op when notifier unset.
- **`FeishuEventListener._handle_event_async`** — table-driven:
  - dedup first call accepts, second drops
  - chat_id mismatch → drop
  - missing root_id → drop
  - sender_type=app → drop
  - notification not found → drop
  - notification consumed → drop
  - notification expired → drop
  - APPROVE → resolve_escalation called with approve, rationale, and notification consumed
  - REJECT → same with reject

### 10.2 Integration tests (`-m integration`)

- **Phase 1 outbound**: stand up a fake Feishu HTTP server (FastAPI) that mimics `tenant_access_token/internal` and `im/v1/messages` endpoints. Drive a real `Orchestrator.run_step` max-steps escalation through `OrgState.load` → `notify_escalated`. Assert: token endpoint hit, message endpoint hit, audit row written, notification row inserted with the returned `message_id`.
- **Phase 1 disabled-config**: same scaffolding, `enabled: false`. Assert: no HTTP traffic to fake Feishu, audit log has `escalation` but no `escalation_notify_*`.
- **Phase 2 listener**: directly invoke `FeishuEventListener._handle_event_async` with constructed `P2ImMessageReceiveV1` fixtures. Assert state transitions on the underlying task. **No real WebSocket is stood up** — the SDK's WS layer is treated as I/O we don't unit-test. The listener's `start()` method is exercised in a smoke test that confirms the WS thread is created and the handler is wired, but doesn't connect.

### 10.3 What we explicitly skip

- Real Feishu API in CI (would require real credentials).
- WebSocket reconnect behavior (SDK responsibility).
- Feishu's signature verification (SDK responsibility).

## 11. Phasing Within This Spec

Phase 1 (outbound) and Phase 2 (listener) ship in the same plan. Phase 1 lands as a logical commit group first (DB, config, client, notifier, run_step hooks, OrgState wiring, integration test). Phase 2 builds on it (listener module, reply_parser, processed_event_ids table, lifespan integration, listener tests).

The daemon can ship Phase 1 alone if Phase 2 needs another iteration — the Phase 1 message body advertises both reply and CLI paths since both work once Phase 2 lands; if we ship Phase 1 alone, the body wording would say "reply support coming soon" temporarily. **However the user has confirmed both phases ship together**, so the body text uses the both-paths shape.

## 12. Open Questions

None blocking implementation. Items below are deferred design choices:

- **Token cleanup cron.** Expired notification rows accumulate. A future tidiness PR can sweep.
- **Multi-channel future.** The `provider:` field in config is reserved. If a future need is Slack or another platform, the seam is `EscalationNotifier`'s constructor — swap the inner client.
- **Interactive cards (Phase 3).** Cards with APPROVE / REJECT buttons would let the founder act with one tap. Requires switching from `msg_type=post` to `msg_type=interactive` and handling `card.action.trigger` events. Out of scope for this spec — text reply is fine for v1.
