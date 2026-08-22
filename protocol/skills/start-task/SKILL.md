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

   **Engineering frontend readiness gate.** Before handing a frontend PR to a
   reviewer or QA, include a concise evidence block that maps every acceptance
   criterion/spec requirement to proof; covers loading, empty, error, and
   populated states plus auth/permission where applicable; provides a
   screenshot or deterministic test result; and states exactly what changed
   since any prior review. The manager or reviewer must return an incomplete
   handoff for completion, rather than discovering absent proof one item at a
   time. Reuse the existing `doc-sweep-after-behavioral-change` and
   `adversarial-browser-evidence-harness-requirements` KB checklists; this gate
   does not replace them. At a third fix-forward round, stop and surface a
   structural diagnosis/escalation per KB
   `fix-forward-cascade-prevention-checklist` — never silently attempt a
   fourth retry.

   If the task produces a standalone document (report, plan, analysis), write its files under `output/<task_id>/` in your workspace root — **not** inside any repo or worktree. Capture the relative path (e.g. `output/TASK-001`) and include it as `output_dir` in your completion payload so future sessions can retrieve it via `happyranch recall --org {ORG_SLUG} <task_id>`.

   If during the task you realize you need async input from another agent
   (and you're not yet blocked), consult `protocol/skills/thread/SKILL.md`
   "Compose a new thread" rather than escalating.

5. **Report progress (long-running tasks).** If the task spans more than a
   few minutes — multi-phase implementation, lengthy build/test, large
   research sweep — emit a one-line progress note at every meaningful
   milestone so the founder can `happyranch tail` / `happyranch details` and see live
   movement instead of a black box until completion.

   ```bash
   happyranch progress --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_name> --message "Phase 3 of 6: tests passing"
   ```

   When to emit: phase boundaries, before/after long shell-outs (>1 min),
   when changing direction, on a non-fatal blocker you're working around.
   When NOT to emit: every file edit, every grep, anything you'd consider
   trivial mid-step bookkeeping. Treat it like a status line, not a log.

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
   - It's already in `protocol/` docs.
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
     "output_dir": "output/<task_id>",
     "local_ci": {
       "command": "scripts/local_ci.sh all",
       "exit_code": 0
     }
   }
   ```

   **Local-CI evidence.** Any completion report for a task that pushed a PR
   MUST include the `local_ci` field with the exact command (normally
   `scripts/local_ci.sh all`) and a zero exit status. Engineering managers
   reject a PR completion missing this evidence. If the local-CI hook ran
   the full real suite, state its exact command and exit code; do not claim
   it without output. Tasks that do not push a PR may omit `local_ci`.

   **GitHub CI is authoritative.** Local pre-push hooks provide feedforward
   signal only. The full Python 3.12/3.13/3.14 matrix and nightly
   integration runs on clean Ubuntu runners in GitHub Actions are the only
   merge gate. Local-CI hooks CANNOT prevent `git push --no-verify` —
   `--no-verify` bypasses hooks entirely and remains prohibited by
   engineering policy.

   For a blocker, set `"status": "blocked"`, `"confidence": 0`, and put the
   reason in `summary`. Optional keys (`risks`, `dependencies`,
   `reviewer_focus`, `confidence`, `output_dir`) may be omitted.

   - If your role is to issue a verdict (code review, QA, design review, etc.), include `"verdict": "<value>"` in your payload. Free string; your team's workflow KB entry documents the vocabulary. Optional — workers without verdicts simply omit the field.

   **Team-manager only — add a `decision` field.** Alongside the prose
   `summary`, a team-manager session must include a top-level `decision`
   object that the orchestrator will execute. Workers omit it. Omitting it
   from a manager session escalates the task. See the response-format
   section of your role_guidance for the exact shapes. The canonical
   contract is `protocol/00-completion-contract.md` — this skill restates
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
     complete with a summary). NO fan-out review gate at any width — the width cap (8)
     is a machine-resource limit only; control over what lands is the per-PR merge gate
     (each mutating child opens its own PR needing reviewer APPROVE + qa PASS +
     CI + founder/EM merge). Children own DISJOINT file sets; shared-file convergence
     routes through a serial follow-up delegate after join, never a fan-out child.
     Team-manager gated. The parent parks in `in_progress(delegated)` with `active_fanout`
     metadata and wakes once when all children are terminal.
   - `done` — the task is complete; requires `summary` of the outcome.
   - `escalate` — the task needs founder intervention; requires `reason`.

   Full shapes and examples: `protocol/00-completion-contract.md`.

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

If your executor refuses a command (Claude `--allowedTools`, opencode `permission.bash`, Codex sandbox), and the operation genuinely needs founder-grade credentials, see `protocol/skills/scripts/SKILL.md`. Pi has no HappyRanch-managed command-refusal surface; use the same script-review path for founder-grade operations even if Pi itself would run the command. Submit the script for founder review, then self-block your task referencing the SR-NNN.
