# Handoff to a desktop app (THR-362)

Status: **proposal, not approved, nothing built.** Design only (TASK-16549). No
product code, daemon, schema, auth or app-config change is made by this document.
Every item in "Founder decisions" is open until the founder rules on it.

Evidence labels used throughout:

- **[V]** read in this repository at `origin/main` `f69728b57`.
- **[P]** proven in the THR-362 throwaway kit on the Linux ChatGPT/Codex app
  (`codex-mcp-client 0.167.0-alpha`, 2026-11-15/16). The kit was never connected
  to the daemon.
- **[U]** unverified. **Every Claude Desktop statement is [U]**: the app is not
  installed on the host and nothing was exercised.
- **[D]** a design proposal in this document.

## 1. Goal

Founder direction (THR-362 msg44): polish the proven daemon-to-app communication
so that a HappyRanch agent can be told to "hand off the task to codex (or claude
code) desktop", and the founder can then open the desktop project and tell the
app to pick the task up.

Three steps:

1. An agent hands its task to a named desktop app. The task is parked, not failed.
2. The founder opens the app in a HappyRanch-provided folder and tells it to pick
   the task up. The app talks to a HappyRanch MCP tool server.
3. The result flows back into the original task and normal handling continues.

Non-goals for v1: unattended or scheduled pickup, any polling queue, computer
use, remote (non-loopback) apps, a new executor kind, metering of the app's usage.

## 2. What the experiment established

| Finding | Label |
|---|---|
| The app loaded a project-level `.codex/config.toml` from the opened folder and connected to a local MCP server over streamable HTTP (`url = "http://127.0.0.1:<port>/mcp"`) | [P] |
| `default_tools_approval_mode = "approve"` and `tool_timeout_sec = 60` in that project config gave tool calls with no approval prompt | [P] |
| `next_task` / `heartbeat` / `complete` worked; the requested structured result shape was honoured in 16 of 16 completions | [P] |
| 10 of 10 hourly scheduled check-ins arrived overnight; pickup latency was minutes to an hour | [P] |
| The built-in browser worked only in founder-attended runs; timer runs failed with "Browser is not available: iab" | [P] |
| Opening the folder makes the app add a `trust_level = "trusted"` entry for it to the user's `~/.codex/config.toml` | [P] (the app's own write, on the founder's accept) |
| A static `Authorization` header in the project config is sent by the desktop app | [U] (the kit server had no authentication) |
| The app writes result files into the opened project folder when asked | [U] (the kit prompt forbade file edits; this is ordinary app behaviour but was not exercised) |
| Anything about Claude Desktop, including whether its Code tab loads a project `.mcp.json` over HTTP | [U] |

The browser result is the reason this design is human-initiated: the capability
that makes the desktop app worth using is only present when the founder is there.

## 3. Current seams this design relies on

All [V] unless marked.

- **Task lifecycle.** `TaskStatus` is `pending`, `in_progress`, `escalated`,
  `completed`, `failed`, `cancelled`, `superseded` (`runtime/models.py:14-52`).
  `BlockKind` is `delegated` or `blocked_on_job` (`runtime/models.py:56-61`). A task
  waiting on its own jobs stays `in_progress` with `block_kind=blocked_on_job` and
  `tasks.blocked_on_job_ids`. The lifecycle contract now lives in
  `docs/agent-guides/orchestrator-contracts.md` (status list near line 2579);
  `protocol/05c-orchestrator.md` named in older notes has been retired.
- **Job wait.** An agent reports `status=blocked` with `waiting_on_job_ids`; the
  task auto-resumes when all listed jobs are terminal. Transitions are owned by
  `run_step_impl`; there are three symmetric resume callers
  (`docs/agent-guides/features-and-invariants.md`, "Task Blocked By Job").
- **Jobs.** `POST /api/v1/orgs/{slug}/jobs/submit` authenticates by
  `(task_id + session_id)`; `persistent=true` removes the daemon-side runtime
  ceiling; a job is killed when its task ends (`task_ended`) and on daemon
  shutdown (`daemon_shutdown`) (`docs/superpowers/specs/2026-05-26-jobs-design.md`).
- **Precedent for an external wait.** `python -m runtime.daemon.pr_ci_waiter` is a
  daemon-shipped poll entrypoint that an agent runs as a job and waits on. It
  reports readiness only; the agent acts on resume.
- **Registration tokens.** `hrreg_` tokens are minted only from loopback with the
  master bearer (`runtime/daemon/routes/auth.py:97-176`), are single-use, live 30
  minutes and are held in memory (`runtime/daemon/registration_token.py:29-33`).
  `require_registration_token()` (`runtime/daemon/auth.py:104`) guards the scoped
  routes. Purposes today are `binary` and `adapter` only
  (`registration_token.py:391-427`).
- **Audit scope prefixes.** Non-task actors use a mandatory prefix in
  `audit_log.task_id`: `artifact:<name>`, `config:<section>`, thread ids for thread
  actions; runtime-level executor events go to `<daemon-home>/runtime-audit.db`
  with `task_id="executor:<profile>"` (`runtime/daemon/routes/executors.py:80-100`,
  `features-and-invariants.md` "Shared Artifacts" traps).
- **Contract surfaces for a new route.** `tests/contract/openapi.json` (snapshot)
  and `tests/contract/route-classification.json` (`included` = browser-consumed,
  `excluded` = everything else with a reason; read by the Mac app's Swift
  drift-guard test). The former web `openapi-coverage` Vitest check **was retired
  by founder decision (THR-343)**, so the "two surfaces" are now the snapshot and
  the classification file. Remote access is default-deny through
  `runtime/remote_access/route_policy.json`.
- **MCP.** There is no MCP code in the repository. The April daemon design
  (`docs/superpowers/specs/2026-04-14-orchestrator-daemon-design.md:414`) records
  MCP as "explicitly rejected in brainstorming" for agent-to-daemon calls.
- **Daemon listener.** Default port 8765 (`runtime/config.py:86`); it can be
  configured or ephemeral.
- **Thread messages.** `ThreadMessageKind` has `message`, `decline`, `system`
  (`runtime/models.py:4001-4004`); task follow-ups are already posted as system
  messages (`runtime/orchestrator/run_step.py:6115`).

## 4. User experience [D]

### 4.1 Handing off

The founder tells an agent, in a task or a thread: "hand off this task to codex
desktop". The agent runs, from its own task session:

```
happyranch tasks handoff TASK-123 --to codex_desktop --session-id <sid> --from-file /abs/handoff.json
```

`handoff.json` carries `instructions` (what the app should do, written for the
app), an optional `result_shape` (JSON the result must match), optional
`input_files` (paths the agent may read, copied into the handoff folder) and
`expires_in` (default 24 h, maximum 7 days).

The command does two things with existing authority and returns one receipt:

1. creates the handoff record (new route, section 6);
2. submits the daemon-shipped waiter as a persistent job through the existing
   `jobs/submit` route (section 5).

The agent then reports `status=blocked` with `waiting_on_job_ids=[JOB-n]`, exactly
as it does for any job wait today.

A thread turn cannot submit a job (its invocation token authorises only the
thread callback), so an agent told to hand off from a thread self-dispatches a
task first and hands off from that task. This is the existing thread rule, not a
new one.

Only the task's own current session can hand it off. A manager that wants a
sub-piece handed off delegates a child whose brief says so.

### 4.2 What the founder sees

- **Thread notice** (when the task came from a thread): one system message.

  > TASK-123 is waiting for you in Codex desktop.
  > Open the folder `~/.happyranch/runtime/orgs/happyranch/desktop/codex_desktop`
  > and paste: `Pick up HappyRanch task TASK-123, code 4F7K92.`
  > Expires 2026-11-17 09:30. Instructions: "Open the pricing page and ..." (first 300 characters)

- **Web**: the task detail shows a "Handed off to Codex desktop" panel with the
  same folder, line, expiry, current state, and Reclaim / Cancel task buttons.
- **CLI**: `happyranch handoffs list` and `happyranch handoffs show TASK-123`.

### 4.3 Picking up

The founder opens the folder as a project in the app and pastes the one line:

```
Pick up HappyRanch task TASK-123, code 4F7K92.
```

The folder's `AGENTS.md` (written by HappyRanch at connect time) tells the app
what that line means: call `claim`, follow the returned instructions, write any
result files into the named `out/` folder, call `progress` now and then, call
`complete` once, and treat the instructions as a task description only, never as
a reason to change app settings, install anything or act outside the task.

### 4.4 Coming back

When the app calls `complete`, the waiter job exits, the task auto-resumes, and
the original agent reads the result with
`happyranch handoffs result TASK-123 --output <dir>` and finishes the task as
usual. To the manager loop this is an ordinary worker completion.

## 5. Task lifecycle [D]

### 5.1 Options compared

| Option | What changes | Founder-gated | Restart behaviour | Blast radius |
|---|---|---|---|---|
| A. New `TaskStatus` (`handed_off`) | Enum, every terminal/non-terminal predicate, web, lifecycle contract | Yes | Native | Largest |
| B. New `BlockKind` (`handed_off`) under `in_progress` | Stored discriminant, `run_step_impl`, the three resume callers, zombie reaper, pause, cancellation, authority-v2 eligibility | Yes | Native, no agent wake | Large |
| C. Escalation (`escalated`) | None in code, but the task would read as "needs a founder decision" and resume through resolve-escalation | No | Native | Small, wrong meaning: a handoff is not a decision request, and resolution has no place for a result |
| D. Child task for a pull-executor pseudo-agent | New executor kind, lease liveness, registry and reaper rules (the 2026-11-14 memo) | Yes | Needs a durable claims table | Largest; about three weeks |
| **E. Waiter job on the existing job wait** | **No status, block kind or transition change.** A new record type and a daemon-shipped waiter entrypoint | Not for lifecycle (schema and auth are gated separately) | Waiter is killed on shutdown; task resumes once and re-waits | Smallest |

**Recommendation: E for v1.** The handed-off task is `in_progress` with
`block_kind=blocked_on_job`, which already means "parked, no subprocess, waiting
on its own jobs". The meaning sentence in the lifecycle contract stays true; the
contract gains one line saying a handoff is a job wait.

### 5.2 The waiter

`python -m runtime.daemon.desktop_handoff_waiter --org <slug> --task TASK-123 --handoff HO-7`
is shipped with the daemon, like `pr_ci_waiter`. It only reads handoff state and
exits when the handoff is closed. It prints one JSON line and exits with a mapped
code:

| Handoff outcome | Exit | Meaning for the resumed agent |
|---|---|---|
| `completed` | 0 | Read the result and continue |
| `failed` (the app reported failure) | 10 | Read the reason; redo, re-hand-off or report |
| `expired` | 11 | Nobody picked it up, or it was not finished in time |
| `reclaimed` (founder took it back) | 12 | Continue without the app |
| `revoked` (app credential revoked) | 13 | As reclaimed |

The handoff record is the durable thing; the waiter is disposable. If the waiter
dies (daemon restart, Stop), the task resumes through the existing job-terminal
path, the agent sees a non-terminal handoff, and it submits a new waiter for the
same handoff with `happyranch tasks handoff TASK-123 --rewait`. No handoff state
is lost.

**Known cost of E:** each daemon restart while a handoff is open costs one short
agent session per parked task. If handoffs become routine and that churn matters,
option B is the upgrade; it is listed as a later decision.

### 5.3 Handoff states

`open` → `claimed` → `completed` | `failed`; `open`/`claimed` → `expired` |
`reclaimed` | `revoked` | `cancelled`. All but `open` and `claimed` are closed and
immutable.

- **Expiry.** One deadline per handoff, set at creation. It applies whether or not
  the handoff has been claimed. There is no heartbeat lease in v1: with a human
  present, a missed heartbeat is not evidence of failure. `progress` only stamps
  `last_seen_at` and an optional note.
- **Founder reclaim.** `happyranch handoffs reclaim TASK-123` (and the web button)
  closes the handoff as `reclaimed`. Later tool calls with the old lease are
  refused with a clear "stop" answer.
- **Founder cancel.** The existing task cancel. The job is killed with
  `task_ended` as today. The tool server checks the parent task on every call and
  closes the handoff as `cancelled` when the task is no longer
  `in_progress(blocked_on_job)` waiting on this handoff's job, or `handoffs`
  reads observe the same thing. No new hook in the terminal tail.
- **App reports failure.** `complete` with `status="failed"` and a reason closes
  the handoff as `failed`. The task is not failed; the agent decides.
- **One open handoff per task.** A second `handoff` while one is open is refused.
- **Repeated claim.** A claim with the right code from the same app instance
  returns the same lease, so a restarted chat can carry on. A wrong code is
  counted; five wrong codes lock the handoff until the founder reclaims it.

## 6. Storage and routes [D]

### 6.1 Storage

Two additive tables in the per-org SQLite database (schema additions are
founder-gated):

- `desktop_app_instances`: `app_id` (`codex_desktop` | `claude_desktop`),
  `token_sha256`, `folder_path`, `created_at`, `revoked_at`, `last_seen_at`,
  `client_info` (what the MCP client reported).
- `task_handoffs`: `id` (`HO-n`), `task_id`, `app_id`, `state`, `instructions`,
  `result_shape`, `pickup_code_sha256`, `wrong_code_count`, `lease_sha256`,
  `created_by_agent`, `created_session_id`, `job_ids` (waiters), `created_at`,
  `expires_at`, `claimed_at`, `last_seen_at`, `closed_at`, `result_summary`,
  `result_json`, `failure_reason`, `files` (name, size, sha256).

Both are new tables created in the fresh-schema block with their indexes; no
existing table gains a column, so the "index on a newly added column" upgrade
trap does not arise. No existing column is overloaded.

Files live under `<runtime>/orgs/<slug>/desktop/<app_id>/handoffs/TASK-123/`
(`in/` and `out/`), inside the folder the app has open.

### 6.2 Routes

Under `/api/v1/orgs/{slug}`:

| Route | Caller and auth | Purpose |
|---|---|---|
| `POST /tasks/{task_id}/handoff` | Agent CLI, `(task_id + session_id)` like `jobs/submit` | Create the handoff; returns `HO-n`, folder, expiry and the waiter command. Does **not** return the pickup code |
| `GET /handoffs`, `GET /handoffs/{task_id}` | Master bearer (founder CLI, web, the waiter) | List and show; the founder-facing read includes the pickup line |
| `GET /handoffs/{task_id}/result` | `(task_id + session_id)` of the owning task, or master bearer | Result text, JSON and file download |
| `POST /handoffs/{task_id}/reclaim` | Founder (human-only gate, as other founder controls) | Close as `reclaimed` |
| `POST /desktop/apps/{app_id}/connect` | Loopback **and** master bearer, the same gate as the registration-token mint | Mint or rotate the app credential, write the folder |
| `POST /desktop/apps/{app_id}/revoke` | Founder | Revoke the credential; close its claimed handoffs as `revoked` |
| `GET /desktop/apps` | Master bearer | Connected apps and last-seen times |
| `POST /desktop/mcp` | Loopback, `hrdesk_` credential only; the master bearer is refused here | The MCP tool server (section 8) |

Contract work for every new route, in the same PR that adds it: regenerate
`tests/contract/openapi.json`; add each route to
`tests/contract/route-classification.json` (`/desktop/mcp`, `connect` and the
agent-only routes as `excluded` with a reason; the routes the web consumes as
`included`, each with a TypeScript client mirror in `web/src/lib/api`). None of
them is added to `runtime/remote_access/route_policy.json`, so all stay denied
remotely by default; `connect` and `/desktop/mcp` must never be allowed remotely.

## 7. Registration and auth [D]

**This is a new credential class and a new authenticated surface. Auth-boundary
changes are founder-gated; nothing here is approved.**

- **Minting mirrors the registration-token mint.** `connect` is loopback-only and
  needs the master bearer, exactly like `POST /auth/registration-token/runtime`.
  The founder runs `happyranch desktop connect codex_desktop --org <slug>` or
  presses Connect in Settings.
- **It differs in two deliberate ways.** The credential (`hrdesk_` + 32 random
  bytes) is durable, because the app must reconnect across days, so only its
  SHA-256 is stored and it survives a daemon restart. And there is no single-use
  exchange or conformance check-in, because there is no candidate binary to
  qualify: the founder's own command writes the credential straight into the
  project folder's config file (mode 0600, folder 0700).
- **Scope.** `hrdesk_` is accepted on `POST /desktop/mcp` of its own org and
  nowhere else. Through it an app instance can: claim a handoff addressed to its
  `app_id` when it presents that handoff's pickup code; then, with the lease from
  that claim, read that handoff's instructions and input list, post progress, and
  complete it. It cannot list handoffs, read any task, thread, KB or artifact,
  touch another instance's claims, or mint anything.
- **Revocation.** `revoke` deletes the hash and closes claimed handoffs.
  Re-running `connect` rotates the credential (the old one stops working) and
  rewrites the config; the founder reopens the folder in the app.
- **Honest limit.** The daemon cannot prove the caller is the real desktop app.
  Any process running as the founder's OS user can read the credential file, as
  it can already read the master bearer file. The credential gives least
  privilege and attribution, not a boundary against local processes.

## 8. The MCP tool server [D]

### 8.1 Placement

| Option | For | Against |
|---|---|---|
| **Daemon route `POST /desktop/mcp`, streamable HTTP on loopback** | The transport the experiment proved. Scope is enforced by the daemon. No extra process to supervise, no new port | MCP enters the daemon, reversing the April note for this one purpose. The route appears in the OpenAPI snapshot |
| Separate `happyranch mcp-serve` stdio process launched by the app | No MCP in the daemon | Not the proven path in the app. The shim would hold the master bearer and scoping would live in shim code only |
| Separate HTTP process on its own port | Isolates MCP | A second listener to supervise, start, stop and secure; more moving parts for the same result |

**Recommendation: the daemon route.** Hand-written JSON-RPC 2.0 over POST with
plain JSON responses (`initialize`, `notifications/initialized`, `ping`,
`tools/list`, `tools/call`), no SSE stream and no new dependency; the kit's
`server.py` is the reference for the wire behaviour the app accepted [P]. The
route refuses non-loopback peers and any `Host` header that is not loopback, as
the kit did.

### 8.2 Tools

| Tool | Input | Output |
|---|---|---|
| `claim` | `task_id`, `code` | `lease`, `instructions`, `result_shape`, `in_dir`, `out_dir`, `expires_at` |
| `progress` | `task_id`, `lease`, optional `note` (≤ 500 chars) | `ok`, or `stop` with a reason (reclaimed, cancelled, expired) |
| `complete` | `task_id`, `lease`, `status` (`completed` \| `failed`), `summary`, optional `result` (object), optional `files` (names inside `out_dir`), `reason` when failed | `ok` |

There is deliberately no `next_task` and no listing tool. Without a task id and
its pickup code the server hands out nothing, so a scheduled poll has nothing to
fetch. This makes "no unattended polling in v1" a property of the server, not a
convention.

Tool answers contain task data only. They never contain instructions about the
app's own settings, approvals or other projects.

### 8.3 Folder provisioning

`connect` writes, for Codex [P for the first three keys, U for the header]:

```toml
# <runtime>/orgs/<slug>/desktop/codex_desktop/.codex/config.toml
[mcp_servers.happyranch]
url = "http://127.0.0.1:8765/api/v1/orgs/<slug>/desktop/mcp"
default_tools_approval_mode = "approve"
tool_timeout_sec = 60
http_headers = { Authorization = "Bearer hrdesk_..." }   # key name and app support unverified
```

plus `AGENTS.md` (the pickup protocol from section 4.3) and an empty `handoffs/`.
The config is project-level, so HappyRanch's own Codex agents, which never run in
that folder, do not see the server. HappyRanch does not edit `~/.codex/config.toml`;
the app adds its own trust entry when the founder accepts the folder.

If the daemon port changes, `connect` is re-run. If the desktop app does not send
a static header, the fallbacks in order are `bearer_token_env_var` (documented
for Codex, but a GUI-launched app may not have the variable) and a stdio shim.
This is the one open technical risk in the Codex leg; it is checked first in
slice 2 with the founder present, before the rest of the slice is built.

**Claude Desktop [U throughout]:** the expected equivalent is a `.mcp.json` with an
HTTP server entry and headers, `.claude/settings.json` pre-allowing the three
tools, and a `CLAUDE.md` carrying the pickup protocol, opened in the Code tab.
None of it has been run. The Claude leg is a separate later slice that starts
with an attended check after the founder installs the app.

## 9. Results [D]

- **Text.** `summary` (≤ 16 KiB) and optional `result` object (≤ 48 KiB, checked
  against `result_shape` when one was given; a mismatch is recorded, not refused).
- **Files.** The app writes files into `out_dir` and names them in `complete`.
  The daemon takes only regular files directly inside `out_dir`, opened without
  following links, at most 20 files and 10 MB each (the artifact cap), and records
  name, size and SHA-256. Sending file bytes through a tool call was rejected: the
  model would have to emit base64, which is slow, costly and error-prone.
  Files stay in the handoff folder. The resumed agent fetches them with
  `handoffs result` and decides what to publish as a shared artifact through the
  existing `artifacts put`. Nothing is published automatically.
- **Token usage.** The app reports none. No `session_token_usage` row is written
  for the app's work, and the handoff record and its audit rows carry
  `"metering": "unavailable"`. A zero row would read as "free"; no row plus an
  explicit marker reads as "unknown". The agent sessions before and after the
  handoff are metered as usual.
- **Trust.** The result is data produced by another model after browsing. The
  resumed agent treats it as untrusted input, as it treats a web page.

### 9.1 Audit rows

In the org `audit_log`, keyed by the **real task id** (no new overloading of
`task_id` for task events):

| Action | `agent` | Payload (all carry `handoff_id`, `app_id`) |
|---|---|---|
| `task_handoff_created` | handing agent | `expires_at`, `session_id`, instructions SHA-256 and length |
| `task_handoff_claimed` | `founder` | `client_info`, `metering: unavailable` |
| `task_handoff_completed` / `task_handoff_failed` | `founder` | summary length, file list (name, size, sha256), `reason`, `metering: unavailable` |
| `task_handoff_expired` | `daemon` | `was_claimed` |
| `task_handoff_reclaimed` | `founder` | — |
| `task_handoff_cancelled` / `task_handoff_revoked` | `daemon` / `founder` | cause |

Claim and complete are attributed to `founder` because the app acts for the
founder who started it; the desktop app is not an agent and gets no agent
identity. (Alternative for review: a literal `desktop:<app_id>` actor string.)
`progress` calls are not audited; they only stamp `last_seen_at`.

App-instance events (`desktop_app_connected`, `desktop_app_revoked`,
`desktop_app_rotated`) are org rows keyed `task_id="desktop:<app_id>"`, a new
scope prefix under the existing mandatory-prefix convention; it cannot collide
with `TASK-`, `THR-`, `JOB-`, `artifact:`, `config:` or `executor:` ids. Payloads
never contain the credential, the pickup code or the lease; at most an 8-character
hash prefix. The waiter job keeps its ordinary job audit rows.

## 10. Security [D]

The main risk is unchanged from the earlier memo: instructions written by an
agent, which may have read web pages and files, steer an app that is signed in as
the founder and has a browser.

v1 controls:

1. **Founder-started only.** Work is released only by `claim` with a task id and
   that task's pickup code, which appear on founder surfaces. No listing, no
   queue, no scheduled pickup.
2. **Seen before run.** The notice and the web panel show the instructions before
   the founder pastes the pickup line; the app also shows them on claim.
3. **Off by default.** A per-org setting `desktop_handoff.enabled` (default off)
   gates `tasks handoff`; `connect` is a separate founder act per app.
4. **Least privilege** for the app credential (section 7) and loopback-only,
   never remote.
5. **Results are untrusted data** to the resuming agent.
6. **Everything audited** (section 9.1).
7. **Stop controls.** Reclaim one handoff, cancel the task, revoke the app, turn
   the org setting off, or simply close the app.

Stated limits: the pickup code is visible to agents that can read the thread, so
it binds the founder's intent to one task but is not a secret from agents; agents
cannot operate the desktop app, so this does not let them start a handoff
themselves. Nothing technical stops the founder from creating a scheduled task in
the app that claims a specific task id and code; v1 simply provides nothing to
poll. Vendor terms for subscription-login apps remain the founder's call, and a
founder-attended session is the closest case to ordinary use.

## 11. Build plan [D]

Each slice is one PR with independent code review, QA of what changed, normal
hooks and a guarded merge. Estimates are working days for dev plus review and QA
rounds, excluding waiting time.

| Slice | Content | Days |
|---|---|---|
| 1 | Org setting; `desktop_app_instances` + `task_handoffs` tables; `connect` / `revoke` / app list; handoff create / list / show / result / reclaim routes; audit rows; CLI `happyranch desktop …` and `happyranch handoffs …`; contract files; docs | 4–5 |
| 2 | `POST /desktop/mcp` with `claim` / `progress` / `complete`; credential check; folder provisioning (Codex); file intake. **Starts with a 20-minute founder-attended check** of the static header and of file writing in the Codex app | 3–4 |
| 3 | Waiter entrypoint; `happyranch tasks handoff` (create + submit waiter + `--rewait`); bundled skill text for handing off and resuming; agent-guide sections; end-to-end QA with the founder doing one real pickup | 2–3 |
| 4 | Web: task-detail handoff panel, Settings connect/revoke, thread system notice | 2–3 |
| Later | Claude Desktop leg (attended check first, then provisioning) | 1–2 plus founder time |

**v1 (slices 1–4): about 11–15 working days, four PRs.** A CLI-only v1 (slices
1–3, with the handing agent posting the pickup line in its own thread reply) is
about 9–12 days.

Docs that change with the code, in the same PRs: `docs/agent-guides/features-and-invariants.md`
(new section), `docs/agent-guides/orchestrator-contracts.md` (one line: a handoff
is a job wait; no status change), `docs/agent-guides/agent-executors-and-permissions.md`
(the credential class), `docs/agent-guides/web-and-cli.md`, and the bundled
skills under `runtime/skills/bundled/`.

## 12. Founder decisions

### For v1

| # | Decision | Recommendation |
|---|---|---|
| 1 | Build this at all, at about 11–15 days? | Yes, slices 1–3 first, web after one real handoff has worked |
| 2 | Lifecycle: waiter job on the existing job wait (no status or block-kind change) vs a new `block_kind` | Waiter job. Accept one short agent wake per daemon restart while a handoff is open |
| 3 | New durable credential class `hrdesk_` and the new routes (auth boundary) | Approve as scoped in section 7: loopback only, MCP route only, hashed at rest, revocable |
| 4 | MCP inside the daemon as one loopback route, reversing the April "MCP rejected" note for this purpose only | Approve |
| 5 | Two additive tables in the org database (schema) | Approve |
| 6 | Pickup code required on claim, and no listing or `next_task` tool | Yes to both. The alternative ("pick up the next task") is friendlier but lets a timer fetch work |
| 7 | Which agents may hand off | Any agent, but only when the org setting is on and only its own task; the founder still starts every pickup. Tighten to a named list later if needed |
| 8 | Unmetered app work: no usage row plus an explicit "metering unavailable" marker | Approve |
| 9 | Expiry default 24 hours, maximum 7 days, no heartbeat lease | Approve |
| 10 | A 20-minute attended check in the Codex app at the start of slice 2 (static header, file writing). This is attended, not the unattended or browser experiments stopped in msg44 | Approve; without it slice 2 is built on two unverified assumptions |
| 11 | Result files stay in the handoff folder; the agent chooses what to publish | Approve |

### Later, not needed now

| # | Decision | Note |
|---|---|---|
| 12 | Claude Desktop leg | Needs the Linux beta installed (sudo) and the founder's login; everything on that side is unverified |
| 13 | Native parking (`block_kind=handed_off`) to avoid restart wakes | Only if handoffs become routine |
| 14 | Unattended or scheduled pickup | Not recommended while the browser is unavailable in timer runs |
| 15 | Mac or other remote app instances | Needs a remote-access decision; `connect` and the MCP route are loopback-only in v1 |
| 16 | Vendor terms for using subscription-login apps this way | Attended use is the mild case; revisit before any unattended mode |

## 13. Assumptions and open points for review

- The waiter reads handoff state through the daemon with the same CLI client and
  credential path other daemon-shipped job entrypoints use; whether a job
  subprocess should instead read the org database directly is left to slice 3.
- `tasks handoff` composes two existing-authority calls client-side (create the
  handoff, then `jobs/submit`). If job content authorisation rules require it, the
  founder's "hand off" instruction is the authorisation for the fixed waiter
  command; the bundled skill must say so explicitly.
- The founder-only gate for `reclaim` and `revoke` is assumed to reuse the
  existing human-only dependency (`runtime/daemon/auth.py:140`).
- Posting the thread notice from the handoff-create path reuses the system-message
  writer used by task follow-ups; the exact call is to be confirmed in slice 4.
- Single founder, single desktop instance per app per org, on the daemon host.
