---
name: thread
description: Use this skill when the orchestrator invokes you for thread participation. Decide whether to reply, decline, or dispatch a task — all based on the thread context provided in your prompt.
---

# thread

You've been invoked because something happened on a thread (THR-NNN). The full
prior history is in your prompt, along with a note explaining WHY you were
invoked AND an `invocation_token` that authorizes this single turn. Read the
history end-to-end, then decide one outcome.

> **Decision rule for reply-vs-decline** lives in the thread invocation prompt
> (see the "Decline-by-Default" section injected for `purpose=REPLY` turns),
> not in this skill. This skill covers the operational mechanics — how to
> format and submit each outcome.

## Your invocation_token

Look for this line in your prompt:

    Your invocation_token for this turn is: <opaque-string>

You MUST include this token in every callback payload (reply, decline,
dispatch). It proves the callback is part of this live turn. Without
it, the daemon will reject the call with 401 invocation_token_invalid. The
token is single-use for the terminal callback (reply/decline) — a
second terminal callback with the same token returns 409.

## Identify the trigger

The "You have been invoked because" line tells you which case applies:

- "A new message was posted on this thread" — a `REPLY` turn. Every participant
  except the speaker receives this invocation. Read the prompt's
  "Decline-by-Default" section to decide whether to reply or decline.
- "The founder has added you to this thread" — bootstrap. You may post a brief
  intro or decline. No further obligation.

## Reply, decline, or dispatch

Pick exactly one terminal outcome (reply OR decline). You MAY additionally
dispatch a task before the terminal callback; dispatch alone does not end the
turn.

### Reply

Write `/tmp/thread-reply-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "body_markdown": "...", "in_response_to_seq": <N>}

Then single-line:
happyranch threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json

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
{"thread_id": "<id>", "agent": "<your name>", "invocation_token": "<token>"}
```
Then:

    happyranch threads attachments list --org <slug> --from-file /tmp/att-list-<id>.json

Or inline (single-line):

    happyranch threads attachments list --org <slug> --thread-id <id> --agent <your name> --invocation-token <token>

To download an attachment, write `/tmp/att-get-<thread_id>.json`:
```json
{"thread_id": "<id>", "attachment_id": "<att-id>", "agent": "<your name>", "invocation_token": "<token>"}
```
Then:

    happyranch threads attachments get --org <slug> --from-file /tmp/att-get-<id>.json --output /tmp/file.ext

Or inline:

    happyranch threads attachments get --org <slug> --thread-id <id> <attachment-id> --agent <your name> --invocation-token <token> --output /tmp/file.ext

The founder (web UI / bearer token) may also list/download without these
proofs. Agent callers must provide both `agent` and `invocation_token`.

**Explicit shared-artifact escape hatch (for cross-task handoffs):**

When you need a file to be visible across tasks or threads, use the pre-upload
pattern with `--shared`:

    happyranch artifacts put /tmp/file.ext --agent <your name> --name <artifact-name> --org <slug>

Then include the artifact reference in your reply payload:

    "attachments": [{"artifact_name": "<artifact-name>", "display_name": "file.ext"}]

Recipients download shared-artifact attachments with:

    happyranch artifacts get <artifact-name> --output /tmp/file.ext --org <slug>

Legacy shared artifact refs (using `artifact_name`) continue to work for
existing payloads.

### Decline

Write `/tmp/thread-decline-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "reason": "...", "in_response_to_seq": <N>}

Then:
happyranch threads decline --org <slug> --thread-id <id> --from-file /tmp/thread-decline-<id>-<seq>.json

Decline when:
- Another participant has already covered what you'd say — restating wastes
  founder attention.
- You don't have relevant expertise on the topic.
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
{"thread_id": "<id>", "invocation_token": "<token>",
 "dispatcher": "<your name>", "brief": "...",
 "target_agent": "<your name>" /* optional; any other target is rejected */,
 "team": "<team>" /* optional, defaults to your team */}

Then:
happyranch threads dispatch --org <slug> --thread-id <id> --from-file /tmp/thread-dispatch-<id>.json

Each dispatch posts a system message into the thread for transparency.

Dispatching does NOT end the turn — you MUST still issue a reply or decline
afterwards to release the invocation token. If you exit without either, the
daemon will auto-decline on your behalf with reason="no_callback".

## Dispatch from a thread is self-only

When you are participating in a thread (REPLY / BOOTSTRAP turn), `happyranch
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

- **Loop in another agent in your team:** use `happyranch threads compose
  --to <agent>` or `happyranch threads invite`. They receive a thread
  invocation (BOOTSTRAP or REPLY) and decide what to do with it.

- **Cross-team handoff:** use `happyranch threads compose --to
  <other-team-manager>` — possibly opening a new thread for the cross-team
  subject. Their manager receives a BOOTSTRAP turn and self-dispatches if
  they take the work on.

If you see `thread_dispatch_must_be_self` (or `thread_dispatch_must_be_self`)
in an error envelope: you tried to push work onto another agent from inside
a thread. Re-route via compose, or self-dispatch and own the phase.

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
reason) and the prompt-header asks you to restate the ask in-thread. In that turn:
state concisely what you need from the founder and why — do NOT try to resolve the
escalation yourself, and do NOT dispatch a new task. Decline if the Feishu escalation
already covers it and a thread restatement adds nothing.

**What you may NOT do:** Dispatch a new task from this turn. The runtime rejects
dispatch with purpose `task_followup` (HTTP 400 `wrong_invocation_purpose`). If a
new action is warranted, mention it in your reply and let the founder loop in.

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
- You name the OTHER agents you want in the thread. The founder is not a
  participant — they follow the thread via the web UI.

### Procedure

1. Write `/tmp/thread-compose-<short-tag>.json`:

   {"composer": "<your name>",
    "subject": "<≤120 chars>",
    "recipients": ["agent_a", "agent_b"],
    "body_markdown": "<the message>"}

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

This is the `post-as-agent` thread endpoint
(`POST /threads/{thread_id}/post-as-agent`). It is the task-session way to
append to a thread, and it is distinct from the other two ways an agent
contributes:

- **compose-as-agent** opens a **NEW** thread (you become a participant).
- **post-as-agent** appends to an **EXISTING** thread you **already
  participate in**. Authenticated by your live `task_id` + `session_id`
  binding (same as compose); gated to current participants — a
  non-participant is rejected (`not_a_participant`). No invocation token
  needed.
- **reply** responds within a thread turn you were **invoked** for, and
  requires the single-use `invocation_token` from that invocation.

Posting appends the message attributed to you, increments the thread's turn
count by one, and mints a reply turn for every **other** participant (not
you). It fails if you are not a participant, the thread is not open, or the
turn cap is reached.

## What NOT to do

- Do NOT spawn arbitrary side-effects (run repos, hit APIs) inside a thread
  invocation. Threads are conversation. Side-effects flow through tasks.
- Do NOT issue multiple terminal callbacks (reply AND decline) in one
  invocation. One terminal outcome per turn. Dispatch is the only non-terminal
  extra.
- Do NOT parse `@text` in message bodies as routing. Every message broadcasts
  to all participants; body @-mentions are visual only.
- Do NOT share or persist your `invocation_token` outside the current
  subprocess — it's single-use and turn-scoped.
