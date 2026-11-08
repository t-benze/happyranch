# U2b explicit sequential storage installer/check source

Append-only pointer for TASK-14597 under TASK-14595 / ROOT TASK-14563.
Founder THR-336 msg85 approved TASK-14461 plan revision3 U1–U9 SOURCE;
msg95 continues it. This unit delivers only the explicit operator script,
workflow compatibility-owner installer and maintained contract/runbook parity.

Current contract: [reusable workflows](../../agent-guides/contracts/reusable-workflows.md#u2b-explicit-installercheck-source).
Operator prerequisites and limits: [storage runbook](../../runbooks/sequential-workflow-storage.md).
The original sequential/ownership DDL literals and complete readers remain owners;
U2a history is preserved in 2026-11-08-sequential-storage-u2a.md.

No startup caller, real/live installation/migration, deploy/restart, V3, U3–U9
or ordinary-use authority follows. Disposable SQLite observations are structural
only. Execution behavior and real backup/reopen/backout acceptance are UNVERIFIED.
