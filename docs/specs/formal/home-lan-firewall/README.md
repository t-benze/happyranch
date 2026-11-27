# Home LAN firewall effects

Review model for F3's optional activation-journal record. Each port has separate
write-ahead add/delete intent and completion; an add can survive a crash before
completion and be adopted or removed. Initial states cover old journals without
a plan, every collision subset, UFW activity, rejected subnets and lock
availability at helper startup. An unavailable lock skips acquisition before
any tagged rule exists. Crashes retain all durable state. No-effect cleanup
commits pending/skipped ports directly to removed, preserving the evidence that
no rule was attempted until that commit. It needs neither UFW nor its lock;
uncertain native removals still use remove intent. Turn off removes only tagged
state and never foreign rules.
Invariants check preserved foreign rules, journal-before-effect, subnet/activity,
collision non-rewrite and absence after completed removal.

TASK-19599 changes the modeled state machine for R1/R2. `StartWorker`, `Add`,
`ReapWorker`, `OwnerDies`, `Deadline` and `ReleaseCustody` abstract the native
supervisor's process group and inherited activation-directory flock. A worker
may race backend death while custody remains held. A subsequent Turn off starts
only after every earlier worker has been positively reaped and custody released;
an expired original deadline prevents further adds. `WorkerCustody` and
`NoEffectProvenance` check these boundaries. A new Turn off has its own deadline;
the original worker never receives an extended deadline. Native removal remains
atomic in this model. Reaping is a positive proof assumption, not PID absence.

UFW activity and startup lock availability are fixed per model run; the
standalone lifecycle table separately covers making UFW inactive after acquisition and subsequent Turn off cleanup,
plus lost lock access retaining removal intent instead of claiming cleanup.
The model does not prove systemd bind-mount, POSIX lockf, prctl, process-group,
PID reuse, escaped/daemonized descendants or inode behavior. Stock UFW children
are assumed to remain in the worker's process group. The implementation retains
the group leader unreaped until signaling, and subreaps every descendant. R3's
CLI-release/re-acquire/fresh-readback fence is checked separately with real
distro locks and an independent subprocess probe in the standalone venue.
This abstracts UFW's matcher, kernel inventory, custody, audit fsync, native
packet acceptance and process interruption. It proves finite safety properties,
not Python refinement, liveness, external root writers or disposable-VM results.
Use the pinned jar and Java recipe in
[task lifecycle](../task-lifecycle/README.md#run-tlc), from this directory:

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/states-home-lan" -config home-lan.cfg HomeLANFirewall
```

TLC execution is review evidence, outside required lint/static CI. The maker's
task output records the actual command, digest, exit and result; no pass is
claimed here without that receipt.
