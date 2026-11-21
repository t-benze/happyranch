---
name: dream
description: Use this skill only when the daemon starts a private scheduled dream invocation. It produces private learnings, KB candidates, and optional founder-thread output.
---

# dream

This is a private reflection invocation. It is not a task or thread.

Preparation and recovery refusals belong to the maintained
[runtime lifecycle contract](../../../../docs/agent-guides/contracts/runtime-lifecycle.md).
Preserve the original occurrence and identity; never create a replacement,
fabricate completion or reset a window. Process return alone is not cleanup proof.

## Procedure

1. Review the dream prompt and the recent window it provides.
2. Identify durable private lessons for your own future work.
3. Identify possible org-wide KB candidates, but do not write KB entries directly.
4. If founder attention is needed, prepare a short founder-visible thread body.
5. Write KB candidate bodies to temporary markdown files.
6. Write a JSON payload to an absolute writable path, for example
   `/absolute/workspace/dream-result-<DREAM-ID>.json` (replace the example
   workspace path with your actual writable workspace).
7. Complete with a single-line callback:

```bash
happyranch dreams complete --org <slug> --dream-id <DREAM-ID> --from-file /absolute/workspace/dream-result-<DREAM-ID>.json
```

## Payload Shape

For a quiet reflection with no candidate or founder action:

```json
{
  "summary": "Reviewed the supplied window; no new durable lessons or founder action.",
  "learnings": [],
  "kb_candidates": [],
  "founder_thread": {"needed": false}
}
```

Add optional lessons, candidates or a founder thread only when warranted:

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
      "body_path": "/absolute/workspace/dream-kb-candidate-slug.md"
    }
  ],
  "founder_thread": {
    "needed": true,
    "subject": "Nightly reflection: agent_name",
    "body_markdown": "Short founder-visible summary with candidates/actions"
  }
}
```

## Callback result and refusals

The CLI reads every candidate's `body_path` locally and sends its contents as
`body_markdown`. Each candidate needs a readable file; missing `body_path`, an
unreadable file or malformed JSON exits 1 locally without a request.

Completion needs an existing running dream (404 `not_found` or 400
`dream_not_running` otherwise) and a nonempty `summary` (422 on field
validation). With `founder_thread.needed: true`, provide both nonblank `subject`
and `body_markdown` (422 `empty_thread_subject` / `empty_thread_body`). Correct
payload fields only within the original invocation; a refusal grants no new dream.

Check the callback response for `ok: completed <DREAM-ID> status=...`. If it fails
or transport is lost, preserve the original dream identity and report uncertainty;
do not blindly replay effects, invent another occurrence or claim completion.

## Rules

- Keep the full dream private unless `founder_thread.needed` is true.
- Do not write KB entries directly. Dreams produce KB candidates for founder review.
- Do not dispatch tasks or other agents from a dream.
- Do not call `happyranch report-completion`.
- Use `body_path` for KB candidate bodies so the JSON payload stays small.
