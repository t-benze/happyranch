# Mac app lifetime — MAC-4b option A

Founder THR-387 seq178; manager TASK-19576 R1/R2. Request reserves synchronously.
Consent is native actual-effects confirmation BEFORE the web lifecycle event.
GateReply is one synchronous final check and reply: no prompt, await, polling or
I/O can occupy the shared adapter's unchanged 5000 ms receipt deadline. It
abstracts current document/target/generation/process identity, all-owner save
summary and native Connection/local custody. A failed check releases only the
unstarted attempt with effect:none; a successful ACK precedes effects and is
not exit success. Uncertain effects retain ownership. Retirement cannot clear a
sent save or a started effect. A new document supplies evidence only for itself.

MacAppLifetime.request/offerConfirmation/respondToLifecycleConfirmation map to
Request/Consent/Cancel. AppScheme's matching gate-bearing owner receipt maps to
GateReply. AppDelegate's synchronous owned-child teardown maps to Stop and
ObserveExit (not a signal-as-exit assertion); Complete is Main close/ordinary
Quit or Sparkle release, exactly once after exit. External processes receive no
signals, but their update still requires gate admission. When idle re-enters
Request and fresh Consent; no earlier click authorizes a later stop.

Round-1 F1 correspondence: Complete grants the update's one platform termination
admission; PlatformTermination consumes it. AbortUpdate maps to the Sparkle
didAbortWithError callback and revokes an unused grant. NewProtectedOwner models
new dirty/pending/unknown-save or native custody after abort. LaterQuit observes
applicationShouldTerminate routing to a fresh gate rather than admitting an
exit from the old update. Historical first-update gate/effect facts remain
unchanged; laterGate is the separate later-Quit admission boundary. This finite
extension does not model the later sheet/receipt or prove Swift execution.

The finite model uses at most two unstarted attempts and one consumed effect.
There is no liveness promise: unknown saves, an unresponsive web process or an
incomplete stop can remain held. It abstracts clocks, all owner generations,
copy/UI, JSON/provenance validation, actor scheduling, actual process exit,
Sparkle behavior and remote stream closure. Native tests and independent Mac QA
remain necessary. There is no launch/handoff transition: Mac recovery Relaunch,
post-exit handoff and packaged C/K declaration/reader/validator are deferred to
a separately approved piece. Sparkle restart uses wire action `relaunch` and
performs its own launch; install-on-Quit uses `quit`, Main close uses `close`.

Use the pinned official TLC recipe in [task-lifecycle](../task-lifecycle/README.md#run-tlc)
(v1.7.4 jar SHA256 936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88).
From this directory, substitute each config and use distinct task-scratch state
directories, one worker, fp 0, seed 1 and a private java.io.tmpdir:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/states-owned" -config owned.cfg MacAppLifetime
```

Run owned and external (expected exit 0) and negative-pre-gate-install (expected
invariant counterexample exit 12), sequentially. The negative models an updater
release before either gate, including an unowned daemon. Results belong in the
PR/task evidence; no CI trigger or new execution venue is added.
Also run negative-aborted-admission (expected exit 12 at LaterQuitRequiresGate):
RetainAbortedAdmission deliberately retains the pre-fix grant after abort and
allows the later protected Quit to bypass its gate. The normal configs revoke
it and explore all four new-owner cases. Native regression execution and native
pre-fix/restored proof remain NOT RUN / PROOF NOT RUN on Linux.

Author TLC evidence (TASK-19582, OpenJDK 21.0.13.1 / TLC 2.19, pinned jar,
one worker/fp0/seed1): owned exit 0 (1179 generated / 522 distinct, depth 10),
external exit 0 (1107 / 474, depth 10), both queues empty. The intended
negative-pre-gate-install exits 12 at EffectsAfterGate (50 / 42, depth 3).
Development first exposed an unparenthesized disjunction assignment (exit 75)
and retirement incorrectly dropping a post-stop reservation (exit 12); both
were repaired before these final complete runs. These are finite-model
results, not a Swift/OS/Sparkle behavior pass. Exact logs remain in
`dev_agent/output/TASK-19582/tlc-*.log`.

Round-1 author evidence (TASK-19594, same pinned jar/JDK/options): owned exit 0
(1331 generated / 598 distinct, depth 14); external exit 0 (1259 / 550, depth 13),
both queues empty. Pre-gate-install negative exit 12 at EffectsAfterGate
(50 / 42, depth 3); aborted-admission negative exit 12 at LaterQuitRequiresGate
(1179 / 534, depth 10), showing release → abort → new unknown save → later Quit
with staleExit TRUE and laterGate FALSE. These are model results only; logs are
`dev_agent/output/TASK-19594/tlc-*.stdout.txt`.
