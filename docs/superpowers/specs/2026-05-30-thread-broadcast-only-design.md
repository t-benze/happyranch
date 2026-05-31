# Threads: Broadcast-Only, Remove `addressed_to` — Design Spec

**Date:** 2026-05-30
**Status:** Draft, pending implementation.
**Origin:** Founder-reported pattern on THR-011 (tourism-org, 2026-05-29): the founder addressed seq 10 to `["finance_agent"]` only; finance_agent's seq 11 prose-mentioned `@admin_head 你那边合同库归档...` but its structured `addressed_to_json` was empty; no invocation was ever minted for admin_head, so the hand-off was silently dropped. Founder diagnosis: structured `addressed_to` invites exactly this class of silent-drop bug, and every reasonable thread should broadcast to all participants.
**Relates to:**
- `docs/superpowers/specs/2026-05-13-threads-design.md` — the threads primitive this changes.
- `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md` — orthogonal rule; both reinforce "threads are broadcast coordination, task trees are iterative delegation".
- `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md` — task-followup invocation mechanics; unchanged.
- `protocol/skills/thread/SKILL.md` — agent-facing thread skill; operational mechanics stay, judgment doctrine moves to the invocation prompt.

## 1. Goal

Remove the structured `addressed_to` concept from threads entirely. Every `kind=message` written to a thread mints a `REPLY` invocation for every participant except the speaker. Agents decide reply-or-decline per-message using a doctrine injected into the thread-invocation prompt; declines are silent (status-only, no transcript row). Founder Feishu pushes for in-thread messages are removed; the web UI is the sole surface for thread participation.

## 2. Motivation

THR-011 evidence (tourism-org, 2026-05-29):

- Seq 8 (admin_head): terminal reply, waiting on founder's accounting-policy ruling.
- Seq 9 (system): founder adds `finance_agent` as participant.
- Seq 10 (founder, addressed_to=`["finance_agent"]`): "@finance_agent 确认一下记录".
- Seq 11 (finance_agent, addressed_to=empty): substantive reply; prose says "@admin_head 你那边合同库归档 + 法务台账继续推进".
- Seq 12 (finance_agent, addressed_to=empty): confirms record.
- No invocation minted for admin_head after seq 8. admin_head goes dormant. Thread stalls.

Root cause (verified in this codebase, 2026-05-29):

- `routes/threads.py::_resolve_addressed_agents` mints `REPLY` invocations from the structured `addressed_to_json` field only. Body @-mentions are visible content, not routing signals.
- The founder's CLI (`src/cli.py:1999`) hardcodes `"addressed_to": ["@all"]`, so CLI users have always been broadcasting. Selective addressing exists only on the web UI composer and on agent-side `compose-as-agent` / `send` / `reply` JSON payloads. The bug surface is "narrow addressing via web UI or agent payload" — exactly what THR-011 hit.
- The `_verify_addressed` reply-eligibility check (current invariant: speaker must be in `addressed_to` or in an `@all` broadcast to reply) further constrains who can engage. Removing addressing also removes this constraint.

**System-design observation:** the existing kernel already supports the right pattern (broadcast on every message + agent-side discipline on whether to reply). The structured-addressing field was an early-design over-engineering — it offered selective routing as a power feature but landed as a footgun. Removing it forces the simpler model where the agent is the only entity that decides "should I reply to this?".

## 3. Non-goals

**Out of scope for v1:**

- Dropping the `thread_messages.addressed_to_json` column. Kept as nullable, unread by new code, scheduled for cleanup in a later release.
- Dropping `thread_messages.kind='decline'` rows or the `decline_reason` column. Old rows readable for audit; new code never writes either.
- Body @-mention parsing. We considered it; it relocates the same fragility under an unstructured surface. Skill discipline in the invocation prompt is the only routing signal.
- "Expected responders" hint (a softer `addressed_to`). Rejected as likely to collapse back into the original concept.
- Runtime ping-pong brake beyond the existing `turn_cap`. We accept that an over-eager agent pair can burn through a thread; audit logs make it visible.
- `notify_thread_compose` (the founder-side Feishu push when an agent **opens** a new thread). Kept — the founder needs the heads-up to even know the thread exists. Only in-thread back-and-forth pings are removed.
- Top-level founder dispatch via Feishu (`allow_dispatch=true`). Unchanged — orthogonal feature.

## 4. The rule

For every `thread_messages` row inserted with `kind='message'` (whether via founder compose, founder send, agent compose-as-agent, or agent reply):

```
For each participant p in thread.participants:
    if p == speaker: continue           # self-exclusion (unchanged)
    mint thread_invocations row:
        agent_name = p
        triggering_seq = <this message's seq>
        purpose = REPLY
        status = pending
```

The founder is **not** a participant in `thread_participants` today and never has been — she's modeled exclusively as the `FOUNDER_LITERAL = "@founder"` address token. With addressing removed she has no presence in the participant set, so no special-case skip is needed in the mint loop. She continues to read every thread via the web UI; the only thread-related Feishu push she still receives is `notify_thread_compose` (agent opens a new thread).

No selective addressing. No `@all` token. No `@founder` token. The participant set bounds the broadcast.

Special-purpose invocations (`BOOTSTRAP` on invite, `CLOSE_OUT` on archive request, `TASK_FOLLOWUP` on dispatched-task terminal) are unchanged.

## 5. Agent doctrine — injection into invocation prompt

The orchestrator's thread-invocation prompt builder gains a new top-of-prompt section that prepends to the existing "You are participating in thread THR-NNN..." block. Section title and shape:

```
## Decline-by-Default in Threads

This invocation was minted because a new message was posted to THR-NNN.
Every participant gets an invocation on every message — that does NOT mean
every participant should reply.

Default behavior: call `happyranch threads decline --from-file <payload>`
with no reason. Your invocation is consumed silently; no transcript entry
is written.

Reply (with `happyranch threads reply --from-file <payload>`) only when ALL
of the following hold:
- The latest message contains a question, request, or hand-off that
  you can uniquely answer based on your role.
- You have substantive content to add — not acknowledgment, not "I agree",
  not "noted".
- No other participant has already covered the same ground in a recent
  reply.

The founder is a participant; she reads the full thread in the web UI.
You do not need to "keep her informed" by replying.

If you are unsure: decline. The thread can always be re-engaged by another
message.
```

Injected by a new section in the thread-invocation prompt builder (`src/daemon/thread_runner.py`, around the "You are participating in thread …" block at line 109). Gated to `purpose=REPLY` only — not BOOTSTRAP (new participant should engage on first turn), not CLOSE_OUT (distinct submission flow, no reply/decline choice), not TASK_FOLLOWUP (agent has an explicit obligation to report).

This parallels the recent self-dispatch doctrine (`_thread_talk_dispatch_doctrine_section()` in `workspace_adapters.py`, shipped via PR #40) but lives in a different surface: the dispatch rule is invariant across all agent work and goes in the bootstrap doc; the decline doctrine only matters when an agent is deciding whether to reply right now, so it goes in the per-invocation prompt where it's most visible.

The skill file (`protocol/skills/thread/SKILL.md`) retains operational mechanics: payload shapes for `reply`/`decline`/`dispatch`/`close-out`, the `--from-file` requirement, invocation-token handling. The judgment rule (when to reply vs. decline) moves out of the skill and into the invocation prompt.

## 6. Decline semantics

`POST /threads/{id}/decline` retains its wire shape (invocation_token + optional `reason`) but behavior changes:

| Action | Before | After |
|---|---|---|
| `thread_messages` row inserted | Yes, with `kind='decline'`, `decline_reason` | **No row inserted.** |
| `thread_invocations.status` | `declined` (unchanged) | `declined` (unchanged) |
| `thread_invocations.consumed_at` | set | set |
| `thread_invocations.decline_reason` | set when provided | set when provided |
| `threads.turns_used` | `+= 1` | unchanged |
| Audit log | `thread_decline_consumed` | `thread_decline_consumed` with optional `reason` in payload |
| Web UI rendering | Decline row appears in transcript | No transcript row. Per-message status strip shows "<agent>: declined". |

The behavioral delta is narrow: (a) stop inserting the `thread_messages` row, (b) stop incrementing `turns_used`. The invocation-row update stays exactly as it is today; the per-invocation `decline_reason` column continues to capture reasons when provided.

The `kind='decline'` value and `decline_reason` column become deprecated. Old data is preserved and readable; new code never writes either.

## 7. Turn-cap accounting

New rule: `threads.turns_used` increments by exactly 1 for every `thread_messages` row inserted with `kind='message'`. Everything else (invocation minting, decline consumption, archive, invite, close-out) is free.

Projection at send-time simplifies from:
```
projected = turns_used + pending_load + len(addressed)
```
to:
```
projected = turns_used + 1
```
because under broadcast there is no pending_load that isn't already counted in `turns_used` (mint happens after append) and there is no per-recipient cost.

Default `turn_cap=500` means literally 500 messages — an order of magnitude more conversational headroom than the prior accounting on a multi-participant thread. The cap exists to prevent runaway threads; this matches the intent.

The `TASK_FOLLOWUP` auto-extend hook (CLAUDE.md invariant: "silently bumps `turn_cap` by 1 when projected over") stays. Operates on the same projection; the trigger is now a single message instead of a recipient-sized batch. Audit row `thread_turn_cap_auto_extended(reason=task_followup)` shape unchanged.

`Database.count_pending_turn_obligations` continues to exist but its result is no longer used for compose/send projection (it's still consulted by the task-followup auto-extend path, which counts pending REPLY+BOOTSTRAP+TASK_FOLLOWUP invocations — the semantics there are unchanged).

## 8. Feishu — removal of in-thread paths

| Path | Action |
|---|---|
| `_maybe_notify_founder_addressed` in `routes/threads.py` (outbound push when founder is in `addressed_to`) | **Delete** function and all call sites. |
| `escalation_notifications.kind='thread_addressed'` (outbound row minted by the above) | New code stops writing this kind. Existing rows stay readable for audit; the inbound listener no longer routes them. |
| `feishu_listener.py` — `thread_addressed` branch in `_handle_event_async` (inbound reply-to-founder-card → post to thread) | **Remove the branch.** Listener still handles `escalation`, `failure`, top-level `dispatch`, and `job_request`. |
| `notify_thread_compose` (outbound push when an agent **opens** a thread to the founder) | **Keep.** The founder needs to learn the thread exists before she can read it in the web UI. |

Net: zero Feishu pings for in-thread back-and-forth. The web UI is the single founder-facing surface for thread participation. The only remaining thread-related Feishu surface is the "agent composed a new thread" notification.

## 9. Web UI changes

In `web/src/features/threads/` and `web/src/lib/api/threads.ts`:

| Surface | Before | After |
|---|---|---|
| Founder send composer (`ThreadsPage.tsx`) | Body input + "Addressed to" picker (defaulting to `@all`) | Body input only. Send button posts directly. |
| Compose-new-thread dialog | Recipients picker + "Addressed to" picker | Recipients picker only ("who's in the room"). |
| `MessageCard` rendering | Renders "To: @all" / "To: finance_agent" badges from `m.addressed_to` | Badges removed. Field ignored on read. |
| Per-message **status strip** (NEW) | — | Below every `kind=message` row, a thin status line showing per-participant invocation state: `admin_head: replied · finance_agent: declined · ops_lead: pending`. Greyed-out, small font. Click-through deep-links to invocation in audit view. |
| Mocks (`_mock-threads.ts`) | Sample messages carry `addressed_to: ['@all']` etc. | Field removed from all mock data. |
| `sendThreadFollowUp` (`lib/api/threads.ts`) | Accepts `addressed_to?: string[]` | Parameter removed. |

The status strip is the load-bearing UI addition. Without it, silent declines are invisible and the founder cannot distinguish "still thinking" from "everyone passed" — exactly the THR-011 confusion in reverse. With it, the broadcast model is legible at a glance.

**Data shape for the status strip:** extend `GET /threads/{id}` response with a `responder_status` array per message: `{ agent_name: str, status: 'pending'|'replied'|'declined'|'failed', responded_at: str|None }`. Joined from `thread_invocations` keyed on `triggering_seq`. The wire value `replied` is **derived in the response builder** from the underlying `thread_invocations.status='consumed'` (which today means "agent's reply message was appended") — the DB stays canonical with its existing four values (`pending`, `consumed`, `declined`, `failed`), the wire renames `consumed → replied` for UI clarity. `responded_at` is `consumed_at` from the row, null when `status='pending'`. Cheap query; existing `idx_thread_invocations_thread` index covers it.

## 10. Wire / API changes

**Removed from request bodies:**

- `ComposeBody.addressed_to` (`routes/threads.py:77`)
- `SendBody.addressed_to` (`routes/threads.py:924`)
- `ComposeAsAgentBody.addressed_to` (`routes/threads.py:315`)

**Removed from response bodies / models:**

- `ThreadMessage.addressed_to` (`src/models.py:205`) — model field deleted. The DB column remains; read path stops populating the field.

**Removed helpers:**

- `_validate_addressed_to` (`routes/threads.py:86`)
- `_resolve_addressed_agents` (`routes/threads.py:102`)
- `_verify_addressed` (`routes/threads.py:672`)
- `FOUNDER_LITERAL = "@founder"` — constant retained. Still used by `compose-as-agent` to permit `@founder` in the `recipients` list as an external-recipient flag (the founder is not added as a participant; this is the "founder should see this thread" semantic). The constant's role in `addressed_to` routing is gone (since `addressed_to` itself is gone), but its role in recipients validation stays.

**New on `GET /threads/{id}` response:** the `responder_status` array described in §9.

**Audit shape change:** `AuditLogger.log_thread_message_sent` drops `addressed_to` from its payload. Old rows keep the field; consumers tolerate missing-or-present.

**Pydantic compat:** v2 silently ignores extra fields. An agent that posts `addressed_to: [...]` against the new API will not be rejected; the field is just ignored. This is intentional — provides a one-release grace window for any cached agent payloads.

## 11. CLI

`src/cli.py` — the hardcoded `"addressed_to": ["@all"]` in compose just gets deleted. No user-visible CLI change because CLI users were always broadcasting anyway. No CLI flags added or removed.

## 12. Migration

Minimal-impact, no big-bang:

1. **DB schema.** `thread_messages.addressed_to_json` stays. Nullable, not written by new code, not read by render path. `thread_messages.decline_reason` and `thread_messages.kind='decline'` rows similarly preserved (these become orphaned after the decline-mechanics change in §6). The `thread_invocations.decline_reason` column is **not** deprecated — it continues to capture per-invocation decline reasons. A follow-up cleanup migration drops the `thread_messages`-side dead columns after one stable release; not in v1.

2. **In-flight invocations at deploy.** Any pre-existing `pending` REPLY invocations stay valid and resolvable. They were minted under the old narrow-addressing rules; the runtime doesn't care, the agent skill just sees an invocation token. Startup recovery scan (existing) handles orphans.

3. **Open threads at deploy.** No retroactive change. The first new message in any open thread (including stalled ones like THR-011) broadcasts to all participants by the new rule. Stalled threads auto-unblock on the next message regardless of sender.

4. **Old transcript rendering.** Web UI reads `addressed_to_json` is removed; old messages render with no recipient badges (the field is just ignored). Old `kind='decline'` messages render as a slim "decline" entry in transcript for backward visual compat — this is the one render branch the new UI keeps for pre-migration data.

5. **OpenAPI snapshot.** `tests/contract/test_openapi_snapshot.py` fails on the body field removals. Regenerate intentionally with `HAPPYRANCH_REGEN_OPENAPI=1`. TS coverage test in `web/src/test/openapi-coverage.test.ts` does not need changes (path set unchanged; only request bodies shifted).

6. **Integration test fixtures.** Every `tests/integration/test_threads_*.py` that posts `addressed_to: ["@all"]` has the field removed. `fake_claude.sh` does not inspect addressing per the existing inventory; thread-prompt routing path is untouched.

## 13. Knowingly-accepted tradeoffs

- **Token cost grows by ~Nx for thread sessions.** A 4-agent thread = up to 4 sessions spun up per message instead of 1-2. Counterweighted by: (a) most sessions decline quickly without heavy LLM engagement, (b) silent-decline discipline keeps work bounded, (c) the THR-011 class of silent-drop bugs disappears entirely. We judge the trade worthwhile.
- **Skill discipline is the only brake on ping-pong.** No runtime cap on reply chains other than `turn_cap=500`. An over-eager agent pair can burn through a thread. Audit logs make this visible after the fact; if it becomes a recurring problem, a per-agent consecutive-reply cap is a future addition.
- **Status strip is non-trivial UI work** — new join query, new response field, new component. ~1 day of work, but it is the difference between the broadcast model being legible vs. cryptic. Not optional.
- **Pydantic ignore-extras grace.** Agents posting `addressed_to` against the new API get silently-accepted-and-ignored, not rejected. This eases the one-release transition; the field becomes fully unreachable after the cleanup migration.

## 14. Files touched (implementation summary)

Backend:
- `src/daemon/routes/threads.py` — remove `_validate_addressed_to`, `_resolve_addressed_agents`, `_verify_addressed`, `FOUNDER_LITERAL`, `_maybe_notify_founder_addressed`; rewrite mint loop to broadcast across participants; rewrite turn projection; add `responder_status` to GET response.
- `src/daemon/feishu_listener.py` — remove `thread_addressed` inbound branch.
- `src/infrastructure/feishu/notifier.py` — remove `send_thread_addressed` (`notifier.py:401`) and `send_thread_reply` (`notifier.py:25`, the inbound founder-reply-to-card path). Keep `notify_thread_compose` (the agent-opened-a-thread heads-up).
- `src/infrastructure/audit_logger.py` — drop `addressed_to` arg from `log_thread_message_sent`.
- `src/infrastructure/database.py` — drop `addressed_to` from `ThreadMessage` write path; keep column nullable.
- `src/models.py` — drop `ThreadMessage.addressed_to` field; add `responder_status` response model.
- `src/daemon/thread_runner.py` — add a "Decline-by-Default in Threads" section to the thread-invocation prompt for `purpose=REPLY` invocations (prompt is built around `thread_runner.py:109,115`). Inject only on REPLY, not on BOOTSTRAP / CLOSE_OUT / TASK_FOLLOWUP. The bootstrap doc and `src/orchestrator/workspace_adapters.py` are deliberately untouched — this doctrine is invocation-scoped, not session-scoped.
- `src/cli.py` — delete hardcoded `addressed_to` in compose payload.

Tests:
- `tests/contract/openapi.json` — regenerate.
- `tests/integration/test_threads_*.py` — remove `addressed_to` from all compose/send/compose-as-agent payloads.
- New `tests/test_thread_broadcast_routing.py` — covers: broadcast-mints-for-N-1, self-exclusion holds, founder-not-pinged, silent decline produces no transcript row, status strip data join.

Web:
- `web/src/features/threads/ThreadsPage.tsx` — remove addressed_to picker from composer; remove badge rendering from MessageCard; add status strip component.
- `web/src/features/threads/InviteDialog.tsx` — unchanged by routing change, but verify no addressed_to inputs.
- `web/src/lib/api/threads.ts` — drop `addressed_to` param from `sendThreadFollowUp`; add `responder_status` to `ThreadMessage` type or as separate `ThreadResponse` field.
- `web/src/lib/api/types.ts` — drop `addressed_to` from `ThreadMessage`.
- `web/src/mocks/messages.ts` and `_mock-threads.ts` — remove addressed_to fields; add sample responder_status data.

Docs:
- `CLAUDE.md` — update threads section to document broadcast-only model + invocation-prompt doctrine injection; remove references to `addressed_to` field and Feishu in-thread push.
- `protocol/skills/thread/SKILL.md` — remove `to:` field guidance; add note that judgment doctrine lives in invocation prompt.
- `docs/superpowers/specs/2026-05-13-threads-design.md` — append "Superseded by 2026-05-30-thread-broadcast-only-design.md (addressing model)" pointer at the addressing section.

## 15. Out-of-spec follow-ups

- **Cleanup migration (one release later):** drop `thread_messages.addressed_to_json`, `thread_messages.decline_reason` (the messages-side column, not the invocations-side); remove old `kind='decline'` rendering branch from web UI.
- **Per-agent consecutive-reply cap:** revisit if ping-pong becomes a pattern in audit.
- **Founder-dashboard signal:** "threads where everyone declined" might be a useful weekly-review surface; defer to dashboard work.
