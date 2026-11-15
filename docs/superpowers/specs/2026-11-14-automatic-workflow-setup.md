# Automatic workflow setup — incremental delivery

Founder THR-336 message 194 assigns full physical integrity to installation
and a separate daily report, while retaining complete logical validation on
loads. The approved design is TASK-16078 design v6 (SHA256
`5f2205bbdde16f016f27b9becf7f0ac2d9205831f904dc08f19e1f7820382497`),
reviewed in TASK-16087. Root TASK-16061's `i1-split-ruling.md` separates the
install gates (I1a) from the daily read-only advisory report (I1b).

## I1a: mandatory full integrity during offline installation

`runtime/infrastructure/workflow_integrity.py` owns the explicit native full
`PRAGMA integrity_check`. Success requires exactly one single-cell text `ok`
row followed by normal cursor completion. Non-ok results and corruption errors
fail; malformed results, interruptions, SQL exhaustion, expired deadlines,
busy locks and I/O failures are unavailable. Neither disposition can pass.
Returned corruption text, SQL, native errors and cells are never published.
The helper has no daemon dependency, does no writes and supports read-only,
query-only snapshots for I1b's later caller. Its caller owns the progress slot.

One operation owns one cumulative integrity ledger of at most 640,000,000 VM
steps. Reservations precede execution and each progress interval (at most 1000
steps); unused final reservations stay charged across checks. The absolute
deadline is clipped to the original operation deadline; the offline operation
retains its 900-second ceiling. This ledger is separate from L32M, A/B/C2M,
logical limits, inventory and installer ownership allowances. Creating a ledger
without `SequentialInstallAllowance` never selects that allowance's larger
ownership/inventory acquisition. Complete-reader and load policies are unchanged.

The offline CLI and owning primitive require these checks in order:

1. The coherent read-only source, before accepting the operator's backup.
2. The independently hashed backup reopened read-only/query-only, before
   accepting it as verified or opening a source writer.
3. The source in the actual writer snapshot after `BEGIN IMMEDIATE`, before
   copying or rebuilding storage.
4. The actual writable target transaction after complete target and original
   preservation validation, before the final offline/identity checks and any
   COMMIT attempt.

The offline CLI still consumes an operator-created checkpointed SQLite backup;
it creates no backup, durable `backup_verified` phase, fsync/permit coordinator,
size rehearsal or automatic bootstrap caller. Those existing operator contracts
remain in force; the later automatic bootstrap caller belongs to A4.

Failures expose only `install_integrity_failed stage=source|backup|writer_source|target`
or `install_integrity_unavailable stage=... cause=sql_budget|deadline|busy|io|invalid_result`.
Before writes, refusal has zero install effects. The primitive owns rollback;
after writer acquisition the CLI closes that connection and independently
reopens read-only to prove original SQL/typed-cell/rowid inventory equality.
Only proved pre-COMMIT preservation is `not_installed` (exit 1); failed rollback,
close or equality proof is `unknown` (exit 4). Attempted COMMIT retains its
existing uncertainty contract. No post-COMMIT physical veto, restore, retry or
compensating DDL is introduced. Backup bytes remain untouched.

## Delivery boundaries

Inline load-time full integrity checks remain unchanged until I2. Redundancy
during I1a through I2 is intentional. Full FK validation, parts 2–7, L32M,
separate A/B/C2M allowances, logical caps, five-second complete-reader deadline
and existing load callers remain unchanged. Installed primary capacity remains
**EXPECTED REFUSAL — part 1 retained until I2**, never a success claim here.

The daily advisory report is I1b; this slice adds no monitor, scheduler or daemon
entrypoint. I2 depends on both I1a and I1b and the separately assigned status
visibility work. No live installation, restart, migration, role/admission change,
workflow run or Product E2E authority follows source delivery.

## Verification venue

Extend `scripts/test-workflow-schema-load.sh`, including its focused
`--install-integrity` selection. Four gate cases use a genuine native corrupt
index result while preceding gates remain real; observe refusal, original
inventory/hash equality, zero pre-write install DML or owned rollback, and zero
COMMIT attempts. Also cover rollback/equality uncertainty, malformed/interrupted
delivery, busy/I/O, cumulative SQL/deadline exhaustion, sanitized output/audit
and clean end-to-end install/reopen. Test-side fault injection adds no production
success stub or test-only parameter. Gate-removal proof runs in scratch with
byte-exact restoration on Python 3.12 and 3.14.

The full existing shell venue on both runtimes and `scripts/local_ci.sh all`
run as one durable own-task job at the final clean head. Actual terminal
show/output receipts are required before claiming those checks pass. Required
hosted checks remain exactly Source Boundary Lint and Web Lint (Node 24).
