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
   2. **Consult per-agent learnings.** If `learnings/_index.md` exists in your workspace, scan it for entries relevant to the current brief and fetch full bodies with `opc learning get --org {ORG_SLUG} --agent <your_agent_name> <LRN-NNN-or-slug>`. Pre-migration workspaces have a flat `learnings.md` inlined into your bootstrap doc instead.
   3. If the current brief references prior work — phrases like "follow up on", "continue", "the report from last week", a specific date, or an explicit `TASK-xxx` — identify the matching entry and fetch the details:

      ```bash
      opc recall --org {ORG_SLUG} <task_id>                       # brief + final summary
      opc recall --org {ORG_SLUG} <task_id> --tree                # include the full subtree of child tasks
      opc recall --org {ORG_SLUG} <task_id> --fetch-artifact      # inline artifact bodies (capped at 200KB)
      ```
   4. If the brief does not reference prior work, skip step 3. Do not pull history speculatively.

3. **Consult the knowledge base.** Before planning, check for durable knowledge relevant to this task.

   Run either:

   ```bash
   opc kb list --org {ORG_SLUG} --topic <guess>                # browse a topic
   opc kb search --org {ORG_SLUG} "<terms from brief>"         # keyword search
   ```

   Fetch full entries with:

   ```bash
   opc kb get --org {ORG_SLUG} <slug>
   ```

   **Consult triggers** — scan the KB whenever your brief touches:
   - regulatory / compliance rules that bind your org;
   - partner / vendor APIs, integration quirks, rate limits;
   - payment, refund, or other money-flow policies;
   - any topic where a past escalation likely produced a binding ruling.

   If nothing matches, proceed. If something matches, treat it as authoritative unless the brief explicitly contradicts it — in which case escalate rather than silently override.

4. **Plan and execute.** Treat `role_guidance` as your primary instruction. If repo writes are needed, invoke the **make-worktree** skill first.

   If the task produces a standalone document (report, plan, analysis), write its files under `artifacts/<task_id>/` in your workspace root — **not** inside any repo or worktree. Capture the relative path (e.g. `artifacts/TASK-001`) and include it as `artifact_dir` in your completion payload so future sessions can retrieve it via `opc recall --org {ORG_SLUG} <task_id>`.

5. **Report progress (long-running tasks).** If the task spans more than a
   few minutes — multi-phase implementation, lengthy build/test, large
   research sweep — emit a one-line progress note at every meaningful
   milestone so the founder can `opc tail` / `opc details` and see live
   movement instead of a black box until completion.

   ```bash
   opc progress --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --message "Phase 3 of 6: tests passing"
   ```

   When to emit: phase boundaries, before/after long shell-outs (>1 min),
   when changing direction, on a non-fatal blocker you're working around.
   When NOT to emit: every file edit, every grep, anything you'd consider
   trivial mid-step bookkeeping. Treat it like a status line, not a log.

6. **Report mid-task learnings (optional).** Whenever you discover something reusable for future tasks.

   **Migrated workspaces (per-entry learnings, `learnings/` dir exists):** write a YAML payload to `/tmp/lrn-<slug>.yaml` (`slug`, `title`, `topic`, optional `tags`, `related_to`, `body`) and call:

   ```bash
   opc learning add --org {ORG_SLUG} --agent <your_agent_name> --from-file /tmp/lrn-<slug>.yaml
   ```

   **Pre-migration workspaces (legacy flat `learnings.md`):** the single-line `--text` form still appends to the flat file. The daemon returns `410 Gone` for this form on migrated workspaces; switch to the verb-dispatched form above if you see that error.

   ```bash
   opc learning --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --text "..."
   ```

7. **Contribute to the KB (optional).** Before reporting completion, ask yourself: did I discover or confirm durable, cross-agent-relevant knowledge that isn't already in the KB?

   **Contribute YES if any are true:**
   - Factual rule other agents would need (API rate limit, regulatory deadline, partner contract term).
   - You consulted the KB and an entry was wrong or outdated — update it.
   - A non-trivial procedural decision worth preserving as a mini-SOP (not a one-off workaround).

   **Contribute NO if:**
   - The info is specific to this task (→ task artifact).
   - It's your own operational preference (record it via the mid-task learning callback instead).
   - It's already in `protocol/` docs.
   - The info has a <12-month useful lifespan.

   Write `/tmp/kb-<slug>.md` with YAML frontmatter (`slug`, `title`, `type`, `topic`, optional `tags`, `source_task`) followed by a markdown body, then:

   ```bash
   opc kb add --org {ORG_SLUG} --agent <your_agent_name> --from-file /tmp/kb-<slug>.md
   ```

   For updates: `opc kb update --org {ORG_SLUG} <slug> --agent <you> --from-file /tmp/kb-<slug>.md`. Resolve collision 409s by updating the existing entry instead of forcing a sibling. The `--from-file` pattern is mandatory across executors; in Claude sessions multi-line `opc` payloads are rejected by the `Bash(opc:*)` permission rule.

8. **Report completion.** When you finish (success or blocker), write a JSON
   payload to a file and invoke `opc report-completion --org {ORG_SLUG} --from-file <path>` as
   a single-line command. The file form is mandatory across executors. In
   Claude sessions, multi-line bash commands with backslash continuations are
   rejected by the permission rule because newlines count as command
   separators and only the first subcommand matches `Bash(opc:*)`.

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

   **Team-manager only — add a `decision` field.** Alongside the prose
   `summary`, a team-manager session must include a top-level `decision`
   object that the orchestrator will execute. Workers omit it. Omitting it
   from a manager session escalates the task. See the response-format
   section of your role_guidance for the exact shapes. The `action` must be
   one of `delegate`, `done`, or `escalate`:

   - `delegate` — hand the next subtask to a worker; requires `agent` and `prompt`.
     Note the field is `prompt`, **not** `brief` — the orchestrator silently
     drops unknown keys, so writing `"brief"` produces a child task with an
     empty brief.
   - `done` — the task is complete; requires `summary` of the outcome.
   - `escalate` — the task needs founder intervention; requires `reason`.

   Example (delegation):

   ```json
   {
     "task_id": "TASK-XXX",
     "session_id": "<sid>",
     "agent": "<your_agent_name>",
     "status": "completed",
     "confidence": 90,
     "summary": "Triaged and staged implementation for the worker.",
     "decision": {"action": "delegate", "agent": "<worker_agent_name>", "prompt": "..."}
   }
   ```

   Then submit:

   ```bash
   opc report-completion --org {ORG_SLUG} --from-file /tmp/completion-<task_id>.json
   ```

9. **Cleanup.** Always run worktree cleanup as the final step, even on the blocker path. The make-worktree skill describes how.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has spawned a newer session for this `(task_id, agent)`) and `409 unknown_session` (the daemon has no record of this spawn — the session is orphaned). Either way, exit immediately.
