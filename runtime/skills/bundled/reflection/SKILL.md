---
name: reflection
description: Use this skill when asked mid-thread to reflect on your recent work. Produces an opening report (recent tasks, learnings, frictions) posted as a thread reply, and captures learnings + optional KB entries via direct `happyranch memory add` / `happyranch kb add` calls at the end.
---

# reflection

Triggered on-demand when the founder asks you to "reflect" inside a thread turn. It produces a structured opening report and, when the founder is satisfied, persists learnings and optional KB entries directly — no endpoints, no transcript store, no task dispatch.

## Identity

Use the permanent agent ID supplied in the current invocation; do not derive it from a displayed name or workspace directory. Every `happyranch memory ...` and `happyranch kb ...` call takes `--agent <your_agent_id>` where required.

## START — opening report as a thread reply

1. **Find the window for the report.** Use an explicitly identified prior reflection timestamp from available thread context. If unavailable, state that the prior boundary is unknown and choose a window capped at 30 days. Do not infer a reflection from a generic "review" match in task history.

2. **Gather inputs:**
   - `task_history.md` (in the workspace root; at most 50 recent task records).
   - Private memory — `memory/_index.md`; use `happyranch memory list --org {ORG_SLUG} --agent <your_agent_id>` and the `memory get` / `memory search` commands with the same permanent ID for full `MEM-NNN` entries in the window. `LRN-` IDs and the `learning` CLI alias are compatibility forms only.
   - `happyranch audit --org {ORG_SLUG} --agent <your_agent_id> --since <window_start>`.

3. **Emit the opening report as a thread reply.** Use exactly these section headings, in this order:

   ## Since last reflection
   Window dates and one-line counts by terminal status. Use complete windowed audit/listing evidence when available; otherwise label counts as the observed sample, not exhaustive window totals.

   ## Notable tasks
   3–5 items you picked for significance (not just most recent). One-line takeaway each.

   ## New learnings
   Learnings added in the window. Bullet list.

   ## Open questions / frictions
   Your own reflection — anything confusing, contradictory, a recurring issue pattern, a decision you're unsure about. Can be empty.

   ## Suggested topics
   2–3 things you think are worth discussing.

4. **Post and end the turn.** Follow the maintained [thread reply payload contract](../thread/SKILL.md#reply): use the supplied invocation identity, write the reply payload to an absolute file and call single-line `happyranch threads reply --org {ORG_SLUG} --thread-id <id> --from-file <absolute-path>`. End this turn after the callback; continue only when a new founder message arrives. Do not sleep or invent a task completion callback.

## END — direct learning/KB capture (no endpoint)

When the founder indicates the reflection is done:

1. **Summarize.** In your own head, list: what was discussed, what decisions got reached, what remains open.

2. **Extract learnings.** A learning is a durable, non-obvious operational fact you'll want on future tasks. Search memory for semantic duplicates before choosing a new slug. Each successful add creates a validated, stamped structured `MEM` entry; an existing slug returns HTTP 409. There is no semantic duplicate detector. For each new learning, write a YAML payload:

   ```
   slug: <slug>
   title: <title>
   topic: <topic>
   tags: [tag1, tag2]
   body: |
     <markdown body>
   ```

   Quote YAML strings containing ` #`, for example
   `title: "Fix PR #123 regression"`. Top-level unquoted single-line string
   fields with trailing comments (including `topic: ci  # note`) are rejected;
   remove the comment or quote the entire value to preserve its text. Multiline
   bodies retain normal YAML behavior. A JSON object is also accepted, detected
   by a `.json` suffix or a leading `{` after whitespace.

   Then call:

   ```bash
   happyranch memory add --org {ORG_SLUG} --agent <your_agent_id> --from-file /tmp/mem-<slug>.yaml
   ```

3. **Identify KB-worthy material.** Prefer durable, cross-agent knowledge with an expected useful lifespan of at least 12 months; keep agent-private lessons in memory. Write `/tmp/kb-<slug>.md` with the four required YAML frontmatter fields and a markdown body:

   ```markdown
   ---
   slug: example-rule
   title: Example durable rule
   type: reference
   topic: workflow
   ---

   Durable cross-agent guidance goes here.
   ```

   Then call:

   ```bash
   happyranch kb add --org {ORG_SLUG} --agent <your_agent_id> --from-file /tmp/kb-<slug>.md
   ```

4. **Post a final thread reply** summarizing outcomes: what was discussed, decisions reached, learnings/KB entries written. Use the same [thread reply contract](../thread/SKILL.md#reply), supplied invocation identity and absolute payload file, then end the turn. This replaces the lost transcript.

## Why single-line call + temp file

The `--from-file` pattern matches `happyranch report-completion` and other callbacks. Claude's headless-mode permission matcher treats multi-line bash as multiple separate commands, which breaks the allowlist. Staging the payload in a temp file and invoking `happyranch` once keeps the callback inside the `Bash(happyranch *)` allow rule.

## No dispatch

**A reflection session does not create tasks.** Do not call `happyranch dispatch` or `happyranch run`. If actionable work surfaces, recommend the founder open a thread or dispatch it separately, or self-dispatch a root from a normal thread turn — not from reflection.

This is skill-level enforcement. The reflection skill has no task-path auth; there is no talk-mode dispatch surface to abuse. If you need to act on a finding, tell the founder and let them decide the next step.

## What NOT to do

- Don't start tasks (`happyranch run ...`) from inside a reflection. If something actionable comes up, tell the founder explicitly.
- Don't add semantic duplicates under a new slug; search first. Slug collisions return 409, but different slugs can still duplicate a lesson.
- Don't treat KB entries as a catch-all for in-reflection notes. KB is for durable, cross-agent-relevant knowledge. Everything else is a per-agent learning.
- Don't call `happyranch talk start|end|resume|abandon` — those endpoints no longer exist. This skill replaces the talk flow entirely.
