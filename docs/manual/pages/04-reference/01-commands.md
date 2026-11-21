# Commands

Every per-org command takes `--org <slug>`; container-level commands do not.

## Multi-org operation

A runtime container can host multiple orgs side by side. Per-org commands take `--org <slug>` (or honor `HAPPYRANCH_ORG_SLUG`). Container-level commands operate on the container as a whole.

```bash
happyranch orgs                                              # list orgs in the active container
happyranch orgs init my-other-org --from /path/to/example    # materialize a second org
happyranch use ~/another-runtime                             # switch which container the daemon serves
happyranch orgs unload <slug>                                # detach an org (does not delete files)
```

Slug resolution for per-org commands: explicit `--org <slug>` flag > `HAPPYRANCH_ORG_SLUG` env > auto-infer (only if exactly one org exists in the container) > error.

### Runtime layout

```
<runtime>/
|-- happyranch.yaml                           # container marker (schema_version: 2)
+-- orgs/
    +-- <slug>/
        |-- happyranch.db                     # per-org SQLite
        |-- org/                       # editable org content (you can hand-edit this)
        |   |-- charter.md
        |   |-- escalation-rules.md
        |   |-- teams.yaml
        |   |-- config.yaml            # optional org-level overrides
        |   +-- agents/
        |       |-- <name>.md          # active agents
        |       +-- _pending/<name>.md # awaiting founder approval
        |-- workspaces/<agent>/        # per-agent workspace
        |-- kb/                        # per-org knowledge base

        |-- threads/                   # multi-agent workchannel transcripts
        |-- jobs/                      # JOB-NNN.{out,err,script}
        +-- artifacts/                 # org-shared blob store
```

The files under `org/` are the source of truth for that organization. You can hand-edit them between tasks (e.g., to refine an agent's system prompt) — the next `happyranch init-agent` regenerates the workspace bootstrap accordingly.

## Container-level

| Command | Description |
|---------|-------------|
| `happyranch init <path>` | Create a runtime container and set it as active (slugless) |
| `happyranch use <path>` | Switch the daemon's active container |
| `happyranch orgs` | List orgs in the active container |
| `happyranch orgs init <slug> [--from <example-tree>]` | Materialize a new org |
| `happyranch orgs unload <slug>` | Detach an org from the daemon (does not delete files) |

## Local connection fallback

`happyranch connection status` reports nonsecret platform/role availability
without daemon access. `happyranch connection host --help` lists the Linux host
fallback: `status`, `devices`, `next-device`, `approve-device`, `reject-device`,
`revoke-device`, `enable`, `turn-off`, `create-invite`, `cancel-invite`,
`replace-failed`, `recover-invite` and `retry-policy-renewal`.
It uses the same fixed native helper as the desktop app, with
zero D-Bus inputs. Mutations require a desktop and fresh system authorization;
headless attempts return `unavailable` / `none` / `nativePermission` without mutation.
Revoke selects interactively on the same bus inside one invocation (32 choices,
120 seconds); there is no `--device-id` or cross-command cursor. Output is closed
nonsecret JSON; post-dispatch loss is unknown and never retried automatically.
Exit 0 means completed read/action or cancelled selection, 1 a closed error, and
2 invalid syntax. An unavailable read observation stays explicit.

`create-invite` delivers a validated invite only to the controlling terminal
(`/dev/tty`); stdout JSON contains only nonsecret fields.
Without a terminal, it refuses before dispatch. `replace-failed` requires a
terminal and shows “Replace failed remote access; existing pairings will be lost”,
then `Continue? [y/N] `; only `y` or `yes` confirms. EOF or any other answer
leaves effect `none`. `turn-off` uses fresh system authorization without an
additional CLI prompt. Lifecycle admission (`accepted`) is not completion:
`enable`, `turn-off`, `cancel-invite`, `replace-failed`, `recover-invite` and
`retry-policy-renewal` report `outcomeUnknown` / effect `unknown`, exit 1, because
the helper acknowledges before effects finish. The invocation keeps its bus alive
through the original budget plus margin: 482 seconds for Enable/ReplaceFailed,
302 for Disable and 122 for invite/policy calls, so the helper can revalidate the
live caller. This proves no denial or cleanup;
the CLI never polls or retries the mutation. Only `refresh-home-address` remains
closed unavailable without dispatch (B13).
Linux `connection client` registers `status`, `accept-invite`, `saved-computers`,
`connect-saved`, `remove-saved`, `disconnect`, `cancel-attempt`, `retry`, `retry-save`
and `health`. Hidden terminal invite entry requires explicit target consent;
saved connect/remove use bounded selection, exact binding reread and confirmation.
On Linux, accept-invite recognizes a unique saved same-home/control-plane/CA/connector
with changed address metadata. The terminal shows the updated control URL, dial IP
and endpoint and requires typing `update` (default Cancel). It verifies the saved
node and live grant without redeeming the invite, then saves/readbacks the updated
record. Failed validation requires separate fresh consent and normal Host approval;
declining update launches nothing. No Client result claims Host-wide invite invalidation.
Real-network C19 acceptance is slice 7 NOT RUN.
Remove confirms only local saved-access deletion.
No invite/device/path/credential flags exist. Login-keyring custody and explicit
session-only warnings apply. Connections live in the foreground; Ctrl-C/SIGTERM
cancels/disconnects with bounded cleanup and truthful `cleanupIncomplete`.
Authenticated health yields `runtimeReachable` and a terminal-only loopback URL;
loaded RELEASE UI is unverified, so CLI never reports Connected. Separate control/
retry/health actions return `unavailable/notOwned`, effect none, exit 1. Status
observes only this invocation, not global health. No new IPC or GUI/other-process
control exists. Mac host and Mac Client CLI are deferred/unavailable.
See the [Linux release handbook](../../../operations/linux-release-handbook.md#linux-host-cli-fallback-slice-5a).

## Per-org

| Command | Description |
|---------|-------------|
| `happyranch run --org <slug> --brief "..."` | Submit a task. The team manager decides the approach. |
| `happyranch run --org <slug> --team TEAM --brief "..."` | Route a task to a specific team |
| `happyranch run --org <slug> --brief-file PATH` | Read the task brief from a file (multi-line briefs); mutually exclusive with `--brief` |
| `happyranch tail --org <slug> TASK-ID` | Stream live events for a running (or historical) task |
| `happyranch details --org <slug> TASK-ID [--full]` | Task details. `--full` skips per-step truncation. |
| `happyranch tasks --org <slug> [--limit N]` | List recent tasks (default: 20) |
| `happyranch init-agent --org <slug> [name]` | Initialize agent workspaces (all or one) |
| `happyranch audit --org <slug> TASK-ID [--json]` | View audit log (or filter by `--agent`, `--action`, `--since`, `--limit`) |
| `happyranch tokens --org <slug> [--task-id X --agent Y --since DATE --limit N]` | Per-session token usage; `--by-agent` / `--by-task` for rollups |
| `happyranch recall --org <slug> TASK-ID [--tree] [--fetch-output]` | Fetch task brief, full subtask tree (`--tree`), and (optionally) output file bodies (`--fetch-output`) |

The CLI does not take a runtime path — every command operates on whichever container is currently active. Use `happyranch use` to switch.

## Founder primitives

| Command | Description |
|---------|-------------|
| `happyranch resolve-escalation --org <slug> --task-id <id> --decision approve\|reject --rationale "..."` | Resolve an escalated task. Approve resumes; reject fails and cascades. |
| `happyranch revisit --org <slug> <task-id> [--note "..." \| --note-file PATH] [--session-timeout-seconds N]` | Spawn a new root inheriting a terminal predecessor's brief. **TTY-gated.** |
| `happyranch enrollments --org <slug> [--status pending]` | List agent enrollment requests |
| `happyranch approve-agent --org <slug> <name>` | Approve a pending enrollment and bootstrap workspace |
| `happyranch reject-agent --org <slug> <name>` | Reject a pending enrollment |

`happyranch revisit` walks any task's lineage to its root and — if the root ended `failed`, `cancelled`, `escalated`, or `completed` — spawns a fresh root inheriting the original brief and team. The old tree stays frozen (read-only history); the new root's manager gets a prompt-header pointer back to it so it can inspect what happened via `happyranch details` / `happyranch audit` / `happyranch recall`. Only humans can trigger it. If a direct or transitive revisit successor is still
live (including blocked or escalated), the command refuses and names that task.
Once successors are terminal, another revisit works normally. An escalated
successor can itself be revisited.

## Knowledge base

```bash
happyranch kb list      --org <slug> [--topic <t>] [--type <label>]
happyranch kb get       --org <slug> <slug>
happyranch kb search    --org <slug> "<query>"
happyranch kb add       --org <slug> --agent <you> --from-file /tmp/kb-<slug>.md
happyranch kb update    --org <slug> <slug> --agent <you> --from-file /tmp/kb-<slug>.md
happyranch kb delete    --org <slug> <slug> --agent <you> --confirm [--as-founder]
happyranch kb reindex   --org <slug>
```

Any agent reads/writes; team managers delete (audited); the founder overrides delete via `--as-founder`. Founder rulings on escalated tasks are written through plain `happyranch kb add` — set `source_task: TASK-XXX` in the frontmatter to keep the link to the escalation.

## Per-agent memory

Each agent has its own private learnings under `<runtime>/orgs/<slug>/workspaces/<agent>/memory/`, one markdown file per entry with YAML frontmatter (`id`, `slug`, `title`, `topic`, `tags`, `related_to`, `supersedes`, `promoted_to`). The bootstrap doc inlines the regenerated `_index.md` so agents see their accumulated rules at session start.

```bash
happyranch memory list     --org <slug> --agent <you> [--topic T --tag T --promoted|--not-promoted --json]
happyranch memory get      --org <slug> --agent <you> <MEM-NNN-or-slug> [--json]
happyranch memory search   --org <slug> --agent <you> "<query>" [--limit N --include-promoted --json]
happyranch memory add      --org <slug> --agent <you> --from-file /tmp/lrn-<slug>.yaml
happyranch memory update   --org <slug> --agent <you> <MEM-NNN> --from-file /tmp/lrn-<slug>.yaml
happyranch memory promote  --org <slug> --agent <you> <MEM-NNN> --kb-slug <kb-slug>
happyranch memory reindex  --org <slug> --agent <you>
```

`add`/`update` take a YAML mapping or JSON object with `slug`, `title`, `topic`, `body`, and optional `tags`/`source_task`/`related_to`/`supersedes`. JSON is detected by a `.json` suffix or a leading `{` (ignoring whitespace). Quote YAML strings containing ` #`, for example `title: "Fix PR #123 regression"`. Unquoted single-line string fields with trailing comments, including `topic: ci  # note`, are rejected: quote the entire value to retain the comment text, or remove the comment. Quoted strings and multiline YAML bodies retain normal YAML behavior. The deprecated `learning` alias shares these rules.

Promotion is one-way: the body becomes a 2-line pointer stub and the entry locks against further edits — use `supersedes:` on a new entry to evolve a rule that has already been promoted. Workspaces that predate this layout still use a flat `learnings.md` (legacy `happyranch memory --agent X --text "..."` form); the founder dispatches a one-shot migration task per agent when ready.

## Threads

Email-style multi-agent workchannels. Use threads when you need to involve
multiple agents in a single asynchronous conversation, or when you want to
loop new agents into an existing discussion. Each thread has a subject, a
participants list, and a chronological message log.

**Web UI (primary surface).** Build the bundle once, then launch:

```bash
scripts/build_web.sh               # local UI flag profile; builds web/dist/
scripts/build_web.sh --production  # production UI flag profile; builds web/dist/
happyranch web                     # opens http://127.0.0.1:<port>/ in your browser
happyranch web --no-open           # print the URL only
```

The localhost SPA renders the full threads inbox (compose / reply / invite /
archive / extend / resume / SSE-driven live updates) and is the
single interactive interface for founders. In the composer, `Enter` sends
and `Shift+Enter` inserts a newline. Archived threads expose a "Resume thread" button
to reopen them. The dev server (`cd web && npm run dev`)
proxies to the daemon for hot-reload development.

Unfinished screens sit behind build-time UI flags. `scripts/build_web.sh`
defaults to `npm run build:local`: like the dev server, it uses
`web/config/features.development.json`, then optional gitignored
`web/config/features.local.json` overrides, then the existing explicit
`VITE_ENABLE_KB_COMPOSE` compatibility input. Release builds
(`scripts/build_web.sh --production`, `cd web && npm run build`, and Mac
packaging) use only the production profile and hide unfinished UI such as
Work Hours. The shell script installs dependencies with `npm ci` when
`node_modules` or its `.package-lock.json` is missing. Flags only change what
the UI shows; daemon behavior and saved settings are unchanged. See
`web/ARCHITECTURE.md`.

(The previous Textual TUI under `src/tui/` was removed in favor of the
web UI; `happyranch threads` with no subcommand now points you at `happyranch web`.)

**Founder CLI commands** (unchanged — scripts and automations depend on
them):

```bash
happyranch threads compose --org <slug> --subject "..." --recipients alice,bob --body "..."
happyranch threads list --org <slug>
happyranch threads show --org <slug> THR-001
happyranch threads send --org <slug> --thread-id THR-001 --from-file /tmp/send.json
happyranch threads invite --org <slug> --thread-id THR-001 --agent qa
happyranch threads forward --org <slug> --source THR-008 --recipients alice,bob
happyranch threads archive --org <slug> --thread-id THR-001 --from-file /tmp/arch.json
happyranch threads resume --org <slug> --thread-id THR-001
happyranch threads extend --org <slug> --thread-id THR-001 --new-cap 1000
happyranch threads abort-replies --org <slug> --thread-id THR-001
happyranch threads attest-abort-predecessor --org <slug> --thread-id THR-001 --invocation-id 123 --reason "..."
```

Archive is synchronous: the daemon writes the transcript file and flips the
thread to `archived` inside the POST handler. The founder can later reopen
an archived thread with `happyranch threads resume --thread-id <id>`.

Configure per-org:

```yaml
# <runtime>/orgs/<slug>/org/config.yaml
threads:
  enabled: true                       # default true
  default_turn_cap: 500               # total agent-turns budgeted per thread
  # invocation_timeout_seconds: null  # optional override of session_timeout for thread turns
```

`happyranch threads --org <slug>` with no subcommand is kept as a compatibility
stub. It prints a pointer to `happyranch web`; it does not launch a TUI.

## Jobs

When an agent's executor refuses a command (e.g., a `gh` / `aws` / `stripe` /
`sudo` invocation that needs founder-grade credentials), or when an agent needs
a long-running process, it can submit a job. Review-required jobs wait for
founder approval; auto-run jobs start without review. Tasks can self-block on
jobs and resume automatically when all listed jobs are terminal.

```bash
happyranch jobs list                                    # default: --status pending
happyranch jobs list --status all --agent <name>        # narrow by status/agent/task
happyranch jobs show JOB-019                            # see rationale, full script, output if terminal
happyranch jobs run JOB-019                             # TTY-gated confirm + live SSE stream
happyranch jobs run JOB-019 --cwd repos/web-app --timeout-seconds 600
happyranch jobs reject JOB-019 --reason "..."           # or omit --reason to be prompted
happyranch jobs output JOB-019                          # fetch the full captured output post-run
happyranch jobs output JOB-019 --stream stderr --max-bytes 5000000
happyranch jobs tail JOB-019 --stream stdout --lines 100
happyranch jobs wait JOB-019 --timeout-seconds 30
happyranch jobs stop JOB-019
```

The same surface is in the web UI at `/jobs` (list with status chips,
detail drawer with rationale + script preview, Run modal with cwd/timeout
overrides, Reject modal, live SSE output panel). If an agent self-blocks with
`waiting_on_job_ids`, the daemon resumes that task automatically when every
listed job reaches `completed`, `failed`, or `rejected`; the resumed session
gets an injected header pointing at `happyranch jobs show JOB-019` /
`happyranch jobs output JOB-019`.

Output is captured to `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`
on disk (full output, no size cap in v1) plus a 64 KB head per stream in
the database for fast rendering.

Operational notes:
- Jobs run inside the daemon process with `os.environ` inherited from the
  daemon's launch shell. If you rotate credentials in your interactive shell,
  restart the daemon so the new env is picked up:
  `scripts/daemon.sh stop --force && scripts/daemon.sh start`.
- `jobs run` requires a TTY. To run non-interactively, use the web UI's
  Run modal (it has the same confirm step in a visual form).
- If the daemon is killed mid-run, the next startup recovery scan marks any
  orphaned `running` rows as `failed`. Output captured up to the kill point
  is preserved on disk.
- `happyranch scripts ...` remains as a deprecated alias for `happyranch jobs ...`
  so older scripts keep working, but new docs and agent skills should use jobs.

## Managing repos

Agents can request repo changes through the `manage-repo` skill, or the founder can manage them directly:

```bash
happyranch manage-repo --org <slug> add    --agent <name> --repo-name docs --url https://github.com/user/docs.git
happyranch manage-repo --org <slug> remove --agent <name> --repo-name docs
happyranch manage-repo --org <slug> update --agent <name> --repo-name docs --url https://github.com/user/docs-v2.git
```

## Enrolling new agents

A manager can propose new agents during task execution using the `manage-agent` skill. Enrollment requires founder approval:

```bash
happyranch enrollments --org <slug> --status pending     # founder reviews
happyranch approve-agent --org <slug> content_writer     # bootstraps workspace, skills, repo clones
happyranch reject-agent  --org <slug> content_writer
```

Agent names must be lowercase with underscores only (e.g., `content_writer`, `seo_agent`).

To enroll an agent with a non-default executor, the manager's `manage-agent` payload sets `executor` to the registered profile name (built-in or custom):

```json
{
  "action": "enroll",
  "name": "dev_agent_codex",
  "task_id": "TASK-123",
  "session_id": "sess-abc123",
  "description": "Implements product and platform changes as a Codex-backed developer agent.",
  "system_prompt": "You are the Dev Agent. Your responsibilities are...",
  "executor": "codex",
  "repos": {
    "happyranch": "https://github.com/t-benze/happyranch.git"
  }
}
```

After approval, the requested executor and repos are persisted to the agent's
`org/agents/<name>.md` frontmatter (`AgentDef`), the declared repos are cloned
into the workspace, and every built-in executor workspace is bootstrapped with
a regular `AGENTS.md` plus a raw relative `CLAUDE.md -> AGENTS.md` link. Startup
refuses an incomplete or noncanonical pair before executor launch and directs
the operator to repair it with `happyranch init-agent <agent>`.

## Managing the daemon

`scripts/daemon.sh` is a tiny supervisor that records the pid/port under `~/.happyranch/`:

```bash
scripts/daemon.sh start    # start in background (binds to localhost:8765)
scripts/daemon.sh status   # check if running
scripts/daemon.sh stop --force     # graceful shutdown (default daemon needs --force)
```

The daemon binds to port **8765** by default. Override with `HAPPYRANCH_DAEMON_PORT=<n>` before starting if that port is taken.
`scripts/daemon.sh start` waits up to 120 seconds for the daemon's public
`GET /api/v1/health` endpoint to answer. Set
`HAPPYRANCH_DAEMON_START_TIMEOUT=<seconds>` to another positive integer for a
slower host. If the daemon process exits during startup, the command fails
immediately; process-exit and timeout failures print the last 20 log lines.
When `curl` is unavailable, the script reports that it is falling back to the
fresh `daemon.port` file as its readiness signal.

> **Offline org relocation:** for the founder-operated manual procedure to move
> an org between runtimes, see
> [`docs/operations/offline-organization-relocation.md`](https://github.com/t-benze/happyranch/blob/main/docs/operations/offline-organization-relocation.md).

## Custom-skill logical removal

To permanently deny an already-retired current-v2 custom skill without deleting its history or files, use `happyranch skills purge <skill-id> --org <org>`. The CLI asks you to type the exact slug; automation may pass `--typed-slug`. This is an irreversible logical purge (`physical_erasure=false`), and repeated calls return the existing tombstone. Removed tombstones are hidden from the ordinary Custom catalog and available in its explicit Removed view and by saved detail URL; their permanently reserved slugs cannot be recreated. Current policy is withdrawn by supersession while all rule history is retained. Downgrade alone leaves the record retired and invisible; the documented pre-purge-binary limitation requires both an operator downgrade and explicit restore of that exact record, while purge-aware restore refuses permanently.
