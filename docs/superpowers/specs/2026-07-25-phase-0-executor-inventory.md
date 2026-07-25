# Phase 0 Executor Inventory — D1 Deliverable

**THR-107 / TASK-3347** | **2026-07-25** | **DOCS + TESTS ONLY**

This document records the **current** executor lifecycle inventory as shipping
on `origin/main` @ `a7134f00` (merged PR #495). It is the Phase 0 "D1"
deliverable only — inventory of existing behavior plus behavior-locking
shadow contract tests. No design, proposal, future architecture, or
production change is authorized. Distinguish each enumerated fact from
later-phase proposals (the merged unified-adapter architecture spec in
`2026-07-24-unified-adapter-runtime-architecture.md` is the design document;
this inventory documents what code exists *today*).

---

## 1. Profile Registry

### 1.1 Registration Source

The `ExecutorRegistry` (`runtime/orchestrator/executor_registry.py:139`)
is the process-wide singleton that holds all known executor profiles.

- **Built-in profiles** registered at construction time via
  `_register_builtins()` (`executor_registry.py:145-176`): `claude`, `codex`,
  `opencode`, `pi`.
- **Custom profiles** registered at runtime via `register_custom_profile()`
  (`executor_registry.py:199-246`) from the durable runtime store
  (`~/.happyranch/executor_profiles.yaml`) at daemon startup, or via the
  org/runtime-level register routes (`routes/executors.py`).

### 1.2 Built-in Profile Definitions

| Name | kind | adapter_id | readiness_marker_fragment | model_arg |
|------|------|-----------|--------------------------|-----------|
| `claude` | builtin | `claude` | `.claude/skills/start-task/SKILL.md` | `["--model", "{model}"]` |
| `codex` | builtin | `codex` | `AGENTS.md` | `["-m", "{model}"]` |
| `opencode` | builtin | `opencode` | `AGENTS.md` | `["-m", "{model}"]` |
| `pi` | builtin | `pi` | `AGENTS.md` | `["--model", "{model}"]` |

Source: `executor_registry.py:148-173`.

### 1.3 Custom Profile Registration Path

```text
registrant → POST /api/v1/orgs/{slug}/executors or
             POST /api/v1/runtime/executors
         → validate_custom_profile_config() (executor_registry.py:270-344)
         → ExecutorRegistry.register_custom_profile() (executor_registry.py:199-246)
         → persist durable store + in-memory registry
```

Validation (`validate_custom_profile_config`, line 270):
- `adapter` must be one of `claude`/`codex`/`opencode`/`pi` — selects workspace adapter
- `argv_template` must be a non-empty list of strings with valid placeholders (`{prompt}`, `{timeout_seconds}`, `{workspace}`)
- `command` must resolve on PATH; `argv_template[0]` must resolve to the same executable
- Readiness marker: `AGENTS.md` for `codex`/`opencode`/`pi`, `.claude/skills/start-task/SKILL.md` for `claude`

### 1.4 `build_executor()`: The Executor Factory

`build_executor()` (`executor_registry.py:350-408`) resolves a profile name to
an executor instance. It contains a **hard-coded if/elif chain**:

```python
if profile.name == "claude":   return ClaudeExecutor(...)
if profile.name == "codex":    return CodexExecutor(...)
if profile.name == "opencode": return OpencodeExecutor(...)
if profile.name == "pi":       return PiExecutor(...)
# Custom: return GenericCliExecutor(profile_name=..., argv_template=..., provider=name)
```

The factory reads CLI paths from `Settings` for built-ins; custom profiles use
`argv_template[0]` as the executable.

**Current callers:**
- `Orchestrator._build_executor()` — task execution
- `thread_runner._build_executor_for_provider()` — threads/wakes/dreams/schedules

---

## 2. Five Executor Profiles — Current argv and Parsing

### 2.1 ClaudeExecutor

**File:** `runtime/orchestrator/executors.py:757-822`

**Argv shape** (model injection before permission flags):
```
<resolved claude> [--model <model>] -p <prompt> --permission-mode auto
  --allowedTools <allow_rules> --output-format json [--resume <id>]
```

- `--allowedTools` is the permission surface (settings.json is bypassed in headless `-p` mode).
- Model injects via `--model {model}` replacement.
- `--resume` only when `resume_session_id` is provided.
- Calls `_run_command(cmd, workspace, ..., usage_parser=_parse_claude_usage, session_id_parser=_parse_claude_session_id, provider="claude")`.

**Workspace adapter:** `claude` — writes `CLAUDE.md`, `.claude/settings.json`, `.claude/skills/`.

**Output parser:** `_parse_claude_usage()` (line 220-249) — expects `--output-format json` JSON object with `usage.input_tokens/output_tokens/cache_read_input_tokens/cache_creation_input_tokens`. Model resolved from `modelUsage` (highest-output-tokens model id). Falls back to raw-only TokenUsage on parse failure.

**Session ID parser:** `_parse_claude_session_id()` (line 253-266) — reads `session_id` from the result JSON object.

### 2.2 CodexExecutor

**File:** `runtime/orchestrator/executors.py:824-870`

**Argv shape:**
```
<resolved codex> exec [--model <model>] --sandbox <mode>
  -c sandbox_workspace_write.network_access=true --skip-git-repo-check --json -
```

- Model injects via `-m {model}` replacement.
- Sandbox mode from `Settings.codex_sandbox_mode`.
- `-c sandbox_workspace_write.network_access=true` enables network for daemon callbacks.
- Reads prompt from stdin (passed as `input_text` to `_run_command`).
- Calls `_run_command(cmd, workspace, ..., usage_parser=_parse_codex_usage, provider="codex")`.

**Workspace adapter:** `codex` — writes `AGENTS.md`, `.agents/skills/`.

**Output parser:** `_parse_codex_usage()` (line 270-325) — walks JSONL events for last `{"type":"turn.completed"}` with cumulative `usage.input_tokens/output_tokens/cached_input_tokens/reasoning_output_tokens`. **Normalizes on ingest (issue #216):** Codex `input_tokens` includes `cached_input_tokens`; the parser computes `max(input_tokens - cached, 0)` for net-fresh input, consistent with Claude semantics.

### 2.3 OpencodeExecutor

**File:** `runtime/orchestrator/executors.py:872-927`

**Argv shape:**
```
<resolved opencode> run [--model <model>] --dir <workspace> --format json <prompt>
```

- Model injects via `-m {model}` replacement.
- Uses positional prompt (opencode >= 1.14.0 rejects `--prompt`).
- No `--allowedTools` equivalent; permission via workspace's `opencode.json`.

**Workspace adapter:** `opencode` — writes `AGENTS.md`, `opencode.json`, `.agents/skills/`.

**Output parser:** `_parse_opencode_usage()` (line 328-428) — supports two shapes:
- **Old format** (single JSON object): sums `usage` per assistant-role message from `messages[]`.
- **New JSONL format** (opencode >= 1.14.31): walks events for last `step_finish.part.tokens`.

### 2.4 PiExecutor

**File:** `runtime/orchestrator/executors.py:929-969`

**Argv shape:**
```
<resolved pi> [--model <model>] -p <prompt> --mode json
```

- Model injects via `--model {model}` replacement.
- Uses `-p` for headless mode, `--mode json` for structured output.
- No HappyRanch-managed permission surface (doc note at line 939).

**Workspace adapter:** `pi` — writes `AGENTS.md`, `.agents/skills/`.

**Output parser:** `_parse_pi_usage()` (line 431-473) — walks JSONL for terminal `message_end` or `turn_end` events with `message.usage` (keys: `input`, `output`, `cacheRead`, `cacheWrite`, `totalTokens`). Last event wins. Falls back to raw-only on parse failure.

### 2.5 GenericCliExecutor (Custom Profiles)

**File:** `runtime/orchestrator/executors.py:971-1038`

**Argv shape:** Fully derived from `argv_template` with placeholder substitution at launch time:

| Placeholder | Substitution |
|-------------|-------------|
| `{prompt}` | Full prompt (with session-lifetime preamble prepended) |
| `{timeout_seconds}` | `str(timeout_seconds)` |
| `{workspace}` | `str(workspace)` |

- `argv_template[0]` is resolved to an absolute path via `_resolve_binary()`.
- **Each placeholder resolves to exactly ONE argv element** — no splitting, no shell interpolation, no concatenation with literal text.
- Calls `_run_command(cmd, workspace, ..., usage_parser=_parse_generic_cli_usage, provider=profile_name)`.

**Workspace adapter:** Per-profile's `adapter_id` (one of `claude`/`codex`/`opencode`/`pi`).

**Output parser:** `_parse_generic_cli_usage()` (line 480-545) — looks for v1 sentinel envelope:
```
__HR_ENVELOPE_BEGIN__
{json}
__HR_ENVELOPE_END__
```
- Envelope is **optional** — absence = no token accounting (returns `None`).
- `envelope_version` must be `1` (int), else raw-only TokenUsage.
- Maps `token_usage` dict keys with key-name parity to `TokenUsage` fields.
- Top-level `model` backfills `token_usage.model` when absent.

---

## 3. Shared Execution Layer: `_run_command`

**File:** `runtime/orchestrator/executors.py:607-725`

All five executors converge through `_run_command()`:

```
_executor.run() → _run_command(cmd, workspace, session_id, timeout_seconds,
                                input_text, on_started, usage_parser,
                                session_id_parser, provider, on_throttle_event)
                → ExecutorResult
```

Key behaviors:

1. **Subprocess launch:** `subprocess.Popen` with `cwd=workspace`, `stdin=PIPE` (only when `input_text` is set), `stdout=PIPE`, `stderr=PIPE`, `text=True`.
2. **Timeout:** `proc.communicate(timeout=timeout_seconds)`. On `TimeoutExpired`: `proc.kill()`, drain pipes, return `ExecutorResult(success=False, error="Session timed out after N seconds")`. `returncode` is `None` on timeout.
3. **Non-zero exit:** `ExecutorResult(success=False, returncode=rc, error="Command exited with code N: ...")`. No token usage row is written.
4. **Tail truncation:** Both `stdout_tail` and `stderr_tail` are the last 2000 bytes.
5. **Rate-limit detection:** `is_rate_limit_signature()` checks combined stdout+stderr; sets `rate_limited=True`.
6. **Token usage parser:** Called on `returncode==0`. Exception-safe — parser raises are caught and logged, `token_usage` stays `None`.
7. **Model back-fill:** If `token_usage.model is None`, filled with `provider` string.
8. **Throttle:** `_run_command` wraps `_launch` in `get_throttle().run(provider, _launch, on_throttle_event)` for per-provider rate limiting.

---

## 4. Convergent Persistence and Audit

### 4.1 `ExecutorResult` → `run_step`

`run_step._run_agent()` (`orchestrator.py`) consumes `ExecutorResult`:

- On **success**: `result.token_usage` → `audit.log_session_end(task_id, ..., token_usage=...)` → `database.insert_session_token_usage(...)`.
  - `stdout_tail`/`stderr_tail` used for `_session_failed_note` enrichment on parse failures (`run_step.py:1087-1091`).
- On **failure** (success=False): `result.error`, `result.stdout_tail`, `result.stderr_tail` → `audit.log_agent_failure(...)`.
  - `stdout_tail`/`stderr_tail` read by `run_step.py:1850-1852` (rate-limit heuristic), `run_step.py:1922-1928` (failure audit note), `run_step.py:2489-2490` (error-summary).
  - `result.rate_limited` → per-provider throttle backoff logic.

### 4.2 `ExecutorResult` → `thread_runner`

Thread/wake/schedule/dream execution also consumes `ExecutorResult`:

- `thread_runner.py:58`: `result.stderr_tail` for invocation error message.
- `thread_runner.py:293-294`: `result.stderr_tail` + `result.stdout_tail` for wake/schedule/dream error formatting.

---

## 5. Workspace Preparation Mapping

| profile name | adapter_id | bootstrap file | skills dir | permission surface |
|---|---|---|---|---|
| `claude` | `claude` | `CLAUDE.md` | `.claude/skills/` | `.claude/settings.json` + `--allowedTools` CLI flag |
| `codex` | `codex` | `AGENTS.md` | `.agents/skills/` | Codex sandbox flags (`--sandbox`, `-c sandbox_workspace_write.network_access=true`) |
| `opencode` | `opencode` | `AGENTS.md` | `.agents/skills/` | `opencode.json` (permission file, no CLI flag) |
| `pi` | `pi` | `AGENTS.md` | `.agents/skills/` | No HR-managed permission surface |
| custom | per-profile `adapter_id` | per adapter above | per adapter above | per adapter above |

Source: `workspace_adapters.py` — `ClaudeWorkspaceAdapter`, `CodexWorkspaceAdapter`, `OpencodeWorkspaceAdapter`, `PiWorkspaceAdapter`.

---

## 6. Current vs. Target (What Phase 1+ Proposes, NOT Current)

This inventory documents the **current** implementation only. The merged
unified-adapter architecture spec (`2026-07-24-unified-adapter-runtime-architecture.md`)
proposes (but does NOT authorize):

| Aspect | Current (TODAY) | Proposed (Phase 1+) |
|---|---|---|
| `build_executor()` | Hard-coded `if/elif` chain | Data-driven via adapter catalog |
| argv construction | Inline in each `*Executor.run()` | Extracted to adapter `build_argv()` |
| output parsing | Five hand-written parsers in `executors.py` | Per-adapter `parse_output()` |
| adapter_id meaning | workspace adapter only | proposed split to `workspace_adapter_id` + `command_adapter_id` |
| custom adapter model | `GenericCliExecutor` with template substitution | proposed separate executable + stdin/stdout contract |
| ExecutorResult fields | `success`, `duration_seconds`, `session_id`, `returncode`, `stdout_tail`, `stderr_tail`, `error`, `token_usage`, `agent_session_id`, `rate_limited` | proposed additive `result.text` and `adapter_metadata` (both require founder decision D2) |

---

## 7. Seams for Behavior-Locking Tests

The following test seams are used in the accompanying Phase-0 behavior-locking
tests at `tests/test_phase0_executor_contracts.py`:

| Seam | How exercised |
|---|---|
| `_run_command` with mocked `subprocess.Popen` | Tests nonzero, timeout, tail truncation, rate-limit, token-accounting forward |
| `_parse_claude_usage`, `_parse_codex_usage`, `_parse_opencode_usage`, `_parse_pi_usage`, `_parse_generic_cli_usage` | Direct unit coverage with fixture data |
| `validate_custom_profile_config` | Profile validation path exercised through registry |
| `build_executor` + per-executor `.run()` cmd capture | Captures exact argv lists without subprocess launch |
| `ExecutorRegistry.register_custom_profile` + `build_executor` → `GenericCliExecutor` | Custom profile argv_template substitution and `_resolve_binary` |
| `run_step` / `audit` seam | Verified through direct `ExecutorResult` shape forwarded to audit/db call site |
| Workspace adapter mapping | Verified through profile → adapter_id → bootstrap file assertions |

---

## Appendix: Source Evidence

| File | Lines | Content |
|---|---|---|
| `runtime/orchestrator/executor_registry.py` | 1-443 | `ExecutorProfile`, `ExecutorRegistry`, `_register_builtins`, `validate_custom_profile_config`, `build_executor`, `get_registry` |
| `runtime/orchestrator/executors.py` | 1-1038 | `ExecutorResult`, `_run_command`, `_parse_claude_usage`, `_parse_codex_usage`, `_parse_opencode_usage`, `_parse_pi_usage`, `_parse_generic_cli_usage`, `ClaudeExecutor`, `CodexExecutor`, `OpencodeExecutor`, `PiExecutor`, `GenericCliExecutor`, `_SESSION_LIFETIME_PREAMBLE` |
| `runtime/orchestrator/workspace_adapters.py` | 1-1299 | `ClaudeWorkspaceAdapter`, `CodexWorkspaceAdapter`, `OpencodeWorkspaceAdapter`, `PiWorkspaceAdapter` |
| `runtime/models.py` | 302-316 | `TokenUsage` model |
| `tests/fixtures/usage_claude.json` | — | Claude `--output-format json` fixture |
| `tests/fixtures/usage_codex.jsonl` | — | Codex `exec --json` JSONL fixture |
| `tests/fixtures/usage_opencode.json` | — | Opencode old-format fixture |
| `tests/fixtures/usage_opencode_jsonl.json` | — | Opencode JSONL (>=1.14.31) fixture |
| `tests/fixtures/usage_pi.jsonl` | — | Pi `--mode json` JSONL fixture |

### GitNexus Impact (corroborated @ `a7134f00`)

| Symbol | Risk | Impacted | Direct | Processes | Modules |
|---|---|---|---|---|---|
| `ExecutorRegistry` (class, `executor_registry.py:139`) | **CRITICAL** | 83 | 13 | 23 | 4 |
| `build_executor` (function, `executor_registry.py:350`) | **HIGH** | 12 | 2 | 0 | 3 |
| `GenericCliExecutor` (class, `executors.py:971`) | **MEDIUM** | 53 | 7 | 0 | 2 |
| `_run_command` (function, `executors.py:607`) | **MEDIUM** | 5 | 5 | 0 | 1 |

Pre-established results from the merged spec Appendix A confirmed against
current GitNexus index at `a7134f00`. No production symbol is edited.

---

*Phase 0 — DOCS + TESTS ONLY. No production code change, no adapter extraction, no compatibility facade, no schema/auth/protocol change.*
