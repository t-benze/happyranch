# Home LAN firewall effects

Review model for F3's optional activation-journal record. Each port has separate
write-ahead add/delete intent and completion; an add can survive a crash before
completion and be adopted or removed. Initial states cover old journals without
a plan, every collision subset, UFW activity, rejected subnets and lock
availability at helper startup. An unavailable lock skips acquisition before
any tagged rule exists. Crashes retain all durable state. Turn off removes only tagged state and never foreign rules.
Invariants check preserved foreign rules, journal-before-effect, subnet/activity,
collision non-rewrite and absence after completed removal.

UFW activity and startup lock availability are fixed per model run; the
standalone lifecycle table separately covers making UFW inactive after acquisition and subsequent Turn off cleanup,
plus lost lock access retaining removal intent instead of claiming cleanup.
The model does not prove systemd bind-mount or inode behavior.
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
