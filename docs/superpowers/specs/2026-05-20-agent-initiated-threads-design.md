# Agent-initiated threads — Design Spec

**Date:** 2026-05-20
**Status:** Draft, pending implementation plan.
**Amends:** `docs/superpowers/specs/2026-05-13-threads-design.md` — specifically its §2 non-goals "Agent-initiated threads" and "Agent-to-agent reply chains without founder involvement". This spec is the formal lift of those bars.
**Relates to:** `docs/superpowers/specs/2026-04-26-talk-dispatch-design.md` (talk→task auth pattern reused for talk→thread), `docs/superpowers/specs/2026-05-08-feishu-notification-design.md` (founder push when `@founder` addressed), `protocol/skills/dispatch/SKILL.md` (sibling agent-initiated primitive).

## 1. Goal

Let an agent compose a new thread from inside a task or talk session, addressing any combination of other agents and the founder. The fan-out, reply, decline, dispatch, archive, and close-out machinery stays exactly as in the v1 thread spec. The only deltas are:

- the **composer** can be an agent, not just the founder;
- the new authority model is **liberal** — any agent may address any approved agent in the org, no team/role gates;
- `@founder` is a **recognized addressee** (not stored as a `thread_participants` row); when present, the existing Feishu/inbox surfaces notify the founder.

Use case examples:

- A worker mid-task realizes they need clarification from a peer on another team — they compose a thread including that peer and continue executing what they can.
- A manager wants a written record of a cross-team coordination decision — they compose a thread with the other team's manager and `@founder` so the founder has the trail.
- Inside a talk, the agent says "I'll loop in `payment_agt` so the context isn't lost" — they compose a thread targeting `payment_agt` (founder is already present in the terminal; addressing them is optional).

## 2. Non-goals

Still founder-only, unchanged from v1:

- Inviting a new participant mid-thread (`POST /threads/{id}/invite`).
- Archiving / abandoning a thread.
- Bumping `turn_cap` via `/extend`.
- Forwarding (forward stays a founder UI action — agent forwarding is not a v2 ask).
- Founder authoring inside an agent subprocess. Founder still drives their side from CLI/Web.

Also out of scope:

- Composer auto-selection rules ("if the brief mentions X, auto-create a thread to Y"). Agents decide when to compose.
- Per-agent compose budgets / daily quotas. Audit-only in v1; add throttling later if abuse surfaces.
- A new `@founder` invocation surface — the founder is not a subprocess. `@founder` addressing routes to Feishu + inbox, not to the `ThreadInvocationRunner`.
- Cross-org thread composition.

## 3. Data model

### 3.1 Schema deltas

Idempotent ALTERs on daemon startup (mirrors the existing `dispatched_from_*` pattern):

```sql
ALTER TABLE threads ADD COLUMN composed_by TEXT NOT NULL DEFAULT 'founder';
ALTER TABLE threads ADD COLUMN composed_from_task_id TEXT;
ALTER TABLE threads ADD COLUMN composed_from_talk_id TEXT;
CREATE INDEX IF NOT EXISTS idx_threads_composed_from_task
    ON threads(composed_from_task_id) WHERE composed_from_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_threads_composed_from_talk
    ON threads(composed_from_talk_id) WHERE composed_from_talk_id IS NOT NULL;
```

- `composed_by` is either `"founder"` (legacy + future founder composes) or an agent name. Defaults preserve all existing rows.
- `composed_from_task_id` / `composed_from_talk_id` are mutually exclusive (daemon enforces at insert: at most one non-NULL); together they're mutually exclusive with `composed_by='founder'` (founder composes have neither set).
- Both columns are **sideways refs** — `walk_ancestors` MUST NOT follow them, same rule as `dispatched_from_*`.

### 3.2 ThreadRecord change

Add to `src/models.py:ThreadRecord`:

```python
composed_by: str = "founder"
composed_from_task_id: str | None = None
composed_from_talk_id: str | None = None
```

`_thread_row_to_dict` in `src/daemon/routes/threads.py` surfaces all three fields. The web `lib/api/threads.ts` mirror picks them up via the OpenAPI snapshot test.

### 3.3 `@founder` addressing

`thread_messages.addressed_to_json` may contain `"@founder"` as a literal addressee name (in addition to today's `["@all"]` and concrete agent names).

`thread_participants` is **not** extended. Founder remains implicit. The `@founder` token is a routing marker on the message, not a participant row. Consequence: every existing code path that iterates `thread_participants` continues to behave correctly (founder never receives a `ThreadInvocation`).

For agent-initiated composes, the composer themselves IS added to `thread_participants` with `added_by=<composer>` so they receive subsequent reply fan-outs like any other participant.

### 3.4 Audit-log additions

- `thread_started` payload gains `composed_by`, `composed_from_task_id`, `composed_from_talk_id` (any of which may be null).
- New action `thread_founder_addressed` (scope: thread_id) emitted whenever a message's `addressed_to` includes `@founder`. Payload: `{seq, speaker, notify_channel: "feishu"|"none"}`. Distinct event so founder-attention queries don't have to JSON-parse `addressed_to`.

No new tables.

## 4. Authority and addressing

### 4.1 Composer authority (liberal)

Any approved agent in the org may compose a thread targeting any combination of:

- one or more other approved agents in the same org (cross-team OK);
- optionally `@founder`.

No role gate. No team gate. The composer is added to `thread_participants` and shows in `recipients` for symmetry with reply fan-out logic.

Self-only addressing is rejected (a thread with the composer as the only addressee is pointless): if after deduplication `recipients == [composer]` and `@founder` is not present, return 422 `empty_external_recipients`.

### 4.2 Founder addressing

The founder is "addressed" by including `"@founder"` in either:

- the message's `addressed_to` field (must also appear in `recipients` for non-`@all` addressing), OR
- the compose request's `recipients` list with `addressed_to: ["@all"]`.

When `@founder` is addressed:

- Feishu push fires via the existing `EscalationNotifier` mechanism, with a new payload kind `thread_addressed`. Reusing the inbound listener path means a founder reply via Feishu can land back on the thread (see §6.2 for the read side).
- The thread appears in `GET /threads` results regardless of any filter — it is by definition "open with founder attention pending."

When `@founder` is NOT addressed, the thread is agent-to-agent. The founder still sees it via `happyranch threads list` (god-mode read), but no proactive push fires. This is the explicit cost-of-attention tradeoff: silent inter-agent threads are observable but not interruptive.

### 4.3 Addressing semantics with `@founder`

The §4.3 table from the v1 spec extends:

| Addressing | Who is invoked | Founder notified? |
|---|---|---|
| Specific agent names (e.g., `[engineering_head]`) | Just those agents | No |
| `[engineering_head, @founder]` | engineering_head | Yes, this message |
| `["@all"]` (and `@founder` in `recipients`) | Every participant except the speaker | Yes, this message |
| `["@founder"]` only | Nobody (no agent invocations) | Yes, this message |

A founder-only-addressed message is allowed and does NOT count toward `turns_used` (no agent invocations are minted). Use case: an agent leaves a written note explicitly for the founder, with other agents on the thread as silent observers.

## 5. HTTP API

### 5.1 New route — `POST /api/v1/orgs/{slug}/threads/compose-as-agent`

The founder route `POST /threads` is left untouched. Agent composes go through a distinct route because the auth model differs — bearer alone is not enough; the composer must prove an active session or talk binding.

Request:
```json
{
  "composer": "engineering_head",
  "task_id": "TASK-091",
  "session_id": "8b3f-...-e91a",
  "talk_id": null,
  "subject": "45-day refund window — implementation handoff",
  "recipients": ["payment_agt", "qa_engineer"],
  "addressed_to": ["payment_agt", "@founder"],
  "body_markdown": "Pinning down the contract between payment + QA..."
}
```

Required: `composer`, `subject`, `recipients`, `body_markdown`. Exactly one of `task_id`+`session_id` or `talk_id` must be set (the other side null/omitted). `addressed_to` defaults to `["@all"]` (with `@founder` included if present in `recipients`); the literal `@founder` token is allowed alongside concrete agent names but the `@all`-mixing rule from v1 (§5.1) still applies — `["@all", "@founder"]` is rejected.

### 5.2 Validation order

Each step gates the next:

1. **Composer is an approved agent** with a workspace under `<runtime>/orgs/<slug>/workspaces/<composer>/`. Else 404 `unknown_composer`.
2. **Exactly one binding** (`task_id`+`session_id` XOR `talk_id`). Else 422 `binding_required` / `binding_ambiguous`.
3. **Task path** (if `task_id` set):
   - Task exists and `assigned_agent == composer`. Else 404 `unknown_task` / 403 `composer_not_task_owner`.
   - Active session for `task_id` matches `session_id` (same `SessionTracker.expected_session_id` check used by `happyranch report-completion`). Else 409 `session_mismatch`.
   - Task `status` ∈ {`pending`, `in_progress`}. Else 400 `task_not_active`.
4. **Talk path** (if `talk_id` set):
   - Talk exists. Else 404 `unknown_talk`.
   - `talk.agent_name == composer`. Else 403 `composer_not_talk_owner`.
   - `talk.status == OPEN`. Else 400 `talk_not_open`.
5. **Subject** non-empty after strip. Else 422 `empty_subject`.
6. **Recipients** non-empty after dedup. Each name (excluding the literal `@founder`) must be an approved agent with a workspace. Else 404 `unknown_agent`.
7. **External recipients required**: after dedup and removing the composer, `recipients` minus `{composer}` must be non-empty OR `@founder` must be addressed. Else 422 `empty_external_recipients`.
8. **`body_markdown`** non-empty after strip. Else 422 `empty_body`.
9. **`addressed_to`** is either `["@all"]` or a non-empty subset of `recipients`. The literal `@founder` is a permitted member of `recipients` (it skips the agent-existence check in step 6) and is therefore also permitted in `addressed_to` whenever it's present in `recipients`. Else 422 `addressed_to_not_subset`. `@all` mixed with explicit names is still rejected per v1.
10. **Turn cap**: `addressed_count` (agent-only — `@founder` doesn't count) + `0` (new thread) ≤ `turn_cap`. Else 429 `turn_cap_exceeded` (almost impossible at thread start; included for symmetry).

### 5.3 Effect

Single transaction under `org.db_lock`:

1. Allocate `thread_id = state.db.next_thread_id()`.
2. Insert `threads` row with `composed_by=composer`, `composed_from_task_id` OR `composed_from_talk_id` (depending on binding), `turn_cap` from org config.
3. For each name in `recipients` (after dedup), insert `thread_participants` with `added_by=composer`. **Composer is added too** so reply fan-out reaches them.
4. Insert message at `seq=1`: `speaker=composer`, `kind="message"`, `addressed_to_json` from body.
5. Audit `thread_started` with the new payload fields.
6. Audit `thread_message_sent`.
7. If `@founder` is in `addressed_to` (resolved — `@all` expands to all participants but `@founder` is a literal addressee, not a participant): audit `thread_founder_addressed`.
8. For each addressed concrete agent (not `@founder`, not the composer), mint a pending `thread_invocations` row (purpose=`reply`).

Outside the lock:

- Enqueue invocation tokens on `org.thread_queue`.
- If `@founder` addressed: fire a Feishu push (see §6.2) via the loop-aware notifier helper. Failure is swallowed and audited as `thread_founder_notify_failed`.
- Publish `thread_started` SSE event.

Response:
```json
{
  "thread_id": "THR-019",
  "started_at": "2026-05-20T14:22:10Z",
  "composed_by": "engineering_head",
  "composed_from_task_id": "TASK-091",
  "composed_from_talk_id": null,
  "pending_replies": ["payment_agt"],
  "founder_notified": true
}
```

`pending_replies` lists agent names that received an invocation. `founder_notified` is `true` iff `@founder` was in `addressed_to` AND the Feishu config for the org is enabled AND the send was attempted (a swallowed failure still reports `true` — the caller cannot rely on push delivery, only on the audit log).

### 5.4 No change to other routes

All other thread routes (`/send`, `/reply`, `/decline`, `/dispatch`, `/close-out`, `/invite`, `/archive`, `/abandon`, `/extend`, `/messages`, `/tail`, `/events`) are unchanged. In particular:

- `POST /threads/{id}/send` (founder reply to existing thread) remains founder-bearer-only. Agents reply via `/reply`, not `/send`, regardless of whether they composed the thread.
- `POST /threads/{id}/invite` remains founder-only. The composer cannot invite further participants after the thread is started.

This keeps the founder authority surface intact: the composer started the thread, but the founder still owns its membership and lifecycle.

## 6. Founder UX

### 6.1 Inbox / list

`GET /threads` results carry the new `composed_by` field on each thread row. The Web UI thread list shows a small attribution chip (e.g., `started by engineering_head`) for agent-composed threads; founder-composed threads continue to render with no chip. No new endpoints; the list filter set is unchanged (still by `status`).

A future enhancement (out of v1 scope): filter `?composed_by=agent` to scope the inbox. Add when the founder has enough agent-initiated threads to need it.

### 6.2 Feishu push when `@founder` addressed

`EscalationNotifier` gains a new kind `thread_addressed` alongside today's `escalation` and `failure` payloads. Message body shape (rendered by the existing card template):

```
Thread THR-019 · started by engineering_head
Subject: 45-day refund window — implementation handoff
Recipients: payment_agt, qa_engineer, @founder

Pinning down the contract between payment + QA...
```

Stored in `escalation_notifications` (existing table, no schema change — the `kind` column already holds a free string). The inbound Feishu listener (`src/daemon/feishu_listener.py`) needs to recognize replies to `thread_addressed` messages: the reply text becomes a founder reply on the thread, routed through `POST /threads/{id}/send` server-side. This is the **same architectural pattern as today's escalation reply** — the listener resolver function differs by kind. Add a new resolver `reply_to_thread_from_notification(org, message_id, founder_text)` in `src/infrastructure/feishu/notifier.py` (or a sibling module) that calls into the threads route logic in-process.

If Feishu is not configured for the org, the `thread_addressed` push is a no-op (same as today's escalation behavior).

### 6.3 No proactive push for agent-to-agent (no `@founder`)

When `@founder` is NOT addressed, no push fires. The founder sees the thread in `happyranch threads list` and in the Web UI inbox. This is the deliberate "silent observation" mode.

## 7. CLI

### 7.1 Extended `happyranch threads compose`

The existing `cmd_threads_compose` (single CLI subcommand) gains optional flags:

```
happyranch threads compose [--org <slug>] --from-file <path>
    [--task-id TASK-NNN --session-id <sid>]
    [--talk-id TALK-NNN]
```

Behavior:

- If `--task-id`/`--session-id` or `--talk-id` is present, the CLI calls the new agent-compose route. The `--from-file` JSON must contain `composer`; the CLI does NOT default-fill it (forces explicit declaration).
- If neither is present, the CLI calls today's founder `POST /threads` route. `composer` in the JSON is ignored if present (founder mode).

This keeps the single-subcommand discipline (matches `happyranch threads ... --from-file` pattern). The flag presence is the routing signal.

### 7.2 JSON payload shape

`/tmp/thread-compose-agent.json`:

```json
{
  "composer": "engineering_head",
  "subject": "45-day refund window — implementation handoff",
  "recipients": ["payment_agt", "qa_engineer"],
  "addressed_to": ["payment_agt", "@founder"],
  "body_markdown": "..."
}
```

`task_id` / `session_id` / `talk_id` come from CLI flags, not the JSON, so the agent skill doesn't have to template them into the file body. The CLI merges flags + file into the request body server-side.

### 7.3 Allow-rule baseline

The agent's existing `Bash(happyranch *)` allow rule covers `happyranch threads compose ...`. No frontmatter `allow_rules` changes anywhere. The single-line `--from-file` discipline is mandatory — same reason as every other agent callback.

## 8. Skill updates

### 8.1 `protocol/skills/thread/SKILL.md` — new section "Compose a new thread"

Add after the "Close-out" section, before "What NOT to do":

```markdown
## Compose a new thread (from inside a task or talk)

Use this when:

- You need written async input from another agent and aren't blocked enough
  to justify an escalation.
- You want a durable record of a cross-team coordination decision.
- You're inside a talk and want to loop in an agent who isn't present.

Requirements:

- You are currently in an active task session (you have a `task_id` + `session_id`
  from `start-task`) OR an open talk (`talk_id` from `/talk start`).
- You name the OTHER agents you want in the thread. You may also include
  `@founder` if you want the founder pushed via Feishu (and otherwise notified).

### Procedure

1. Write `/tmp/thread-compose-<short-tag>.json`:

   {"composer": "<your name>",
    "subject": "<≤120 chars>",
    "recipients": ["agent_a", "agent_b"],
    "addressed_to": ["@all"] OR a subset of recipients (+ optional "@founder"),
    "body_markdown": "<the message>"}

2. From a task, single-line:

   happyranch threads compose --org <slug> --task-id <TASK> --session-id <SID> --from-file /tmp/thread-compose-<tag>.json

   From a talk:

   happyranch threads compose --org <slug> --talk-id <TALK> --from-file /tmp/thread-compose-<tag>.json

3. Capture the returned `thread_id`. Mention it in your task completion
   summary (or talk transcript) so the founder can find it.

### Authority

- Any agent → any agent. No team or role gate.
- You are automatically added as a participant; replies will come back to you
  on a future invocation, NOT in your current session.

### When NOT to compose

- The work is yours to do → don't outsource it via a thread. Do the work
  (or dispatch a task to yourself).
- You're blocked and need founder intervention → use `status: "blocked"` on
  `report-completion` instead. Threads are for conversation, not escalation.
- You'd be sending the same content to every agent — that's a broadcast, not
  a conversation. Talk to the founder first.
- You're already on a thread that covers the same topic → reply there.
```

### 8.2 `protocol/skills/start-task/SKILL.md` — cross-reference

Add a brief mention in step 4 ("Plan and execute"):

> If during the task you realize you need async input from another agent (and you're not yet blocked), consult `protocol/skills/thread/SKILL.md` "Compose a new thread" rather than escalating.

### 8.3 `protocol/skills/talk/SKILL.md` — cross-reference

In the "What NOT to do" exceptions list, add:

> **Exception:** Composing a thread to loop in another agent is allowed via the talk-path payload (`--talk-id` on `happyranch threads compose`). See the `thread` skill. Record the thread_id in your `transcript_markdown` so the founder has a record at talk-end.

## 9. Configuration

No new `org/config.yaml` fields. The feature is always-on when threads are enabled (`threads.enabled: true` — already true by default per the v1 spec). Disabling threads entirely also disables agent compose; that's the only kill switch v1 needs.

`threads.default_turn_cap` continues to govern the fan-out cap on agent-composed threads.

## 10. Error handling (additions)

| Condition | Response |
|---|---|
| `composer` is not an approved agent in this org | 404 `unknown_composer` |
| Neither `task_id`+`session_id` nor `talk_id` is set | 422 `binding_required` |
| Both task and talk binding set | 422 `binding_ambiguous` |
| `task_id` exists but `assigned_agent != composer` | 403 `composer_not_task_owner` |
| `session_id` doesn't match the active session for `task_id` | 409 `session_mismatch` |
| Task is not in `{pending, in_progress}` | 400 `task_not_active` |
| `talk_id` exists but `agent_name != composer` | 403 `composer_not_talk_owner` |
| `recipients - {composer}` is empty AND `@founder` not addressed | 422 `empty_external_recipients` |
| `@founder` in `addressed_to` but not in `recipients` | 422 `addressed_to_not_subset` |
| `addressed_to == ["@all"]` AND `@founder` in `recipients` | Accepted; `@all` includes `@founder` for notification purposes |
| Feishu config missing / push failed | Thread is still created; `thread_founder_notify_failed` audited; response `founder_notified: true` (attempt was made) |

All other v1 errors (turn-cap, unknown recipient, etc.) flow through unchanged.

## 11. Testing

### 11.1 Unit

- Schema migration: `composed_by` defaults to `"founder"` on existing rows; ALTERs are idempotent.
- Compose-as-agent validation: each step of §5.2 gated correctly.
- Liberal authority: `compose-as-agent` from a worker addressing a manager on a different team succeeds.
- Composer is auto-added to `thread_participants` and shows in `recipients` of the resulting thread row.
- `@founder` in `addressed_to`: emits `thread_founder_addressed` audit row; does NOT mint a `thread_invocations` row for `@founder`.
- `@founder` only in `addressed_to`: thread created with zero pending invocations.
- Self-only addressing: 422 `empty_external_recipients`.
- Binding XOR: both/neither rejected.
- Session/talk binding mismatches: `unknown_task`, `composer_not_task_owner`, `session_mismatch`, `task_not_active`, `composer_not_talk_owner`, `talk_not_open`.
- Mutual exclusion of `composed_from_task_id` / `composed_from_talk_id` at insert.
- Founder route `POST /threads` is unchanged — existing tests pass.

### 11.2 Integration

A new fixture extension is needed: `fake_claude.sh` already routes thread prompts to `$FAKE_CLAUDE_THREAD_PLAN`. For agent-initiated compose, the composer is INSIDE a task or talk — so the task-side `$FAKE_CLAUDE_PLAN` (or talk-side `$FAKE_CLAUDE_TALK_PLAN` if present) needs to invoke `happyranch threads compose ... --task-id ... --session-id ...`. Add this exercise to:

- `tests/integration/test_threads_e2e.py::test_agent_compose_from_task_creates_thread_and_invokes_recipients`
- `tests/integration/test_threads_e2e.py::test_agent_compose_from_task_with_founder_addressed_fires_feishu` (mocks the Feishu client; asserts the message_id row in `escalation_notifications`)
- `tests/integration/test_threads_e2e.py::test_agent_compose_from_talk_creates_thread`
- `tests/integration/test_threads_e2e.py::test_compose_with_invalid_session_rejected`
- `tests/integration/test_threads_e2e.py::test_composer_appears_as_participant_and_receives_reply_fanout`

The last test is the key behavioral check: composer A composes thread targeting B → B replies → A's next invocation (on the same thread) has the full history including A's own opening message.

### 11.3 Contract pinning

`tests/contract/test_openapi_snapshot.py` will pick up the new route automatically; regenerate snapshot:

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py
```

`web/src/test/openapi-coverage.test.ts` will fail until the new path is listed in `INCLUDED_PATHS` or `EXCLUDED_PATHS`. Since the Web UI doesn't currently use agent-callback routes, list `POST /api/v1/orgs/{slug}/threads/compose-as-agent` under `EXCLUDED_PATHS` with reason `"agent callback — not exercised from the Web UI"`.

## 12. Migration / backward compatibility

Pure additive change:

- Three new columns (defaulted).
- One new HTTP route.
- No CLI subcommand rename.
- No skill rename.
- Existing data migrates implicitly (`composed_by = 'founder'` for every existing row).

Rollback: drop the new route (404 returns); the new columns are harmless (founder composes ignore them). No data corruption risk.

## 13. Out-of-scope reminders

(See §2 for the full list. Repeated here as a "do not slip into" pointer.)

- Agent inviting more participants mid-thread.
- Agent archive / abandon / extend.
- Agent forward composition.
- Per-agent compose budgets.
- `@founder` as a subprocess-invokable participant.
- `compose-as-agent` from outside an active task/talk (e.g., from a cron or batch context).

## 14. Open implementation choices (not gating the design)

- Whether `happyranch threads compose` autodetects `--task-id` vs. `--talk-id` from environment (e.g., `HAPPYRANCH_TASK_ID`) when called from inside a session. Recommendation: NO autodetect in v1. Skill spells out the flags explicitly so failures are localizable.
- Whether to surface `composed_from_task_id` / `composed_from_talk_id` in `happyranch threads show` output. Recommendation: yes — one extra line in the human view, near-zero cost.
- Whether the Feishu card for `thread_addressed` includes the full body or a 200-char preview. Recommendation: preview + a "open in Web UI" deeplink, same as today's escalation cards.
