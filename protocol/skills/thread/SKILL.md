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
dispatch, close-out). It proves the callback is part of this live turn. Without
it, the daemon will reject the call with 401 invocation_token_invalid. The
token is single-use for the terminal callback (reply/decline/close-out) — a
second terminal callback with the same token returns 409.

## Identify the trigger

The "You have been invoked because" line tells you which case applies:

- "A new message was posted on this thread" — a `REPLY` turn. Every participant
  except the speaker receives this invocation. Read the prompt's
  "Decline-by-Default" section to decide whether to reply or decline.
- "The founder has added you to this thread" — bootstrap. You may post a brief
  intro or decline. No further obligation.
- "This thread is being archived" — close-out. Different procedure: see §
  Close-out below.

## Reply, decline, or dispatch

For everything except close-out, pick exactly one terminal outcome (reply OR
decline). You MAY additionally dispatch a task before the terminal callback;
dispatch alone does not end the turn.

### Reply

Write `/tmp/thread-reply-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "body_markdown": "...", "in_response_to_seq": <N>}

Then single-line:
grassland threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json

Reply when:
- You have material to add that others haven't covered (correction, missing
  context, substantive input). See the invocation prompt's "Decline-by-Default"
  section for the full triage rule.

### Decline

Write `/tmp/thread-decline-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "reason": "...", "in_response_to_seq": <N>}

Then:
grassland threads decline --org <slug> --thread-id <id> --from-file /tmp/thread-decline-<id>-<seq>.json

Decline when:
- Another participant has already covered what you'd say — restating wastes
  founder attention.
- You don't have relevant expertise on the topic.
- See the invocation prompt's "Decline-by-Default" section for the full rule.

Keep the reason short and substantive ("payment_agt covered the constraint",
not "I have nothing to add").

### Dispatch a task (optional, before reply/decline)

If the thread has converged on a concrete action that fits your authority
(workers self-dispatch only; managers can dispatch to anyone on their team),
you may submit a task without ending the thread. Cross-team dispatch is
forbidden — if the action belongs to another team, surface it in your reply
and let the founder loop their manager in.

Write `/tmp/thread-dispatch-<thread_id>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "dispatcher": "<your name>", "brief": "...",
 "target_agent": "<name>" /* optional, defaults to yourself */,
 "team": "<team>" /* optional, defaults to your team */}

Then:
grassland threads dispatch --org <slug> --thread-id <id> --from-file /tmp/thread-dispatch-<id>.json

Each dispatch posts a system message into the thread for transparency.

Dispatching does NOT end the turn — you MUST still issue a reply or decline
afterwards to release the invocation token. If you exit without either, the
daemon will auto-decline on your behalf with reason="no_callback".

## Dispatch from a thread is self-only

When you are participating in a thread (REPLY / BOOTSTRAP turn), `grassland
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

- **Loop in another agent in your team:** use `grassland threads compose
  --to <agent>` or `grassland threads invite`. They receive a thread
  invocation (BOOTSTRAP or REPLY) and decide what to do with it.

- **Cross-team handoff:** use `grassland threads compose --to
  <other-team-manager>` — possibly opening a new thread for the cross-team
  subject. Their manager receives a BOOTSTRAP turn and self-dispatches if
  they take the work on.

If you see `thread_dispatch_must_be_self` (or `talk_dispatch_must_be_self`)
in an error envelope: you tried to push work onto another agent from inside
a thread or talk. Re-route via compose, or self-dispatch and own the phase.

## Task-followup turn

You may be invoked with the prompt-header line:

> Task TASK-NNN that you dispatched from this thread reached `<status>`. ...

This is a `task_followup` turn — the runtime injects it when a task you previously
dispatched from this thread reaches a terminal state. The thread now contains a
`task_completed` or `task_failed` system message with the task id, final summary,
and artifact dir (if any).

**What to do:** Reply with the result-bearing follow-up you owe the founder. Use
`grassland details TASK-NNN` to read the full task record. If there is nothing
substantive to add (e.g., the task was founder-cancelled and the founder already
knows), decline.

**What you may NOT do:** Dispatch a new task from this turn. The runtime rejects
dispatch with purpose `task_followup` (HTTP 400 `wrong_invocation_purpose`). If a
new action is warranted, mention it in your reply and let the founder loop in.

Callback shapes are unchanged: same `reply` / `decline` payload schema as a normal
reply turn.

## Close-out (archive)

When invoked with "This thread is being archived":

1. Review what was discussed.
2. Identify KB-worthy material (apply rules from protocol/06-knowledge-base.md
   §2). Write those with `grassland kb add` BEFORE the close-out callback.
3. Identify durable learnings for yourself — write them to
   /tmp/thread-closeout-<thread_id>-<your_name>.json:
   {"thread_id": "<id>", "invocation_token": "<token>",
    "agent": "<your name>",
    "learnings": [{"text": "..."}],
    "kb_slugs": ["the-slugs-you-just-added"]}
4. Run:
   grassland threads close-out --org <slug> --thread-id <id> --from-file /tmp/thread-closeout-<id>-<your_name>.json

Other participants will produce their own close-outs in parallel. Each
contributes to their own learnings.md; KB slugs are unioned.

Close-out tokens may NOT dispatch tasks. If something actionable surfaces in
the close-out, mention it in the learnings text instead.

## Compose a new thread (from inside a task or talk)

Use this when:

- You need written async input from another agent and aren't blocked enough
  to justify an escalation.
- You want a durable record of a cross-team coordination decision.
- You're inside a talk and want to loop in an agent who isn't present.

Requirements:

- You are currently in an active task session (you have a `task_id` +
  `session_id` from `start-task`) OR an open talk (`talk_id` from `/talk
  start`).
- You name the OTHER agents you want in the thread. The founder is not a
  participant — they follow the thread via the web UI.

### Procedure

1. Write `/tmp/thread-compose-<short-tag>.json`:

   {"composer": "<your name>",
    "subject": "<≤120 chars>",
    "recipients": ["agent_a", "agent_b"],
    "body_markdown": "<the message>"}

2. From a task, single-line:

   grassland threads compose --org <slug> --task-id <TASK> --session-id <SID> --from-file /tmp/thread-compose-<tag>.json

   From a talk:

   grassland threads compose --org <slug> --talk-id <TALK> --from-file /tmp/thread-compose-<tag>.json

3. Capture the returned `thread_id`. Mention it in your task completion
   summary (or talk transcript) so the founder can find it.

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
