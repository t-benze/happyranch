# Mac app-owned local controls

MAC-3, Founder THR-387 seq164. This finite safety model covers the app-owned
child dispatcher, not launchd or Linux service policy. Two callers each issue
one explicit request. Request reserves the single slot before Consent; competing
requests refuse without queuing. Effect represents AppDelegate’s existing owned
child start/stop/restart. Readmission represents fresh AppAPIHealth admission
through LocalAppAPITransport/LocalTokenCredentialProvider. No bearer exists in
the model; Mac rereads daemon.token on each request.

`MacLocalControls.call` maps Request/Consent/Publish/FailedEffect/Reconcile to
native reservation, Cancel-default sheet, fresh result, retained recovery and
explicit local.status. LoseDocument abstracts the current-document closure
becoming false. Native custody belongs to AppDelegate and outlives AppScheme.
Read is always enabled and never releases an executing owner. AppScheme’s
localServiceWillChange/localServiceDidChange preserve target/generation/owners
and fence cached admission; remote selection receives no local publication.

Checked invariants: slot coverage through consent/effect/readmission/recovery,
unknown-owner retention, external refusal, no automatic replay, fresh readiness,
and Main/remote preservation. `negative-release-on-loss` deliberately frees an
unknown owner and must fail SlotCoversEffect/UnknownRetainsOwner. There is no
liveness promise: without fresh observation and a new explicit status request,
unknown custody intentionally remains held.

Limits: this is not a proof of Swift actor isolation, nested AppKit run-loop
execution, WebKit provenance, OS signal/exit behavior, actual health/token I/O,
JSON parsing, consent copy or window layout. Epochs abstract compatibility only;
source revision fences and stale reconnects also have native tests. General
failures are collapsed; installed-update hints and unsupported lifetime controls
have no effect transition. Native execution and real-Mac QA remain required.

Use the existing pinned official TLC v1.7.4/2.19 recipe in
[task-lifecycle](../task-lifecycle/README.md#run-tlc), SHA-256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.
With TASK_TMP an absolute task scratch directory outside the repo, run from here:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/states-local" -config local.cfg MacLocalControls
```

Run local, remote, external and negative-release-on-loss sequentially with
separate metadirs and a private java.io.tmpdir. Results are recorded below.
No CI trigger or merge check is added.

## Author TLC result (TASK-19311)

Official jar hash above verified; OpenJDK 21.0.13.1, TLC 2.19, one worker,
fp 0, seed 1. local and remote: exit 0, 317 generated / 108 distinct states,
depth 12, empty queue. external: exit 0, 29 generated / 12 distinct states,
depth 5, empty queue. negative-release-on-loss: expected exit 12, violation of
SlotCoversEffect after document loss (57 generated / 28 distinct, depth 5).
Initial author runs exited 75 for an incompletely specified Consent action;
parenthesizing that conditional corrected the model, then every config above
was rerun. These are model results only, not native execution. Small logs are
retained in dev_agent output/TASK-19311/tlc-*.log.
