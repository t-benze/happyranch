# Workflow source continuation model

This bounded companion extends the task-lifecycle model's explicit workflow gap.
It models four member slots and two atomic callback batches with capacity three,
positive original source release, independent Held/running members, one stopped
recovery, immediate and launched cancellation, carrier drain, inbox consumption,
ordinary dependencies and genuine source done. Result and receipt identities are
abstract batch IDs. Replay/reopen stutter; Disable blocks fresh admission only.
A refused oversized batch is a disabled transition with zero writes.

`WorkflowContinuation.callback_uncommitted` / `preflight_start_uncommitted` map to
Admit; `settle_source_runs` to Settle; dispatcher/hold/control to Launch, Hold and
Recover; `apply_control_uncommitted` / `finish_cancellation_uncommitted` to Cancel
and Return; result-owned delivery consumption to Drain; native done to Done.
The source's one genuine terminal transition abstracts its once-only parent wake.

Capacity abstracts the unchanged complete-history/cancellation reservation; it
proves neither the byte arithmetic nor an eight-member fit. Closure is positive
original evidence, never missing-PID inference. Authority, CAS, per-run counters,
page/receipt bytes, SQLite rollback and providers require the private source
cases; they are assumptions here, not a Python refinement or deployment proof.
The original model and its liveness exceptions remain unchanged.

Use the pinned jar/Java command documented in
[task lifecycle](../task-lifecycle/README.md#run-tlc), replacing the module with
`WorkflowContinuation` and config with `current.cfg` or `negative-return.cfg`,
and a distinct task-scratch metadir. The negative config must violate
SourceFinalOnly when a member return completes its source. TLC results belong in
the PR/task evidence, not a new CI gate. No model-check result is claimed here.
