---
name: talk
description: Use this skill when the founder activates a conversational session with you via `/talk start`. It produces a structured opening report (recent tasks, learnings, frictions) and, at `/talk end`, summarizes the conversation and persists learnings + optional KB entries.
---

# talk

The founder sometimes opens an interactive session inside your workspace (by running `claude` or `codex` here) and says something like `/talk start` or "let's have a talk." You are the same agent as always; the talk flow is a structured wrapper around a normal conversation.

## Triggers

- `/talk start` — begin the flow.
- `/talk end` — wrap it up.

If you're not sure whether the founder wants to start a talk, ask once before invoking the flow.

## Identity

Read your agent name from `agent.yaml` in the workspace root if you don't already know it. Every `opc talk ...` call takes `--agent <your_name>` where required.

## /talk start — procedure

1. **Check for an existing open talk.**

   ```bash
   opc talk status --agent <your_name>
   ```

   If it prints `no open talks`, continue to step 2.

   If it prints a row with a `TALK-NNN`, tell the founder:

   > "There's an open talk TALK-NNN from <started_at>. Do you want to **resume** it or **abandon** it and start fresh?"

   - On **resume** → run `opc talk resume --talk-id TALK-NNN` and skip the opening report. Prior context is in the founder's head; just pick up where you left off.
   - On **abandon** → run `opc talk abandon --talk-id TALK-NNN --reason orphan_at_new_start`, then continue to step 2.

2. **Start a new talk.**

   ```bash
   opc talk start --agent <your_name>
   ```

   Capture the `TALK-NNN` that comes back.

3. **Find the window for the report.**

   Run `opc talk list --agent <your_name> --limit 5` and find the most recent talk with `status=closed`. Its `ended_at` is the window start. If no prior closed talk exists, use "all-time, capped at 30 days."

4. **Gather inputs:**

   - `task_history.md` (in the workspace root).
   - `learnings.md` (entries added in the window).
   - `scorecard.md`.
   - `opc audit --agent <your_name> --since <window_start>`.

5. **Emit the opening report.** Use exactly these section headings, in this order:

   ## Since last talk
   Window dates and one-line counts by terminal status.

   ## Notable tasks
   3–5 items you picked for significance (not just most recent). One-line takeaway each.

   ## New learnings
   Learnings added in the window. Bullet list.

   ## Open questions / frictions
   Your own reflection — anything confusing, contradictory, a recurring issue pattern, a decision you're unsure about. Can be empty.

   ## Scorecard delta
   Tier change (if any) and acceptance-rate shift.

   ## Suggested topics
   2–3 things you think are worth discussing.

6. **Wait for the founder.** The rest is a normal conversation.

## /talk end — procedure

1. **Summarize.** In your own head, list: what was discussed, what decisions got reached, what remains open.

2. **Extract learnings.** A learning is a durable, non-obvious operational fact you'll want on future tasks. Skip things already in `learnings.md`. Keep each entry short.

3. **Identify KB-worthy material.** Apply the rules in `protocol/06-knowledge-base.md` (≥12-month lifespan; not agent-private). For each KB-worthy item:

   ```bash
   # Write a markdown file with the frontmatter that the KB add route expects.
   # See .claude/skills/manage-repo or the existing opc kb add docs for the shape.
   opc kb add --agent <your_name> --from-file /tmp/kb-<slug>.md
   ```

   Collect each slug you wrote.

4. **Assemble the end payload** at `/tmp/talk-end-<talk_id>.json`:

   ```json
   {
     "summary": "<≤16 KiB markdown>",
     "topic_list": ["topic 1", "topic 2"],
     "transcript_markdown": "<full transcript as you recorded it>",
     "learnings": [
       {"text": "learning one"},
       {"text": "learning two"}
     ],
     "kb_slugs": ["slug-you-wrote-in-step-3"]
   }
   ```

5. **Single-line call** (write the JSON to the temp path first, then run the CLI once):

   ```bash
   opc talk end --talk-id TALK-NNN --from-file /tmp/talk-end-TALK-NNN.json
   ```

6. **Confirm to the founder:** the transcript path, the number of new learnings, and any KB slugs written.

## Why single-line call + temp file

The `--from-file` pattern matches `opc report-completion`, `opc manage-agent`, and `opc kb add`. Claude's headless-mode permission matcher treats multi-line bash as multiple separate commands, which breaks the allowlist. Staging the payload in a temp file and invoking `opc` once keeps the callback inside the `Bash(opc *)` allow rule. Codex has no such constraint, but using the same pattern everywhere keeps the skill portable.

## What NOT to do

- Don't start tasks (`opc run ...`) from inside a talk — that's out of scope for v1. If something actionable comes up, tell the founder explicitly and let them submit.
- **Exception:** `opc manage-agent` (enroll / update / terminate) is allowed during a talk via the talk-path payload (pass `talk_id` instead of `task_id`+`session_id`). See the `manage-agent` skill. Record any such call in your `transcript_markdown` so the founder has a human-readable record at talk-end.
- Don't call `opc talk end` without a summary + transcript. An empty payload is useless on recall.
- Don't write learnings you've already written — the daemon appends verbatim, so duplicates will clutter `learnings.md`.
- Don't treat KB entries as a catch-all for in-talk notes. KB is for durable, cross-agent-relevant knowledge. Everything else is a per-agent learning.
