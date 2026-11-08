# U3d-2 sequential queue, launch and native owner ports

TASK-14727, engineering_manager ROOT TASK-14640, Founder THR336 msg85 U1–U9
SOURCE plan rev3 and msg102 Decision1=B/Decision2=1/SB-1(a)/narrowed F4.
Base b643516d4ce50b1c87d954a30c83ab0ce7999273 (U3d-1 PR1317);
TASK-14625 u3d-design §1.2, adjusted by current manager RD1–RD6, controls scope.
The installed TASK-14711 link is admission metadata; this is fresh work, with
failed histories and consumed allowances preserved. Return only to ROOT14640.

## Resulting source contract

SequentialDispatcher adds selected-visit admission gated on positive source
outer evidence at the existing deterministic PK (visit_id NULL, kind outer),
queue preparation/finish/binding, notification/dispatch, authority-serialized
Pause launch, generic Database claim CAS, U3c session binding, request key,
original handle/PID capture and data-driven node instructions. Source/carrier
remain held. Heartbeat stays unchanged/no-op. U3c owns running/SID updates;
all U3d-1 consumer/helper function bytes remain unchanged.

Shared changes are confined to the named queue/Pause/host/orchestrator/PID/route
functions. worker_started adds exactly one classify; Coding's call is unchanged.
The native journal adds only owner sequential. No retries, deferred recovery,
new audit action/shape, DDL/index/CHECK/column, model, permission, credential,
route or schema/engine/reader change is supplied. Existing U3d-1 route/database/
settle hunks, U3s files, skill paragraph, Coding modules and all other protected
paths remain byte-identical. New dispatcher reads of sequential data use PKs;
existing engine/selected-validator internals retain their supported indexed reads.

The node renderer receives only pinned request/declared inputs, selected immutable
output references with stored recipient checks, and attributed prior findings.
It keeps fixed safety text and untrusted authored JSON separate and preserves the
existing bounded-text input limits; unavailable inputs refuse rather than truncate.
Actual capabilities/access/domain supplements remain U5 prerequisites.

## Assumptions and reconciliations

- A1: U3c owns visit/run lifecycle writes; use merged bind_session_uncommitted,
  not a dispatcher update. Existing U3d-1 consumers remain untouched.
- A2/RD1: renderer format is workflow-sequential-node-brief@1 and the consumed
  callback is workflow-sequential-callback@1. The paragraph already shipped in
  U3d-1; bundled SKILL.md is unchanged.
- A3: reuse generic Database.claim_coding_step_uncommitted for the native CAS,
  with the complete current tasks PK row. No Coding module import or fallback
  Database method is added.
- A4: worker_started remains the sole queue owner hook. One classify selects
  sequential; the existing Coding tuple handles every other task kind.
- RD2/RD3/RD6: locate current symbols after line drift; reuse unchanged U3d-1
  _entry/_reader/_writer/_invocation/_evidence_id/_evidence helpers and merged
  U3c signature. D6's coding_request/coding_running attributes retain the actual
  original sequential host request/handle; names do not change their owner.
- RD4: outer is already accepted by unchanged evidence validators/contracts.
  Require positive=True at the source PK; no source row is written here.
- RD5: leave U3s files and check_venue_start attachment to U3e.

## Backout, evidence and remaining gates

SB-8 consequence: older code refuses journals retaining owner sequential;
**roll back only with no nonterminal sequential Pause entries**. This grants
no journal cleanup or admission authority.

Only structural observations on disposable task-tmp SQLite are authorized:
source gate false/seeded true; worker owner tuple and legacy tuple; zero heartbeat
writes; PID false without live invocation; journal sequential accepted/unknown
refused; source/carrier route held; storage oracle readable; static launch ordering.
Writer costs are measured against33554432 bytes/4096 rows/32768 steps/2.5s.
Actual measurements and immutable-head evidence belong to output/TASK-14727/report.md.
These are structural observations, never behavioral PASS. Real launch, handle,
PID, callback, terminal and outer settlement through the host are UNVERIFIED (V3).
No source-outer producer exists until U3e; U3s is not attached; settings remain
Disabled by default. No live DB, install/migration, settings publication,
deployment/restart, U3e/U4–U9, V3 run, Product E2E trigger or job is authorized.

THR291 required CI is lint/static only: boundary-lint and web; hosted Source
Boundary Lint and Web Lint (Node24) observed once at the exact head, without
rerun/trigger/wait. Python units RETIRED; integration SKIPPED THR243seq42.
Independent full code review, same-head scoped QA and guarded manager merge
remain required. No self-review, QA, approval or merge is performed.
