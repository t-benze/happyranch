# Thread invocation resolution — 2026-11-11 addendum

Founder THR-339 seq5/16; authoritative design TASK-15304. This companion to
`2026-11-08-thread-resolution-auto-archive-design.md` preserves that historical
spec and extends only opener/opener-manager reply-time resolution.

The existing reply and decline bodies accept the existing strict, extra-forbid
`resolution: {"next_owner": null | "agent_id"}` object. Pending REPLY and
TASK_FOLLOWUP tokens prove the callback turn without a task-session binding.
BOOTSTRAP refuses400 `resolution_wrong_invocation_purpose`. A token grants no
resolution authority beyond the opener or opener's current executable team
manager. Human team heads grant none. Participation remains required.

The DB transaction rechecks open/task composition, non-founder opener and NULL
dream attribution, actor/participation, token identity/pending status/purpose,
the actual triggering MESSAGE (by another agent, never self/founder), no newer
MESSAGE, next_owner and the existing founder hold. Refusals leave the token,
messages, deliveries and audits unchanged. Ineligible composition is403
`thread_not_task_composed`, actor403 `thread_resolution_not_authorized`, trigger
409 `resolution_not_answer_turn`, newer MESSAGE409 `resolution_stale`, unknown
owner422 `unknown_next_owner`, founder owner/hold409 `founder_decision_pending`.
The resolving reply's own founder address is included in the hold check.

Reply requires substantive nonempty body (422 `resolution_body_required`),
appends that message, sets resolved_seq to its new seq and settles the caller's
invocation in ONE existing transaction with the thread_resolved audit. No new
REPLY/follow-on or exchange catch-up tokens are minted. Existing live delivery
ranges/holds remain; resolution cannot fabricate their acknowledgement.

Decline requires a nonempty reason (422 `resolution_reason_required`), appends
no message, and sets resolved_seq to the actual invocation triggering_seq in the
same transaction as the decline settlement and thread_resolved audit. Existing
conversational decline settlement/exchange closure remains in force.

Audit scope stays task_id=thread_id, agent=resolver. Existing send keys remain;
via is send/reply/decline, triggering_seq is NULL for send or the stored trigger
for callbacks, invocation_id identifies callbacks, and reason appears only for
decline. resolved_from_task_id is NULL for invocation resolution. ThreadRecord,
list/detail JSON and web types already permit NULL; archive predicates use
resolved_seq and composition, not resolved_from_task_id.

The existing invocation-terminal observer snapshots only ALREADY resolved
threads. Both newly resolving callbacks therefore explicitly evaluate best-effort
auto-archive after COMMIT and normal message/status publication. Archive still
rechecks every live obligation and founder hold. Later MESSAGE clears, founder
resume clears, plain decline/silence/time/task-end never resolve. Founder/dream
threads stay manual. No schema, route, UI or broader answerer authority changes.

The bundled thread skill carries payload examples. Independent code review,
scoped QA, normal hooks and lint/static CI remain required. Python units are
retired; integration is SKIPPED THR-243. Maker behavioral evidence uses only a
throwaway Database and in-process FastAPI app, never the live daemon/DB.
