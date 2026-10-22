---
name: start-task
description: Use this skill at the start of every task. Parses task_id, session_id, brief, and role_guidance from the prompt, executes the work, reports completion back to the daemon, and cleans up worktrees.
---

## Current names and permanent automation IDs

Fresh prompts may show current Name · ID for this turn. Use the permanent ID
from the injected parameters for callback agent/composer/speaker, task/session/
invocation proofs, configuration and delegate/then/fanout targets. A name is
human-addressing/presentation data, never actor authority. Human destination
arguments and @mentions accept current names or IDs; former names refuse with
the current name and do not forward. Founder is a separate human; recipient
transport remains `@founder`. Metadata failure gives ID-only context.


# start-task


One daemon-wide operator control is `<daemon_home>/disk-sweep/stop.json`.
Its only supported content is a JSON object containing exactly `stop` with a
real Boolean: `{"stop":true}` suspends scheduled deletion for all orgs;
`{"stop":false}` or a genuinely absent literal file permits the default policy.
The control is read afresh once per regular scheduled attempt, after the normal
same-night reservation and before native writer reservation or worker launch,
inside the shared1800s budget. Invalid/unreadable/oversized/truncated/changed
content, wrong/extra/duplicate keys or types, invalid UTF8, symlinks (including
dangling links or aliased parents), hardlinks, directories and special files
visibly suspend. Only final-file ENOENT means absent; other I/O failures suspend.
Malformed control does not prevent daemon/org startup. Operators alone write or
remove this local file; there is no per-org override, setting/API or new schedule.
Suspension logs its cause and, when publication is safe, writes `outcome:
suspended` with daemon-scoped `scheduled_admission` (`path,state,reason` and an
error when invalid) in the existing summary/audit payload. All orgs remain
unreached, with no worker/PID, reaping, removal-byte or successful-sweep claim.
The existing audit empty-task/daemon envelope and org ordering remain. Marker
and report reads/writes traverse literal no-follow parents; publication uses a
new exclusive pending inode and descriptor-relative replacement. A missing or
aliased parent, aliased/hardlinked/special final metadata or existing pending
entry refuses without writing through that path. Scheduled work never creates
a missing control parent. Refusal is visible through the existing daemon log;
a summary or org audit may be unavailable and must not be claimed as persisted.
Only manual preview can create missing report parents, with no-follow traversal.
After the single stop observation, the actual initial date summary is published
before any native writer reservation. Allowed admission initially records
`incomplete`, empty org results and all orgs unreached, without a finished/PID or
mutation claim. Unsafe date-summary or pending metadata therefore refuses with
zero reservations or worker effects. Later checkpoint/publication failures after
genuine reservation retain native uncertainty; this initial observation is not
a fence against subsequent filesystem changes.
These are filesystem observations, not a racing-rename fence or hard real-time
certification. Publication failure never permits deletion. Manual `--dry-run` remains usable and nonmutating with
true, invalid or absent control; it may write its existing reports/audits.
Removing/fixing the file affects the NEXT existing scheduled attempt only:
no same-night catchup/retry or marker reset. This is a run-admission boundary;
writing stop does not cancel an already launched worker or promise a hard
real-time stop. Existing deletion/ownership/age/containment gates still apply.

Under Founder THR307seq134, the independent daemon disk sweeper deletes eligible
scratch/worktrees for every normally admitted org on its nightly schedule. There
is no per-org opt-out; obsolete disk_sweep config is ignored. Explicit manual
--dry-run remains a nonmutating preview. The sweeper keeps its own ended-task/
ended-structural-root, fresh jobs, recursive-age and containment contract. Under
Founder seq117 it does not inspect OS-process usage: once both the task and its
structural root have ended, nightly sweeping may remove their task scratch and eligible worktrees. Stop and reap task-owned processes before
successful completion; do not rely on those folders afterward. Settle owned jobs;
genuine authorized native job waits keep the existing blocked/wait callbacks,
never fake completion. Filesystem mount containment remains in force.
This grants no agent deletion authority or end-of-task deletion hook, replaces
none of this skill's legacy gates, and disables none of the seven nightly agent
sessions. Other cleanup owners' process gates and old flags remain unchanged.
Durable output and sibling task-scratch manifests/locks stay excluded. Put
regenerable fixture/cache trees in runtime task-tmp; retain only concise evidence
in output. Do not run historical cleanup, change live configuration, deploy/
restart, or use a manual cleanup fallback on this note's authority.

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

## Workflow-owned document tasks

For a workflow-owned task, follow its immutable document contract and only its
recipient-filtered inputs. Do not use generic delegation, fanout, coding, approval
or merge actions. Historical @1/@2 tasks retain their original drafting contract.
A newly pinned @3 original author with explicit stage+submit authority receives
its bound handoff supplement: preserve exact UTF8 output under one stable
operation key before the genuine final completion callback. Staging reserves
bytes only; the owned service fulfills that SAME operation after committed
successful/quiescent terminal proof and current authority checks. The stage
CLI is `happyranch workflows submissions stage --org ORG --session-id SID
--from-file /absolute/command.json`. Supply the closed bound command, keep its
original key on transport loss, and stage before the final callback. If the
compatible adapter is unavailable, report that dependency; never fabricate a
result or substitute an unapproved direct-submit/reviewer-stage operation.

For a newly pinned @4 artifact original author, the template singleton selects
kind. B1 admits documents only; code activation refuses before allocation.
Follow the actual bound supplement and use workflow-author-handoff-command@2
with artifact_kind document, never the historical @1 command for @4. The
repository_binding/resolution slots are null and grant no repository capability.
A current configured agent reviewer follows its bound launch supplement: read
only the selected pinned request/inputs, then stage exact UTF8 findings with
`happyranch workflows reviews stage --org ORG --session-id CURRENT
--from-file /absolute/command.json --json` before the genuine final callback.
Use workflow-review-stage-command@1 for an @1 request scope and @2 for an @2
scope or @3 for a revision2 @3 scope, the selected request/digest/revision/declared outcome and one stable key.
Staging returns an unsigned five-field acknowledgement; it is not a receipt.
Keep the same key/body after transport loss, without automatic retry. Only the
actual INTEGER/full callback plus successful Boolean-quiescent terminal and
current authority can admit a receipt. Report unavailable dependencies and
failure/BLOCK evidence honestly. No execution/merge authority follows.

A genuine new revision author follows the bound return brief and all collected
r1 feedback, with only originally author-addressed inputs. Stage literal r2 using
`happyranch workflows revisions stage REVISION_OPERATION --org ORG
--session-id CURRENT --from-file /absolute/command.json --json`. Supply exactly
workflow-author-revision-command@1, operation_key, revision_operation_id,
expected_revision=2, artifact_kind=document, submission_bytes_base64 and
submission_digest. Keep the same key/body after transport loss and stage before
the actual final callback. Admission, staged bytes and transport success supply
no result, signature or review graph. Only this new task's own full INTEGER
callback and successful Boolean-quiescent terminal can fulfill under current
authority. Never revive the old task or reuse old findings/receipts as r2 approval.
A new reviewer selected by its current @3 request can read only its own bounded
r1 prior_review, not another reviewer's private inputs or proof material.

Configured review tasks independently assess their pinned document and filtered
inputs, then report through their actual task/session callback. Requests and
callbacks remain unsigned; do not claim a receipt, joined approval or another
principal's signature. Report unavailable dependencies honestly. In every lane,
retain this skill's FINAL report-completion action and current session binding.

## Disabled coding-start composition (ROOT10693 / K3a)

Only an independently authorized current decision-owning agent manager may
compose the closed `{action:workflow_start,workflow:workflow-coding-start@1}`
decision for its OWN genuine final callback. `happyranch workflows coding
run-start --from-file ABS --emit-decision` validates canonical pins/work and
prints that decision only; it allocates nothing. Preserve the original key/file.
The actual result owner refuses unavailable downstream coding readiness before
claim consumption/allocation; K3a remains disabled. A descriptor, mapping or
printed decision supplies no repository access, execution, approval or merge
permission. Queued carriers/attempts have no launcher or execution supplement in
this slice; do not substitute legacy delegate/chain/F5 or invent a callback.
The skill's final report-completion and exact current-session binding still apply.

## Steps

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
`happyranch attach-upload --org ORG --as-agent CURRENT_AGENT --file /absolute/source`
within the actual authorized environment (upload has no --session-id flag).
Inherited access grants reading, not another claim. Never upload task-only
logs/screenshots/review packets to Shared Artifacts as an ownership workaround.


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

   Existing audit/task/job acquisition refuses exhausted scan/lock/row/byte work
   with a non-success response. Never treat that as an empty completed census or
   retry with a caller-selected subset. Output totals/truncation remain evidence;
   missing/incomplete streams refuse collection validation. Cooperative budgets
   and practical measurements do not certify hard RSS or wall time.

   Memory telemetry reports are observation-only. Valid collection requires
   independently admitted G1 role/job/probe evidence and current serving health;
   source fixtures, a summary tag, merge/deploy, or raw sample counts do not start
   production collection. Probe freshness applies at new initial/reset final
   admission. Continuing reports/replay preserve the authenticated original
   boundary and recheck current full health. The root retains any explicitly
   requested independent durable within48h AFTER-epoch check and production
   observation follow-through; never infer a renewal, schedule or tuning action.
   Seq480 census acquisition covers current boot/epoch activity and its required
   lifecycle/ancestry/retry/QA/job/control references, including current calls on
   old retired SIDs. Historical memory_read closure uses payload.task_id for
   required full-history owners, preserving outer AGENT scope. Each capture
   exhausts one bounded existing action read; closing/atomic rereads rediscover
   late preboot rows. Exhaustion refuses without an absence claim.
   Unrelated global SID sharing is the documented PR1084 trust
   limit; related contradictions and missing evidence still refuse. Both reports
   share closing/commit membership recomputation. Bounded refusal or source sizing
   is not healthy observation, installed qualification or collection acceptance.
   THR091seq557/559/560 allows0.5s CUMULATIVE DB admission waiting across
   both serving revision bookends and every whole-pair attempt, clipped by the
   ORIGINAL acquisition deadline. All attempts share SQL/work/file/row/byte and
   transport limits; nested reads do not replenish them. Release DB ownership
   before metadata/identity/file/network work. Only concurrent-write movement
   retries a completely new pair; retain charged raw failed pairs in existing
   bounded logs and accept one stable pair without stitching. Hard errors,
   malformed/sticky state, observer replacement or source/identity/boot/cohort/
   profile/provider/path drift refuse without retry. Capture task comparisons
   may accept strictly forward aware-string last_heartbeat AND accompanying
   updated_at only with every other projected field/membership/type identical;
   unchanged pairs and valid rendering-time exclusions keep their meanings.
   Missing/invalid/equal/reverse changed stamps or movement of only one stamp
   refuse. Preserve raw tasks and exact locked publication/control checks.

   Completed file, cohort, comparison and validation work is checked against the
   same original deadline before successful serving/report/preparation/current-epoch
   returns, including no-control and HTTP cutoff paths. Both full internal snapshots
   must validate every required counter/digest/reference/seal field before movement
   can retry; the abbreviated public boot projection has a separate validation mode.
   Retry requires a full write and flush of the raw pair through an existing standard
   synchronous stream/file log sink, with filters and formatting applied. Disabled,
   filtered, unavailable, short or failed delivery without another acknowledged sink
   refuses; a normal warning return is insufficient. Serialization and formatted
   bytes consume the original aggregate budget. This establishes synchronous delivery,
   not crash/fsync durability or hard OS-I/O timing. Deadline checks precede successful
   read results and never reclassify an already successful publication COMMIT.


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

8. **Cleanup or record deferral.** Before the completion callback, apply the
   `make-worktree` cleanup contract to the exact task-owned worktree. Remove it
   only when every preservation check succeeds. Otherwise leave it intact and
   add `worktree-deferred: <specific reason>` to `risks_flagged` in the
   completion payload. Never force removal or delete the branch.

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

   Ordinary worker-owned root tasks also return a decision: task_type=task
   defines the decision owner. A worker root may finish, self-delegate or escalate
   within its server-authorized contract. It may delegate only to itself; it gains
   no peer/fanout, manager administration, policy or template rights. Human
   Founder manages the team but never executes as an agent. Worker-owned
   delegated leaves return plain completions. Their review_verdict records an
   agent outcome attributed to the executable parent owner, not human approval.

   - If your role is to issue a verdict (code review, QA, design review, etc.), include `"verdict": "<value>"` in your payload. Free string; your team's workflow KB entry documents the vocabulary. Optional — workers without verdicts simply omit the field.

   **Decision-owning roots — add a `decision` field.** Alongside the prose
   `summary`, a root task owner includes the supported top-level `decision`.
   An ordinary worker root may use `done`, self-only `delegate` or `escalate`
   within its server-authorized contract, with no peer, fanout or manager
   administration. Delegated worker leaves omit decisions.
   Team-manager roots retain their existing decision and escalation contract.
   See the response-format
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
     to the same agent, or a FAILED child assigned to the same agent under a
     predecessor root connected to the current root by the runtime's bounded,
     recorded-supersession verifier. A missing or invalid link rejects the
     WHOLE fanout before any child is spawned. A retrying `delegate` follows
     the same rule. The link
     never reparents or rewrites the failed child, and an unresolved local
     failure cannot be bypassed with a remote historical link. A repeated
     failed slice wakes its owning manager for a revised-work or escalation
     decision; no runtime retry-ceiling successor is created.
   - `supersede` — manager-only root replanning; requires `successor_brief`, `rationale`,
     and the validated recovery attestation. This replanning disposition is
     root-scoped: if a non-root task owner returns it, the runtime fails that
     child through the ordinary terminal tail and wakes its parent to decide;
     no successor is created.
   - `done` — the task is complete; requires `summary` of the outcome.
   - `escalate` — a decision-owning root's request for founder intervention;
     requires `reason`. Ordinary worker roots retain this server-authorized action.
     This founder-facing disposition is root-scoped: if a non-root task owner
     returns it, the runtime fails that child and wakes its parent to decide.

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

## Error handling

- If `happyranch` returns non-zero, retry once after 1 second.
- **Exceptions (no retry, fatal):** `409 session_mismatch` (the daemon has spawned a newer session for this `(task_id, agent)`) and `409 unknown_session` (the daemon has no record of this spawn — the session is orphaned). Either way, exit immediately.

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

**Role-specific callback fields.** Use the ordinary worker completion shape
and add the fields your injected role contract requires:

- a **worker** sends `status` + `summary` (and `waiting_on_job_ids` only while a
  live job wait exists);
- a **reviewer/QA** role additionally sends `"verdict": "<value>"` (its role
  contract defines the vocabulary);
- a **decision-owning root**, including an ordinary worker root, additionally
  sends a top-level `"decision"` object within its server-authorized contract.
  A delegated worker leaf omits `decision`. Team-manager roots retain their
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
  assessment. Its own root decision is limited to the server-authorized `done`,
  self-only `delegate` or `escalate` contract; a delegated worker leaf omits
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


## Native launch-refusal outcomes

Optional pull diagnostic labels describe advisory wording only; absent labels or historical exit128 remain unknown and grant no cleanup or retry authority.

Ordinary native prelaunch refusals have a durable maximum of three consecutive
attempts per logical continuation; an unclosed durable preparation can fail earlier
because unknown ownership prevents safe retry. A `launch_refused` FAILED task uses
normal manager/parent handling. Inspect its `task_native_launch_boundary` audits for
the first safe cause and exact reserved SID/attempt; a reservation is not a running
session. Failure never certifies cleanup or permits clearing unknown owners, replaying
callbacks, resetting chains or retrying consumed failures. Intentional holds/waits and
stale ownership do not consume this allowance. Resolve the evidenced preparation or
storage cause through the existing owner before an explicit supported retry. A new
retry/recovery refusal is distinct from its previous genuine launch. Deferred adoption
refusals retain the authenticated original owner; unclaimed recovery uses its original
SID and existing claim exclusions. Prior unknown evidence and callback/cancel/hold
winners survive both boundaries. Human-team failed-recovery history treats the
exact native launch/refusal audit action as informational, retaining selected
result/session/worker/team, replay and foreign-history fences. Source
publication/merge does not establish that the running daemon contains the correction.

## Root task Pause outcomes

A root Pause holds future task execution, not your already running callback. Complete truthful progress/completion for your original task/session and preserve real job evidence. A held-root deferred invocation is not an execution failure, and Resume grants no new session, step, budget or permission. Only the server-captured ordinary-purpose actual running session (including a normal v2-admitted manager session) may auto-run its already-authorized unreviewed jobs while held; recovery sessions retain their existing purpose restrictions.

A bound `workflow-coding-development@1` supplement selects only the actual
first-development task. Preserve its repository/object-format/fixed-base/path
identity and use existing authorized repository tools. A descriptor grants no
access, inspection or execution authority. Head remains absent until you actually
produce the commit. Report only your own result through the supplied genuine
task/SID final callback. No coding staging or advancement command is available
in this disabled slice; never substitute a document staging command.


A bound workflow-coding-development@1 or workflow-coding-review@1 supplement
selects only your actual assigned stage/task/SID. Preserve release/mapping,
revision, repository/object-format/fixed-base/paths and the reviewer's exact
produced head/PR/developer result. Use existing authorized repository tools;
metadata grants no access. Development introduces only its genuinely produced
full commit; reviewer independently inspects that exact subject and returns
APPROVE, REQUEST_CHANGES or BLOCK (including unavailable access).
For a completed stage, summary is the canonical UTF8 JSON STRING of the closed
workflow-coding-stage-output@1 envelope: format, instance_id, attempt_id,
revision=1, stage=development|code_review, subject, findings, outcome. subject
uses workflow-code-change@1 with kind=code-change, repository:{id}, object_format,
head, comparison:{base}, navigation:{branch,pr_url}; findings are nonblank and
at most16384 UTF8 bytes. Serialize sorted keys, compact separators, Unicode
verbatim, finite values, no duplicates/unknown fields or trailing newline.
Development outcome=produced and top-level verdict=null; completed reviewer
verdict equals its exact outcome. Report actual failed/blocked causes without
inventing a subject or receipt. Use your own genuine final report-completion
callback as the FINAL action. No staging/advancement command is available;
never substitute document staging. Callback alone does not advance, and a
review result does not grant QA/final-manager execution or merge authority.


A bound workflow-coding-qa@1 supplement selects only the actual assigned qa
stage, selected qa_engineer role, task and current SID. Preserve its full
workflow-coding-qa-scope@1: release/mapping/revision/work, actual QA assignment,
exact repository/object-format/B/H/branch/PR and original developer AND independent
reviewer attempt/task/SID/INTEGER result/outcome/subject. Use only existing
authorized repository tools. Independently inspect that exact subject and write
your own findings; use BLOCK for unavailable access or required proof. Metadata
and prior approval grant no permission and never substitute for your inspection.
For a completed assessment, summary is a canonical UTF8 JSON STRING with exactly
format=workflow-coding-stage-output@1, instance_id, attempt_id, revision=1,
stage=qa, subject (unchanged full workflow-code-change@1), findings (nonblank,
at most16384 UTF8 bytes), outcome=PASS|REVISE|BLOCK. Top-level verdict exactly
matches outcome. Use sorted keys/compact separators, Unicode verbatim, finite
values and no duplicates/extra keys/trailing newline. Report genuine own
failed/blocked causes without copying another principal findings or inventing
results/receipts. No coding decision/job wait, staging or advancement command.
Use only your genuine task/agent/SID report-completion as FINAL action. Native
host and positive outer settlement remain separate; settled QA output stays held,
even PASS, pending final-manager implementation. Readiness remains disabled.


A workflow-coding-final-manager@2 supplement belongs only to its actual assigned
coding-owned agent_manager invocation. SQL manager_closeout is translated explicitly;
never classify it as legacy or reuse source/upstream/recovery SIDs. Preserve exact
scope/release/mapping/revision/subject and all three original task/SID/INTEGER
result/outcome/digest pins. Supply the closed finalize-command@1 from the supplement
with your existing absolute authorized repository_path, saved stable operation_key
and current SID to `happyranch workflows coding finalize --org ORG --session-id CURRENT
--from-file ABS --json`. Actual entitlement for that local command is separately
required; descriptors grant no access. Preserve the selected authenticated repository
reference and original native OrgPaths workspace capture. Only its repo_key checkout/
authorized task worktrees and exact raw-origin digest/opaque ID may identify the
GitHub target; navigation alone cannot. ONE captured workspace task/attempt
reservation applies across cwd/key/path variants. Full selected/native/output/
history identity must agree; missing, foreign or aliased roots refuse. Preserve
existing/partial reservations even after failed recording; never choose a new cwd
or key to repeat an uncertain action. It selects only your own current run evidence,
observes exact successful checks once, saves task-local intent, immediately revalidates
and calls the reviewed expected-head/no-admin guard. Preserve partial intent/action
receipts and never rerun after uncertainty. Pending checks produce held output, no
coding job/decision or automatic later manager allocation. Save exact canonical
final-output@2 as your callback summary JSON STRING, stage=agent_manager, null verdict,
no decisions/job waits; own findings and original causes only. Actual remote merge
commit/aware time must be observed in THIS live session before claiming merged.
Failed command remains failed/uncertain despite separate later effect observation.
Callback through actual task/agent/SID is FINAL. Only genuine successful host and
positive outer settlement can record performed work; daemon never performs PR I/O.
Historical event@4 held QA PASS stays held; new event@5 accepts eligible settled QA
PASS. Readiness remains disabled and source presence grants no execution/merge.
