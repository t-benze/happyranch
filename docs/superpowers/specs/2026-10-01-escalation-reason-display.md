# Escalation reason display

> Status: implemented
> Current Source: docs/agent-guides/web-and-cli.md
> Notes: THR-279 PR A; read-side only. PR B owns any future writer change.

## Decision

Founder-facing escalation surfaces show the manager's own current-episode
reason as the primary text. An authority-policy-v2 refusal additionally shows
a server-owned plain-English explanation of its closed refusal code.

The current episode is delimited by consecutive exact-task `escalation` audit
IDs. For a v2 refusal, an `orchestration_step` supplies primary text only when
its ID is strictly inside those boundaries and its decision action is
`escalate`. If the callback decision was delegate, done, or fanout, the primary
is absent. An ordinary later escalation has no stale v2 secondary. A resolved
older reason is never reused.

## Surfaces

- Task detail: primary banner line followed by the v2 explanation; its root
  recall-tree Outcome uses the same read-side text instead of the stored code.
- Dashboard Waiting on you: `question` remains the primary display string and
  the additive projection supplies the secondary line.
- `happyranch details`: v2 primary and secondary replace the opaque refusal
  note; ordinary `Note:` output stays byte-identical.
- Task list rows and the escalation resolution dialog do not show reasons and
  remain unchanged.

## Preserved invariants

There is no schema or migration. The authority hook and refusal finalizer are
unchanged. `tasks.note` remains `authority_v2_refusal:<code>` for v2 refusals,
and the `escalation` audit retains its existing three-field payload. Exact
`task_id` equality excludes prefixed audit scopes. The read is capped to the
newest 256 escalation/orchestration rows in primary-key order. Unknown future
codes display the raw code instead of disappearing.
