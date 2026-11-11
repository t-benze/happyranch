# Ordinary audit coding-scan prefilter — THR308seq353/354

> Superseded by THR-308 seq403: the generic audit scan, prefilter, process-local
> watermark and scan diagnostic are removed. Unrecognised coding-shaped audit
> rows and ordinary audit decoding/depth/size/digit failures are no longer
> detected by that scan. Direct history readers and non-audit checks remain.
> The text below records the former behavior, not a current requirement.

TASK-14558, PR3 of the four-part startup-performance plan. This note supplements
the historical C15 discriminator inventory in
`2026-10-14-ordered-coding-workflow.md`; its original bytes remain preserved.
Current behavior belongs to `docs/agent-guides/contracts/coding-workflow.md`.

The optimization stays inside ordinary audit discrimination. SQL returns every
original rowid, action and raw payload, plus a conservative ordinary marker.
Python's original SQLite TEXT decoder runs before acting on that marker, so
invalid UTF8 cannot turn into an accepted placeholder. BLOB/NULL/nontext and
non-ASCII/NUL text retain the original scan. Only the exact action/payload
projection participates; other tables and wider native projections stay complete.

The marker requires small text under both 64KiB and any configured Python integer
digit limit, noncoding scalar action, at most one raw container opener, a leading
object/array, no literal backslash-u, and strict SQLite `json_valid`. Only inside
that guarded CASE does `json_each` check all members, including duplicates,
against keys derived from the existing CODING_PREFIXES/CODING_VALUES/namespace
inventory. Any recognized key falls back irrespective of its value. Every
incomplete/malformed envelope falls back, preserving partial-token recognition
and original refusal codes. The shallow/size guards preserve recursive-visitor,
decoder and integer-digit refusals. Ordinary invalid JSON is never called valid.

Raw SQL byte/character equality alone does not validate UTF8: unconditional
original TEXT acquisition is essential. JSON1 is already used by runtime audit
and task queries. No extension/dependency or connection configuration is added.

Expected coding rows bypass only ordinary watermark/discrimination as before;
they still re-fetch full native values, compare complete reconstructed history,
and spend the same shared candidate/byte/traversal budgets. No row WHERE filter
or MAX shortcut is introduced. File identity, rowid regression, transaction and
success/COMMIT publication rules remain unchanged. PR2 load bookends/reuse,
authority sweep and thread recovery remain outside this change.

The additive `workflow_coding_audit_scan` diagnostic times the successful audit
loop and reports observed rows and its starting watermark. It claims neither
COMMIT/publication nor end-to-end startup time. Existing startup fields retain
their meanings. The standalone command in `docs/local-ci.md` drives the real scan
with/without the SQL projection on disposable synthetic SQLite files. It covers
refusals, accepted coding history, budgets, watermark publication and both sparse
and wider row timings. SQL overhead may outweigh savings on sparse payloads;
production savings require a separately authorized serving-daemon observation.
No schema/index/cache/history mutation, new required check/trigger, integration/
E2E execution, deployment or restart is authorized by this source change.
