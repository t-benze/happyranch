---
name: start-task
description: Use this skill at the start of every task. Parses task_id, session_id, brief, and role_guidance from the prompt, executes the work, reports completion back to the daemon, and cleans up worktrees.
---

# start-task

The orchestrator daemon spawns you with a prompt of this form:

```
You are <agent_name>. Use the start-task skill to handle this task.
Parameters:
  task_id: TASK-XXX
  session_id: <uuid>
  brief: <task brief>
  role_guidance: <role-specific instructions>
```

## Steps

1. **Parse parameters.** Extract `task_id`, `session_id`, `brief`, and `role_guidance` from the prompt above. Hold `session_id` in a variable for the lifetime of this session — every callback to `opc` must include it.

2. **Consult memory.** Before planning:

   1. Read `task_history.md` in your workspace root. It lists your recent tasks with briefs, outcomes, and (when present) artifact paths.
   2. If the current brief references prior work — phrases like "follow up on", "continue", "the report from last week", a specific date, or an explicit `TASK-xxx` — identify the matching entry and fetch the details:

      ```bash
      opc recall <task_id> --fetch-artifact
      ```

      Add `--tree` if you need the child tasks too.
   3. If the brief does not reference prior work, skip this step. Do not pull history speculatively.

3. **Plan and execute.** Treat `role_guidance` as your primary instruction. If repo writes are needed, invoke the **make-worktree** skill first.

   If the task produces a standalone document (report, plan, analysis), write its files under `artifacts/<task_id>/` in your workspace root — **not** inside any repo or worktree. Capture the relative path (e.g. `artifacts/TASK-001`) and include it as `artifact_dir` in your completion payload so future sessions can retrieve it via `opc recall <task_id>`.

4. **Report mid-task learnings (optional).** Whenever you discover something reusable for future tasks:

   ```bash
   opc learning --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --text "..."
   ```

5. **Report completion.** When you finish (success or blocker), write a JSON
   payload to a file and invoke `opc report-completion --from-file <path>` as
   a single-line command. The file form is mandatory: multi-line bash commands
   with backslash continuations are rejected by the agent's Claude Code
   permission rule (newlines count as command separators, and only the first
   subcommand matches `Bash(opc:*)`).

   Use the Write tool to create `/tmp/completion-<task_id>.json` with this shape:

   ```json
   {
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "agent": "<your_agent_name>",
     "status": "completed",
     "confidence": 85,
     "summary": "<what you did>",
     "risks": ["<concern>"],
     "dependencies": ["<assumption>"],
     "reviewer_focus": ["<where to look hardest>"],
     "artifact_dir": "artifacts/<task_id>"
   }
   ```

   For a blocker, set `"status": "blocked"`, `"confidence": 0`, and put the
   reason in `summary`. Optional keys (`risks`, `dependencies`,
   `reviewer_focus`, `confidence`, `artifact_dir`) may be omitted.

   Then submit:

   ```bash
   opc report-completion --from-file /tmp/completion-<task_id>.json
   ```

6. **Cleanup.** Always run worktree cleanup as the final step, even on the blocker path. The make-worktree skill describes how.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has spawned a newer session for this `(task_id, agent)`) and `409 unknown_session` (the daemon has no record of this spawn — the session is orphaned). Either way, exit immediately.
