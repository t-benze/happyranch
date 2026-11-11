---
name: dream
description: Use this skill only when the daemon starts a private scheduled dream invocation. It produces private learnings, KB candidates, and optional founder-thread output.
---


## Retained preparation and admission

`OrgState.load` retains the org-owned `team_move_operations` compatibility table.
`install_team_move_schema` validates the existing layout/history and org identity,
creates only the frozen literal table when absent, and validates before COMMIT.
The active-row startup refusal is removed with the online operation/recovery
surface. The release schema digest is unchanged. The accepted
`--reader-guarded-start` launcher flag is now inert. PR-3a removes non-thread
task/job/dream/wake/schedule/workflow and profile-writer move-hold checks. Shared
Pause, identity, retry and native cleanup ownership remain. PR-3b also removes
ordinary participant/session-write, reply-delivery claim/probe and scheduler
redelivery move checks, plus dead move-only projection/publication APIs.
Reserved readers remain in Founder abort/attestation and callback/uncertainty
settlement, delivery-release permission/recovery, the archived-thread retry chain
and nightly disk-sweeper native writer/layout/hold gates. No live state, audit
history or stored move row is rewritten.

C2 freezes thread retry initialization explicitly, including EMPTY
budgets. New initial/fallback/nudge phase initialization is distinct from a
frozen zero budget; deferral/reentry cannot replenish it. Old missing facts keep
their original history interpretation but refuse ambiguous new retry/reentry
with `team_move_thread_retry_initialization_unknown`; no history is backfilled.

Historical task attribution remains separate from current launch eligibility.
PR-3b removes the unused move-only task/workflow closure census. New execution
still requires a current executable owner. Strict saved history, native identity,
Pause and cleanup ownership remain; no historical record, team, callback or
binding is rewritten.

The SAME INTEGER thread invocation/agent/SID preparation owner spans native
materialization, integrity, repository refresh and launch-spec/cache preparation,
including helper-internal omissions and caught failures. Such uncertainty appends
the existing sticky `failed_cleanup` fact BEFORE ordinary failure settlement;
backend preparation/cleanup and native workspace/cache failures retain ownership.
Later success/callback/terminal state cannot erase it. Serializer, audit COMMIT,
strict history reader, restart/reentry and census preserve that fact, independently
of frozen EMPTY/nonempty retries. Ordinary stale-claim/agent/breaker or missing
launch-seam refusals before effects retain their native no-effect disposition;
returned failure alone never proves compensation. No new schema/journal/backfill
or generic producer identity is introduced. All behavior remains UNEXECUTED.

Ordinary tasks carry their SAME native TaskInvocation/task/agent/runtime SID
through materialization, integrity (including retries), package omissions,
repository refresh, attachment writes/cleanup, task scratch and builtin/custom
cache/workspace setup. Safe deferred ownership becomes durably `prepared` before
preparation resumes; this never clears existing uncertainty.
Preparation failure sets the existing local reservation cleanup flag BEFORE the
fallible strict Pause save/COMMIT, then retains `execution_unknown`/`unknown` in
the admitted journal. Terminal/callback/quiescent later attempts and safe unrelated
worktree cleanup cannot clear it. Settlement persists that fact before release;
failed saves retain local ownership and original plus persistence errors.
Restart treats unclosed task `prepared` entries as unknown, including terminal
rows and prepared recovery/retry owners; absence of first launch is no proof of
preparation cleanup. Unknown deferred entries cannot authorize a fresh launch.
Native pre-effect source/artifact validation, repo-scan and missing launch-seam refusals
retain their no-effect disposition. Existing retry/recovery IDs, frozen budgets,
INTEGER callbacks and authority-v2 are preserved. No new field/journal, history
backfill, compensation or clearance writer is introduced. Behavior UNEXECUTED.

# dream

Native C3 preparation failure or ordinary process return never proves file,
package or process cleanup. Unknown ownership remains in retained audit census.
New retry budgets explicitly freeze even an empty schedule; missing legacy facts
cannot authorize replenishment/reentry. Schedule runtime SID is pinned through
real preparation/spec/execution and is distinct from provider resume identity.
Custom-skill authority validates before reservations; only explicit native
validation/rollback/temporary-cleanup evidence can settle a refused writer.
No history deletion, automatic recovery, apply or execution authority follows.

Dream scheduling and launch no longer check a team-move hold. The native owner retains occurrence, identity, retry and cleanup ownership. Let it revalidate and issue its genuine callback; never create a replacement occurrence, fake completion or reset a window. Source behavior remains UNEXECUTED.

This is a private reflection invocation. It is not a task, thread, or thread.

## Procedure

1. Review the dream prompt and the recent window it provides.
2. Identify durable private lessons for your own future work.
3. Identify possible org-wide KB candidates, but do not write KB entries directly.
4. If founder attention is needed, prepare a short founder-visible thread body.
5. Write KB candidate bodies to temporary markdown files.
6. Write a JSON payload to `/tmp/dream-result-<DREAM-ID>.json`.
7. Complete with a single-line callback:

```bash
happyranch dreams complete --org <slug> --dream-id <DREAM-ID> --from-file /tmp/dream-result-<DREAM-ID>.json
```

## Payload Shape

```json
{
  "summary": "private markdown summary",
  "learnings": [
    {
      "slug": "short-id",
      "title": "Durable private lesson",
      "topic": "workflow",
      "body": "..."
    }
  ],
  "kb_candidates": [
    {
      "slug": "candidate-slug",
      "title": "Possible org-wide rule",
      "topic": "operations",
      "rationale": "Why this may belong in KB",
      "body_path": "/tmp/dream-kb-candidate-slug.md"
    }
  ],
  "founder_thread": {
    "needed": true,
    "subject": "Nightly reflection: agent_name",
    "body_markdown": "Short founder-visible summary with candidates/actions"
  }
}
```

## Rules

- Keep the full dream private unless `founder_thread.needed` is true.
- Do not write KB entries directly. Dreams produce KB candidates for founder review.
- Do not dispatch tasks or other agents from a dream.
- Do not call `happyranch report-completion`.
- Use `body_path` for KB candidate bodies so the JSON payload stays small.

C3 writer disposition follows the actual authority/profile interval through
native naming reconciliation and explicit leading validation. Stale rename or
prompt CAS may settle only after successful native rollback/reconciliation;
unfenced status alone proves nothing. Caught reconciliation/publication/clone
failure, cancellation and partial effects retain unknown ownership. Skill scratch
cleanup requires no-follow lstat ENOENT; surviving entries, inspection errors and
prior uncertainty stay unknown. Cleanup causes are retained; existing unknown
history is never cleared. These are source accounting rules, not behavioral
verification or new execution/activation authority.
