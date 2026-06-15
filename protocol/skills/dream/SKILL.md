---
name: dream
description: Use this skill only when the daemon starts a private scheduled dream invocation. It produces private learnings, KB candidates, and optional founder-thread output.
---

# dream

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
