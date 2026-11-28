# Org secrets hold / restart model (F01)

This finite single-org/single-original-work model covers configured and empty
orgs, locked/cached keys, queued/pending/live/terminal work, a competing hold,
one restart that discards queue hints, unlock, last-secret deletion and redelivery.

| Action | Source correspondence |
| --- | --- |
| Ready / Unlock | OrgSecrets.ready/probe, names-only key-status API and periodic re-probe |
| Discover | Existing startup pending-work discovery, generation-aware deferred task publisher, thread runnable_tokens and producer _released_sources |
| Launch | Dispatcher.task_admission_allowed, Orchestrator.run_step, run_native_producer and run_invocation, followed by existing native ownership/hold validation |
| Restart | Pending source rows retained; safe deferred native producer/thread ownership retains original identity; volatile notification hints disappear |
| Release / Cancel / Finish | Existing independent Pause/terminal/owner gates; secrets recovery grants no new ownership |
| DeleteLast | Locked Delete removes the last configured record; empty org readiness ignores key state |

`NoMissingSecrets`, `OneLaunch`, `TerminalAbsorbing` and `TypeOK` are safety
properties. `Progress` requires a launch or terminal cancellation under explicit
weak fairness for unlock, competing-hold release, discovery, launch and finish.
The restart count is bounded to one. Unavailable scheduling/storage/provider
venues, permanent other holds and sticky unknown owners are outside the progress
assumption. Original identity is one fixed work slot, never a newly allocated task.
The model does not prove Python refinement, keyring/AEAD correctness, filesystem
atomicity, real agent delivery or platform cleanup.

Use the pinned TLC 2.19 / v1.7.4 jar and command from
[the existing formal venue](../task-lifecycle/README.md#run-tlc), substitute
`OrgSecrets` and `current.cfg`, with distinct task-scratch metadirs.
`unsafe.cfg` deliberately removes both readiness checks and must violate
NoMissingSecrets (exit12). `stalled.cfg` disables rediscovery and must violate
Progress (exit13); a model that never launches cannot pass. Keep outside CI.
Actual commands/exits and state counts are recorded in the task evidence/PR.
