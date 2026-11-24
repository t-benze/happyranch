# TLC results — TASK-19025

Checked on 2026-11-24 with `/usr/bin/java`, OpenJDK 21.0.13.1, and official
TLA+ tools v1.7.4 / TLC2 2.19 (revision 5a47802). Jar SHA-256:
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.

| Config | Exit | Result |
| --- | --- | --- |
| current.cfg | 0 | All seven invariants pass; 68 generated / 64 distinct states, depth 6. |
| negative-late-renewal.cfg | 12 | NoStartBeforeRenewal counterexample; 49 generated / 49 distinct states. |
| negative-noncurrent.cfg | 12 | StartRequiresCurrent counterexample; 63 generated / 58 distinct states. |

Each command completed in under one second. From this directory:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp /tmp/TASK-19025-tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir /tmp/TASK-19025-states-final -config current.cfg HomeEnable
```

Negative runs substitute the named config and a unique scratch metadir.
The initial authoring attempt had an unassigned Boolean in Init (exit 75);
explicit Boolean assignments corrected it before these final runs. That
invocation error is not claimed as a negative invariant result. Actual negative
traces are retained in dev_agent `output/TASK-19025/negative-late-renewal.log`
and `negative-noncurrent.log`. This finite model does not prove source execution,
installed runtime or unbounded behavior; see README limits.
