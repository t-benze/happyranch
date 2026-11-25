# Mac actor waiting during Quit and CLI execution

Bounded source-design companion for TASK-19059 / PR #1863, required by the
[coding practice rules](../../../agent-guides/contracts/coding-practice-rules.md).
It addresses the queued actor dependency; it does not diagnose build 76.

`quit.cfg` starts after a Restart whose child survived Stop. `Quit` maps to
AppDelegate.applicationWillTerminate clearing pendingRestart before teardown;
`ChildExit` maps to QuitRetryHandle's retry TERM delivering the registered launch
callback. `LeaveWait` maps to synchronous observed-exit settlement followed by
the test awaiting another actor job. `RunQueued` maps to the queued launch
settlement. Quit must not launch a replacement, and queued work must settle.

`cli.cfg` starts with AppEntry's operation queued to MainActor. `LeaveWait`
maps to awaiting operation.value, releasing the actor so the operation can run.
No credential, connection or signal behavior is modeled or changed here.

The negative keeps the actor busy while waiting for its queued job. It must
violate QueuedWorkSettles. This abstracts an unreturning nested wait; the old
test's nominal one-second bound and AppKit run-loop scheduling are not modeled.
It is not a reproduction or proof of the native 238s stall. Child exit is an
input, not inferred from TERM/KILL. Grace timers, OS process observation,
AppKit reentrancy, Swift scheduling, cancellation, other launches and toolbar
state are outside this finite model. Weak fairness assumes enabled native work
can run. TLC is review evidence, not Swift/native execution or a CI gate.

Use the pinned jar and scratch setup in
[the existing TLC venue](../local-service-supervisor/README.md#run-tlc), then:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/mac-quit" -config quit.cfg MacActorWait
```

Run cli.cfg and negative-nested.cfg sequentially with distinct metadirs.
Positive exits must be 0; the negative must exit 13 with QueuedWorkSettles.
Actual commands/results are recorded in the maker handoff and PR body.

Executed on Linux with the pinned jar (SHA-256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`),
TLC 2.19 and OpenJDK 21.0.13.1:

| Config | Exit | Result |
| --- | --- | --- |
| quit.cfg | 0 | 5 distinct states; safety and queued settlement hold |
| cli.cfg | 0 | 3 distinct states; queued settlement holds |
| negative-nested.cfg | 13 | 3 distinct states; expected temporal counterexample, actor busy with queued work |

These results establish only the finite abstract dependency. Swift compilation
and the repaired native tests remain NOT RUN until separately authorized QA.
