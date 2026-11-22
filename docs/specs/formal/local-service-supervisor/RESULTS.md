# Actual TLC results — TASK-18530, round 2

Linux, 2026-11-22 06:23–06:25 +08:00. Model includes the background status
worker missed by the original TASK-18525 model. Source correspondence and bounds
are in [README.md](README.md); this is finite design evidence, not native QA.

Reused official tla2tools **v1.7.4**, verified SHA-256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88` (exit 0).
Existing `/usr/bin/java`: OpenJDK `21.0.13.1+2-1-26.04.1-Ubuntu`, TLC `2.19`,
revision `5a47802`. No tools installed. All five runs were **sequential** with
`-XX:+UseParallelGC -Xmx512m -workers 1 -fp 0 -seed 1`, distinct absolute metadirs,
TMPDIR `/home/benze/.t/TASK-18530` and private
`-Djava.io.tmpdir=/home/benze/.t/TASK-18530/java`. No concurrent parser/temp race.

| Config | Exit | Generated | Distinct | Queue left | Depth | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `service-local.cfg` | 0 | 871468 | 172758 | 0 | 20 | Eight invariants and conditional MutationEventuallyReleases; no error |
| `service-remote.cfg` | 0 | 871468 | 172758 | 0 | 20 | Same checks with remote selected; no error |
| `foreground.cfg` | 0 | 146 | 16 | 0 | 4 | Unsupported reads/refused mutations; unchanged owned child |
| `negative-skip-recheck.cfg` | 12 | 6189 | 1312 | 565 | 8 | Expected NoStateChangedWithStaleBearer counterexample |
| `negative-skip-status-fence.cfg` | 12 | 1878 | 423 | 231 | 6 | Expected NoStaleLocalCache counterexample |

Positive service runs took about 12 seconds each; the remaining runs each took
under a second. All positive runs completed temporal checking of their finite
graphs. Negative runs intentionally terminate early on invariant failure and
are not passing designs or liveness results.

The new negative trace admits a status worker before interruption, pauses it
before bootstrap, accepts Restart, then starts the worker after WorkerGate has
reset local transport. With cancellation disabled, it caches an old-service
bearer and marks transport ready while `interrupted=TRUE` and `phase="effect"`.
This maps to review F1's production interleaving. With SkipStatusFence=FALSE,
bootstrap and queued completion discard cancelled admission; both positive
service configurations explore the interleaving without a stale cache/event.
The older recheck negative still demonstrates that bearer refresh alone cannot
establish compatibility.

Four authoring answers for affected configs/model: (1) observable design
contracts are no stale local cache/ready emission, single mutation slot, retained
Main/remote, foreground no-effect and conditional slot release; (2) credible
regressions are dropping status cancellation or compatibility recheck; (3) the
old model omitted background admission, and native tests cover actual Python
rather than all bounded schedules; (4) no test-only production seam exists.
The two negative controls are model constants only. The attributable negative
exits above and restored positive graphs supply fail/pass evidence. Existing
native tests independently execute the shipping boundary.

Exact invocation pattern (from this directory), replacing CONFIG with each name:

```sh
TMPDIR=/home/benze/.t/TASK-18530 /usr/bin/java -Djava.io.tmpdir=/home/benze/.t/TASK-18530/java -XX:+UseParallelGC -Xmx512m -cp <workspace>/.happyranch/task-tmp/TASK-18525/tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir <workspace>/.happyranch/task-tmp/TASK-18530/states-CONFIG -config CONFIG.cfg LocalServiceSupervisor
```

Full small logs and byte counts/SHA-256 are retained under dev_agent
`output/TASK-18530/tlc-CONFIG.log` and `manifest.json`. Real service/logind,
credential custody, GTK layout and unbounded concurrency remain outside this
model. No CI gate or automation is added.

## S7-L3 application lifecycle — TASK-18557

2026-11-22 11:12 +08:00, same verified jar SHA-256 and TLC 2.19 as above.
Sequential `-XX:+UseParallelGC -Xmx512m -workers 1 -fp 0 -seed 1` runs used
existing `/usr/bin/java`, private `/dev/shm/hr18557` TMPDIR/java.io.tmpdir
and distinct metadirs. The directory was created in the same invocation.

| Config | Exit | Generated | Distinct | Queue left | Depth | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| app-local-off | 0 | 6490 | 3768 | 0 | 13 | Six safety invariants; no error |
| app-remote-on | 0 | 6490 | 3768 | 0 | 13 | Same policy under remote selection; no error |
| app-negative-skip-assets | 12 | 9 | 7 | 5 | 2 | Expected RelaunchNeedsAssets counterexample |

All completed in under one second each. An earlier draft exited 75 because
an unparenthesized disjunction left `uncertain` unassigned; it was corrected
before the recorded positive runs. That failed draft is retained in the task
output and is not passing evidence. Small actual logs are in dev_agent
`output/TASK-18557/tlc-app-*.log`; finite abstractions and exclusions are in
README. Existing service-only supervisor behavior/model is unchanged.
