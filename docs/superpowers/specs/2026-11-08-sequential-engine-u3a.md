# Sequential U3a source pointer

TASK-14616 · ROOT TASK-14563 · immediate parent TASK-14614 · 2026-11-08.
Founder THR336 msg85 approves U1–U9 SOURCE in TASK-14461 plan revision3;
msg95 continues. TASK-14596 `u3-plan.md` sections 1, 2/U3a and 2a define this
unit's ownership union, census and hold-only dispatcher boundary. Base includes
U2b PR1298, `07f0c4b854802b9016e629d2f43fe46303c491d0`.

Maintained contracts:

- [Ownership marks and holds](../../agent-guides/contracts/task-orchestration.md#u3a-sequential-ownership-holds-thr336-msg8595)
- [Reusable workflow U3a boundary](../../agent-guides/contracts/reusable-workflows.md#u3a-ownership-union-and-holding-attachment)
- [Startup/recovery hold](../../agent-guides/contracts/runtime-lifecycle.md#sequential-ownership-startuprecovery-hold-thr336-u3a)

Only recognition and holds ship. No sequential producer/engine, U3b–U3e,
U4–U9, V3 runs, live install/migration, deploy/restart or activation is included.
Structural observation is distinct from behavioral acceptance. Prior failures
and frozen contracts/history remain preserved. This additive pointer supersedes
no historical spec or receipt.
