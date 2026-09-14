---
name: start-task
description: Use this skill at the start of every task. Parses task_id, session_id, brief, and role_guidance from the prompt, executes the work, reports completion back to the daemon, and cleans up worktrees.
---

# start-task

## THR-247 recovery-turn exception

When the supplied runtime binding is a completion-recovery turn, use that
fresh binding only to report already-performed work or observe an actual
task-owned job wait. Do not execute the ordinary brief, create work, or submit
new jobs. Ordinary resumed turns retain their normal policy.

The orchestrator daemon spawns you with a prompt of this form:

```
You are <agent_name>. Use the start-task skill to handle this task.
Parameters:
  task_id: TASK-XXX
  session_id: <uuid>
  brief: <task brief>
  role_guidance: |             # optional — present only when the orchestrator
    <role-specific overlay>     # has a per-task overlay (team-manager spawns
                                # carry the orchestration capabilities block here)
```

## Steps

1. **Parse parameters.** Extract `task_id`, `session_id`, `brief`, and (when present) `role_guidance` from the prompt above. Hold `session_id` in a variable for the lifetime of this session — every callback to `happyranch` must include it. When the `role_guidance` block is absent (typical worker spawn), treat `brief` as your complete per-task instruction.

2. **Consult memory.** Before planning:

   1. Read `task_history.md` in your workspace root. It lists your recent tasks with briefs, outcomes, and (when present) output dir paths.
   2. **Consult per-agent learnings.** If `learnings/_index.md` exists in your workspace, scan it for entries relevant to the current brief and fetch full bodies with `happyranch learning get --org {ORG_SLUG} --agent <your_agent_name> <LRN-NNN-or-slug>`. Pre-migration workspaces have a flat `learnings.md` inlined into your bootstrap doc instead.
   3. If the current brief references prior work — phrases like "follow up on", "continue", "the report from last week", a specific date, or an explicit `TASK-xxx` — identify the matching entry and fetch the details:

      ```bash
      happyranch recall --org {ORG_SLUG} <task_id>                       # brief + final summary
      happyranch recall --org {ORG_SLUG} <task_id> --tree                # include the full subtree of child tasks
      happyranch recall --org {ORG_SLUG} <task_id> --fetch-output      # inline output file bodies (capped at 200KB)
      ```
   4. If the brief does not reference prior work, skip step 3. Do not pull history speculatively.

3. **Consult the knowledge base.** Before planning, check for durable knowledge relevant to this task.

   Run either:

   ```bash
   happyranch kb list --org {ORG_SLUG} --topic <guess>                # browse a topic
   happyranch kb search --org {ORG_SLUG} "<terms from brief>"         # keyword search
   ```

   Fetch full entries with:

   ```bash
   happyranch kb get --org {ORG_SLUG} <slug>
   ```

   **Consult triggers** — scan the KB whenever your brief touches:
   - regulatory / compliance rules that bind your org;
   - partner / vendor APIs, integration quirks, rate limits;
   - payment, refund, or other money-flow policies;
   - any topic where a past escalation likely produced a binding ruling.

   If nothing matches, proceed. If something matches, treat it as authoritative unless the brief explicitly contradicts it — in which case escalate rather than silently override.

4. **Plan and execute.** Treat `role_guidance` as your primary instruction when present; otherwise treat `brief` as the full instruction. If repo writes are needed, invoke the **make-worktree** skill first.

   Follow applicable task and role instructions for verification and review.
   Report incomplete work and blockers with concrete evidence, and use the
   daemon's supported completion and decision actions.

   If the task produces a standalone document (report, plan, analysis), write its files under `output/<task_id>/` in your workspace root — **not** inside any repo or worktree. Capture the relative path (e.g. `output/TASK-001`) and include it as `output_dir` in your completion payload so future sessions can retrieve it via `happyranch recall --org {ORG_SLUG} <task_id>`.

   If during the task you realize you need async input from another agent
   (and you're not yet blocked), use the delivered `thread` skill selected
   from the injected skill catalog and follow its "Compose a new thread"
   guidance rather than escalating.

5. **Report progress (long-running tasks).** If the task spans more than a
   few minutes — multi-phase implementation, lengthy build/test, large
   research sweep — emit a one-line progress note at every meaningful
   milestone so the founder can `happyranch tail` / `happyranch details` and see live
   movement instead of a black box until completion.

   ```bash
   happyranch progress --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --message "Phase 3 of 6: tests passing"
   ```

   **Concrete checkpoint policy:** for any task expected to take
   more than a few minutes, emit a concise progress receipt at these points —
   milestones only, never chain of thought, reasoning, or command stdout:

   1. **After the initial scope/progress checkpoint** (before the first edit),
      one line naming the work underway, e.g. `Implementing the assigned
      task scope`.
   2. **Immediately before a command expected to exceed one minute** (long
      test suite, large build/install, migration), one line naming the
      command intent, e.g. `Running required verification`.
   3. **Immediately after that command returns**, one line with the outcome
      (exit status / pass-fail summary), e.g. `Verification completed (exit 0)`.

   The observer surfaces are honest about noncompliance: a live session whose
   latest receipt (or whose very start) is 5+ minutes old is shown as
   **stale-but-alive** in `happyranch details` and the Tasks UI rather than
   fabricating activity from heartbeats. Emit at phase boundaries, when
   changing direction, and on a non-fatal blocker you're working around;
   do NOT emit for every file edit, grep, or trivial mid-step bookkeeping.
   Treat it like a status line, not a log.

6. **Report mid-task learnings (optional).** Whenever you discover something reusable for future tasks.

   **Migrated workspaces (per-entry learnings, `learnings/` dir exists):** write a YAML payload to `/tmp/lrn-<slug>.yaml` (`slug`, `title`, `topic`, optional `tags`, `related_to`, `body`) and call:

   ```bash
   happyranch learning add --org {ORG_SLUG} --agent <your_agent_name> --from-file /tmp/lrn-<slug>.yaml
   ```

   **Pre-migration workspaces (legacy flat `learnings.md`):** the single-line `--text` form still appends to the flat file. The daemon returns `410 Gone` for this form on migrated workspaces; switch to the verb-dispatched form above if you see that error.

   ```bash
   happyranch learning --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --text "..."
   ```

7. **Contribute to the KB (optional).** Before reporting completion, ask yourself: did I discover or confirm durable, cross-agent-relevant knowledge that isn't already in the KB?

   **Contribute YES if any are true:**
   - Factual rule other agents would need (API rate limit, regulatory deadline, partner contract term).
   - You consulted the KB and an entry was wrong or outdated — update it.
   - A non-trivial procedural decision worth preserving as a mini-SOP (not a one-off workaround).

   **Contribute NO if:**
   - The info is specific to this task (→ task artifact).
   - It's your own operational preference (record it via the mid-task learning callback instead).
   - It duplicates runtime instructions or implementation reference material.
   - The info has a <12-month useful lifespan.

   Write `/tmp/kb-<slug>.md` with YAML frontmatter (`slug`, `title`, `type`, `topic`, optional `tags`, `source_task`) followed by a markdown body, then:

   ```bash
   happyranch kb add --org {ORG_SLUG} --agent <your_agent_name> --from-file /tmp/kb-<slug>.md
   ```

   For updates: `happyranch kb update --org {ORG_SLUG} <slug> --agent <you> --from-file /tmp/kb-<slug>.md`. Resolve collision 409s by updating the existing entry instead of forcing a sibling. The `--from-file` pattern is mandatory across executors; in Claude sessions multi-line `happyranch` payloads are rejected by the `Bash(happyranch:*)` permission rule.

8. **Report completion.** When you finish (success or blocker), write a JSON
   payload to a file and invoke `happyranch report-completion --org {ORG_SLUG} --from-file <path>` as
   a single-line command. The file form is mandatory across executors. In
   Claude sessions, multi-line bash commands with backslash continuations are
   rejected by the permission rule because newlines count as command
   separators and only the first subcommand matches `Bash(happyranch:*)`.

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
     "output_dir": "output/<task_id>"
   }
   ```

   Include verification evidence fields when the applicable task or role
   instructions require them. Report actual commands and exit statuses
   truthfully; do not label skipped or incomplete verification as passing.

   For a blocker, set `"status": "blocked"`, `"confidence": 0`, and put the
   reason in `summary`. Optional keys (`risks`, `dependencies`,
   `reviewer_focus`, `confidence`, `output_dir`) may be omitted.

   - If your role is to issue a verdict (code review, QA, design review, etc.), include `"verdict": "<value>"` in your payload. Free string; your team's workflow KB entry documents the vocabulary. Optional — workers without verdicts simply omit the field.

   **Team-manager only — add a `decision` field.** Alongside the prose
   `summary`, a team-manager session must include a top-level `decision`
   object that the orchestrator will execute. Workers omit it. Omitting it
   from a manager session escalates the task. See the response-format
   section of your role_guidance for the exact shapes. The runtime request models and transition handlers enforce the
   contract; this skill explains
   the valid actions:

   - `delegate` — hand the next subtask to a worker; requires `agent` and `prompt`.
     Note the field is `prompt`, **not** `brief` — the orchestrator silently
     drops unknown keys, so writing `"brief"` produces a child task with an
     empty brief. Managers can declare a multi-leg workflow chain inline by
     adding `"then": [...]` to a delegate decision. The orchestrator
     auto-advances routine legs without consuming orchestration steps.
     Optional `"attachments"`: a list of `{storage_key, display_name?}` refs
     to pre-uploaded task-attachment-store keys (upload-only, no path or URL).
     These become the spawned child's own attachment links.
     Each `"then"` leg may also carry its own `"attachments"`, persisted
     when the orchestrator auto-advances to that leg.
   - `fanout` (the `parallel` alias is also accepted) — spawn N child tasks in parallel (2 ≤ N ≤ 8,
     Phase 2). Requires `children` (array of `{agent, prompt}` objects)
     and `width_cap_ack` (must exactly equal the child count). Optional `join_summary`
     (prose directive for the join prompt). Per-child `then`/`expect_verdict`
     are accepted as a *pipeline carrier* — the child runs its own inline delegation chain.
     Each child may have optional `"attachments"` (same shape). Pipeline
     carriers own their declared refs; the first leg inherits by ancestry.
     Duplicate storage keys across siblings are a single invalid fanout.
     Children targeted at a **team manager** are decision-capable (mutating fan-out);
     children targeted at regular **workers** are read-only (structured decisions ignored,
     complete with a summary). The width cap (8) is a machine-resource limit only.
     Children own DISJOINT file sets; shared-file convergence routes through a serial
     follow-up delegate after join, never a fan-out child.
     Team-manager gated. The parent parks in `in_progress(delegated)` with `active_fanout`
     metadata and wakes once when all children are terminal.
     When retrying a failed child, each retrying child MUST include
     `children[].revisit_of_task_id`: the FAILED child of this parent assigned
     to the same agent. A missing or invalid link rejects the WHOLE fanout
     before any child is spawned. A retrying `delegate` likewise supplies the
     failed child's id as `revisit_of_task_id`. A repeated failed slice wakes
     its owning manager for a revised-work or escalation decision; no runtime
     retry-ceiling successor is created.
   - `done` — the task is complete; requires `summary` of the outcome.
   - `escalate` — the task needs founder intervention; requires `reason`.

   Use the decision shapes below; the daemon validates them at submission and consumption.

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

   Example (delegation with attachment):

   ```json
   {
     "decision": {
       "action": "delegate",
       "agent": "dev_agent",
       "prompt": "Implement the dashboard per the attached mockup.",
       "attachments": [
         {"storage_key": "upload-abc123", "display_name": "dashboard-mockup.png"}
       ]
     }
   }
   ```

   Example (inline chain with later-leg attachment):

   ```json
   {
     "decision": {
       "action": "delegate",
       "agent": "dev_agent",
       "prompt": "Build feature X.",
       "then": [
         {"agent": "code_reviewer", "prompt": "Review the PR.", "expect_verdict": "APPROVE"},
         {
           "agent": "qa_engineer", "prompt": "QA the feature.", "expect_verdict": "PASS",
           "attachments": [
             {"storage_key": "upload-def456", "display_name": "test-plan.md"}
           ]
         }
       ]
     }
   }
   ```

   Example (fanout with per-child attachments — sibling keys must be unique):

   ```json
   {
     "decision": {
       "action": "fanout",
       "children": [
         {
           "agent": "dev_agent", "prompt": "Implement module A.",
           "attachments": [{"storage_key": "upload-aaa", "display_name": "spec-a.png"}]
         },
         {
           "agent": "qa_engineer", "prompt": "Test module A.",
           "attachments": [{"storage_key": "upload-bbb", "display_name": "spec-b.png"}]
         }
       ],
       "width_cap_ack": 2
     }
   }
   ```

   Example (pipeline carrier — carrier owns refs, first leg inherits):

   ```json
   {
     "decision": {
       "action": "fanout",
       "children": [
         {
           "agent": "senior_dev", "prompt": "Review and QA the feature.",
           "expect_verdict": "APPROVE",
           "then": [
             {"agent": "qa_engineer", "prompt": "QA pass.", "expect_verdict": "PASS"}
           ],
           "attachments": [
             {"storage_key": "upload-ccc", "display_name": "review-checklist.md"}
           ]
         }
       ],
       "width_cap_ack": 1
     }
   }
   ```

   Then submit:

   ```bash
   happyranch report-completion --org {ORG_SLUG} --from-file /tmp/completion-<task_id>.json
   ```

9. **Cleanup.** Always run worktree cleanup as the final step, even on the blocker path. The make-worktree skill describes how.

## Error handling

- If `happyranch` returns non-zero, retry once after 1 second.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has spawned a newer session for this `(task_id, agent)`) and `409 unknown_session` (the daemon has no record of this spawn — the session is orphaned). Either way, exit immediately.

## Permission walls

If your executor refuses a command and the operation needs founder-grade
credentials, use the **jobs** skill. Submit a job with `review_required=true`
and a concrete rationale, then report `status="blocked"` with its `JOB-NNN`
in `waiting_on_job_ids`. Resume only through the existing job-result workflow.
Pi has no HappyRanch-managed command-refusal surface; founder-grade operations
still use reviewed jobs. Review does not grant new executor permissions.
