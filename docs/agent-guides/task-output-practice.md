# Task output folder practice

Use `workspaces/<agent>/output/TASK-N/` for a task's evidence and handoff
record. The agent owns its output hygiene; these practices add no daemon
auto-expiry or end-of-task deletion hook.

The [start-task skill](../../runtime/skills/bundled/start-task/SKILL.md)
is normative. This guide explains its task output hygiene block and gives
examples. Quoted rules below come from that block; examples do not change
retention classes, windows or citation safeguards.

## Keep the work outside output/

Output should let the next reader understand what happened without recreating
your working environment. Do the work in the task worktree or runtime
task-tmp, and write the conclusion and concise evidence into output.

| Do | Don't |
| --- | --- |
| Keep a result, command exits and a short failing excerpt. | Use output as the command's working directory. |
| Put scratch and `*-tmp` trees in runtime task-tmp or the task worktree. | Copy a verification scratch tree into output. |
| Keep `node_modules`, `.venv`, `dist` and `build` with the work. | Save dependency caches or build directories as evidence. |
| Record DB observations or image metadata in a small report. | Store DB/WAL copies, VM/disk images or tarballs of repos in output. |

A copied repository or database is still working material when it is named
“evidence.” Extract the observation the reader needs and explain how it was
obtained instead of preserving the whole environment.

## Control size when writing

Per start-task:

> Gzip any file over 1 MB at write time in `output/` (e.g. `.json.gz`, `.log.gz`).

> Retain command stdout/stderr as head/tail excerpts totaling at most 256 KB,
> plus each full stream's total byte count and SHA-256 digest.

> The soft cap is 50 MB stored per task output directory. Before completion,
> run `du -sh output/<task_id>/`; exceeding the cap needs a stated reason
> in the completion summary.

`happyranch recall --fetch-output` caps retrieved output at about 200 KB.
That is a retrieval limit, not another storage allowance. A huge JSON dump
cannot be read through recall anyway; gzip saves space but does not make
the conclusion visible. Put that conclusion in a small readable record.

| Do | Don't |
| --- | --- |
| Write an allowed large evidence file directly as gzip. | Plan to compress a raw dump only after the task ends. |
| Save bounded head/tail excerpts and each full stream's byte count and digest. | Retain full stdout/stderr streams as the default record. |
| Explain a soft-cap overrun in the completion summary. | Assume compression alone makes any output size acceptable. |

## Leave a durable small record

Every task should leave a handoff, summary or verdict that carries its
conclusion. Per start-task, keep handoff, completion, summary, verdict, receipt
and manifest files under 1 MB regardless of age. Their names are examples of
record roles; creating every possible filename is unnecessary.

A minimal recommended layout is:

```text
output/TASK-N/
  handoff.md          # conclusion, branch/PR/head, commands and exits, limits
  completion.json     # the completion payload prepared for the final callback
  command-excerpts.md # bounded stdout/stderr, full byte counts and SHA-256
```

For review or QA, `verdict.md` may be the main record. A task that produces
an execution receipt can add a small `receipt.json` or summarize a large
receipt in the handoff. The record should say what was established, what
failed or was not run, and what the next owner must do.

| Do | Don't |
| --- | --- |
| State the conclusion and relevant source/head identity in the record. | Leave only a dump and expect the reader to infer the result. |
| Record raw evidence size, digest and the observation it supports. | Require a future reader to reopen raw evidence to discover the verdict. |
| Keep the result understandable after eligible raw files are deleted. | Make the durable result depend on a disposable scratch tree. |

## Cite the record precisely

Per start-task, cite exact small record files: handoff, summary, verdict or
receipt. Use the agent workspace and filename so the next owner can find it.
Bare TASK folders and multi-GB raw dumps are poor handoff references.

During cleanup pass 2, a folder-level citation was found to protect whole
scratch trees. Pass 3 found a second problem: the cleanup's own thread and
briefs listed target paths, making those targets “cited” by the cleanup
discussion itself. These are reasons to make future citations precise,
not permission to disregard a citation safeguard.

| Do | Don't |
| --- | --- |
| Cite `dev_agent/output/TASK-N/handoff.md`. | Cite only `dev_agent/output/TASK-N/`. |
| Cite the handoff that records a raw file's size and digest. | Link directly to a multi-GB JSON/log dump as the task result. |
| Link a small cleanup plan/receipt that describes the selection. | Paste inventories of bulk target paths into briefs and thread messages. |

For example, a brief or PR can say:

```text
Evidence: dev_agent/output/TASK-N/handoff.md — records the command,
exit status, full stdout byte count and SHA-256, and the relevant excerpt.
```

The handoff can record a raw artifact's logical label, byte count and full
SHA-256 without repeating its bulk filesystem path in the citing brief,
thread or PR. This preserves the result without making bulk the reference.
If a running task actually needs the raw file, name that need honestly;
do not hide a dependency to obtain cleanup eligibility.

## Apply the existing retention rules

Retention applies to the agent's **own terminal tasks only**. The task
folder's age alone does not establish eligibility: the writing task tree
and the protected tasks/citations must be checked. Per start-task:

> For your **own terminal tasks only**, after 14 days remove regenerable
> leftovers. Delete log, JSON and stdout/stderr files over 1 MB (including
> `.gz` variants) once the whole task tree that wrote them has been terminal
> for at least 14 days, unless a currently running or queued task, or a task
> that failed or escalated in the last 14 days, names the file or an ancestor
> folder (a retry may read it). Keep handoff, completion, summary, verdict,
> receipt and manifest files under 1 MB regardless of age. Delete rather
> than compress or archive; never chmod/chown or follow symlinks. Older
> brief, thread or PR citations no longer protect those large files. For
> other content, after 60 days shrink uncited task directories to summary
> and completion files. Never delete or compress other content cited by a
> task brief, thread message or PR body.

The exception for large log/JSON/stdout/stderr files does not remove citation
protection from other content. Gzipping at write time and deleting eligible
old logs serve different stages; an archive is not a retention substitute.

| Do | Don't |
| --- | --- |
| Check the whole writing task tree and current protective references. | Decide eligibility from file mtime or a terminal task label alone. |
| Retain a raw log needed by a queued retry under the quoted rule. | Treat an old original task as clearance despite that retry. |
| Preserve small records and cited other content under the quoted rule. | Apply the large-log exception to every kind of cited artifact. |

## Write with usable owner permissions

Per start-task, create output files and directories owner-writable, with no
read-only modes, so later own-output cleanup is not denied by their
permissions. Directory permissions also govern traversal and removal;
making a file writable alone does not guarantee that unlink can succeed.

| Do | Don't |
| --- | --- |
| Create records and their directories with usable owner permissions. | Copy a read-only dependency tree or hardened package into output. |
| Report a permission denial and retain the affected item. | Use chmod/chown to force cleanup or follow a symlink to its target. |

Prevent permission problems at creation. Existing denials are findings to
report, not permission to change ownership or modes as a cleanup workaround.

## Run a bulk cleanup through review

When an agent or manager is assigned a bulk own-output cleanup, follow the
authorized task scope and the established execution practice:

1. Produce a non-destructive dry run with explicit output roots, candidate
   classes, retention/citation checks, projected sizes and refusal reasons.
2. Obtain independent code review and QA of the script and dry run.
3. Run the reviewed script once as one logged job, checking that its digest
   matches the reviewed script and rechecking eligibility at execution.
4. Keep a small receipt summary of actual actions, bytes, denials and limits.
   Report refused items rather than forcing them or silently rerunning.

Keep every action inside the explicitly authorized output roots. Never
chmod/chown or follow symlinks. Agent ownership is responsibility for one's
own output; a manager's multi-root run needs explicit scope covering those
roots. Reading this guide does not itself commission a cleanup run.

| Do | Don't |
| --- | --- |
| Dry run, independent review + QA, then one logged job with a receipt. | Turn a candidate inventory into an unreviewed deletion run. |
| Report permission denials and retained items. | Widen roots or change modes to improve the reclaimed-space number. |

## Self-check before completion

From your workspace root, inspect your own task output. These commands
only inspect; substitute your actual task ID and do not add `-L`:

```bash
du -sh output/<task_id>/
find output/<task_id> -type f -size +1M ! -name '*.gz'
find output/<task_id> \( -name node_modules -o -name .venv -o -name dist -o -name build -o -name '*-tmp' \) -print
```

Inspect the findings for scratch, caches and copied environments. The size
scan is a reminder of the write-time rule, not a deletion command or a full
retention decision. Check excerpts, stream metadata, owner permissions and
the exact record-file citation before preparing the final callback.

| Do | Don't |
| --- | --- |
| State any 50 MB cap overrun and its reason in the completion summary. | Omit the overrun because raw files are gzipped. |
| Ensure the small record explains the result and remaining gates. | Mark missing checks as passing or leave only raw output. |

## Background

THR-326 cleanup passes 1–4 exposed bulk working material, broad citations
and permission denials in engineering output. Pass 4 freed 86.9 GiB,
taking the five engineering output roots from 185.1 GiB to 98.2 GiB;
8,103 permission-denied files were retained. This history motivates small,
readable records and usable permissions; it adds no retention exception.

Reference: THR-326 seq42 / TASK-14930; the merged start-task skill governs policy.
