---
name: thread
description: Use this skill when the orchestrator invokes you for thread participation. Decide whether to reply, decline, or dispatch a task — all based on the thread context provided in your prompt.
---

# thread

You've been invoked because something happened on a thread (THR-NNN). The full
prior history is in your prompt, along with a note explaining WHY you were
invoked AND an `invocation_token` that authorizes this single turn. Read the
history end-to-end, then decide one outcome.

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

- "Message N addressed you individually" — the founder (or another participant
  via the system) put your name in the To: field of message N.
- "Message N addressed @all" — message N targets every participant.
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
opc threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json

Reply when:
- You were addressed individually (default behavior).
- You were addressed via @all AND you have material to add the others haven't
  covered (correction, missing context, agreement with reasoning).

### Decline

Write `/tmp/thread-decline-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "reason": "...", "in_response_to_seq": <N>}

Then:
opc threads decline --org <slug> --thread-id <id> --from-file /tmp/thread-decline-<id>-<seq>.json

Decline when:
- You were addressed via @all AND another participant has already covered what
  you'd say — restating wastes founder attention.
- You don't have relevant expertise on the topic.

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
opc threads dispatch --org <slug> --thread-id <id> --from-file /tmp/thread-dispatch-<id>.json

Each dispatch posts a system message into the thread for transparency.

Dispatching does NOT end the turn — you MUST still issue a reply or decline
afterwards to release the invocation token. If you exit without either, the
daemon will auto-decline on your behalf with reason="no_callback".

## Close-out (archive)

When invoked with "This thread is being archived":

1. Review what was discussed.
2. Identify KB-worthy material (apply rules from protocol/06-knowledge-base.md
   §2). Write those with `opc kb add` BEFORE the close-out callback.
3. Identify durable learnings for yourself — write them to
   /tmp/thread-closeout-<thread_id>-<your_name>.json:
   {"thread_id": "<id>", "invocation_token": "<token>",
    "agent": "<your name>",
    "learnings": [{"text": "..."}],
    "kb_slugs": ["the-slugs-you-just-added"]}
4. Run:
   opc threads close-out --org <slug> --thread-id <id> --from-file /tmp/thread-closeout-<id>-<your_name>.json

Other participants will produce their own close-outs in parallel. Each
contributes to their own learnings.md; KB slugs are unioned.

Close-out tokens may NOT dispatch tasks. If something actionable surfaces in
the close-out, mention it in the learnings text instead.

## What NOT to do

- Do NOT spawn arbitrary side-effects (run repos, hit APIs) inside a thread
  invocation. Threads are conversation. Side-effects flow through tasks.
- Do NOT issue multiple terminal callbacks (reply AND decline) in one
  invocation. One terminal outcome per turn. Dispatch is the only non-terminal
  extra.
- Do NOT parse `@text` in message bodies as routing. The `addressed_to` list
  in the message is authoritative.
- Do NOT share or persist your `invocation_token` outside the current
  subprocess — it's single-use and turn-scoped.
