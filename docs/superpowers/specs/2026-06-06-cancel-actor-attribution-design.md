# Cancel actor attribution

## Problem

When a task is cancelled, the audit log and the task note always record the
actor as `"founder"`, regardless of who actually triggered the cancellation.

This was observed in `family/THR-003`: `family_manager` cancelled `TASK-012`
itself (it ran `happyranch cancel` during a turn, to supersede an
in-flight report with `TASK-013`), but the trail reads as if the human founder
did it:

- audit log id 146 — `agent:"founder"`, `action:"task_cancelled"`,
  `payload:{rationale:"", cascade:true}`
- task note — `"cancelled by founder"`
- thread `task_failed` system payload `final_output_summary` — `"cancelled by founder"`

The misattribution comes from two hardcoded sites:

- `runtime/infrastructure/audit_logger.py` — `log_task_cancelled()` writes
  `agent="founder"` unconditionally.
- `runtime/daemon/routes/tasks.py:844` — the note is unconditionally
  `"cancelled by founder"`.

The `POST /tasks/{id}/cancel` route has no actor concept at all; it carries
only `rationale` and `cascade`.

## Constraint: shared token

Founder (CLI/web) and agents authenticate to the daemon with the **same**
bearer token (`runtime/daemon/auth.py`, whole `tasks` router gated by
`require_token()`). The daemon therefore cannot distinguish founder from agent
at the cancel route. Actor attribution is **advisory** — declared by the
caller. Within this single-founder, honest-actor runtime that is acceptable;
we are not defending against a caller that lies about its identity.

The executor spawns agent subprocesses with no agent-identity env var
(`subprocess.Popen` in `runtime/orchestrator/executors.py` passes no `env=`),
so the CLI cannot auto-detect the caller. The agent must pass its own name —
consistent with how agents already pass `--agent NAME` / `--session-id` on
completion callbacks (the name is in the agent's prompt).

## Design

Add an optional, caller-declared `actor` to the cancel path. Default
`"founder"` preserves current behavior byte-for-byte.

### Changes

1. **`runtime/infrastructure/audit_logger.py`**
   `log_task_cancelled(self, task_id, rationale, cascade, actor="founder")`;
   log `agent=actor` instead of the hardcoded `"founder"`.

2. **`runtime/daemon/routes/tasks.py`**
   - `CancelBody` gains `actor: str | None = None`.
   - In `cancel_task`: `actor = (body.actor or "").strip() or "founder"`.
   - Note: `f"cancelled by {actor}: {rationale}"` when `rationale` else
     `f"cancelled by {actor}"`.
   - Pass `actor=actor` into `audit.log_task_cancelled(...)`.

3. **`cli/commands/tasks.py`** (`cmd_cancel` + parser)
   - Add `--as-agent NAME`. When set, include `"actor": NAME` in the POST body;
     omit the key otherwise (so the route default applies).
   - Soften the founder-exclusive framing in the subcommand/`--rationale` help.

4. **`runtime/orchestrator/run_step.py:998`**
   Update the stale comment referencing `"cancelled by founder: ..."` to reflect
   the now-variable actor. No logic change — the `_fail` cancel-race guard keys
   on `_is_already_terminal` (status / `cancelled_at`), not on the note text.

### Data flow

```
CLI: happyranch cancel TASK-012 --as-agent family_manager
  -> POST /tasks/TASK-012/cancel {rationale:"", cascade:true, actor:"family_manager"}
     -> note = "cancelled by family_manager"
     -> audit: agent="family_manager", action="task_cancelled"
     -> thread task_failed final_output_summary = "cancelled by family_manager"
```

Single source: the note feeds the thread-followup summary, so fixing the note
fixes the thread view too.

## Backward compatibility

- No `actor` / no `--as-agent` → strings stay exactly `"founder"` /
  `"cancelled by founder"`. Existing cancel and thread-followup tests stay green.
- `CancelBody` gains an optional field. The OpenAPI snapshot
  (`tests/contract/test_openapi_snapshot.py`) only pins paths/params/response
  codes — not request-body schemas — so it does NOT change. No new TS api
  function (existing cancel function gains an optional arg); confirm
  `web/src/test/openapi-coverage.test.ts` still passes.

## Testing

- Cancel with `actor="family_manager"`: audit `agent="family_manager"`, note
  `"cancelled by family_manager"`.
- Cancel with no actor: audit `agent="founder"`, note `"cancelled by founder"`.
- With `rationale`: note `"cancelled by <actor>: <rationale>"`.

## Out of scope (follow-up)

This adds the runtime/CLI capability to *honor* a supplied actor. It does not
make `family_manager` (or any agent) actually pass `--as-agent` — that requires
org-side prompt/skill guidance, which lives in the runtime container
(`<runtime>/orgs/<slug>/org/`), outside this repo. Until that runtime-side
change lands, agent-initiated cancels will continue to default to `"founder"`.
