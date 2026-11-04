# CI, E2E and test-venue contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

Retained pytest integration collection and execution use deterministic external CLI stubs through
`tests/helpers/integration_parent.py` before pytest/runtime imports. Use disposable
GitHub runners or a separately authorized Mac Linux guest; the live Linux daemon
host cannot run integration tests, including through jobs. See `docs/local-ci.md`
for parent isolation, explicit plans, source/callback identity and bounded two-org
exception observation. General integration remains SKIPPED under THR-243 seq42;
a focused task authorization does not establish full-suite health.

THR-291's separately authorized standalone `e2e/run.py` uses public bootstrap,
registered deterministic external stubs and actual CLI/HTTP/browser boundaries,
without pytest, integration-parent monkeypatches or product hooks. It runs only
on disposable GitHub Ubuntu with exact Python/browser inputs, Node24, bounded
owned-process cleanup and complete variant/cell receipts. Retained pytest
integration/platform/callback selections keep the existing parent fences.
`Product E2E` retains the daily default-branch 19:00 UTC schedule under THR-291
seq69 and admits main-only `workflow_dispatch`. Founder THR291seq277 authorizes
engineering_manager under sole linked ROOT TASK-12445 (resolving superseded TASK-12281/TASK-12262)
to verify concrete reviewed corrections on demand without per-run approval or a
nightly wait, after reconciling active owners/runs. Full independent review,
scoped QA, normal hooks/nonforce publication, guarded merge and exact-main
Source Boundary Lint + Web Lint(Node24) remain required. Historical seq247's
ONE-run allocation to TASK-11897 remains history. No PR-branch/local E2E,
blind same-source retry, new trigger/schedule or automatic retry is authorized.
Scheduled runs38482572306 and38644465643 failed at their own sources; corrected
behavior remains pending an authorized main run. Historical baseline is separate evidence.
Current THR291seq495 / sole ROOT13112 bounded test-harness composition captures
original E05 responses at BrowserRelay and admits E07 after failed E05 only from
complete genuine same-runtime pre-E05/C/N/D checkpoints. Starts29/32/33/36 and
current-host17/20/21/24 select exact source-owned memberships; the only suffix is
two actual P/W starts/four commands/one P result, final31/34/35/38 starts. Healthy
53/36/38/23tasks31results/B24→26 remains the strict original specification. Both
real boots/index1, positive owner/resource/provider/cgroup closure, complete raw
content/public/SQL/files/witness/source joins, safe export and a full50s remaining
in ORIGINAL480 work are mandatory; unsafe/unknown/expired/poisoned states refuse
before allocation, without reset or borrowed reserve. Separate producer groups
and independent scoped readers preserve the first E05 error and later errors;
full56/Health remains strict FAIL for failed/missing evidence. Known open item:
independent browser-copy body/hash/length/full-projection proof is UNAVAILABLE;
healthy E05/full56 remains unmet, never satisfied by relay/public/SQL/DOM copies.
All56 BOTH3.12/3.14,31s/full1s/BOTH50ms/GC/content assertions, original budgets,
TERM/INT/four archives/daily19UTC and lint-only checks remain. See e2e/README.md
for the maintained current contract,26 cases/keeper/four-answer records and actual
source/control evidence; historical observations and execution holds remain.
TASK13130 additionally publishes safely selected sealed originals without requiring
a browser join and retains bounded detached fully redacted scenario/group/first-
cause/admission/finalization claims through later failure publication. Missing or
refused inputs supply no raw rescue or manufactured artifacts; independent readers,
full56 and global Health remain strict. Historical Request/document identity and
positive GET/I/O stop closure remain UNKNOWN/refused, never inferred from callback
timing, same-URL order, BrokenPipe or physical zero owners. Native proof is NOT RUN.
The aggregate also requires real TERM/INT launcher diagnostics with non-success
and complete owned cleanup; product cancellation remains uncovered.
See `e2e/README.md` and `docs/local-ci.md`. Old units remain retired.

Founder THR-291 seq69 makes automated PR/main merge CI **lint/static only**.
Required checks at the final PR head and exact merge on main are exactly
`Source Boundary Lint` and `Web Lint (Node 24)`, both unconditional with no
path applicability. The manager supplies these two names to `pr_ci_waiter`;
`pr_ci_merge` retains its independent review/QA, pinned-head and guarded-merge
contract. Freshly observed GitHub protection has empty required contexts/checks
and `strict=false`; source configuration is not external enforcement.

Default/`all` and `web` run only source boundary lint (all), Node24 colour scan,
ESLint and TypeScript `--noEmit` analysis, with locked `npm ci` setup.
Behavioral tests and builds are outside all/pre-push/required merge checks.
Normal hooks stay enabled; never bypass hooks or the guarded worktree.
Independent full code review and scoped QA remain mandatory. Static checks
cannot establish lifecycle, callback, browser or platform correctness;
behavioral regressions can be detected only later when their coverage executes.

Fresh Product E2E keeps the daily `0 19 * * *` schedule (19:00 UTC /
03:00 Asia/Shanghai) on immutable default-branch `github.sha`. Founder
THR291seq277 authorizes engineering_manager under sole linked ROOT TASK-12445
(resolving superseded TASK-12281/TASK-12262) to use the existing main-only `workflow_dispatch`
on demand after concrete reviewed corrections, without per-run approval or a
nightly wait. Reconcile active owners/runs first; no blind same-source retry loop.
Manual admission requires actual `refs/heads/main`, default branch `main`, and
matching repository/workflow/run/source identity. Historical seq247's ONE-run
allocation to TASK-11897 and its receipts remain history. Baseline56 variants
EACH Python3.12/3.14 plus TERM3.12/INT3.14 retain
exactly four execution artifacts (`e2e-3.12`, `e2e-3.14`, `e2e-cancel-TERM`,
`e2e-cancel-INT`) and the original bounds. Product Python support is unchanged.
Founder THR291seq301(b)/303 replaces only slot44/index43 with
`E04.review-required-refused`: genuine local CLI exit2 plus separate public422,
no effects, then same-SID completed result. No legacy fixture or rejection/wake
parity keeper; other automatic-job wake assertions remain. Current composition
is38 starts=A12+B26,23 tasks/31 results, seven jobs/seven scripts,27 E04 anchors.
Native replacement/protected/sensitivity/full-suffix proof remains UNEXECUTED.
Retain historical hashes/failures/receipts and unknown native ownership.

`Daily Product E2E Health` remains outside merge checks and reports the actual event, run/attempt,
SHA and checkout tree; manual receipts are never labeled scheduled.
There is no local E2E alias, PR/push/release/workflow_call/repository_dispatch
entry, automatic retry, extra schedule or SLA. Admission, lint and scoped QA
success do not establish product execution success. The root owns each actual
dispatch after full independent review/scoped QA, both final-head lints, normal
hooks/nonforce publication, guarded merge and an exact-main two-lint receipt.
Scheduled historical runs38482572306 and38644465643 were observed and failed
at their own immutable sources. Corrected behavior remains **PENDING** until
an authorized main run executes the repair; historical3.13
failures and all-six-cell evidence remain unchanged. Prior first-slice receipts remain
historical evidence at their own source, not daily/final-head verification.
Engineering_manager owns daily failure/missing-evidence triage through existing
tasks/threads; no new notifications or guaranteed response SLA is implied.

Python units stay RETIRED; `python` exits2. General integration is **SKIPPED**
under THR-243 seq42, never PASS. Web Vitest, native/platform, integration,
the named actual CLI callback and their isolation fences retain their sources.
Docs Manual build and the real tenant-isolation harness retain manual-only
workflows, outside PR/main. Manual/tag macOS packaging is a separate release
activity and invokes no fresh E2E. Legacy nightly integration remains separate
at 07:00 UTC, with its historical failures and fixtures unchanged; its manual
`all_only` input runs the same lint/static all selection. No legacy repair or
run is authorized by this cadence change.

See [the reset coverage gap record](../../python-test-reset.md) and
[Local CI](../../local-ci.md) for retained selectors and evidence limits.

Source boundary enforcement: `python scripts/check_source_boundaries.py` and
`python scripts/validate_source_boundaries.py` use only stdlib (local Python 3.12+).
`--source-root <root>` on the checker selects isolated repository-shaped sources.
Only infrastructure/orchestrator `*.py` are parsed; explicit daemon import/from
forms at module time are forbidden, including classes/ordinary flow. Syntactic
top-level TYPE_CHECKING/typing aliases exempt true branches but not else;
function/async-function/lambda nodes are skipped. No allowlist/opt-out, relative/
dynamic import resolution, alias dataflow, callable metadata inspection or
runtime isolation proof. Invalid/missing/empty roots and filesystem/decode/parse
failures return 2; violations return 1 with file:line/target and guidance; clean
scans return 0. No product imports occur. `boundary-lint` and `all` run baseline
plus bounded synthetic CLI validation; unconditional PR/push-main hosted
`Source Boundary Lint` uses Python 3.14 without project dependency installation.
See the project-layout boundary section and [Local CI](../../local-ci.md).

PR1022's TASK10553 continuation retains all 224 C4 parameters and the finite
L/W source. Historical PR shards are superseded by merged PR1025's THR291seq69
lint-only automation; retained behavioral sources are not automatic merge gates. Feature-only phase tracebacks/checkpoints survive earlier
than final teardown; parent/command inner deadlines fail visibly within the
unchanged 90-minute hosted limit. W10 retains exact accessible unforced worker
selection using observed adjacent agent/team text. Held M fixtures no longer
import retired unit helpers, but M/restore/reboot/paired-observer/causal/repetition
proof remains absent. See docs/local-ci.md and the roster migration runbook;
no feature or maintenance readiness follows from source correction.

W10's owned HTTP proxy forwards actual daemon SSE headers and frames as streams.
Finite-request settlement excludes only streams whose headers were forwarded;
browser disconnect/fixture cleanup closes idle upstreams, and a 30-minute stream
deadline fails visibly. `C10-http-prefix.json` survives a failed browser oracle;
it is partial diagnostic evidence, never the completed 29-row browser receipt.
