# Full sequential-history reader — Decision 2

TASK-14734 under ROOT TASK-14640 implements Founder THR336 msg102 Decision 2,
per accepted TASK-14643 d2-reader-design-rev4.md SHA256
02938d04e0121909307a1a088152b70e598fa7828029652880f52364f81d5caf
(design APPROVE TASK-14671; design sizing QA PASS TASK-14676).

The current [storage contract](../../agent-guides/contracts/reusable-workflows.md#u2a-storage-and-standalone-reader-boundary)
separates observed schema/native metadata A, independent reference/comparison B,
and data/run-scoped ownership C. Each has the existing 8192-row, 64 MiB,
65536-step and 2M SQL ceilings. L meters org-wide zero-row guards and retained
legacy SQL at 32M; its acquired rows/bytes/steps consume C. All share the same
original five-second deadline and accumulate without reset. The fixed literal
reference prelude remains deadline-bracketed only. Schema comparison, literal
DDL, supported layouts, per-value guards and default legacy readers are unchanged.

This replaces the single cumulative allowance/shared handler described in
2026-11-08-sequential-storage-u2a.md (lines 17, 25–28, 41–44). The known
TASK14631 small-org limits and open Decision2 wording in
2026-11-08-sequential-engine-u3b.md (lines 22–28) describe the reader at U3b's
head. D2 supersedes those limits for the current contract. Those append-only
historical paragraphs retain their original-head meaning and are not rewritten.

Correction to accepted design section 6: its claim that one bounded fresh run
fits the old reader was disproved by TASK-14733. A valid engine-built Coding-data
returned history with 10 visits, 3 epochs and 2 content returns exhausted the old
combined allowance at row 8193. It blocked the U3e-2 return consumer; D2's
structural regression observation covers this case. No V3 acceptance follows.

F-A: retained legacy SQL remains O(org). F-C: non-run type/size/NUL/decode
predicates also remain org-wide SQL work under L, while whole-org row acquisition
and ownership traversal are removed from C. Both are disclosed engineering
choices inside Decision 2, with no decision requested. F-B: C still bounds
cumulative history and payloads; a later Founder-owned capacity decision is
reserved before ordinary production use. This PR makes no capacity decision.

Evidence is disposable structural observation only, never behavioral or V3 PASS.
No live DB, installation, configuration, migration, deployment, restart, admission
or U3e/U4–U9 capability is authorized. Independent full review, scoped QA,
lint/static checks, normal hooks and guarded manager merge remain required.
