# System Assistant: Self-Registration Replaces Executor Probing

> Status: current
> Current Source: this spec (until implemented, then see docs/agent-guides/features-and-invariants.md)
> Supersedes: docs/superpowers/specs/2026-06-08-system-assistant-design.md (executor probing / `assistant probes` / `assistant configure` onboarding only; runtime attach, workspace, KB/learnings unchanged)

## Problem

The current `happyranch assistant init` flow discovers a usable agentic CLI by
**probing**: for each executor in a fixed list (`claude`, `codex`, `opencode`,
`pi`) the daemon launches the real interactive CLI in a PTY in a throwaway temp
workspace, writes a `CLAUDE.md`/`AGENTS.md` prompt-surface asking the agent to
echo a probe token, and waits for the token on the PTY.

On a real machine every probe fails, for reasons the probe cannot dismiss:

- **claude** blocks on a folder-trust dialog ("Is this a project you trust?")
  that fires for every fresh temp dir.
- **codex** and **opencode** block on "update available" dialogs.
- **pi** renders the request but does not respond within the timeout.

Because the four probes run sequentially at up to 15s each, the whole
`POST /assistant/probes` call can take ~60s, which also exceeds the CLI HTTP
client's hard 30s timeout — so `assistant init` times out before the (already
failing) probe even returns.

The root cause is structural: probing assumes each CLI boots straight into an
interactive agent loop, but real CLIs gate startup behind onboarding/update
dialogs that only a human can clear.

## Approach

Invert control. Instead of HappyRanch launching and probing CLIs, the agentic
CLI **registers itself** by calling the `happyranch` CLI — the same principle
the rest of the runtime already uses ("agents act through the `happyranch`
CLI"). A successful registration callback is itself proof that the CLI is
installed, authenticated, and able to run commands. No dialog problem, because
the **human** launches their own CLI and clears any dialogs once.

This mirrors the existing `manage-agent enroll` callback pattern.

### Decisions

1. **Self-registration replaces probing.** Remove the probe machinery entirely.
2. **Human launches, agent self-reports.** The founder opens their own agentic
   CLI in the assistant workspace; the agent calls back to register.
3. **Agent declares the command; daemon validates structurally.** The agent
   declares `{executor, command, argv}`. The daemon validates that the command
   resolves to a real executable, argv is well-formed, and the workspace exists.
   The callback already proves liveness; the daemon does not re-launch the CLI.
4. **`init` writes a prompt-surface + prints guidance.** Any agent opened in the
   workspace reads the instructions and self-registers.
5. **Auto-configure.** A valid registration immediately writes config and the
   assistant becomes `configured`. No separate founder approval step.

## End-to-End Flow

1. Founder runs `happyranch assistant init`. It ensures the assistant workspace
   exists and writes a **registration prompt-surface** (`CLAUDE.md` and
   `AGENTS.md`) into the workspace, then prints founder guidance. State stays
   `uninitialized`.
2. Founder opens their own agentic CLI in the workspace, clearing any
   trust/update dialogs once.
3. The agent reads the prompt-surface and runs a single-line callback:
   `happyranch assistant register --from-file <path>` where the file contains
   `{"executor": "...", "command": "...", "argv": ["..."]}`.
4. The daemon structurally validates the payload. On success it writes
   `AssistantConfig` and the state becomes `configured`. On failure it returns
   `400` with a clear error code.
5. `attach` works as today, launching `selected_command`/`selected_argv` in the
   persistent workspace. (Unchanged.)

## CLI Surface

- **New:** `happyranch assistant register --from-file <path>` — agent callback,
  modeled on `manage-agent enroll`. Single-line `--from-file` form per the
  callback invariant (no shell separators / multiline continuations). The
  assistant is container-level, so no `--org` is required.
  - Payload: `{"executor": str, "command": str, "argv": [str]}`.
- **Changed:** `happyranch assistant init` no longer probes. It bootstraps the
  workspace, writes the registration prompt-surface, and prints instructions.
  `--repair` and `--reconfigure` retained; `--reconfigure` clears existing
  config so a new registration can land.
- **Unchanged:** `assistant status`, `assistant attach`.

## Daemon & Routes

- **Remove** `POST /api/v1/assistant/probes`.
- **Replace** `POST /api/v1/assistant/configure` with
  `POST /api/v1/assistant/register`.
  - Body: `{executor, command, argv}`.
  - Validation:
    - `executor` is a non-empty string.
    - `argv` is a non-empty list of non-empty strings.
    - `shutil.which(command)` (and/or `argv[0]`) resolves to an executable.
    - The assistant workspace exists.
  - On pass: write `AssistantConfig` (workspace path filled by the daemon),
    return the new `classify_assistant_state(...)` status.
  - On fail: `400` with a descriptive `code` (e.g.
    `assistant_executable_not_found`, `assistant_registration_invalid`).
- **Unchanged:** `GET /assistant/status`, `POST /assistant/repair`,
  `WS /assistant/session`. `repair` continues to call
  `bootstrap_assistant_workspace(root, executor=config.selected_executor)` with
  a string executor.

## Data Model

- `AssistantExecutor` (`StrEnum` of claude/codex/opencode/pi) → **plain `str`**.
  Any CLI can register, so a fixed enum no longer fits; this follows the
  existing "agent names are plain strings; do not introduce static agent enums"
  rule. Affects `AssistantConfig.selected_executor` and
  `AssistantStatus.selected_executor`.
- `AssistantConfig.latest_probe_results` is dropped (no more probing).

## Code Removal

In `runtime/daemon/assistant_pty.py`, remove the probe path:

- `ProbeRunner` and all its PTY-fork probe helpers
  (`_probe_in_workspace`, `_write_probe_request`, `_has_ready_response`,
  `_wait_for_child_exec`, the probe `_result` helper, etc.).
- `build_executor_specs`, `InteractiveExecutorSpec`.
- `build_probe_request`, `build_probe_response`, `PROBE_REQUEST`, `PROBE_READY`.
- Probe prompt-surface writing (`_write_prompt_surface`) and `ProbeResult`.

**Keep unchanged** (the runtime attach path, which is independent of probing):
`AssistantPtySession`, `AssistantSessionManager`, `_set_pty_window_size`,
`_DEFAULT_PTY_ROWS/COLS`.

In `runtime/daemon/routes/assistant.py`, remove probe helpers
(`_probe_result_to_dict`, `_normalize_probe_results`, `_probe_selected_executor`,
`_resolved_argv_for_spec`, `_spec_with_argv`, `_spec_for_executor`) and the
`probes`/`configure` routes; add the `register` route + structural validation.

## Prompt-Surface

`init` writes a workspace `CLAUDE.md` and `AGENTS.md` containing registration
instructions, roughly:

> You are being registered as the HappyRanch system assistant. Write a JSON file
> containing `{"executor": "<your cli name>", "command": "<launch command>",
> "argv": ["<launch>", "<args>", ...]}` and run:
> `happyranch assistant register --from-file <that file>`.

## Testing & Contract

- Rewrite `tests/test_assistant_pty.py` — drop probe tests; keep/verify the
  runtime PTY session tests.
- Rewrite `tests/daemon/test_routes_assistant.py` — replace probes/configure
  tests with `register` validation (valid payload configures; missing
  executable, empty argv, missing workspace each rejected with the right code).
- Update `tests/test_system_assistant.py` — `selected_executor` is now a string;
  drop `AssistantExecutor` references.
- Update `tests/test_config.py` — drop `assistant_probe_timeout_seconds` if it
  becomes unused.
- Regenerate the OpenAPI snapshot:
  `HAPPYRANCH_REGEN_OPENAPI=1 uv run python -m pytest tests/contract/test_openapi_snapshot.py`.
- Edit `web/src/test/openapi-coverage.test.ts` allowlist: remove the
  `POST /api/v1/assistant/probes` entry and rename
  `POST /api/v1/assistant/configure` → `POST /api/v1/assistant/register`. (These
  routes are CLI-only; no TS API client function is required.)

## Out Of Scope

- Web-based attach/registration (the routes stay CLI-only).
- Re-launch liveness verification (structural validation only, by decision 3).
- Multiple registered assistants / executor switching beyond `--reconfigure`.

## Risks & Notes

- A founder could declare a `command` that resolves to a real executable but
  with wrong flags that fail at attach time. Accepted: structural validation
  only; the failure surfaces at first attach, which is interactive and
  diagnosable. A future enhancement could add opt-in liveness verification.
- `executor` becoming a free string means status output is only as descriptive
  as what the agent declares; the prompt-surface should instruct a sensible
  value (e.g. the CLI's own name).
