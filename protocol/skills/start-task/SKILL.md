---
name: start-task
description: Use this skill at the start of every task. Parses task_id, session_id, brief, and role_guidance from the prompt, executes the work, reports completion via the opc CLI, and cleans up worktrees.
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

2. **Plan and execute.** Treat `role_guidance` as your primary instruction. If repo writes are needed, invoke the **make-worktree** skill first.

3. **Report mid-task learnings (optional).** Whenever you discover something reusable for future tasks:

   ```bash
   opc learning --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --text "..."
   ```

4. **Report completion.** When you finish (success or blocker), call:

   - **Success:**
     ```bash
     opc report-completion \
       --task-id <task_id> --session-id <session_id> --agent <your_agent_name> \
       --status completed --confidence <0-100> \
       --summary "<what you did>" \
       --risks "<concern>" \
       --dependencies "<assumption>" \
       --reviewer-focus "<where to look hardest>"
     ```
   - **Blocker:**
     ```bash
     opc report-completion \
       --task-id <task_id> --session-id <session_id> --agent <your_agent_name> \
       --status blocked --confidence 0 --summary "<what blocked you>"
     ```

5. **Cleanup.** Always run worktree cleanup as the final step, even on the blocker path. The make-worktree skill describes how.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has spawned a newer session for this `(task_id, agent)`) and `409 unknown_session` (the daemon has no record of this spawn — the session is orphaned). Either way, exit immediately.
