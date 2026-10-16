---
name: dream
description: Use this skill only when the daemon starts a private scheduled dream invocation. It produces private learnings, KB candidates, and optional founder-thread output.
---

# dream

Native C3 preparation failure or ordinary process return never proves file,
package or process cleanup. Unknown ownership remains in retained audit census.
New retry budgets explicitly freeze even an empty schedule; missing legacy facts
cannot authorize replenishment/reentry. Schedule runtime SID is pinned through
real preparation/spec/execution and is distinct from provider resume identity.
Custom-skill authority validates before reservations; only explicit native
validation/rollback/temporary-cleanup evidence can settle a refused writer.
No history deletion, automatic recovery, apply or execution authority follows.

During an online team-move hold, the native scheduler/runner retains due or pending work without consuming its occurrence. Running work is a blocker. Let the real dream owner revalidate and issue its genuine callback; never create a replacement occurrence, fake completion or reset a window. Source behavior remains UNEXECUTED and apply is unavailable.

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
