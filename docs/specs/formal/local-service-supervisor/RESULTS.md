# Actual TLC results — TASK-18525

Run on Linux, 2026-11-22 05:21:03–05:21:09 +08:00. Source mapping:
native `fd266a01342453c32b3acacf68f1ef99cc475dd0`, unchanged by this model/docs
continuation. Model SHA-256:
`341bba9cf30e7621bf71a419dba2ce098e4f145e585d301b922809ee6d282603`.

Downloaded official tla2tools **v1.7.4**, verified jar SHA-256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.
Download and hash commands exited 0. Runtime: existing `/usr/bin/java`,
OpenJDK `21.0.13.1+2-1-26.04.1-Ubuntu`; TLC `2.19`, revision `5a47802`.
No tools were installed. All runs used `-XX:+UseParallelGC -Xmx512m`,
`-workers 1 -fp 0 -seed 1`, a distinct absolute task-scratch `-metadir`, and
task-scratch TMPDIR/Java temporary directory outside the repository.

| Config | Exit | Generated | Distinct | Queue left | Depth | Wall seconds | Actual outcome |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `service-local.cfg` | 0 | 148794 | 34330 | 0 | 18 | 2.638 | All six invariants and MutationEventuallyReleases: no error found |
| `service-remote.cfg` | 0 | 148794 | 34330 | 0 | 18 | 2.536 | Same checks with remote selected: no error found |
| `foreground.cfg` | 0 | 146 | 16 | 0 | 4 | 0.672 | Unsupported reads/no-effect mutation refusal and unchanged child; liveness vacuous because nothing admitted |
| `negative-skip-recheck.cfg` | 12 | 2516 | 577 | 233 | 8 | 0.610 | Expected invariant counterexample: NoStateChangedWithStaleBearer |

The three positive stdout streams end with `Model checking completed. No error
has been found.` Temporal checking completed over the complete finite graphs.
The negative stdout says `Invariant NoStateChangedWithStaleBearer is violated.`
Its eight-state trace is:

`Init → Request(Restart) → Consent(TRUE) → WorkerGate(TRUE) → Effect → Readiness
(bypass Recheck) → RefreshBearer → Publish`.

At the final emission: `state="ready"`, `checked=FALSE`, `bearer="fresh"`,
`ready=TRUE`. Thus a newly refreshed bearer alone cannot justify ready before
compatibility recheck. `SkipRecheck=FALSE` in both positive service configs
checks the restored path, including Restart, over the larger unconstrained
command set. The negative config intentionally stops early on a safety failure;
it is not a passing design or a liveness result.

No real native defect was found in the modeled source sequence, and no Python
production/test file was changed in this continuation. The model assumes the
source mutex/queue boundaries; it does not execute or refine Python. Actual
systemd/logind effects, GTK consent/layout, token custody, frame admission and
GLib delivery remain outside this proof. Conditional fairness and all finite
bounds are described in [README.md](README.md).

Exact argv, cwd, durations, model/config hashes and stdout/stderr byte counts
and SHA-256 are retained in dev_agent `output/TASK-18525/{service-local,
service-remote,foreground,negative-skip-recheck}.json`, with each corresponding
small `.stdout.log`/`.stderr.log`. The scratch-only runner does not add a repo
verification framework. Preliminary runs also passed/reproduced before the
negative config was limited to safety and Java temp extraction was explicitly
redirected; the table and retained receipts describe the final config runs.
Every run finished in under three seconds; no TLC job was necessary.
