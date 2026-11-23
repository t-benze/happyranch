---
name: start-task
description: Use this skill at the start of every task. Parses task_id, session_id, brief, and role_guidance from the prompt, executes the work, reports completion back to the daemon, and cleans up worktrees.
---

# start-task

## Steps

1. **Parse parameters.** Extract `task_id`, `session_id`, `brief`, and (when present) `role_guidance` from the prompt above. Hold `session_id` in a variable for the lifetime of this session — every callback to `happyranch` must include it. When the `role_guidance` block is absent (typical worker spawn), treat `brief` as your complete per-task instruction.

   Apply any [completion-recovery binding](#thr-247-recovery-turn-exception) or
   [bound workflow supplement](#bound-workflow-lane) before ordinary execution.
   Copy permanent agent IDs into automation fields, as explained below.

2. **Consult memory.** Before planning:

   1. Read `task_history.md` in your workspace root. It lists your recent tasks with briefs, outcomes, and (when present) output dir paths.
   2. **Consult per-agent memory before the first substantive decision.** Select up to TWO memories pertinent to the actual task from the available digest, memory index or current context. Read selected bodies with `happyranch memory get --org {ORG_SLUG} --agent <your_agent_id> <MEM-NNN-or-slug>` and apply their relevant constraints before deciding. Bodies already available in the current context, including full-body directives, need no reread; apply their relevant constraints directly. Select zero when none is relevant: no ritual fetch, search or requirement to find two. Use `happyranch memory search --org {ORG_SLUG} --agent <your_agent_id> "<terms>"` when relevant recall is missing. Keep the task-history, referenced-task recall and KB obligations below.
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

   **Task output hygiene (Founder THR-326 seq13 and seq35/seq39).** These are
   agent obligations, not daemon auto-expiry:

   - Gzip any file over 1 MB at write time in `output/` (e.g. `.json.gz`, `.log.gz`).
   - Keep scratch trees, caches, `node_modules`, `.venv`, build directories,
     checked-out repos, DB copies/WAL and VM/disk images out of `output/`;
     use runtime task-tmp or the task worktree.
   - Retain command stdout/stderr as head/tail excerpts totaling at most 256 KB,
     plus each full stream's total byte count and SHA-256 digest.
   - The soft cap is 50 MB stored per task output directory. Before completion,
     run `du -sh output/<task_id>/`; exceeding the cap needs a stated reason
     in the completion summary.
   - Create output files and directories owner-writable (no read-only modes),
     so later own-output cleanup is not denied by their permissions.
   - Cite exact small record files (handoff, summary, verdict, receipt),
     not bare TASK folders or raw large dumps.
   - Examples: task output practice guide (`docs/agent-guides/task-output-practice.md` in the happyranch repository).
   - Routine/automatic screenshots, logs, QA evidence and review packets stay
     here, reported as `output_dir` and retrieved via `happyranch recall`.
     Binary handoff uses a fresh task attachment (see the ownership boundary
     below). Reserve Shared Artifacts for deliverables needed after task closure
     that must travel as downloadable files, with complete metadata and bounded
     retention per the [Shared Artifacts contract](../../../../docs/agent-guides/contracts/shared-artifacts.md).
     Daily expiry/notices remain pending B/D rollout; this is no upload requirement.
   - For your **own terminal tasks only**, after 14 days remove regenerable
     leftovers. Delete log, JSON and stdout/stderr files over 1 MB (including
     `.gz` variants) once the whole task tree that wrote them has been terminal
     for at least 14 days, unless a currently running or queued task, or a task
     that failed or escalated in the last 14 days, names the file or an ancestor
     folder (a retry may read it). Keep handoff, completion, summary, verdict,
     receipt and manifest files under 1 MB regardless of age. Delete rather
     than compress or archive; never chmod/chown or follow symlinks. Older
     brief, thread or PR citations no longer protect those large files. For
     other content, after 60 days shrink uncited task directories to summary
     and completion files. Never delete or compress other content cited by a
     task brief, thread message or PR body.

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
   happyranch progress --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_id> --message "Phase 3 of 6: tests passing"
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

6. **Record mid-task memory (optional).** Whenever you discover something reusable for future tasks.

   **Migrated workspaces (per-entry memory, `memory/` dir exists):** use your executor's file tool at an absolute writable task path, e.g. `<workspace>/output/<task_id>/mem-<slug>.yaml` (`slug`, `title`, `topic`, optional `tags`, `related_to`, `body`) and call:

   For memory add/update, quote YAML values containing ' #' or use a JSON payload.

   ```bash
   happyranch memory add --org {ORG_SLUG} --agent <your_agent_id> --from-file <workspace>/output/<task_id>/mem-<slug>.yaml
   ```

   **Curation and compaction.** Add/update payloads may include integer
   `salience` (clamped to 0–100; default 50 on add, preserved when omitted/null
   on update). Optional `--salience` overrides the payload. Choose 0–10 for a
   low-value note you are willing to retire. Update still supplies the complete
   slug/title/topic/body payload:

   ```bash
   happyranch memory update --org {ORG_SLUG} --agent <your_agent_id> MEM-001 --from-file <workspace>/output/<task_id>/mem-<slug>.yaml --salience 5
   happyranch memory add --org {ORG_SLUG} --agent <your_agent_id> --from-file <workspace>/output/<task_id>/mem-replacement.yaml --supersedes MEM-001
   happyranch memory lifecycle --org {ORG_SLUG} --agent <your_agent_id> MEM-001 --set superseded --reason "Replaced guidance"
   happyranch memory compact --org {ORG_SLUG} --agent <your_agent_id> --dry-run
   happyranch memory lifecycle --org {ORG_SLUG} --agent <your_agent_id> MEM-001 --set valid --reason "Restore useful guidance"
   ```

   `supersedes: MEM-001` in an add/update payload also retires its predecessor;
   `--supersedes` overrides that field. Lifecycle `evicted` explicitly retires a
   note immediately; `evicted -> valid` restores it. Nothing is hard-deleted.
   These commands describe the interface; live curation/config changes still
   require the task's authority. Promoted notes remain locked.

   Org `memory_compaction` defaults: `enabled: true`, `salience_floor: 10`,
   `stale_days: 45`, `superseded_grace_days: 7`, `max_evictions_per_run: 25`,
   `auto_trigger_entry_count: 150`. Dry-run is always available; `--apply` requires
   enablement. A positive auto-trigger count plus enablement runs compaction
   after ordinary adds and dream reflections at/above that many files (including
   evicted files), off the event loop. Explicit `enabled: false` disables apply
   and auto; zero disables the trigger, and updates do not trigger it. Inherited
   imports bypass it. Each automatic eviction uses the existing
   `memory_lifecycle_changed` audit with `source: compaction`, after persistence.
   Sweep or audit errors after the note write log a warning without
   aborting the add or preventing its `memory_added` audit. There is no scheduled sweep.
   Staleness uses local creation `authored_at`, preserved by updates (legacy
   missing/unusable stamps fall back to `updated_at`; neither usable retains the
   note). ISO strings and YAML datetime/date values normalize to UTC; naive
   timestamps and date-only midnight mean UTC. Lowering salience on an old note
   therefore need not wait another 45 days. At default thresholds, 0–10 is
   eligible after 45 days; existing decay subtracts up to 30, so stored values
   through 40 can also qualify. Default 50 stays above the floor.
   Superseded notes instead wait seven days from their latest `updated_at`;
   editing them restarts that grace. Promoted, directive, team/org and explicitly
   referenced notes remain protected. A replacement's `supersedes` link alone
   does not protect a superseded predecessor; `related_to` and body references
   still do. Applied sweeps respect the cap and retain files for restoration.

   `learning` is a deprecated alias of `memory`.

   **Pre-migration workspaces (legacy flat `learnings.md`, neither `memory/` nor structured legacy `learnings/` exists):** the bare `memory --text` callback appends to the flat file only with the active task/session binding. The daemon returns `410 Gone` when either structured directory exists; use the verb-dispatched form above for migrated workspaces.

   ```bash
   happyranch memory --org {ORG_SLUG} --task-id <task_id> --session-id <session_id> --agent <your_agent_id> --text "..."
   ```

7. **Contribute to the KB (optional).** Before reporting completion, ask yourself: did I discover or confirm durable, cross-agent-relevant knowledge that isn't already in the KB?

   **Contribute YES if any are true:**
   - Factual rule other agents would need (API rate limit, regulatory deadline, partner contract term).
   - You consulted the KB and an entry was wrong or outdated — update it.
   - A non-trivial procedural decision worth preserving as a mini-SOP (not a one-off workaround).

   **Contribute NO if:**
   - The info is specific to this task (→ task artifact).
   - It's your own operational preference (record it via the mid-task memory callback instead).
   - It duplicates runtime instructions or implementation reference material.
   - The info has a <12-month useful lifespan.

   Create `<workspace>/output/<task_id>/kb-<slug>.md` with your executor's file tool and YAML frontmatter (`slug`, `title`, `type`, `topic`, optional `tags`, `source_task`) followed by a markdown body, then:

   ```bash
   happyranch kb add --org {ORG_SLUG} --agent <your_agent_id> --from-file <workspace>/output/<task_id>/kb-<slug>.md
   ```

   For updates: `happyranch kb update --org {ORG_SLUG} <slug> --agent <your_agent_id> --from-file <workspace>/output/<task_id>/kb-<slug>.md`. Resolve collision 409s by updating the existing entry instead of forcing a sibling. The `--from-file` pattern is mandatory across executors; in Claude sessions multi-line `happyranch` payloads are rejected by the `Bash(happyranch:*)` permission rule.

8. **Cleanup or record deferral.** Before the completion callback, apply the
   `make-worktree` cleanup contract to the exact task-owned worktree. Remove it
   only when every preservation check succeeds. Otherwise leave it intact and
   add `worktree-deferred: <specific reason>` to the completion file's `risks`
   array. Never force removal or delete the branch.

   A later newly committed structural root in completed/failed/cancelled/
   superseded may reconsider this parent tree through its runtime owner,
   including NULL-parent workflow tasks. Recorded maker deferral is advisory
   only in that separate pass; it is not erased. Fresh preservation, process,
   ownership/protected-path and host/successor gates retain uncertain work.
   Root receipts carry stable start INTEGER identity and exact partial scope;
   missing/interrupted finish never retries. This adds no maker cleanup,
   historical, scheduling or callback authority. Always keep the final callback
   ordering below.

9. **Report completion.** When you finish (success or blocker), write a JSON
   payload to a file and invoke `happyranch report-completion --org {ORG_SLUG} --from-file <path>` as
   a single-line command as the final action of the session; nothing follows
   it. `report-completion is the final action` is the controlling ordering
   rule. The file form is mandatory across executors. In
   Claude sessions, multi-line bash commands with backslash continuations are
   rejected by the permission rule because newlines count as command
   separators and only the first subcommand matches `Bash(happyranch:*)`.

   Create the payload with your executor's file tool at an absolute writable
   path, preferring `<workspace>/output/<task_id>/completion.json`. `/tmp` is
   optional when writable and permitted by the task environment. Use this shape:

   ```json
   {
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "agent": "<your_agent_id>",
     "status": "completed",
     "confidence": 85,
     "summary": "<what you did>",
     "risks": ["<concern>"],
     "dependencies": ["<assumption>"],
     "reviewer_focus": ["<where to look hardest>"],
     "output_dir": "output/<task_id>"
   }
   ```

   The completion-file reader (`cli/commands/tasks.py`,
   `_completion_payload_from_file`) accepts only the fields below. File keys
   map to HTTP keys as shown; `task_id` selects the endpoint. Unknown keys are
   dropped: keep custom evidence in `summary` or a cited output file.

   | Completion file key | Required / forwarding contract |
   | --- | --- |
   | `task_id`, `session_id`, `agent`, `status`, `summary` | Required, nonempty. `agent` is your permanent ID; `summary` maps to HTTP `output_summary`; the other body keys keep their names. |
   | `confidence` | Optional integer 0–100; defaults to 80. |
   | `risks`, `reviewer_focus` | Optional arrays; map to HTTP `risks_flagged`, `suggested_reviewer_focus`, respectively; default to `[]`. |
   | `dependencies` | Optional array; defaults to `[]`. |
   | `output_dir` | Forwarded when nonempty; workspace-relative task output path. |
   | `verdict` | Forwarded when non-null; use your role's canonical vocabulary. |
   | `decision` | Forwarded when non-null; only a decision-owning task may supply its authorized decision. |
   | `waiting_on_job_ids` | Forwarded when present; only `status="blocked"` with a nonempty list of real, owned, nonterminal jobs. Omit when no genuine wait exists. |
   | `manager_self_evaluation` | Forwarded whenever present, including null; supply only when the bound manager-decision policy requires its exact versioned object. |
   | `local_ci` | Forwarded when non-null; exactly `{"command":"scripts/local_ci.sh all","exit_code":0}` (`LocalCiEvidence` in `runtime/models.py`): no extra keys, string command, integer zero, never Boolean or string zero. |

   This field reference grants no test/run authority. Follow the applicable role
   instructions and repository CI contract (`docs/operations/local-ci.md#git-hooks-and-evidence` in the happyranch repository);
   report actual commands and exit statuses in `summary`/output, and never label
   skipped or incomplete checks as passing.

   For a blocker, set `"status": "blocked"`, `"confidence": 0`, and put the
   reason in `summary`. Optional keys (`risks`, `dependencies`,
   `reviewer_focus`, `confidence`, `output_dir`) may be omitted.

   **Decision ownership.** A decision-owning task (`task_type=task`) supplies
   a supported `decision` when completing its work, even if it has a parent.
   A delegated subtask (`task_type=subtask`) returns a plain completion and
   omits `decision`. A structural root (`parent_task_id=null`) is a separate
   property used for root-scoped escalation and supersession.

   An ordinary worker's decision-owning task may use `done`, self-only
   `delegate` or `escalate` within its server-authorized contract; it gains no
   peer/fanout, manager administration, policy or template rights. Team-manager
   decision-owning tasks retain their existing decision contract. Human Founder
   manages the team but never executes as an agent. A delegated subtask's
   review_verdict is an agent outcome attributed to the executable parent owner,
   never human approval. Reviewer/QA tasks include their role's `verdict`;
   roles without verdicts omit it. Follow the injected role guidance for exact
   decision shapes; runtime models and transition handlers enforce them.

   Minimal complete files (replace every placeholder with the actual binding):

   ```json
   {"task_id":"<task_id>","session_id":"<session_id>","agent":"<your_agent_id>","status":"completed","summary":"Delivered the assigned work.","decision":{"action":"done","summary":"Delivered the assigned work."}}
   ```

   A blocked delegated subtask, with no live job wait, returns no decision:

   ```json
   {"task_id":"<task_id>","session_id":"<session_id>","agent":"<your_agent_id>","status":"blocked","confidence":0,"summary":"Blocked: the assigned repository is unavailable; parent must resolve access."}
   ```

   A genuine owned-job wait reports the actual submitted nonterminal ID; this
   example is a delegated subtask and creates no decision:

   ```json
   {"task_id":"<task_id>","session_id":"<session_id>","agent":"<your_agent_id>","status":"blocked","confidence":0,"summary":"Waiting for the submitted verification job's terminal receipt.","waiting_on_job_ids":["<actual_owned_job_id>"]}
   ```

   **Ordinary handoff selections (THR-356).** `delegate`, every `then` leg and
   every `fanout`/`parallel` child (including a carrier's nested legs) accept
   optional `model` and `reasoning_effort`. Choose for the TARGET agent's
   current executor; unset means its own defaults, never the manager's task
   override. Invalid choices anywhere reject the whole decision before any
   child, returning the existing feedback result/step with the offending leg,
   reason and valid options; correct it in the next owner session. A linked
   retry carries omitted fields/source; a value replaces that field and explicit
   JSON null clears it. Any explicit edit records manager provenance for the
   remaining tuple; clearing both removes it. Then choices persist and PR1
   rechecks them before launch; changed executor/options can fail the leg with
   `saved model no longer valid for this agent`. These fields do not extend
   workflow-owned or sequential-coding decision shapes.

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
     Apply the "Fresh work after failure" rule below when choosing independent work versus a
     linked continuation. For an intentional linked retry, each child includes
     `children[].revisit_of_task_id`: the FAILED child of this parent assigned
     to the same agent, or a FAILED child assigned to the same agent under a
     predecessor root connected to the current root by the runtime's bounded,
     recorded-supersession verifier. Installed native checks also require a
     valid link when this parent has a FAILED same-agent child;
     a missing or invalid required link rejects the WHOLE fanout before any
     child is spawned. A `delegate` follows the same mechanical rule. Report
     that constraint if it refuses the selected route; do not invent a link
     or require cross-root repair just to obtain an independent assessment.
     The link
     never reparents or rewrites the failed child, and an unresolved local
     failure cannot be bypassed with a remote historical link. A repeated
     failed slice wakes its owning manager for a revised-work or escalation
     decision; no runtime retry-ceiling successor is created.
   - `supersede` — team-manager replanning for a structural root (`parent_task_id=null`); requires `successor_brief`, `rationale`,
     and the validated recovery attestation. This replanning disposition is
     scoped to a structural root: if a non-root task owner returns it, the runtime fails that
     child through the ordinary terminal tail and wakes its parent to decide;
     no successor is created.
   - `done` — the task is complete; requires `summary` of the outcome.
   - `escalate` — a decision-owning task's request for founder intervention;
     requires `reason` and a structural root (`parent_task_id=null`). Ordinary
     workers with such tasks retain this server-authorized action.
     This founder-facing disposition requires a structural root: if a non-root task owner
     returns it, the runtime fails that child and wakes its parent to decide.

   Use the decision shapes below; the daemon validates them at submission and consumption.

   Example (delegation):

   ```json
   {
     "task_id": "TASK-XXX",
     "session_id": "<sid>",
     "agent": "<your_agent_id>",
     "status": "completed",
     "confidence": 90,
     "summary": "Triaged and staged implementation for the worker.",
     "decision": {"action": "delegate", "agent": "<target_agent_id>", "prompt": "..."}
   }
   ```

   Example (delegation with attachment):

   ```json
   {
     "decision": {
       "action": "delegate",
       "agent": "<target_agent_id>",
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
       "agent": "<target_agent_id>",
       "prompt": "Build feature X.",
       "then": [
         {"agent": "<target_agent_id>", "prompt": "Review the PR.", "expect_verdict": "APPROVE"},
         {
           "agent": "<target_agent_id>", "prompt": "QA the feature.", "expect_verdict": "PASS",
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
           "agent": "<target_agent_id>", "prompt": "Implement module A.",
           "attachments": [{"storage_key": "upload-aaa", "display_name": "spec-a.png"}]
         },
         {
           "agent": "<target_agent_id>", "prompt": "Test module A.",
           "attachments": [{"storage_key": "upload-bbb", "display_name": "spec-b.png"}]
         }
       ],
       "width_cap_ack": 2
     }
   }
   ```

   Example (two-child fanout with a pipeline carrier). The carrier owns
   `upload-ccc`; its first review leg reads it through ancestry without claiming
   it again. The second child has a disjoint scope and no claim on that key.
   Nested `then` legs do not count toward `width_cap_ack`:

   ```json
   {
     "decision": {
       "action": "fanout",
       "children": [
         {
           "agent": "<target_agent_id>", "prompt": "Review and QA the feature.",
           "expect_verdict": "APPROVE",
           "then": [
             {"agent": "<target_agent_id>", "prompt": "QA pass.", "expect_verdict": "PASS"}
           ],
           "attachments": [
             {"storage_key": "upload-ccc", "display_name": "review-checklist.md"}
           ]
         },
         {
           "agent": "<target_agent_id>", "prompt": "Independently inspect module B (disjoint scope)."
         }
       ],
       "width_cap_ack": 2
     }
   }
   ```

   Then submit:

   ```bash
   happyranch report-completion --org {ORG_SLUG} --from-file <workspace>/output/<task_id>/completion.json
   ```

## Error handling

- Inspect the exit/error and the actual operation receipt. A nonzero exit or
  lost transport does not prove that no effect committed. Preserve the original
  body, operation key and unknown state; use the operation's documented readback.
- Retry only where that operation's contract explicitly permits it. The
  `jobs` skill forbids automatic submission retries; the
  [sequential rules](#bound-workflow-lane) forbid reissuing uncertain Final
  effects. [Preparation recovery](#refusal-and-preparation-recovery) keeps its
  original-key one-send contract. None inherits a generic retry allowance.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has
  spawned a newer session for this `(task_id, agent)`) and `409 unknown_session`
  (the daemon has no record of this spawn). Either way, exit immediately.

## Current names and permanent automation IDs

Fresh prompts may show current Name · ID for this turn. Use the permanent ID
from the injected parameters for callback agent/composer/speaker, task/session/
invocation proofs, configuration and delegate/then/fanout targets. A name is
human-addressing/presentation data, never actor authority. Human destination
arguments and @mentions accept current names or IDs; former names refuse with
the current name and do not forward. Founder is a separate human; recipient
transport remains `@founder`. Metadata failure gives ID-only context.

## Fresh work after failure (Founder THR-291 seq557)

After a failed attempt, dispatch a fresh task by default with a complete brief
and references to prior findings, failures and spent allowances. A new manager
task can commission independent QA of a saved design when the earlier reviewer
returned no verdict/output; the design is the assessment subject, not partial
reviewer work. No cross-root ancestry repair is needed just for that assessment.

Use a linked retry only to continue partial work or account for an attempt that
may still be running. Inspect relevant effects and ownership in proportion to
the proposed work to prevent conflicting execution. Preserve partial edits,
PRs/results, failed history, genuine uncertainty and existing active owners;
terminal status or a missing PID alone proves no cleanup. Fresh independent
work has no universal reconciliation or full-census prerequisite.

Fresh numbering preserves existing authorization, holds and consumed/remaining
attempt/resource budgets; it cannot conceal failures, replenish allowances,
revive terminal tasks or authorize duplicate execution. Review, QA, hooks and
guarded merge remain required. This rule supersedes contrary operating advice,
not runtime checks: installed same-parent/same-agent admission may still require
a valid retry link. Report an actual refusal without fabricating links, changing
history or claiming a fresh-task bypass. No deploy/restart or live reconciliation
is required to adopt this operating rule.

## THR-247 recovery-turn exception

When the supplied runtime binding is a completion-recovery turn, use that
fresh binding only to report already-performed work or observe an actual
task-owned job wait. Do not execute the ordinary brief, create work, or submit
new jobs. Ordinary resumed turns retain their normal policy.

The orchestrator daemon spawns you with a prompt of this form:

```
You are <your_agent_id>. Use the start-task skill to handle this task.
Parameters:
  task_id: TASK-XXX
  session_id: <uuid>
  brief: <task brief>
  role_guidance: |             # optional — present only when the orchestrator
    <role-specific overlay>     # has a per-task overlay (team-manager spawns
                                # carry the orchestration capabilities block here)
```

## Attachment ownership boundary

Each attachment storage_key can be claimed by one task only, including within
the same tree. Each direct, child or then-leg list allows at most five refs;
keys must be unique across the entire initial decision, including siblings and
nested legs. Carrier top-level refs belong to the carrier; its first leg reads
through ancestry without a second claim. Initial fanout preserves both child
and nested attachment fields; later chain advancement keeps its existing rules.

Recognized initial pre-child errors include too_many_attachments,
duplicate_attachment, task_attachment_not_found, attachment_already_claimed,
invalid_attachment_display_name and unsupported_attachment_type. Feedback gives
the code, escaped key when supplied, and remediation. Correct in a genuinely
new authorized session/result; never replay the consumed result. Each
decision-owning task has at most TWO committed attachment-error feedback
opportunities shared across sessions and initial delegate/fanout/parallel.
Committed A consumes a chance even if B/enqueue is lost. The third recognized
error uses the existing root ESCALATED or nonroot FAILED disposition. A valid
correction after two remains eligible under current authority and revision
limits. Other authoring, revise, slice and total-step rules stay unchanged.
Keep task-only evidence in output/<actual-task-id>, report output_dir and use
supported recall. Do not reattach an already-claimed maker key to a reviewer.
For a distinct attachment handoff upload a fresh authorized copy with
`happyranch attach-upload --org ORG --as-agent <your_agent_id> --file /absolute/source`
within the actual authorized environment (upload has no --session-id flag).
Inherited access grants reading, not another claim. Never upload task-only
logs/screenshots/review packets to Shared Artifacts as an ownership workaround.

## Retired document-review lane

The older document-review lane is retired (`workflow_document_review_retired`,
HTTP 409). Do not start or advance activation, return-to-author, author/revision
staging, missing review graphs or reviewer claims/launches; do not retry a
retirement refusal. Human-signing/reviewer-staging transports and their mutation
replays are removed. Exact immutable operation replay and authorized selected
history reads remain. Preserve privacy, original keys/bytes and already-owned
native uncertainty. Authentic already-committed evidence may settle only through
genuine native callback/terminal handling with the actual task/agent/SID and
FINAL report-completion. Never invent completion or a new held reason; surface
active/unfulfilled records for separately authorized disposition.
Follow the immutable document contract and recipient-filtered inputs; no generic
delegation, fanout, coding, approval or merge action is available in that lane.

Historical author/revision/reviewer schemas are retained verbatim in
[historical contracts](references/historical-contracts.md#retired-document-review-contracts),
not as authority for fresh work. See the maintained
document-workflow contract (`docs/agent-guides/contracts/document-workflows.md` in the happyranch repository).
Active sequential document instructions are separate below.

## Bound workflow lane

Obey the actually bound trusted supplement and immutable output contract, using
only its addressed inputs and your actual task/agent/SID. Do not select a lane by
reconstructing rollout history. `workflow-sequential-node-brief@2` uses generic
callback@2 and agent-owned Coding v3 role duties; original @1/v2 and legacy
bindings retain their own callback grammar, subject, original keys/bytes/pins,
limits and replay rules. V3 does not inherit historical special repository or
finalize adapters. Fresh old coding start@1/@2 refuses
`coding_fresh_start_retired`; later defaults/Disable never replace admitted pins.
Coding starts still require the selected reviewed definition, latest Allowed NEW
settings, compatible storage, mounted native engine and eligible current roles.
Required genuine scratch/pilot acceptance precedes ordinary use; source/static
evidence is not acceptance. Recovery-purpose invocations grant no new start or
replacement authority; initiator-owned generic controls are described below.
Ordinary routing stays legacy.

S8b registers Coding v3 as generic Coding data only. Public Start@1 discovery
and composition remain unchanged; public @2 discovery/compose and genuine native admission are supported.
Repository resolve/bind/preflight and sequential finalize/journal transports refuse
generic @2 selections with `sequential_domain_transport_unavailable`; perform only
the authorized engineering duties with existing tools. Original @1 replay,
selected history and uncertain Final records retain their original rules; never
reissue an uncertain Final. Registration grants no start, setup, roles or Allow.
R2 unit (a) separately supports Founder Workflows → Setup: Save the four Coding
v3 roles, then explicitly Allow/Disable new runs. Save preserves admission and
creates no work; Disable never changes admitted-run pins. Pending/uncertain
operations keep their original kind/key/body; resolve uncertainty only through
original-key lookup. Agent/task/CLI sessions gain no Founder setup entitlement.
The generic catalog supports Coding@3 and `release/happyranch/draft-review@1` on ABI@2. Draft has independent author/reviewer workers and API-only Founder Save/Allow/Disable; Workflows → Setup remains Coding-only. Live setup,
Allow, deployment/restart and the pilot remain founder-reserved.

Keep these live boundaries in every lane:

- Review/QA independently fetch and inspect the exact selected head; preserve
  author/contributor independence and report only your own findings. Required
  independent APPROVE/PASS and selected-head checks cannot be waived by graph data.
  Original Coding bindings retain their four distinct eligible org principals.
- ACCESS-1 requires current-authority/unchanged-subject rechecks at later
  disclosure/publication/action. Effective access is proved by the selected
  agent's actual operation (review/QA exact-head fetch); record denied versus
  unverified BLOCK. Shared OS user/gh credentials do not supply independently
  observable per-agent access. No daemon GitHub/network observer or new access,
  credential or permission follows from a descriptor, receipt or configured role.
- Resolve-only observations grant no editing; original @1/v2 editing requires
  positive subject COMMIT under the original repository/origin/base/scope.
  Failed COMMIT/Held releases no editing; changed/expanded subjects require a new
  authorized run. Correction visits retain the original subject.
- Preserve native ownership, original Pause/cancellation owners, genuine failed/
  blocked causes and original caps/deadline. Callback/transport success is not
  acceptance: original positive host/outer settlement remains required. Never
  fabricate results/SIDs, clear ownership or infer settlement from PID absence.
- Only the actually selected live Final manager may perform its entitled LOCAL
  finalize/expected-head non-admin merge and independent remote MERGED/full-commit/
  aware-time observation before its own genuine FINAL callback. Preserve partial
  reservations, original body/key/receipts and uncertainty; never reissue an
  uncertain effect or move selection/intent/action/receipt production into a job,
  daemon, second CLI OrgState, recovery runner or old source/upstream SID.
  A later remote MERGED read never clears a failed/uncertain command receipt.
- Source/merge/registration is not operating authority: no self-installation,
  setup, configuration, role assignment, admission/Allow, activation, deploy or
  restart. An unavailable venue supplies no fallback. Missing defaults remain
  Disabled/unset; Save preserves admission; old Explicit settings do not transfer.
- Genuine current-task/current-session `report-completion` remains FINAL.
  Sequential visits supply no generic decision, manager assessment or job wait.
  Cancellation stops fresh work; already-committed callbacks may only replay
  unchanged, without advance. Internal `workflow_control`/`workflow_wait` are not
  available agent capabilities; final done refuses outstanding owned runs or
  ordinary dependencies.

For an actual original @1/v2 binding, the active command contracts are
repository resolve/bind (`docs/specs/2026-11-12-sequential-coding-u4c.md` in the happyranch repository)
(`happyranch workflows sequential repository-resolve`, then `repository-bind`;
`--org ORG --session-id SID --from-file ABS --json`) and
repository-preflight / LOCAL finalize (`docs/specs/2026-11-11-sequential-coding-u7.md` in the happyranch repository)
(`happyranch workflows sequential repository-preflight --org ORG --session-id SID --json`,
`happyranch workflows sequential finalize --org ORG --session-id SID --from-file ABS --json`).
Use only the closed command/body from the matching supplement under actual
entitlement. Bind's explicitly permitted same-body reissue uses current bind
semantics; it never permits reissuing Final. Preserve exact saved callback bytes.

See Coding role reports (`docs/agent-guides/contracts/coding-workflow.md#coding-v3-role-reports--s8b-registration` in the happyranch repository),
original-pin cutover/replay rules (`docs/operations/sequential-coding-cutover.md` in the happyranch repository),
and [retained historical contracts](references/historical-contracts.md#coding-rollout-and-original-pin-contracts)
only for the matching original binding or a specialist audit.

### Generic sequential callback (@2)

For an internally bound `workflow-sequential-node-brief@2`, follow its generic
responsibilities and addressed inputs. Return a canonical UTF8 JSON STRING as
summary using `workflow-sequential-callback@2` with exactly format, outcome,
content_base64 and findings (own text strings); no capability_claims. Use only
a declared outcome, padded standard base64, canonical JSON object content up to
49152 bytes or UTF8 document content up to 1048576 bytes as the bound contract
requires. The daemon composes native identities after the genuine result and
accepts only after positive native terminal/outer closure. Native failure still
Holds even if a callback exists. References grant no domain capability or
semantic proof. Preserve the actual task/agent/SID and genuine FINAL
report-completion contract. This paragraph grants no start, setup or live operation.

### Agent-owned Coding role duties (v3)

Apply these duties only when actually bound to Coding v3 under the generic @2
supplement, with authored node data, addressed reports and actual authorization.
The generic callback@2 contract above controls. BLOCK/held/uncertain use empty
content with your own causal findings; correction outcomes carry a bounded JSON
assessment and findings for the declared advancing edge. Do not fabricate reports
or native IDs.

- Develop performs the authorized repository/base SHA1/worktree/scope preflight
  before editing, then records actual branch/head/PR, evidence and spent allowances.
- Review fetches the exact head itself, checks author/contributor independence,
  and reports APPROVE/REQUEST_CHANGES/BLOCK with reviewed head and findings.
- QA fetches that same head itself, runs supported checks and reports
  PASS/REVISE/BLOCK with actual evidence and limits; unexecuted checks are NOT RUN.
- Final compares developer/reviewer/QA heads, independent APPROVE and same-head
  PASS, requires literal successful Source Boundary Lint and Web Lint (Node 24),
  uses the existing guarded non-admin expected-head merge under actual authority,
  then separately observes remote MERGED/full commit/aware time or reports a
  causal hold. Preserve uncertainty and never reissue an uncertain merge.

Use bounded canonical JSON role reports as described in the Coding contract.
These are attributed agent reports; generic completion is no engine-verified
repository head, review, QA or merge guarantee. No repository/exact-head/merge
capability or new credential, access observer, job or daemon executor is granted.
Semantic adherence requires a separately authorized founder pilot (NOT RUN).

### Original sequential callback and active documents (@1)

For a genuinely bound sequential @1 visit, follow only its `workflow-sequential-node-brief@1` and trusted domain supplement, actual task/SID and addressed inputs. Use `workflow-sequential-callback@1` as the canonical UTF8 JSON STRING in your completion file's summary: exactly format, outcome, content_base64, findings (own text strings) and capability_claims (untrusted check/digest references). Sort keys, compact separators, Unicode verbatim, no duplicate/extra keys or trailing newline. Encode exact output bytes as standard padded base64 without whitespace; documents must be UTF8 and at most 1048576 bytes, JSON contract content canonical and at most 49152 bytes. Metadata with content_base64 empty is at most 16384 bytes. The carrier has no result/task/session/run/visit IDs, recipients, verified checks or OutputRef; the daemon composes identities after your genuine result exists. Success-shaped produced/APPROVE/PASS/merged carries nonempty content; other outcomes carry empty content, no claims and own causal findings. Preserve actual native failed/blocked causes; a negative domain assessment may use completed status. Follow your domain supplement for the matching verdict. Claims, references and transport success confer no capability, access, settlement or advance. The summary has existing native task-result visibility; do not put narrower private material there or substitute a path/staging command. Use only your actual task/agent/SID with `happyranch report-completion --org ORG --from-file /absolute/completion.json` as the FINAL action; no decision, manager assessment or job-wait field. A zero-write 409 refusal may be corrected manually in the same still-live SID before any result committed; after transport uncertainty preserve the exact body and never automatically retry, change SID or invent a result.

For a sequential document supplement selected by its immutable output contract, keep capability_claims empty. A document author uses produced with completed status and null verdict, or BLOCK with empty content and own causal findings. A document reviewer uses APPROVE, REQUEST_CHANGES or BLOCK with matching verdict; APPROVE content is a nonempty canonical JSON object, while REQUEST_CHANGES/BLOCK content is empty with own findings. Review only addressed selected document inputs; no client subject field is required or trusted. Preserve actual native failed/blocked causes. References, JSON and URLs grant no repository capability or effect, and native task-result visibility supplies no role-private custody. Syntax and process success do not establish semantic truth. Keep the original sequential callback and genuine FINAL report-completion contract above.

### Sequential source composition

For a genuine executable team manager's OWN source callback, sequential start uses decision action `workflow_start` with the unchanged `workflow-sequential-start@1` StartCommand. Preserve the exact definition/settings pins, one stable operation key and actual remaining limits; syntax and lower limits create no new allowance. An unavailable venue refuses the start and grants no setup authority. Only positive original source host/outer settlement can release the first visit to the queue. Source and carrier create no fake result or SID. Use only the actual task/agent/session binding; genuine `report-completion` remains the FINAL action.

For an eligible ordinary manager task-owner session, first read your own
CLI-surface GET `/api/v1/orgs/ORG/workflows/sequential/start-context?session_id=SID`.
Use an available bundled definition with Allowed settings. Compose with
`happyranch workflows sequential start-compose --org ORG --session-id SID --from-file ABS --json`.
The absolute bounded canonical JSON file contains exactly definition
(identity@version), settings (the discovered generation/snapshot_digest),
operation_key, request, inputs, and optional lower_limits (all four @1 fields).
The composer only reads and prints the closed decision; copy it unchanged into
your OWN genuine FINAL report-completion decision. It never starts work or
calls report-completion. Preserve the composed bytes/pins and stable key;
stale settings conflict instead of silently repinning. Discovery/composition
reserve nothing and the final callback rechecks admission. This is one run per
source with fixed assignments; the source task ends when the run returns.
Thread-originated ordinary sources have a partial thread tail with no redelivery
guarantee. Worker/self-only and direct thread/wake/dream sessions cannot use this
manager task decision. Retired coding.v1 / old fresh coding start@1/@2 remain
refused. Unavailable discovery grants no setup, install, role or start authority.

@2 admission supported for an eligible executable manager's own ordinary task,
including a thread-originated source and its existing generic wait set. Read your
CLI-only GET `/api/v1/orgs/ORG/workflows/sequential/start-context/v2?session_id=SID`.
Compose with `happyranch workflows sequential start-compose --org ORG --session-id SID --from-file ABS --json`
and explicit `workflow-sequential-start-compose@2`, containing 1–8 workflows.
Concurrent capacity is bounded by existing cancellation reservations (currently
three unfinished runs); larger batches refuse finitely, all-or-none.
Keep exact definition/settings pins, stable member keys, request/inputs, role/step
overrides and explicit lower_limits, including omitted/value/null options.
The composer only reads. Copy its closed plural decision unchanged into your
OWN genuine FINAL completion.
The callback requires your exact active org/task/agent session context and
revalidates it under the binding lease before any publication ownership or write.
Foreign, ended and workflow-owned sessions grant no start authority.
First admission atomically creates the genuine
result/attempt-or-omission, wait set, all members and receipt with the actual
empty delivered page. Later starts reuse that wait set. Entry visits wait for
positive ORIGINAL source closure. Replay preserves original pins and identities;
Disable affects only fresh admission. Save never starts work.

Held and returned runs wake only their recorded initiator. A run return NEVER
completes your source. Read the stored delivery and owned-run context; acknowledge
only IDs actually delivered to this SID using optional consume_notifications
(maximum32). Use workflow_wait with owned run_ids to await dependencies. Use
workflow_control with workflow-sequential-control@2, run_id, stable operation_key,
expected_cas, expected_held_event_id, action and reason. Retry preserves the stopped
inputs/assignment; rebrief adds bounded supplement; reassign requires a permanent
target_agent_id and affects only the stopped occurrence. Retry/rebrief/reassign
refuse HTTP 409 `control_input_retired` with zero committed writes when any
retained `current.*` reference is retired or superseded; the run stays Held and
cancellable. Cancel then your later genuine done remains available after all
dependencies drain. Current-input retries retain their original bytes; cancel preserves unknown
execution until original closure. Recoveries keep original per-run limits and
spent counters. No total-step cap or cross-run numeric pool exists; batch, history,
cancellation reservation, revise/retry and pinned per-run limits still apply.
Only your own genuine done after every run and ordinary dependency drains closes
the source; done refuses while any remain. This grants no thread/wake/dream/worker
start authority, setup, live operation or pilot authority. @1 bytes and historical
source-terminal semantics remain unchanged. Select `release/happyranch/draft-review@1` for supported document drafting/review through the same compose@2 path. Only REQUEST_CHANGES returns attributed callback findings to draft; undeclared REVISE holds. This is separate from retired old document-review/signing transports.

## Task scratch and owned processes

Stop and reap task-owned processes before successful completion, and settle
real owned jobs or report their genuine authorized wait. Once both task and
structural root have ended, nightly sweeping may remove eligible scratch and
worktrees; do not rely on those folders afterward. Put regenerable fixture/cache
trees in runtime task-tmp; durable output and sibling scratch manifests/locks
are excluded. If you created linked worktrees in task-tmp, use `git worktree remove`
on your exact owned worktrees before completion under the existing preservation
gates. This grants no agent deletion, manual cleanup fallback, historical sweep,
live configuration, deploy/restart or end-of-task deletion-hook authority.

For an explicitly assigned cleanup task consult the
`workspace-cleanup` skill; for specialist/operator
sweeper work consult the maintained nightly sweeper spec (`docs/specs/2026-10-15-nightly-disk-sweeper.md` in the happyranch repository).
Operator-only manual-backlog details belong to that contract, not ordinary task startup.

## Authorized memory-collection tasks only

Only for explicitly authorized collection work, consult the
memory census contract (`docs/agent-guides/contracts/memory-census.md` in the happyranch repository)
and its maintained collection/qualification references. Memory telemetry is
observation-only: never infer production health/acceptance from a source report.
Exhaustion or incomplete evidence is a refusal, never an empty completed census
or authority to retry with a caller-selected subset. Collection, renewal,
scheduling and tuning require their own authorization.

## Permission walls

If your executor refuses one concrete command, classify the failure and record
redacted evidence using the **jobs** skill. Missing tools, credentials, network
failures and product policy denials are not executor permission refusals.

**One-off operation.** Obtain any required exact content authorization in an
existing thread BEFORE submission. From a genuine ordinary task session, use
the **jobs** form with deprecated `review_required` omitted/false. True refuses
422 before allocation (CLI exit2); never flip/retry or request job approval.
No job grants executor permissions or blanket authority over its contents.
Pi likewise requires the existing authorization before a daemon operation.

For an actual nonterminal owned job you must wait on, report `status="blocked"`
and its ID in `waiting_on_job_ids`. This is a real result wait, not an approval
request. An unresolved authorization question has no job ID: report the precise
blocker with `waiting_on_job_ids` omitted. Admission is not script success;
inspect actual status/reason/exit/output on resume. Legacy true pending jobs
never run; inspect history and use explicit one-ID reject with a reason if
appropriate. Both run aliases are local tombstones; `/run` returns410.

**Role-specific callback fields.** Use the completion-file field table in Step 9
and only the fields your injected role contract requires:

- a **delegated subtask (`task_type=subtask`)** sends `status` + `summary` (and `waiting_on_job_ids` only while a
  live job wait exists);
- a **reviewer/QA** role additionally sends `"verdict": "<value>"` (its role
  contract defines the vocabulary);
- a **decision-owning task (`task_type=task`)**, including an ordinary worker
  task owner, additionally
  sends a top-level `"decision"` object within its server-authorized contract.
  A delegated subtask omits `decision`. Team-manager task owners retain their
  existing manager decision and policy contract.
  Its injected role guidance conditionally requires `manager_self_evaluation`
  for a versioned policy-bound escalation. When required, the active policy
  block is authoritative: it shows the exact required object and a filled
  launch-bound example to copy beside `decision`; set both assessments honestly.
  Policy blocks outside a manager-decision task have no bound manager-decision
  identity and show shape/type placeholders only; never submit those placeholders.
  Submit `manager_self_evaluation` only from the manager-decision task's
  report-completion, using the root/session identities bound in that task's block.
  Omission, `null`, malformed, uncertain, or incomplete evidence fails closed.
  Do not invent policy wording, clause identifiers, a canonical phrase, or a
  second evaluation. For decisions where the injected guidance
  does not require that assessment, omit the field.
  A worker must **never** manufacture a manager decision or manager policy
  assessment. Its own task-owner decision is limited to the server-authorized `done`,
  self-only `delegate` or `escalate` contract; a delegated subtask omits
  `decision`. An attempted non-root founder escalation fails the child and wakes
  its parent under the existing runtime contract.

**Real waits only.** `waiting_on_job_ids` requires `status="blocked"` and must
list only real, currently non-terminal jobs owned by this task. An explicitly
empty list is rejected (`400 empty_waiting_on_job_ids`), so **omit the key
entirely** when no live job wait exists. Semantic unresolved work is not a live
wait and must not be represented as one.

**On resume, verify the actual receipt** (`happyranch jobs show` /
`happyranch jobs output`) — command, cwd, output, exit code and observable side
effects. Then:

- `completed` with exit 0 — continue; do not repeat successful work.
- `completed` with a non-zero exit — a **failed operation**; reconcile partial
  side effects before any retry.
- `failed` (`timeout`/`output_cap`/external kill) — may have partial side
  effects; reconcile, then retry only the unfinished authorized work.
- `rejected` — **terminal**: no re-wait and no automatic resubmission. If the
  requirement cannot be met by an authorized alternative, report the precise
  blocked disposition **omitting `waiting_on_job_ids` entirely**.
- `running` — stay blocked; never duplicate the submission.

**Context boundaries.** A normal active task session may submit an authorized automatic job.
A resumed (ordinary) task may submit a new job only under ordinary rules if one
is genuinely needed. A **completion-recovery turn** (THR-247) may only observe
an already-owned job and its wait result — it cannot submit new jobs. A **thread
turn** carries an `invocation_token` that authorizes only the thread callback:
it is not a task session id and authorizes no job; route side-effect work through
a supported task dispatch.

**Lasting changes.** Thread authorization for a job is for that exact operation. A *lasting* permission
change goes through the founder-approved central `manage-agent` workflow
invoked by a **team manager** (never the affected worker editing itself); a
worker, or a manager changing its own rules, escalates to the founder. The
executor-specific effects — Claude applies a new `allow_rules` entry on the next
session, opencode stays inert until a bootstrap-forcing refresh, and Codex/Pi
are not covered by that rail — are documented in the **manage-agent** skill and
`docs/agent-guides/agent-executors-and-permissions.md`.

## Refusal and preparation recovery

1. Inspect the exact exit/error/receipt. Read original tuple, digest and missing
   predicates with `happyranch preparation-recovery TASK --org ORG --json`.
   After uncertain POST/COMMIT, add `--operation-key ORIGINAL_KEY` for readback.
2. Preserve the original body/key, already-committed facts and sticky UNKNOWN.
   No automatic reapply/new-key, callback replay, missing-PID clearance or
   reconstruction of absent owners follows from a refusal, later exit, source
   update or restart. Native prelaunch refusals have at most three consecutive
   attempts per logical continuation; unclosed preparation can refuse earlier.
3. Never manually repair/reset journals, clear ownership, fabricate retry links
   or use a reserved SID as running-session proof. The human-only write is
   `happyranch reconcile-preparation TASK --org ORG --from-file ABS`; this syntax
   grants agents no human/bearer write authority. It sends once; inspect with the
   SAME operation key after uncertainty, never automatically apply again.
4. Report the precise blocker, original reserved SID/attempt and safe audit cause
   (`task_native_launch_boundary`) to the accountable owner. Finish only through
   your actual current task/SID callback. No manual cleanup, historical repair,
   deploy/restart, chain reset, extra attempt or recovery authority is granted.

Specialist audits should use the maintained
runtime lifecycle contract (`docs/agent-guides/contracts/runtime-lifecycle.md` in the happyranch repository),
the CLI's `cmd_preparation_recovery` / top-level parser in
`cli/commands/tasks.py`, and the original-owner preparation implementation in
`runtime/infrastructure/db/tasks.py`. A refusal audit is not settlement or retry authority.

## Root task Pause outcomes

An unheld ordinary session with matching unknown journal evidence can undergo
existing startup FAILED or periodic flag/TTL CANCELLED recovery only after
positive dead-process or exact current-session-start/host-boot proof. Current
results retain their startup consumption path even with missing/inconclusive
PID; ordinary job-wake provenance remains ordinary. Boot/PID observations are
read outside the root/DB writer. The post-terminal writer marks only
that producer settled and preserves unknown cleanup history. Missing PID alone
proves nothing; deliberate Pause holds, workflow/v2/recovery owners and other
deferrals retain their own rules. This grants no agent journal cleanup,
historical repair, daemon restart or deployment authority.

A root Pause holds future task execution, not your already running callback. Complete truthful progress/completion for your original task/session and preserve real job evidence. A held-root deferred invocation is not an execution failure, and Resume grants no new session, step, budget or permission. Only the server-captured ordinary-purpose actual running session (including a normal v2-admitted manager session) may auto-run its already-authorized unreviewed jobs while held; recovery sessions retain their existing purpose restrictions.
