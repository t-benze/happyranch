# Linux local-service supervisor: bounded TLA+ model

S7-L2 / THR-387, PR #1815. Source correspondence includes round-2 native
status-admission cancellation in `AppScheme.local_service_changed/refresh_status`
(TASK-18530), extending the original fd266a013 supervisor sequence.
This satisfies the same-PR model requirement in the
[coding practice rules](../../../agent-guides/contracts/coding-practice-rules.md).
[Actual TLC results](RESULTS.md) include broken recheck and status-fence scenarios.
No production dependency, CI job, test/build trigger or merge gate is added.

## State and source correspondence

Source paths below are relative to `packaging/linux/desktop/`.

| State | Source / meaning |
| --- | --- |
| `issued`, `active`, `operation`, `phase` | Two abstract callers, one attempt each; `LocalControls.operation`, `unresolved`, lock and requests queue in `happyranch_window.py`. `active` includes consent and unresolved effects, not just a running subprocess. |
| `service.running` | `LocalControls.pending_status/observe`; `ServiceMode.start/restart/wait_for_attachment/close`. Verified service attachment is distinct from compatibility and bearer readiness. |
| `service.enabled`, `linger`, `owned` | `ServiceMode.set_keep_running/lifetime_state/app_set_linger`. KeepOn and Repair enable service+linger; KeepOff and TurnOff disable the unit and undo only app-owned linger. They do not stop the service. |
| `transport.checked`, `bearer`, `ready`, `failure` | `AppScheme.local_service_changed` in `app_scheme_webkit.py`; `LocalDaemon.reset/bootstrap/_prepare` (including inherited `_Target` methods) in `app_scheme.py`. Booleans/symbols stand for freshness; no credential values exist in this model. |
| `surface` | Main document, generation, owners, candidate, selected local/remote target and foreground owned child. These local actions never replace Main/target or signal the fallback child. |
| `readResult`, `reply` | Closed abstractions of local.status/update.status and mutation replies; not full schema-2 JSON. Busy refusal includes `busy/none/refresh`; foreground mutation refusal includes `unavailable/none/nativeRecovery`. |
| `emission` | Snapshot at an abstract `app.stateChanged` emission of local transport admission, including named mismatch/unreachable/busy. `ready` is not keepRunning=on. A separately ready selected remote may remain ready while local admission fails. |
| `background`, `interrupted` | One background `AppScheme.refresh_status` worker, allowed to pause before bootstrap or GTK completion; its cancellation Event is set before local reset. `local_changing` blocks admission until supervisor readmission; completion checks the captured cancellation/token identity. |

The initial state has a running local service, fresh cached admission and either
absent or externally owned linger. Stop then Start, repeated Restart, and
keep-running changes are explored within the two-attempt bound. The foreground
config has a remote selection and an owned running child; reads are unsupported
and mutations have no effect. Foreground refusal is also independent of target
selection in `Request`.

## Action map

| Action | Implementing source at the native head |
| --- | --- |
| `Request` | `LocalControls.request`: lock, reserve before nested GTK consent, competing busy refusal, foreground refusal. Called by `AppScheme.bridge_message`'s admitted local command path. |
| `Consent` | `LocalControls.request`, `run_gui.confirm_local`: safe cancellation releases reservation, accepted work queues once. Interleaving reads/second requests can occur during consent. |
| `WorkerGate` | `LocalControls.process` current-document/final gate; `AppScheme.bridge_message.gate`, `effect_refusal`, `local_service_changed`: freeze/reset local transport under the final owner/effect lock before service effects. |
| `Effect` | `LocalControls.process`, `ServiceMode.start/restart/close/set_keep_running`; Repair maps to true, TurnOff to false. Stop and Restart leave keep-running unchanged. |
| `Readiness` | `ServiceMode.wait_for_attachment`, then `run_gui.local_changed` → `AppScheme.local_service_changed(recheck=True)`, which resets again and uses existing bootstrap. |
| `Recheck`, `RefreshBearer` | `LocalDaemon.bootstrap` health compatibility, then `_prepare` loopback bootstrap. `SkipRecheck` exists only in this model, as a negative control. |
| `NamedFailure`, `Publish` | Bootstrap `SchemeError` retains named failure; `local_service_changed` queues `emit_state`, which serializes `AppScheme.status`. Successful local readiness is emitted only after check and refresh. |
| `Finish`, `UnknownEffect` | `LocalControls.process` fresh post-effect observation releases slot or retains exact unknown owner with `outcomeUnknown/unknown/nativeRecovery`. |
| `Read`, `Reconcile` | `LocalControls.request/process/observe`: status/update reads do not acquire a slot; in-flight reads return pending state. Only explicit successful local.status clears unresolved outcome; update status cannot clear it. Failed inspections/read attempts may stutter. `run_gui.supervise` suppresses automatic status reconciliation while unresolved. |
| `StatusAdmission`, `StatusBootstrap`, `StatusCompletion` | `AppScheme.refresh_status`: captures a caller cancellation token before launching; bootstrap checks that token even if it begins after transport reset. Queued completion discards cancelled admission. `WorkerGate` cancels that token before the effect. `SkipStatusFence` disables both checks only in the negative model. |

## Properties and limits

- `AtMostOneMutationInFlight` and `SlotTracksMutation`: reservation covers
  consent, queue, effect, readmission and unknown recovery. Two callers can
  compete before consent completes; cancellation releases without effects.
- `NoStateChangedWithStaleBearer`: every emitted local-ready snapshot contains
  successful readiness, compatibility recheck and refreshed bearer. Failed
  admission may emit its named failure. The snapshot remains a historical event,
  not a claim that an earlier event stays current after later invalidation.
- `ForegroundNeverMutates`: service configuration and owned child stay intact.
- `NoStaleLocalCache`: an interrupted background worker cannot cache an old-service
  bearer. `NoReadyDuringInterruption` records interruption at emission time and
  forbids background ready publication before supervisor readmission. Resetting
  the transport alone cannot protect a worker paused before bootstrap.
- `MainAndRemoteUnchanged`: local actions preserve document/generation,
  owners, candidate and selected target even when remote is active.
- `MutationEventuallyReleases`: each admitted caller eventually leaves active.
  Weak fairness of `Progress` assumes native consent resolves and queued work,
  timeouts and observation finish. Weak fairness of `Reconciliation` assumes
  a user eventually requests explicit local.status and fresh inspection succeeds
  after an unknown outcome. Without these assumptions an unresolved operation
  intentionally retains the slot indefinitely. There is no unconditional or
  bounded-time release promise and no automatic mutation replay.

This is a finite design model: two callers with one attempt each, seven command
variants (the Boolean keepRunning argument is split), local/remote selection
checked in separate configs, one background worker with admission/bootstrap/
completion phases, and no target changes during a run. Mutex admission,
one supervisor and source current-owner checks are atomic abstractions, not a
proof of Python locking, frame/origin security or GTK scheduling. The final gate
collapses owner/document refusal categories. Systemd/logind provenance, consent
text, rollback substeps, partial enable/linger observations, process crashes,
external changes, cancellation during effects, networking, token custody,
transport epoch internals and individual GLib delivery mechanics are not modeled.
Caller cancellation and queued completion are represented at their observable
boundaries. A stale symbol denotes an old-service bearer; no credential exists
in the model. Background failure outcomes are collapsed; supervisor named
failures remain explicit. Worker completion fairness is not required for slot
release. A second local interruption cancels the same bounded worker again.
Unknown effects conservatively hold the slot; reconciliation abstracts a fresh
successful observation, not authority to infer success. Stop/keep-running status
event details and update-hint values are collapsed. Deadlock checking is disabled
because terminal stuttering and waiting for a human are valid; temporal checking
remains enabled in all positive configs.

TLC explores this model, not Python. Passing finite checks does not prove live
service effects, real Main preservation, unbounded concurrency or deployment.
Existing native tests and independent scoped QA retain those responsibilities.

## Run TLC

Use the venue and pinned official tools from
[task-lifecycle Run TLC](../task-lifecycle/README.md#run-tlc).
Download the jar to task scratch with the documented `curl -fL` command and
verify SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`
before running it. Set `TASK_TMP` to an absolute task scratch path outside the
repo; create `$TASK_TMP/t`, set `TMPDIR=$TASK_TMP/t` and
`JAVA_TOOL_OPTIONS=-Djava.io.tmpdir=$TASK_TMP/t` (no use of quota-limited /tmp).
From this directory:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/states-service-local" -config service-local.cfg LocalServiceSupervisor
```

Run all configs **sequentially**, with a private `java.io.tmpdir`; concurrent
standard-module extraction has produced parser NPEs. Repeat for `service-remote`,
`foreground`, `negative-skip-recheck`, and `negative-skip-status-fence`, using
distinct `-metadir` paths outside the repo. Both negative configs restrict
mutations to Restart and check safety only, not liveness of a constrained graph.
The first bypasses recheck; the second retains recheck and bypasses status
cancellation/completion fencing. Expected exit is 12 with the respective
freshness/cache counterexample; neither is a passing design. Positive configs check all listed
invariants and liveness. Use a durable job if expanded runs can exceed a minute.

## Native application Quit and Relaunch (S7-L3)

`AppLifecycle.tla` complements the service-only supervisor model. It models
the new ordinary Quit-then-launch behavior in `run_gui`: one document-bound
request, Keep vs current clean Proceed, retained uncertainty after replacement,
separate explicit recovery consent, compatible installed-assets admission,
existing keep-running policy, failed-stop custody/retry, and launch only after
supervisor completion and lock release. Local/remote selection uses the same
local-service policy; neither selection authorizes a remote Host stop.

The model abstracts the validated transport/document/requestId tuple as
`pending`; it does not model JSON parsing, GTK reentrancy or HTTP. `Replace`
represents reload, revocation or WebKit death. `Publish` abstracts an original
document's save/discard/result observation; it cannot clear the retained latch.
`RecoveryExit` is a human's explicit loss-warning acceptance, never timeout.
Native service observations, compatibility and confirmed unchanged installed
assets are atomic inputs; no live systemd/logind, filesystem provenance,
networking, credentials or package-updater proof follows. Unknown/unattached
local service on remote-only launch has no local service effect and is outside
the attached-service model. The model is safety-only and promises no eventual
exit, restart, reconnect or work resumption.

Run sequentially with the same pinned jar and private short non-/tmp TMPDIR:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/states-app-local" -config app-local-off.cfg AppLifecycle
```

Repeat for `app-remote-on.cfg` and `app-negative-skip-assets.cfg` with distinct
metadirs. The negative deliberately bypasses compatible-assets admission;
expected exit 12 violates `RelaunchNeedsAssets`. Actual results are in
[RESULTS.md](RESULTS.md). No CI gate or automation is added.

THR-387 seq146 entry parity: the web app-incompatible needs-update screen uses
closed `app.relaunch` only with explicit `app_relaunch:true`. It maps to the same
`Request("relaunch")`, not a new transition. Document Retry maps to replacement
and never an effect; explicit Exit anyway in a blocked native prompt maps to
`RecoveryExit("quit")`. The model does not prove JSON parsing, support gating or
UI placement. The source entry map is updated in AppLifecycle.tla; its unchanged
safety graph is rechecked for this delivery, separately from behavioral tests.

TASK-19091 recheck (2026-11-25): official v1.7.4 jar with the pinned SHA-256
above, TLC 2.19, one worker, fp 0, seed 1. Both app-local-off.cfg and
app-remote-on.cfg exited 0: 6490 generated / 3768 distinct states, depth 13,
empty queue and no safety error. app-negative-skip-assets.cfg exited 12 with
the intended RelaunchNeedsAssets counterexample. Entry-map comments alone
changed; model transitions and checked invariants are unchanged. Logs are in
output/TASK-19091, not a claim of native UI or effect proof.
