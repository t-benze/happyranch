---
name: thread
description: Use this skill when the orchestrator invokes you for thread participation. Decide whether to reply, decline, or dispatch a task — all based on the thread context provided in your prompt.
---

## Current names and permanent automation IDs

Fresh prompts may show current Name · ID for this turn. Use the permanent ID
from the injected parameters for callback agent/composer/speaker, task/session/
invocation proofs, configuration and delegate/then/fanout targets. A name is
human-addressing/presentation data, never actor authority. Human destination
arguments and @mentions accept current names or IDs; former names refuse with
the current name and do not forward. Founder is a separate human; recipient
transport remains `@founder`. Metadata failure gives ID-only context.

CLI send/reply and both compose upload modes preflight former tokens before
associated uploads; forward validates final quoted+note text. ASCII quoted prose remains eligible; explicit blockquote lines are literal; email/word interiors, escaped tokens
and supported code are literal. Unknown/ineligible body intentions follow the THR310 caller policy below; raw text stays unchanged. Preflight is
read-only, not a reservation or rollback across upload/message requests. The
server still owns admission; a rename between requests can leave an independent
upload before the message refuses. Resumed/fallback prompts refresh current
names without rewriting history. UI labels remain presentation only; source-specific
verification and independent acceptance belong in the maker handoff.


# thread

## Current naming routing contract (THR-293)

Human recipients and @mentions may use current names or permanent IDs; the
server resolves them to canonical IDs. Former names refuse409 with the current
name and never forward, including mixed former/current/founder text. The ASCII
token charset is unchanged; explicit prose boundaries exclude email/word
interiors, escaped tokens and supported code. Agent unknown text retains conditional fallback; Founder invalid intentions refuse422. Founder-only
current mentions create no new agent wake but retain existing due catch-up and
delivery obligations. Founder is a human inbox recipient, never an executor.
Do not substitute names for composer/speaker/agent/task/session/invocation-token
proofs: those remain canonical ID bindings. Read lookup grants no authority or
reservation. Current names of pending/terminated owners grant no wake eligibility.

Compose validates body/recipients before application attachment finalization.
Separate pre-uploaded attachments already exist independently of a later send;
former-name refusal leaves those prior objects unchanged. CLI pre-upload/target resolution and current/resumed prompt source are now
described above; neither source paragraph claims behavioral verification.
Keep the existing callback shapes and authentication mechanics below.

New-message body admission excludes email/word interiors, escaped tokens and
supported inline/fenced code; ASCII quoted prose remains eligible; explicit blockquote lines are literal. Literal-only
bodies perform no body naming lookup. Genuine body aliases fail closed on service
failure, including empty explicit recipients and per-token unavailability. Known
actual canonical IDs retain owner checks; ID-shaped aliases supply no evidence.
Observed Former refusal precedes later chunk failures. en/zh-CN service wording
asks for retry or a known intended-recipient ID, with no body rewrite, automatic
retry/send or locale-triggered effect. Historical messages/delivery and release
measurement keep their original interpretation. The exact finite scanner contract
is in the current identity-names core spec (THR293seq174).

You've been invoked because something happened on a thread (THR-NNN). The full
prior history is in your prompt, along with a note explaining WHY you were
invoked AND an `invocation_token` that authorizes this single turn. Read the
history end-to-end, then decide one outcome.

> **Decision rule for reply-vs-decline** lives in the thread invocation prompt
> (see the "Decline-by-Default" section injected for `purpose=REPLY` turns),
> not in this skill. This skill covers the operational mechanics — how to
> format and submit each outcome.

The C1a/C1b private thread-delivery release service has no supported human
release command or route. Its dormant initial serving attachment is admitted only
after actual successful ASGI/listener startup, without selection/release. Agents,
automatic diagnostics, reapers and schedulers cannot select the human risk
override. Caller proof flags and temporary/dynamic contexts supply no authority.
A companion never clears raw lifecycle/host/move/Pause/effect uncertainty, supplies
process proof, replenishes retries, restores task12044/10693, or grants a callback
for a consumed token. Terminal sources get no second catch-up. Future human
admission must bind exact preview/key/owner/range and explicit late-write,
overlap, repeated-effect and missing-closure risks. Old-reader/mixed-version
operation and downgrade after use are unsupported. Current invocation-token
callbacks below keep their native single-use authority; no agent release follows.
C1-13 now has dormant UNMOUNTED router and UNCALLED CLI registration/handlers,
private custody and client source only. Existing CLI registration/app mounts and
service are unchanged; external_adapters_available=false, proof=false. It is a
future HUMAN tool, no supported installed command or agent/live-use authority.
Original strict16KiB JSON, distinct preview/command digests, explicit four-risk
confirmation, positive private durability before POST, strict bounded32KiB
no-store responses and same-key single-GET/no-reapply rules are source contracts.
Committed history never certifies cleanup/recovery; false/null/true stay distinct.
Fixtures/native denials/public activation are HELD with BOTH Web/Swift attributable
RED/restoration/GREEN MISSING and no waiver/allocation. C1-13 activation and
C2 task-tail remain deferred; behavioral/test-authoring proof UNEXECUTED.
Current guarded owner observations never prove historical process/effect retirement.
Disconnect/revoke timeout cannot discharge native lifetime or permit DB close;
notification after commit can publish only the same genuine replacement token.

## Your invocation_token

Look for this line in your prompt:

    Your invocation_token for this turn is: <opaque-string>

You MUST include this token in every callback payload (reply, decline,
dispatch). It proves the callback is part of this live turn. Without
it, the daemon will reject the call with 401 invocation_token_invalid. The
token is single-use for the terminal callback (reply/decline) — a
second terminal callback with the same token returns 409.

**Scope.** This token authorizes the thread callback for this turn only. It is
NOT a task session id and it authorizes no job submission or other side effect.
If a thread turn reveals a skill operation blocked by an executor permission
refusal, do not try to run it here: dispatch a task to yourself (or route it to
the owning agent). Obtain required authorization in the thread BEFORE automatic
job submission from that ordinary task session, as documented in the **jobs** and **start-task** skills.

## Identify the trigger

The "You have been invoked because" line tells you which case applies:

- "A new message was posted on this thread" — a `REPLY` turn. Every participant
  except the speaker receives this invocation. Read the prompt's
  "Decline-by-Default" section to decide whether to reply or decline.
- "The founder has added you to this thread" — you are now a participant but
  receive NO separate bootstrap turn. You will be invoked with a REPLY turn
  only when the next message is posted.

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

- **Loop in another agent in your team:** use `happyranch threads compose
  --to <agent>` or `happyranch threads invite`. They receive a thread
  invocation. `compose --to <agent>` gives them a REPLY turn; `invite` adds
  them silently (they are invoked on the next message). They decide what to do
  with it.

- **Cross-team handoff:** use `happyranch threads compose --to
  <other-team-manager>` — possibly opening a new thread for the cross-team
  subject. Their manager receives a REPLY turn and self-dispatches if
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
reason). Reply with the precise founder decision needed, or decline if there is nothing
substantive to add. Do not dispatch repair work from this turn. Autonomous escalation
continuation is retired; the optional replacement guidance below does not override
these escalation restrictions or an unresolved founder decision.

**Optional replacement dispatch:** Only a team manager may dispatch a self-targeted,
same-team replacement from an ordinary task-followup turn, and only for an unfinished
goal already covered by existing authority. Workers gain no manager rights. Use the
ordinary dispatch payload, omitting `resolves`. The runtime validates the terminal
event/source status agreement, lineage and original dispatch provenance, and permits
at most one persisted replacement per causal root; neither a new token nor a
replacement task resets that limit. Mechanical terminal eligibility is not authority
to continue cancelled or held work.

Before replacing, inspect the original task details/history and existing artifacts,
branch/PR, process and effect evidence as applicable. Reconcile uncertain previous
effects, preserve failed history and lineage, and carry forward unchanged scope,
allowances and holds. Never infer no launch or cleanup from a failed status, absent
PID or missing callback. Explicit cancellation, holds, exhausted allowances (including
external run allowances) and reserved founder decisions remain binding; this path
grants no new authority or budget. Dispatch does not end the turn: reply or decline
afterwards as usual.

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

   {"composer": "<your name>",
    "subject": "<≤120 chars>",
    "recipients": ["agent_a", "agent_b"],
    "body_markdown": "<the message>"}

   > **To address the founder** — e.g. to open a founder-only thread —
   > include the literal `"@founder"` in `recipients`, WITH the `@`. It is
   > a routing literal, not an agent name: a bare `"founder"` is rejected
   > as an unknown agent (404). `"@founder"` also satisfies the
   > at-least-one-recipient requirement on its own, so
   > `"recipients": ["@founder"]` opens a thread with just you and the
   > founder. The founder is not added as a participant row; they follow
   > the thread via the web UI.

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
you). It fails if you are not a participant or the thread is not open.
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
attribution is a **safety net** that prevents the old mis-stamp-as-founder
bug, not a new preferred path. The intended task-session way for an agent
to append to a thread remains **post-as-agent** (attributed to you) or
**reply** (invocation token).

## What NOT to do

- Do NOT spawn arbitrary side-effects (run repos, hit APIs) inside a thread
  invocation. Threads are conversation. Side-effects flow through tasks. Any
  job is a side effect too: the `invocation_token` is not a task
  session id, so submit it from a dispatched task, not from the thread turn.
- Do NOT issue multiple terminal callbacks (reply AND decline) in one
  invocation. One terminal outcome per turn. Dispatch is the only non-terminal
  extra.
- Do NOT treat `@text` in message bodies as a routing *instruction to yourself*
  beyond the server's decision: the daemon routes every message at write time
  (mention routing, THR-198; unconditional per TASK-6027) — a body that
  mentions one or more valid current participants wakes exactly that set
  (minus the speaker); with zero valid participant mentions (none,
  `@founder`/typos, self-only), every participant minus the speaker is woken.
  There is no routing switch (the shipped `mention_routing_enabled` column is
  inert legacy). You only ever receive a wake the daemon selected; @-mentions
  in bodies are no longer
  purely visual. TASK_FOLLOWUP and BOOTSTRAP wakes are never mention-routed.
- Do NOT share or persist your `invocation_token` outside the current
  subprocess — it's single-use and turn-scoped.


## Root task Pause outcomes

Root Pause refuses work-starting Continue/supersede/revisit and thread dispatch(resolves) at the actual origin before mutation/token consumption. Thread Continue remains retired410. Resume does not consume an invocation or invent a successor. A genuine accepted manager supersession retains its separately created NULL-parent unheld root; root holds do not propagate through thread/revisit/supersession history. Existing thread callback/session rules remain.

## Literal text and recipient intentions (THR310 seq3/13)

All Founder-authenticated writes, including an unbound agent-launched CLI, reject
unknown/nonparticipant body intentions with422 and eligible current-name/ID guidance.
Genuine Former anywhere in To/body retains409 before body422; unknown explicit To
retains404 and unresolved aliases retain503 under naming failure. Preserve native
structural/auth/binding/lifecycle/file errors. To alone selects participants (plus
a genuine agent composer). BODY suggestions are eligible selected prospective
agents in a new thread, current participants in an existing one; Founder is human,
never an agent participant or BODY suggestion. Manual `@founder` remains valid.

Validated agent writers retain compatibility; CLI stderr describes ignored tokens
and canonical narrowed targets or conditional fallback: recipients minus speaker
outside an exchange, frozen cohort intersect recipients minus speaker inside one,
possibly empty. Prior catch-up is independent; pending_replies proves neither
audience nor storage/launches. Direct agent API/internal authors receive no warning
channel (accepted limitation). Complete valid JSON-only bindings count for CLI
warning/upload attribution; partial/invalid bindings retain native refusal. No
response warning field/header/auth change or preflight reservation is introduced.
Founder-only suppresses new selection while preserving obligations/prior catch-up;
Founder+unknown differs, and self-only/quote-only use ordinary conditional fallback.

Only start, finite whitespace, or `(` `[` `{` ASCII single/double quote opens an
unescaped ASCII token. Explicit physical lines with0–3 ASCII spaces then `>` are
literal; unprefixed lazy continuation and four-space indentation are outside this
quote rule. Top-level unclosed fences mask to EOF; quoted ticks/fences cannot own
later notes. The precise finite whitespace/code grammar and examples are in the
identity-names core spec's THR310 section; this is not a full Markdown parser.
Nonblank authored bodies remain exact through storage, including controls,
indentation, CRLF/bare CR and trailing whitespace. The existing strip emptiness
predicate and attachment-only null outcomes remain; subjects/history are unchanged.

Forwarders quote every physical source line/header/blank/marker. Generated source
separators may become LF; notes remain verbatim, including CLI note-file newlines.
CLI's fully serialized quoted excerpt is at most4096 UTF8 bytes, metadata/prefixes/
newlines/marker included; separator and note excluded. Reserve the quoted marker
and retain the longest valid source prefix. Web retains its last-message selection
and size policy. Raw occurrence ranges keep duplicates; admission spellings dedup.
Insertion replaces only the prefix through caret, preserving suffix/spaces/focus.
Thread-body rendering styles only proven exact raw intervals, noninteractively;
ambiguous transformations stay plain, code/Mermaid stay undecorated, task/KB defaults
and historical delivery stay unchanged. No render-time naming or recipient inference.
Shared data is protocol/thread-mention-corpus.json (51 lexical +13 write-path rows).
Public-unexposed mentions_json, authentic debt/outage and genuine task/dream/callback
proof require actual evidence and cannot be inferred from static/mock checks.
