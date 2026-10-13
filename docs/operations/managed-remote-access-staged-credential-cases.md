# PR800 staged credential and mode cases — THR228seq428

Authority: sole root TASK10989, direct maker TASK10991. These are finite source
case records, not executable tests or execution evidence. Every row supplies
four answers: affected behavior, actual production seam, expected outcome and
how a failure would be detected. All behavioral runs, red/green, causal controls,
mutation, repetition and executable QA are **UNEXECUTED/SUSPENDED**, nonblocking
for source delivery. Python units are **RETIRED**, general integration is
**SKIPPED THR243seq42**. Static inspection cannot establish assertion sensitivity,
ACL custody, observed mode, actual readiness, cleanup, timing or lifecycle PASS.
Independent full code review and permitted source/static QA remain later gates.

See [the maintained contract](managed-remote-access-linux-package.md) for the
provider/delivery chain, pinned systemd255 source and observation limitations.
The former failure at8c34a6ad/run38232079005/attempt1 remains historical:
original-process no-listener samples, no numeric staged mode and five unavailable
internal gates. No new run or sole-cause diagnosis follows from this source.

| Case / behavior | Real production seam | Expected outcome | Failure detection when authorized execution exists |
| --- | --- | --- | --- |
| P01 legitimate systemd0400 | SYSTEMD `read_bearer`, generated LoadCredential and service capability preflight | Nonempty readable regular final non-symlink passes mode check; preflight still governs staged location/read-only capability | Provider refusal or capability failure for valid systemd staging |
| P02 legitimate read-only named-user ACL-mask0440 | Same SYSTEMD path, systemd255 read ACL staging | Mode check accepts read-only ACL mask; no inferred arbitrary ACL privacy |0077 regression rejects real service-readable nonwritable ACL staging |
| P03 owner-write0600/0640 | SYSTEMD `read_bearer` |0222 refuses, including owner write; unlike unchanged sidecar0022 | Bearer returned with owner write set |
| P04 group-write0420/0460 | SYSTEMD `read_bearer` |0222 refuses group write | Bearer returned with group write set |
| P05 other-write0402/0442 | SYSTEMD `read_bearer` |0222 refuses other write | Bearer returned with other write set |
| P06 ordinary owner0600 | File `read_bearer` selected only after no environment/configured staging | Existing0077 check permits readable nonempty0600 | Changed ordinary behavior or provider selection |
| P07 ordinary0440/0404/group-other bits | File `read_bearer` | Existing0077 refusal remains; SYSTEMD relaxation does not cross providers | File provider returns bearer with any0077 bit |
| P08 provider precedence | `ConnectorSupervisor.credential_provider` | Nonempty environment SYSTEMD, configured SYSTEMD, ordinary File, missing provider in existing order | Ordinary path selected despite staging or silent fallback from unavailable staging |
| P09 missing staged directory/environment | SYSTEMD constructor/read and CLI capability | Existing unset environment/no injection refuses, no File fallback | Bearer/health accepted from missing staging |
| P10 missing/unreadable/empty staged file | SYSTEMD `is_file`, stat/read/strip and capability read | Existing category-only refusal; no secret/error-path disclosure | Returned empty bearer, readiness acceptance or leaked content |
| P11 wrong type/final symlink | SYSTEMD `is_file`/`is_symlink`; CLI capability `lstat` | Existing non-file/final-symlink refusal | Read from final symlink or nonregular input |
| P12 component path/directory capability | CLI exact expected directory, directory nonwritability and capability component-symlink walk | Wrong unit/name/path, symlink component or writable staged directory refuses at owning preflight | Generated unit starts after any invalid preflight |
| P13 custody distinction | CLI capability expected_uid=None for staged; ordinary source capability expected_uid=current; sidecar ordinary0600/current UID | Preserve actual owning custody checks; SYSTEMD provider itself gains no UID or component-symlink guarantee | Removed owning custody refusal or documentation claiming a nonexistent provider UID guard |
| P14 staged service-readable but write-open succeeds | Unchanged `credential_capability(require_read_only=True)` ExecStartPre | Refuses despite a numeric read-only mode; mode is not actual service capability proof | Unit preflight succeeds with writable staged capability |
| P15 sidecar staged owner-write versus ordinary custody | Unchanged Go `consumeInput` | Staged0022 permits owner-write bit; group/other writes refuse; ordinary exact0600/current ownership remains | Sidecar policy diff or assertion falsely claiming0022 refuses owner writes |
| P16 one-use lifecycle | Existing sidecar marker/drop-in/source retirement and consumption | Unchanged ownership, marker refusal and one-use commit order; no staged chmod/chown | Repeated input consumed, changed marker order, new credential mutation |
| M01 stable numeric mode0400/0440 | Existing connector-process worker `_connector_credential_mode` and parent `capture_connector` | Four octal digits and observation microseconds under enclosing actual unit/boot/invocation/MainPID/process binding | Accepted prose/non-octal mode, missing binding or fabricated gate result |
| M02 namespace/staging view absent | `/proc/MainPID/root` and fixed staged directory metadata | Null mode/time, unavailable_namespace; no claim of service credential absence | Host-path miss represented as observed absent or fallback host stat |
| M03 final location unavailable | Named service-root directory present, daemon.token `lstat` unavailable | Null mode/time, unavailable_location; no inference of persistent absence | Mode fabricated or service-namespace absence claimed beyond the sample |
| M04 observation command error/unreadable metadata | Existing bounded worker/query and mode `OSError` handling | query_error, null mode/time, unaffected original failure/cleanup | Error interpreted as valid empty/mode/absence or original exit replaced |
| M05 timeout/late result | Existing two-second process,11-second section,13-second shell command/+1 kill grace | timeout, null mode/time; owned termination/reap remains, no new budget | Late result retained as observed or cleanup/remaining sections skipped |
| M06 truncation | Existing2048-byte worker/4096-byte section caps | truncated and null mode/time; never retain partial positive mode | Overflow projection accepted or caps/budget increased |
| M07 malformed/duplicate/extra/nonfinite output | `_closed_json`, `_connector_process_sample`, `_credential_mode_sample` | parse_loss; reject wrong keys/types/labels, non-octal mode, Boolean/nonpositive timestamp, unknown loss, value on unavailable loss | Malformed mode/binding accepted as observation |
| M08 unit/process/file/namespace identity drift | Repeated unit/process scans, intra-mode file+mount checks and cross-sample mode agreement | attribution_loss with null mode/time; no inherited positive mode or gate | Mode retained across changed invocation/process/metadata/sample |
| M09 clock drift | Mode observation times checked in enclosing repeated samples | Reversed/future samples refuse; no atomic or hostile-ABA claim | Reversed/future timestamp accepted |
| M10 unavailable original process/child | Existing original connector worker and MainPID classification | failed/absent/unavailable_process/unavailable_child; mode null, no second connector | Replacement diagnose process or unattributed mode reported as original |
| M11 unsafe metadata type | Fixed staged directory and file `lstat` | unsafe_type with null mode/time for symlink/non-directory/nonregular final location | Unsafe location serialized as numeric credential observation |
| M12 capture wrapper launch/write/parse failure | `capture_connector_instance` and `capture_failure_snapshot` | Closed fallback loss, null mode/time; original status survives every cleanup attempt | Fallback contains mode, capture changes initiating exit or suppresses stop/disable/reset |
| M13 unchanged readiness/listener evidence | Existing inode-attributed original process tree plus five closed internal gates | Listener result retains original meaning; five gates stay null/unavailable_internal regardless of mode |0440 treated as healthy credential gate or positive lifecycle proof |

For source inspection, compare both masks and the actual provider selection and
service preflight, then trace each loss to its producer and shell fallback.
The process collector admits mode only with two complete, validated matching
process projections; if another required observation fails the mode is withheld
with that explicit loss. Thus availability/timing of the new field remains
unproved. Metadata shares existing budgets, including termination/reaping,
without claims of a hard bound on contextless kernel filesystem stalls.

The failure snapshot is additive version1 diagnostic inventory. The execution
ledger `n3_evidence.py` consumes its own event/diagnostic contract, not this
snapshot, and gains no new success assertion. Numeric modes are metadata, not
token bytes, ACL identities, hashes, service capability, ownership proof or a
readiness API. No local product exercise/build, Product E2E/Jenkins, publication,
trigger registration, rerun, merge, deploy or permission change is authorized
in the maker leg. Seq428 is consumed1/1; manager retains seq4350/1 and later publication after fresh gates.

## Harness continuation cases — THR228seq435 / TASK11067 / TASK11071

The P/M cases above describe unchanged published590768ff credential/mode source;
their former seq428 delivery authority is historical and consumed1/1, not a new
allocation. The following H cases cover the current harness delta. Every row is
SOURCE/STATIC specification; intended future level is the manager's single
exact-head real-systemd N3 or a separately authorized behavioral proof. Neither
has run here. No assertion sensitivity or behavioral/test-authoring PASS exists.

| Case / behavior requirement | Shipping/harness seam; concrete preconditions and action | Observable expected result | Failure detection/assertion and intended level |
| --- | --- | --- | --- |
| H01 first reset with unloaded services | Missing-enrollment negative start has ended; call first post-leg reset when systemd has collected either service | Reset error does not abort; staging absence, completed-stop-reset snapshot, held-source restoration, denial probe, positive start remain ordered | Bare reset abort, missing independent staging/PID assertions or moved source restore is source defect; runtime N3 UNEXECUTED |
| H02 second reset unloaded | Missing-consumed-state negative leg, marker restored, either service unloaded at second reset | Reset tolerance followed by actual managed start and active wait; no reset-derived success receipt | Bare reset or missing start/active check; runtime N3 UNEXECUTED |
| H03 loaded failed units | Either reset sees loaded failed sidecar/connector | Clear failed request, retain all later checks; reset itself proves neither quiescence nor success | Removed process/staging/refusal assertion; static inspection only |
| H04 loaded successful/active states | Reset returns0 while service remains active | First-leg inactive/MainPID/staging assertions refuse active/residue; second leg still requires managed restart readiness | Reset success used as absence; future N3 sensitivity UNEXECUTED |
| H05 reset denied/query unavailable | sudo/systemctl reset request fails independently of unit state | Best-effort request remains tolerated; later successful independent state/path/start checks decide progression | Ignoring a required query/assertion or reset interpreted as absence; static only |
| H06 missing-credential stop unloaded | Negative startup before first reset; stop includes collected units | Stop request cannot abort expected-negative transition; successful inactive, PID0, marker absence and peer inventory remain mandatory | Bare stop or erased subsequent assertion; future N3 UNEXECUTED |
| H07 absent state status0 | v255 SHOW_PROPERTIES returns not-found/inactive/dead and service MainPID0 | Exact states + absent unit paths + full valid file inventory allow shipping cleanup; target has no MainPID query | Wrong target/service distinction or missing common/PID/path checks; static only |
| H08 failed valid-looking state query | systemctl show exits1/4/other with text0/inactive/not-found | Refuse; output is never confirmed absence at nonzero status | systemctl_absent_value/inactive/MainPID admits failed output; static only; malformed query sensitivity UNEXECUTED |
| H09 empty/malformed states/PIDs | Successful show emits empty/prose/extra line/noninteger/negative PID or unsupported active state | Refuse; inactive accepts only inactive/failed, PID must be canonical bounded integer | Empty substituted PID considered nonzero/fresh or active query failure inverted to stopped; static only |
| H10 stopped versus active/transitional | Run negative state check and readiness-loss waits under conditional wait_for | inactive/failed only pass negative check; active/activating/deactivating/unknown never pass; positive wait still is-active success | `! active` or negated child shell remains; runtime waits UNEXECUTED |
| H11 MainPID substitutions/fresh PID | Negative startup/shutdown check, positive PID, old/shutdown PID acquisition and automatic Restart wait | Successful numeric query first; positive PID>1, fresh PID differs from old and is active; failed output never passes comparison or drives kill | Status-hidden substitution/invalid numeric command accepted; static only |
| H12 privileged path denial | sudo fails rather than executing existence/symlink/writability test | Negative assertion fails; diagnostic presence becomes unknown/query_error, never observed_absent | Outer sudo1 interpreted as inner false-test1; static only |
| H13 residue/staged capability | Source/marker/drop-in/staging or transaction paths remain; staged file symlink/writable/directory writable | Existing retirement/staging/custody assertions refuse; no chmod/chown or source replay | False-test polarity or asserted path changed; future N3 UNEXECUTED |
| H14 staging cleanup/order | First reset tolerated; staging persists or query fails before held source restoration | wait_for cannot succeed; held source remains for diagnostics; positive start not admitted | Source restored before independent staging success or expected-negative evidence/diagnostic omitted; static only |
| H15 find query substitution | Fresh/upgrade rollback finds return nonzero with empty or partial output | Refuse before empty-output comparison; successful empty output alone passes | Hidden failed producer becomes stage absence; static only |
| H16 conditional setup | reset_shipping_unit is the left operand of OR; any install/key/write/package install/reload/log command fails | Explicit return1 prevents setup success even though Bash disables errexit within function | Later printf hides earlier setup failure; static only, no package execution |
| H17 shipping cleanup producer | Headscale ID-list pipeline query/parser fails; then remaining cleanup observations run | Aggregate fixed fixture_absence_unconfirmed; no hidden process-substitution success; all later cleanup attempted | Successful later query erases failed enumeration; static only |
| H18 cleanup mutation failure | Each shipping/final delete/reload/certificate command independently fails | Fixed existing refusal, aggregate failure, remaining cleanup attempted; initiating nonzero status survives | False cleanup complete or premature exit; future failure-matrix proof UNEXECUTED |
| H19 EXIT/INT/TERM | Original37, INT130 or TERM143 plus capture/stop/reset/delete/query failures | One saved-status teardown, all attempts; only original nonzero leaves; successful origin may fail cleanup | Re-armed errexit, replaced initiating status, second pass or dropped teardown; source only, signal proof UNEXECUTED |
| H20 host listener/process census | Successful ss full selected-port query or ps full PID census; failure/malformed/empty census alternative | Zero listener rows/no matching PID may pass; failed query and malformed/empty PID census refuse | Negative port/kill error used as absence; static only, real census proof UNEXECUTED |
| H21 watchdog/numeric timestamps | Ready/order/watchdog queries fail, are empty or nonnumeric; equality queried after stop | Successful canonical bounded numeric acquisition before arithmetic/equality; no empty==empty pass | Failed substitution/nonnumeric arithmetic accepted; static only |
| H22 two CI boundaries | PR800 synchronize on same repo/task6475/main; before590768ff, changed after=head and attempt1 | Both job selection and pre-build event guard retain exact binding under seq435; other trigger/attempt refused | Different before in either boundary, removed repo/branch/attempt binding or broader trigger; static only |
| H23 cleanup diagnostic limits | Multiple failures across shipping/final and trap reentry | Existing closed phase/predicate vocabulary, at most46 deduplicated lines/<4096B; no raw output | New arbitrary failure prose/value, unbounded tokens or capture budget drift; source only |
| H24 remaining observation gaps | Virtual nc refusal, filesystem test internals, pre-trap failures, capture scratch/section failure | Do not infer confirmed continuous virtual/service absence, exact OS errno/custody, hard timing or complete cleanup from source | Out-of-class ambiguity recorded in inventory; no helper/product redesign or claim of behavior PASS |

Malformed installer recovery, persisted-node/credential-free restart, N6,
actual staged0400/0440 compatibility and all new causal/red-green/mutation/
repetition/executable-QA proof remain unproved. Run38259906794 attempt1 had
expected-negative ledger and partial cleanup with terminal=null, held source,
and unavailable connector invocation/mode/listener; it is not these cases' PASS.
