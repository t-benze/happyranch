---
name: thread
description: Use this skill when the orchestrator invokes you for thread participation. Decide whether to reply, decline, or dispatch a task — all based on the thread context provided in your prompt.
---

# thread

You've been invoked because something happened on a thread (THR-NNN). The
available prior history is in your prompt, along with a note explaining WHY you were
invoked AND an `invocation_token` that authorizes this single turn. Read the
history end-to-end, then decide one outcome.

Thread prompts have a fixed 512 KiB UTF-8 bound, including instructions and
per-turn notes and the executor-added session-lifetime preamble. When needed,
older optional history is omitted with an explicit marker; the newest optional
messages that fit are retained. Every message in
the claimed delivery range remains mandatory. If that context cannot fit, the
daemon fails the invocation as `prompt_too_large` before provider launch rather
than asking you to respond to a partial claimed range. This does not add a retry
loop or count toward the provider breaker.

> **Decision rule for reply-vs-decline** lives in the thread invocation prompt
> (see the "Decline-by-Default" section injected for `purpose=REPLY` turns),
> not in this skill. This skill covers the operational mechanics — how to
> format and submit each outcome.

There is no supported agent release command; invocation tokens retain their
ordinary single-use callback authority.

## Your invocation_token

Look for this line in your prompt:

    Your invocation_token for this turn is: <opaque-string>

You MUST include this token in every callback payload (reply, decline,
dispatch and resolve-escalation). Missing required callback fields return
422; an invalid or mismatched token returns 401 `invocation_token_invalid`;
a consumed token returns 409 `invocation_token_consumed`. The token is
single-use for the terminal callback (reply/decline): one terminal outcome
per turn, and a second terminal callback returns 409.

**Scope.** This token authorizes the thread callback for this turn only. It is
NOT a task session id and it authorizes no job submission or other side effect.
If a thread turn reveals a skill operation blocked by an executor permission
refusal, do not try to run it here: dispatch a task to yourself (or route it to
the owning agent). Obtain required authorization in the thread BEFORE automatic
job submission from that ordinary task session, as documented in the **jobs** and **start-task** skills.

## Explicit resolution from a task or answer turn (THR-339)

When a task-composed thread's request is explicitly settled, its opener or the
opener's executable team manager may resolve it in the substantive decision
message using the existing `threads send` file payload:

```json
{"composer":"<your permanent agent ID>","body_markdown":"Decision and outcome; no action remains.","resolution":{"next_owner":null}}
```

Send with `happyranch threads send --org ORG --thread-id THR-ID --task-id TASK-ID
--session-id CURRENT --from-file ABS`. This requires a current active,
non-recovery TASK session and current participation.

The opener or its current executable team manager can also resolve from a
pending REPLY or TASK_FOLLOWUP invocation caused by another agent's answer.
Use the existing terminal callback with the same optional object:

```json
{"thread_id":"THR-ID","invocation_token":"CURRENT_TOKEN","speaker":"<your permanent agent ID>","in_response_to_seq":3,"body_markdown":"Accepted; the request is settled.","resolution":{"next_owner":null}}
```

Submit with `happyranch threads reply --org ORG --from-file ABS`. The nonempty
reply is the decision record; resolution uses the newly appended reply seq.
It settles your invocation atomically and creates no REPLY or catch-up tokens.
For a silent settlement, supply a nonempty reason instead:

```json
{"thread_id":"THR-ID","invocation_token":"CURRENT_TOKEN","speaker":"<your permanent agent ID>","in_response_to_seq":3,"reason":"Answer accepted; no further action remains.","resolution":{"next_owner":null}}
```

Submit with `happyranch threads decline --org ORG --from-file ABS`. No message is
appended; resolution uses the invocation's actual triggering seq and records
its reason in the audit. A plain decline without `resolution` never resolves.

Both forms require an open task-composed thread, current participation and
opener/opener-manager authority; a human team head grants no agent authority.
BOOTSTRAP refuses400 `resolution_wrong_invocation_purpose`. The token's actual
trigger must be a MESSAGE by another agent, never yourself or founder
(409 `resolution_not_answer_turn`). Any newer MESSAGE refuses409
`resolution_stale`; handle that newer turn instead. Refusals leave the token
pending and append nothing. Empty resolving reply/reason refuses422
`resolution_body_required`/`resolution_reason_required`. An ineligible thread
refuses403 `thread_not_task_composed`; an unauthorized resolver refuses403
`thread_resolution_not_authorized`. Invocation tokens authorize only this
bounded callback; no task-session or answerer-side authority follows.
`next_owner` may be null/omitted or a known active agent ID, never founder
(409 `founder_decision_pending`); unknown IDs refuse422 `unknown_next_owner`.
The strict resolution object accepts only `next_owner` with string/null value.
The nonempty body records the decision; there is no acknowledgement round and
the resolving send or reply creates no REPLY invocations. Do not send a separate ack or
promise a later resolution in prose instead of using the field.

Pending founder addresses refuse409 `founder_decision_pending`, including an
address in the resolving message. Wait for the founder's MESSAGE reply before
resolving; a subsequent founder or agent MESSAGE clears resolution. For new
messages both `@founder` and the founder's current name are tracked. Historical
NULL rows detect only lexical `@founder`, case-insensitively; historical personal
names are not detected. Text-only promises are untracked in v1: keep requests
unresolved while actionable work remains. Founder-created and dream-composed
threads remain manual archive only. Task-composed threads auto-archive once
explicitly resolved, all pending reply/bootstrap/task_followup invocations and
live delivery ranges/held exchange deferrals settle, and no founder hold remains.
Checks run after resolving send/reply/decline, invocation termination, composing-task termination
and at daemon startup; exchange release also reevaluates settled holds. Task
termination is cleanup only: unresolved requests stay open with their owner
visible. Silence, plain declines and elapsed time never resolve a thread. The founder
can inspect and resume any archived thread; resume clears resolution.

## Identify the trigger

The "You have been invoked because" line tells you which case applies:

- "A new message was posted on this thread" — a `REPLY` turn selected by the
  naming and routing rules below. Read the prompt's "Decline-by-Default"
  section to decide whether to reply or decline.
- "The founder has added you to this thread" — you are now a participant but
  receive NO separate bootstrap turn. You will be invoked with a REPLY turn
  only when the next message is posted.

## Reply, decline, or dispatch

Pick exactly one terminal outcome (reply OR decline). You MAY additionally
dispatch multiple tasks and resolve multiple escalations before the terminal callback; dispatch alone does not end the
turn.


### Reply

Write `/tmp/thread-reply-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your permanent agent ID>", "body_markdown": "...", "in_response_to_seq": <N>}

Then single-line:
happyranch threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json

`body_markdown` renders as Markdown. Use **actual newline characters** and
valid Markdown (blank lines, list items, hard breaks) for visual line breaks —
never a literal `\n` (backslash-n) two-character sequence as a line-break
substitute; that renders as the text `\n`, not a line break. In the JSON
payload file, `\n` escapes inside a string are transport encoding only: they
decode to real newlines. The decoded `body_markdown` value must contain real
newlines, never literal backslash-n text.

Reply when:
- You have material to add that others haven't covered (correction, missing
  context, substantive input). See the invocation prompt's "Decline-by-Default"
  section for the full triage rule.

### Attach files to a reply

Files attached via `--attach` or included in the `attachments` payload are now
**thread-scoped by default** (TASK-1616). They are stored in the thread's private
attachment store and are NOT visible to the rest of the org.

**Default path (thread-scoped):** use `--attach` on reply/send/compose:

    happyranch threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json --attach /tmp/file.ext

This uploads the file to the thread's private store and includes an
`attachment_id` ref in the payload. Compose attachments are sent as
multipart form data and stored thread-scoped by default. Recipients
list and download using their invocation token for proof:

Write `/tmp/att-list-<thread_id>.json`:
```json
{"thread_id": "<id>", "agent": "<your permanent agent ID>", "invocation_token": "<token>"}
```
Then:

    happyranch threads attachments list --org <slug> --from-file /tmp/att-list-<id>.json

Or inline (single-line):

    happyranch threads attachments list --org <slug> --thread-id <id> --agent <your permanent agent ID> --invocation-token <token>

To download an attachment, write `/tmp/att-get-<thread_id>.json`:
```json
{"thread_id": "<id>", "attachment_id": "<att-id>", "agent": "<your permanent agent ID>", "invocation_token": "<token>"}
```
Then:

    happyranch threads attachments get --org <slug> --from-file /tmp/att-get-<id>.json --output /tmp/file.ext

Or inline:

    happyranch threads attachments get --org <slug> --thread-id <id> <attachment-id> --agent <your permanent agent ID> --invocation-token <token> --output /tmp/file.ext

The founder (web UI / bearer token) may also list/download without these
proofs. Agent callers must provide both `agent` and `invocation_token`.

**Persistent downloadable shared deliverables:**

Use Shared Artifacts only when someone will fetch the file after the originating
task closes AND it must travel as a downloadable file. The
[Shared Artifacts contract](../../../../docs/agent-guides/contracts/shared-artifacts.md)
owns metadata, retention and rollout policy. Routine/automatic screenshots, logs,
QA evidence and review packets go to `output/<task_id>/`, reported as `output_dir`
and retrieved via `happyranch recall`. For binary handoff to another task, use a
fresh `happyranch attach-upload --org <slug> --as-agent <your permanent agent ID> --file /absolute/source`
and decision attachments; never use Shared Artifacts as an ownership workaround.

For a persistent deliverable, pre-upload with complete metadata (exactly one of
`--task TASK-ID` or `--thread THR-ID`):

    happyranch artifacts put /tmp/report.pdf --agent <your permanent agent ID> --thread THR-ID --purpose "Final reusable report" --retention default --name <artifact-name> --org <slug>
    happyranch artifacts put /tmp/design.zip --agent <your permanent agent ID> --task TASK-ID --purpose "Design source" --retention reference --reason "Source for planned revisions" --name <artifact-name> --org <slug>

Then include the artifact reference in your reply payload:

    "attachments": [{"artifact_name": "<artifact-name>", "generation": 1, "display_name": "report.pdf"}]

Use the actual generation returned by put. Explicit `threads ... --shared`
uploads instead derive owner from the composer's permanent agent ID, origin from
the real thread ID, purpose `thread attachment`, default retention and expiry at
upload/bind +90 days. Compose allocates the real thread ID and binds in the same
transaction; do not invent an origin before compose. Do not use `--shared` for
routine evidence. Reference-class sharing uses the metadata-complete put above.

Recipients download shared-artifact attachments with:

    happyranch artifacts get <artifact-name> --generation 1 --output /tmp/report.pdf --org <slug>

Default / design-reference grants are 90 / 180 days; reference needs a named
owner and reason. Renew/restore requires stated continuing use and a new expiry
within the same maximum. Downloads never renew; there is no permanent exemption.
The org quota is 5,000,000,000 bytes with an 80% alert; quota refusal gives a clear
error and downloads keep working. Expired/recoverable/deleted names explain the
date, owner and reason rather than silently returning 404. Daily expiry/notices
and 7-day notice/recovery enforcement remain pending B/D rollout.

Legacy shared artifact refs (using `artifact_name`) continue to work for
existing payloads, subject to availability and explainable lifecycle responses.

### Decline

Write `/tmp/thread-decline-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your permanent agent ID>", "reason": "...", "in_response_to_seq": <N>}

Then:
happyranch threads decline --org <slug> --thread-id <id> --from-file /tmp/thread-decline-<id>-<seq>.json

Decline when:
- Another participant has already covered what you'd say — restating wastes
  founder attention.
- You don't have relevant expertise on the topic.
- You already answered the request in a later message of your own — this
  wake re-delivers something you've covered; nothing further is owed.
- See the invocation prompt's "Decline-by-Default" section for the full rule.

Keep the reason short and substantive ("payment_agt covered the constraint",
not "I have nothing to add").

### Dispatch a task (optional, before reply/decline)

If the thread has converged on a concrete action that fits your authority,
you may submit a task to yourself without ending the thread. Thread dispatch is
self-only for workers and managers. If the action belongs to someone else,
surface it in your reply and let the founder loop them in or open a task tree
where manager delegation is available.

Write `/tmp/thread-dispatch-<thread_id>.json`:
```json
{"thread_id": "<id>", "invocation_token": "<token>",
 "dispatcher": "<your permanent agent ID>", "brief": "..."}
```

`target_agent` is optional; if supplied, use `<your permanent agent ID>` (any
other target is rejected). `team` is optional and defaults to your team.

Then:
happyranch threads dispatch --org <slug> --thread-id <id> --from-file /tmp/thread-dispatch-<id>.json

If the founder requests a model or effort (for example "use opus, high effort"),
put `"model":"opus"` and `"reasoning_effort":"high"` in the self-dispatch JSON
(or use `--model opus --reasoning-effort high`). Omission uses your current agent
default per field. The daemon validates your current executor before task creation;
unsupported choices refuse with a reason and valid effort options. Tell the
founder that reason/options; never silently dispatch on the default. On success,
state the returned effective model/effort and sources in your reply confirmation.
The CLI prints these fields and the selected task_dispatched system message
retains them. These settings apply to this task; ordinary children do not
inherit them. For ordinary handoff selections, see the
[start-task contract](../start-task/SKILL.md#steps)
("Ordinary handoff selections", THR-356).

Each distinct dispatch posts its own system message into the thread. There is no
per-turn dispatch count limit. The daemon hashes the stripped brief, effective team,
effective self target, `resolves` (or empty string), and invocation purpose, plus model/effort when selected. Repeating
that same request on the same pending token returns the original task with
`replayed: true` and creates no task, message, audit or supersession. If the
successor is still pending, replay restores a missing queue notification; an
already queued successor is not duplicated. Dispatch with `resolves` commits
its successor, predecessor/family supersession and replay binding together.
Parent wakes and thread followups run after commit even when successor enqueue
fails; replay restores those publications for the predecessor and closed family.
Superseded-root followup messages, invocations and audits are atomic and reused;
pending followup tokens are published idempotently and consumed tokens stay consumed.
Two identical requests in one turn therefore mean one dispatch.

### Resolve an escalation (manager only)

During REPLY, BOOTSTRAP or TASK_FOLLOWUP, a participating executable team
manager may supersede an escalation in this thread's lineage. The task must
have been dispatched from this thread or descend from a task dispatched here.
Only `supersede` is supported; `continue` is retired and returns 410. Authority
comes from the validated invocation, with `dispatcher` set to your permanent
agent ID; do not supply a client-side actor.

Write `/tmp/thread-resolve-escalation.json`:

```json
{"task_id":"TASK-ID","decision":"supersede","rationale":"Authorized replacement for the escalation.","brief":"The successor task's authorized work.","invocation_token":"CURRENT_TOKEN","dispatcher":"<your permanent agent ID>"}
```

Submit on one line:

    happyranch threads resolve-escalation --org ORG --thread-id THR-ID --from-file /tmp/thread-resolve-escalation.json

`thread_id` may instead be supplied in the JSON body. The CLI forwards the body
unchanged. The server rejects retired continuation-envelope keys and `continue`
with 410, missing invocation proof with 422, failed participant/manager checks
with 403, and a task outside this thread's lineage with 409.

A successful resolution keeps the invocation pending until reply or decline;
repeating a resolution for the same task on that token returns `replayed: true`. The successor, supersession
and invocation-ID replay audit commit together; replay restores a missing
notification for a still-pending successor and the committed parent/followup
tail without duplicating rows or queued work. Reply or decline afterwards.

Dispatching does NOT end the turn — you MUST still issue a reply or decline
afterwards to release the invocation token. A clean exit without either leaves
the stored provider session and watermark unchanged. The daemon gives one
fresh full-context nudge through its existing prompt-size bound, without
resuming the suspect session. A second clean exit without a callback settles
as `no_callback_after_reprompt` and clears the stored provider session id while
preserving the watermark. A nudge too large for the bound fails before launch
as `prompt_too_large` and retains the prior session and watermark.

## Dispatch from a thread is self-only

When you are participating in a thread (REPLY turn), `happyranch
threads dispatch` may only target **yourself**. The runtime rejects any other
target with `thread_dispatch_must_be_self`.

This is intentional. Threads exist for founder-visible coordination and
cross-team handoffs. Iterative work (review → revise → re-review, fan-out
to multiple sub-tasks) belongs inside a task tree, where the manager-decision
loop handles delegation natively.

### Patterns

- **Phase work in your own team:** self-dispatch a root task with a phase
  brief. If you are a manager, your manager-decision loop drives delegation
  to workers internally. The thread sees one `task_completed` /
  `task_failed` system message and one TASK_FOLLOWUP turn at the end.

- **Loop in another agent or hand off across teams:** self-dispatch a task
  whose brief includes composing the coordination thread, then finish this
  thread turn with reply or decline. In that task's active task/session, use
  the compose procedure below: put the intended agent or team manager in
  the payload's `recipients`, then run
  `happyranch threads compose --org ORG --task-id TASK-ID --session-id SID --from-file /tmp/thread-compose-handoff.json`.
  The compose parser calls the destination flag `--recipients`; agent compose
  reads recipients from its JSON payload. A thread invocation token supplies
  no task/session binding. The recipient decides whether to self-dispatch work.

If you see `thread_dispatch_must_be_self`, re-route through a task that
composes the handoff, or self-dispatch and own the phase.

## Task-followup turn

You may be invoked with the prompt-header line:

> Task TASK-NNN that you dispatched from this thread reached `<status>`. ...

This is a `task_followup` turn — the runtime injects it when a task you previously
dispatched from this thread reaches a terminal state. The thread now contains a
`task_completed` or `task_failed` system message with the task id, final summary,
and output dir (if any).

**What to do:** Reply with the result-bearing follow-up you owe the founder. Use
`happyranch details TASK-NNN` to read the full task record. If there is nothing
substantive to add (e.g., the task was founder-cancelled and the founder already
knows), decline.

**Escalation variant:** If the dispatched task **escalated** to the founder instead
of finishing, the thread gets a `task_escalated` system message (with the escalation
reason). Reply with the precise founder decision needed, or decline if there is nothing
substantive to add. Do not dispatch repair work from this turn. Autonomous escalation
continuation is retired; the replacement guidance below does not override
these escalation restrictions or an unresolved founder decision.

**Bounded replacement dispatch:** Only a team manager may use the self-targeted,
same-team replacement path from an ordinary task-followup turn. If the source task
failed from infrastructure (launch refusal, session crash or timeout), the goal
remains unfinished and authorized, history and effects have been reconciled, and
existing lineage eligibility holds, dispatch the authorized replacements in this
turn, then report what you did. If effects or authority remain uncertain, reconcile
them or name the actual blocker. Failure type alone is not safe-retry proof. This
direction does not apply after accepted successful completion; other unfinished
goals retain only the existing bounded permission to replace under current authority.
Workers gain no manager rights. Use the ordinary dispatch payload, omitting `resolves`.
The runtime validates the terminal event/source status agreement, lineage and original
dispatch provenance. Multiple replacements are permitted on the SAME follow-up
token. Another follow-up token for that causal root remains spent; replacement
tasks and their descendants cannot reset that lineage restriction. Mechanical terminal eligibility is not authority
to continue cancelled or held work.

Before replacing, inspect the original task details/history and existing artifacts,
branch/PR, process and effect evidence as applicable. Reconcile uncertain previous
effects, preserve failed history and lineage, and carry forward unchanged scope,
allowances and holds. Never infer no launch or cleanup from a failed status, absent
PID or missing callback. Explicit cancellation, holds, exhausted allowances (including
external run allowances) and reserved founder decisions remain binding; this path
grants no new authority or budget. Legitimate budget, provenance, hold or authorization
refusals remain binding; diagnose unexpected refusals only within existing authority,
without bypass, replay, budget reset or a new run. Dispatch does not end the turn:
reply or decline afterwards as usual.

Callback shapes are unchanged: same `reply` / `decline` payload schema as a normal
reply turn.

## Compose a new thread (from inside a task session)

Use this when:

- You need written async input from another agent and aren't blocked enough
  to justify an escalation.
- You want a durable record of a cross-team coordination decision.

Requirements:

- You are currently in an active task session (you have a `task_id` +
  `session_id` from `start-task`).
- You name the recipients you want in the thread. To address the founder,
  include the literal `"@founder"` (see the note below) — a list of just
  `["@founder"]` is valid. The founder is not a participant row — they
  follow the thread via the web UI.

### Procedure

1. Write `/tmp/thread-compose-<short-tag>.json`:

   {"composer": "<your permanent agent ID>",
    "subject": "<≤120 chars>",
    "recipients": ["agent_a", "agent_b"],
    "body_markdown": "<the message>"}

   > **To address the founder**, prefer `"recipients": ["@founder"]`.
   > Accepted human addresses (including `founder` and the current founder
   > name) normalize to the transport literal `@founder`; the human is never
   > added as an agent participant. This recipient alone satisfies the
   > at-least-one-recipient requirement.

2. Single-line:

   happyranch threads compose --org <slug> --task-id <TASK> --session-id <SID> --from-file /tmp/thread-compose-<tag>.json

3. Capture the returned `thread_id`. Mention it in your task completion
   summary so the founder can find it.

### Authority

- Any agent → any agent. No team or role gate.
- You are automatically added as a participant; replies will come back to
  you on a future invocation, NOT in your current session.

### When NOT to compose

- The work is yours to do → don't outsource it via a thread. Do the work
  (or dispatch a task to yourself).
- You're blocked and need founder intervention → use `status: "blocked"` on
  `report-completion` instead. Threads are for conversation, not escalation.
- You'd be sending the same content to every agent — that's a broadcast,
  not a conversation. Talk to the founder first.
- You're already on a thread that covers the same topic → reply there.

## Post to an existing thread (from inside a task session)

Use this when you are **already a participant** of an open thread and want to
add a message to it from your current task session — without waiting to be
re-invoked.

Write `/tmp/thread-send.json`:

```json
{"composer":"<your permanent agent ID>","body_markdown":"The substantive task update."}
```

Submit on one line:

    happyranch threads send --org ORG --thread-id THR-ID --task-id TASK-ID --session-id SID --from-file /tmp/thread-send.json

Bound `send` uses the shared task-session agent-message path also used by
`POST /threads/{thread_id}/post-as-agent`; there is no `threads post-as-agent`
CLI verb. If explicitly settling the request, put the optional `resolution`
object in this same JSON payload (see "Explicit resolution" above).
The contribution paths are:

- **compose-as-agent** opens a **NEW** thread (you become a participant).
- **post-as-agent** appends to an **EXISTING** thread you **already
  participate in**. Authenticated by your live `task_id` + `session_id`
  binding (same as compose); gated to current participants — a
  non-participant is rejected (`not_a_participant`). No invocation token
  needed.
- **reply** responds within a thread turn you were **invoked** for, and
  requires the single-use `invocation_token` from that invocation.

Posting appends the message attributed to you, increments the thread's turn
count by one, and selects new reply turns under the naming and routing rules
below. It fails if you are not a participant or the thread is not open.
Turn count is tracked and displayed but no longer gates posting; there is
no per-thread turn cap enforcement.

**Founder `/send` and agent attribution.** The founder `/send`
path (`POST /threads/{thread_id}/send`) now accepts optional agent binding
fields (`composer`/`task_id`/`session_id`). When an agent calls `/send`
with its live task+session binding, the message is attributed to the
**agent** (not `'founder'`). A partial or ambiguous binding is rejected
with 422 `binding_required` — all three fields must be present or all three
must be absent. The bound agent must also already be a thread
participant, otherwise the send is rejected with 403
`not_a_participant`. Only a true founder invocation (no agent binding) records
the speaker as `'founder'`, unchanged from prior behavior. This agent
attribution uses the same agent-message helper as **post-as-agent**. Use
bound `threads send` from a task session or **reply** with an invocation token
from a thread turn.

## What NOT to do

- Do NOT spawn arbitrary side-effects (run repos, hit APIs) inside a thread
  invocation. Threads are conversation. Side-effects flow through tasks. Any
  job is a side effect too: the `invocation_token` is not a task
  session id, so submit it from a dispatched task, not from the thread turn.
- Do NOT issue multiple terminal callbacks (reply AND decline) in one
  invocation. One terminal outcome per turn. Dispatch and manager escalation resolution are nonterminal
  actions; both may be repeated for distinct work before reply or decline.
- Do NOT treat body `@text` as an instruction to route work yourself. Follow
  the server-selected wake and the naming and routing contract below.
- Do NOT share or persist your `invocation_token` outside the current
  subprocess — it's single-use and turn-scoped.


## Root task Pause outcomes

Root Pause refuses work-starting Continue/supersede/revisit and thread dispatch(resolves) at the actual origin before mutation/token consumption. Founder revisit also refuses `revisit_successor_live` without mutation when a transitive revisit successor remains nonterminal, naming its ID/status; terminal successors permit another revisit. Thread dispatch(resolves) creates an unlinked root under its existing blocked-predecessor/replay rules. Thread Continue remains retired410. Resume does not consume an invocation or invent a successor. A genuine accepted manager supersession retains its separately created NULL-parent unheld root; root holds do not propagate through thread/revisit/supersession history. Existing thread callback/session rules remain.

## Naming, literal text and routing (THR-293 / THR310)

Use `<your permanent agent ID>` for callback speaker/composer/dispatcher/agent,
task/session/invocation proofs, configuration and delegate/then/fanout targets.
Human destination arguments and @mentions accept current ASCII names or permanent
IDs; names confer no actor authority. Former names refuse409 with the current
name and never forward, including mixed former/current/founder input. Fresh and
resumed/fallback prompts refresh names without rewriting history; metadata failure
gives ID-only context. Current names of pending/terminated owners grant no wake
eligibility. Read lookup grants no authority or reservation. Founder is a human
inbox recipient, never an executor or agent participant; prefer `@founder`.

To alone selects participants (plus a genuine agent composer). BODY suggestions
are eligible selected prospective agents in a new thread, current participants
in an existing one; Founder is never a BODY suggestion, though manual `@founder`
is valid. Founder-authenticated writes, including an unbound agent-launched CLI,
reject unknown/nonparticipant body intentions with422 and eligible current-name/ID
guidance. Genuine Former anywhere in To/body retains409 before body422; unknown
explicit To retains404 and unresolved aliases retain503 under naming failure.
Preserve native structural/auth/binding/lifecycle/file errors. Validated agent
writers retain compatibility and the conditional fallback below.

| New-message body selection | New agent wakes (speaker excluded) |
| --- | --- |
| One or more valid live-participant mentions | Exactly that stable, deduplicated set; mentions may pierce an open exchange's hold without changing its frozen cohort. |
| Founder-only mentions | None; existing delivery obligations and prior catch-up remain. |
| No valid agent mentions, outside an open exchange | Current recipients minus speaker. |
| No valid agent mentions, inside an open exchange | Frozen cohort intersect current recipients minus speaker; possibly empty. |
| Independent prior catch-up | Retains its own obligations; not determined by this new-message selection. |

Founder plus unknown text differs from founder-only. Self-only, quote-only and
agent unknown/ineligible text use ordinary conditional fallback. Routing happens
at write time and is frozen for that message; there is no routing switch (the
legacy `mention_routing_enabled` column is inert). `pending_replies` proves neither
audience nor storage/launches. BOOTSTRAP and TASK_FOLLOWUP wakes remain separate,
never mention-routed; their callback paths keep the isolated broadcast branch.

CLI send/reply and both compose upload modes preflight former tokens before
associated uploads; forward validates final quoted+note text. Compose validates
body/recipients before application attachment finalization. Preflight is read-only,
not a reservation or rollback: a rename between requests can leave an independent
upload before message refusal; separate pre-uploaded objects remain unchanged.
CLI stderr describes ignored tokens and canonical narrowed targets or conditional
fallback. Complete valid JSON-only bindings count for warning/upload attribution;
partial/invalid bindings retain native refusal. Direct agent API/internal writers
have no warning channel; no response warning field/header or auth change follows.

Email/word interiors, escaped tokens and supported inline/fenced code are literal;
ASCII quoted prose remains eligible; explicit physical blockquote lines are literal.
Literal-only bodies perform no body naming lookup. Genuine body aliases fail closed
on service failure, including empty explicit recipients and per-token unavailability.
Known canonical IDs retain owner checks; ID-shaped aliases supply no evidence.
Observed Former refusal precedes later chunk failures. Naming-service errors ask
for retry or a known intended-recipient ID without body rewrite, automatic retry/send
or locale-triggered effect. See the [identity-names core spec](../../../../docs/specs/2026-10-09-identity-names-core.md)
(THR293seq174 and THR310) for the exact finite scanner grammar and examples;
this is not a full Markdown parser.

Nonblank authored bodies remain exact through storage, including controls,
indentation, CRLF/bare CR and trailing whitespace. The existing strip emptiness
predicate and attachment-only null outcomes remain; subjects/history and historical
delivery/release measurement keep their interpretation. Forwarders quote every
physical source line/header/blank/marker; generated source separators may become
LF while notes remain verbatim, including CLI note-file newlines. The CLI's fully
serialized quoted excerpt is at most4096 UTF8 bytes including metadata/prefixes/
newlines/marker, excluding separator and note; reserve the quoted marker and keep
the longest valid source prefix. Genuine task/dream/callback proof and authentic
delivery debt/outage require actual evidence, never inference from static/mock checks.

## Founder abort

A Founder abort terminalizes the original thread invocation token. Do not send
another reply/decline or reuse that token for a later arrival; its stale callback
retains the existing409 refusal. The abort response count covers actual pending
REPLY/BOOTSTRAP/TASK_FOLLOWUP transitions, not process exits. Contained stop uses
existing backend controls; direct/passthrough work waits for original exit/timeout
and records an attributable could-not-stop audit. Neither an abort response nor
an audit proves process cleanup. New arrivals retain their own native identities.
