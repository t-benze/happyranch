---
name: reflection
description: Use this skill when asked mid-thread to reflect on your recent work. Produces an opening report (recent tasks, learnings, frictions) posted as a thread reply, and captures learnings + optional KB entries via direct `happyranch learning add` / `happyranch kb add` calls at the end.
---

# reflection

Triggered on-demand when the founder asks you to "reflect" inside a thread turn. It produces a structured opening report and, when the founder is satisfied, persists learnings and optional KB entries directly — no endpoints, no transcript store, no task dispatch.

## Identity

Read your agent name from `agent.yaml` in the workspace root if you don't already know it. Every `happyranch learning ...` and `happyranch kb ...` call takes `--agent <your_name>` where required.

## START — opening report as a thread reply

1. **Find the window for the report.** Scan `task_history.md` for your most recent reflection (look for "reflection" — or the legacy "review" — in the briefs/outcomes). If no prior reflection exists, use "all-time, capped at 30 days."

2. **Gather inputs:**
   - `task_history.md` (in the workspace root).
   - Per-agent learnings — `learnings/_index.md` (drill into individual `LRN-NNN-<slug>.md` entries dated within the window).
   - `happyranch audit --org {ORG_SLUG} --agent <your_name> --since <window_start>`.

3. **Emit the opening report as a thread reply.** Use exactly these section headings, in this order:

   ## Since last reflection
   Window dates and one-line counts by terminal status.

   ## Notable tasks
   3–5 items you picked for significance (not just most recent). One-line takeaway each.

   ## New learnings
   Learnings added in the window. Bullet list.

   ## Open questions / frictions
   Your own reflection — anything confusing, contradictory, a recurring issue pattern, a decision you're unsure about. Can be empty.

   ## Suggested topics
   2–3 things you think are worth discussing.

4. **Wait for the founder.** The rest is a normal conversation.

## END — direct learning/KB capture (no endpoint)

When the founder indicates the reflection is done:

1. **Summarize.** In your own head, list: what was discussed, what decisions got reached, what remains open.

2. **Extract learnings.** A learning is a durable, non-obvious operational fact you'll want on future tasks. Skip things already recorded. For each learning, write a YAML payload:

   ```
   slug: lrn-<slug>
   title: <title>
   topic: <topic>
   tags: [tag1, tag2]
   body: |
     <markdown body>
   ```

   Then call:

   ```bash
   happyranch learning add --org {ORG_SLUG} --agent <your_name> --from-file /tmp/lrn-<slug>.yaml
   ```

3. **Identify KB-worthy material.** Apply the rules in `protocol/06-knowledge-base.md` (≥12-month lifespan; not agent-private). Write `/tmp/kb-<slug>.md` with YAML frontmatter and a markdown body, then call:

   ```bash
   happyranch kb add --org {ORG_SLUG} --agent <your_name> --from-file /tmp/kb-<slug>.md
   ```

4. **Post a final thread reply** summarizing outcomes: what was discussed, decisions reached, learnings/KB entries written. This replaces the lost transcript.

## Why single-line call + temp file

The `--from-file` pattern matches `happyranch report-completion` and other callbacks. Claude's headless-mode permission matcher treats multi-line bash as multiple separate commands, which breaks the allowlist. Staging the payload in a temp file and invoking `happyranch` once keeps the callback inside the `Bash(happyranch *)` allow rule.

## No dispatch

**A reflection session does not create tasks.** Do not call `happyranch dispatch` or `happyranch run`. If actionable work surfaces, recommend the founder open a thread or dispatch it separately, or self-dispatch a root from a normal thread turn — not from reflection.

This is skill-level enforcement. The reflection skill has no task-path auth; there is no talk-mode dispatch surface to abuse. If you need to act on a finding, tell the founder and let them decide the next step.

## What NOT to do

- Don't start tasks (`happyranch run ...`) from inside a reflection. If something actionable comes up, tell the founder explicitly.
- Don't write learnings you've already written — the daemon appends verbatim, so duplicates will clutter.
- Don't treat KB entries as a catch-all for in-reflection notes. KB is for durable, cross-agent-relevant knowledge. Everything else is a per-agent learning.
- Don't call `happyranch talk start|end|resume|abandon` — those endpoints no longer exist. This skill replaces the talk flow entirely.
