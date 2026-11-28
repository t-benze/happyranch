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


## TASK-19228 — HomeRetry

Same pinned jar/hash and OpenJDK 21.0.13.1 above, verified before use.
`retry.cfg` / HomeRetry: TLC exit 0, 712 generated / 184 distinct states,
depth 17, no errors. Negative mutation making OrdinaryStart increment mints
produces the intended NoReadyMint counterexample, exit 12, 20 generated / 16
distinct states. Exact model bytes restored and final current run exits 0 with
the original counts. Logs: dev_agent output/TASK-19228/F-negative.log and
F-current.log. State dirs stayed under task-tmp.

Command from this directory (negative uses its own scratch metadir):

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp /tmp/task19067-tlc/tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir /home/benze/.happyranch/runtime/orgs/happyranch/workspaces/dev_agent/.happyranch/task-tmp/TASK-19228/tlc-restored -config retry.cfg HomeRetry
```

The first invocation used a wrong cwd and could not find retry.cfg (exit 255);
no model claim comes from that failed invocation. The original HomeEnable
model is unchanged and its prior evidence was not repeated. These are bounded
safety checks with the assumptions and limits described in README.

## TASK-19588 fixed port upgrade

Pinned TLC2 2.19/v1.7.4 jar, OpenJDK21.0.13.1, one worker, fp0, seed1.
`HomePortUpgrade` / `port-upgrade.cfg`: exit0; 73 generated/25 distinct states,
queue0, depth8, all four invariants hold. `port-upgrade-unsafe.cfg`: expected
exit12 with NewOnlyAdmission violation after installing the new build and
accepting the old profile. This is bounded model evidence, not native/runtime
acceptance or proof of custody checks. No CI trigger added.
