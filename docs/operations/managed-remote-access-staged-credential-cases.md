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
in the maker leg. Seq428 and seq435 are consumed1/1; current seq455 is0/1 pending root-manager publication after fresh exact-head gates.

## Harness continuation cases — THR228seq435 / TASK11067 / TASK11071

The P/M cases above describe unchanged published590768ff credential/mode source;
their former seq428 delivery authority is historical and consumed1/1, not a new
allocation. The following H cases cover the historical harness delta. Every row is
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
| H22 two CI boundaries | PR800 synchronize on t-benze/happyranch, same repo/task6475/main; before112f90ec, changed canonical40-hex after=head and attempt1; current manager pins reviewed after/tree | Both job selection and pre-build guard bind seq455; old before590768ff, later update and other trigger/attempt refuse; Python rejects malformed after before build | Different before in either boundary, removed repo/branch/attempt binding or broader trigger; exact-head SOURCE/STATIC only; allocated hosted N3 remains manager-owned |
| H23 cleanup diagnostic limits | Multiple failures across shipping/final and trap reentry | Existing closed phase/predicate vocabulary, 23 shipping+24 final deduplicated pairs/<4096B (seq444 final-label split); no raw output | New arbitrary failure prose/value, unbounded tokens or capture budget drift; source only |
| H24 six virtual removal sites | Final/shipping cleanup, health loss, Stop barrier, readiness loss and shutdown; bind known active instance before stop, or initial NEVER-STARTED full conjunction | Successful same-instance Stop plus unsuccessful real probe and current peer identity permit local removal inference; reachable peer vetoes; initial branch emits no peer observation | Missing target alone or failed nc alone never passes; protected observation/capture boundaries unchanged; source/static only |

Malformed installer recovery, persisted-node/credential-free restart, N6,
actual staged0400/0440 compatibility and all new causal/red-green/mutation/
repetition/executable-QA proof remain unproved. Run38259906794 attempt1 had
expected-negative ledger and partial cleanup with terminal=null, held source,
and unavailable connector invocation/mode/listener; it is not these cases' PASS.


R1/R2 correction cases are four-answer source specifications, not executed proof.
SAME manager TASK11067 step3 now authorizes existing Stop receipts plus a strict
initial NEVER-STARTED conjunction for R3. These remain source/static cases;
no negative virtual runtime evidence or remote refusal is authenticated here. Existing H01–H23 keep
both reset variants, following residue checks, original failure and all-cleanup
requirements. In every new refusal case, the initiating nonzero status remains
unchanged and later cleanup actions must still be attempted; signal/behavioral
proof remains UNEXECUTED/SUSPENDED.

| Case / requirement | Real seam, preconditions and action | Expected observable result | Failure detection / intended level |
| --- | --- | --- | --- |
| H25 successful shutdown acquisition | Bind current shutdown phase PID/invocation/boot/time before stop; journal JSON and decoder exit0 with distinct cursors and two original canonical MESSAGE receipts; MainPID0 | One closed completion result admits original shutdown/revocation records, composed with actual negative attempt and current peer identity; printable unrelated messages are allowed | Missing/duplicate/extra/malformed MESSAGE or current context, nonzero producer/decoder or new running instance refuses; saved status/all cleanup retained; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H26 shutdown producer/decoder error | Journal or decoder nonzero, including a closed valid-looking complete result before journal failure | Refuse pipeline before shutdown success records; saved initiating status and all remaining cleanup attempts retained | Nonzero producer admitted through matching stdout or success override; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H27 genuine no-match or partial receipt set | Successful framed journal yields no receipts/only invocation1, repeated cursor/receipt, or extra completion-like MESSAGE | Empty/partial/duplicate/extra set refuses; exactly the distinct canonical pair is required | Pair weakened or successful no-match accepted; saved status/all cleanup retained; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H28 before/after rollback digest acquisition | Existing sha256sum before and after injected installer failure each exit0, canonical64-hex plus exact manifest path | Compare only validated outputs; unchanged bytes pass existing rollback predicate | Failed, empty, malformed or mismatched digest accepted; source/static only |
| H29 failed digest with matching stdout | Either sha256sum exits nonzero after emitting canonical matching or partial digest; includes package provenance pipe | Explicit acquisition refusal before rollback/provenance ledger, independent of set-e | Comparison or outer command masks producer status; source/static only |
| H30 staged provenance acquisition | Each real staged credential call, also conditionally invoked; readlink returns exact canonical path at exit0 or emits that path at nonzero | Only successful exact provenance reaches later readability/nonwritability checks | Nonzero readlink accepted because comparison matches; source/static only |
| H31 required metadata and custody | Existing payload/binary/source stat predicates and staged file/directory stat; successful expected scalar versus nonzero matching/partial output or malformed scalar | Explicit acquisition first; exact custody comparisons preserved; complete C-locale staged metadata acquired before observation printf | Nonzero producer or malformed metadata admitted into successful report; failed report write accepted; source/static only |
| H32 capture counters and file reads | Existing conditionally invoked capture functions, wc/read producer nonzero with small valid-looking/partial output; malformed or empty counter; existing cap writer fails | Existing query_error/unknown or empty projection, no observed field from failed acquisition; later reserved sections and teardown continue | Counter arithmetic or successful outer conditional hides failure; previous successful payload survives failed read; source/static only |
| H33 capture pipeline and provenance fallback | Query/filter cap pipeline fails with valid-looking bounded output, or date/boot producer fails with partial value, or Python lookup fails | Existing query_error/loss or0/unavailable fallback replaces failed output; no admitted observation from failed pipeline | Ignored downstream writer status or concatenated fallback validates partial failed output; budgets/order unchanged; source/static only |
| H34 failed nc with independent Stop evidence | Each known negative phase has nonmissing target and actual peer attempt; inject privilege/status/backend/dial/copy/timeout failure with and without complete current Stop and peer evidence | Without independent sufficient evidence refuse; with it local teardown can pass while remote result remains inconclusive; actual successful probe always vetoes | Failed nc classified as remote refused or accepted alone; no broad stderr classifier, new typed adapter or stale snapshot; source/static only |

R3 completion cases below preserve the six-site phase/identity and cleanup
contracts. Four answers are maintained SOURCE/STATIC specifications only;
behavioral/red-green/mutation/repetition/executable proof is UNEXECUTED/SUSPENDED.

| Case / requirement | Real seam, preconditions and action | Expected observable result | Failure detection / intended level |
| --- | --- | --- | --- |
| H35 stale or PID-reused receipt | Bind active current tuple; journal contains older boot/invocation/phase receipts with matching PID | Boot, current InvocationID and pre-stop cutoff exclude stale rows; decoder rechecks consumed context; no-match refuses | PID-only, unbounded or mismatched consumed context admits old receipts; saved status/all cleanup retained; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H36 mismatched phase or activation | Bind fails/changes tuple, zero/malformed identity, another phase binding or replacement MainPID | Binding cleared before acquisition; mismatched phase/instance/context refuses; replacement MainPID refuses | Prior binding reused after failure or running replacement passes old Stop; saved status/all cleanup retained; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H37 reachable negative peer | Current Stop receipts complete but real peer nc succeeds | Refuse all six virtual assertions and affected success/aggregate records | Stop proof overrides actual reachability; source/static only |
| H38 valid initial NEVER-STARTED | First reset after file-only install, before any start; exact unit/PID and owned-file/process/path/host-port/Headscale queries all succeed and prove absence | Shipping cleanup passes without a target or Stop receipt and emits no peer observation; full conjunction excludes owned virtual listener | Missing IP or process-only substituted for conjunction; source/static only |
| H39 illegal later no-address | A start was requested, including failed or background start; sidecar_ip missing | NEVER-STARTED permanently revoked before invocation; known path cannot pass missing target and cannot fabricate Stop | Re-enter initial exception after failed start/address query or reset start flag; source/static only |
| H40 peer liveness or identity query failure | Final and other known phases, plus first negative start: process exited/zombie/PID reused or status/parser fails with valid-looking ID | Checked process-start identity before and after consumed status refuses; first negative inventory must itself contain current Running/Self identity | Separate good status blesses malformed consumed inventory or missing peer accepts records; saved status/all cleanup retained; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H41 barrier acquisition refusal | StopPost latch entered; bind/journal/decoder/current identity fails, including malformed MESSAGE framing | No Stop-barrier record or new queued start; EXIT releases both latches before remaining stop/disable/reset/delete/query attempts | Latch treated as Stop receipt or refusal strands barriers/replaces original exit; SOURCE/STATIC only, executable proof UNEXECUTED/SUSPENDED |
| H42 readiness and watchdog composition | Receipt-bound health/readiness Stop plus failed peer attempt; fail original connector/watchdog or monotonic query/order | Original health/timestamp requirements still refuse; receipts cannot replace them or claim new timing precision | Generic inactive state or valid Stop substitutes for original numeric/order predicate; source/static only |
| H43 early failure and original exit | Original37/INT130/TERM143 before successful start or missing bound target/peer; later cleanup steps fail | No fabricated completion/removal/aggregate success; all later attempts remain; saved initiating status survives | Require never-started process receipt as valid proof, skip virtual branch into aggregate success, or replace original status; source/static only |
| H44 malformed initial fixture inventory | Headscale producer/parser fails with plausible output, wrong JSON shape/ID/name, or owned node remains; any unit/PID/process/path/listener query fails | Full initial conjunction refuses; all remaining cleanup attempts continue | Valid-looking failed output or missing name accepted as sidecar absence; source/static only |

R4/R5 acquisition cases are the SAME root's step4 bounded source correction.
All cases retain subsequent assertion refusal, the saved initiating nonzero
(including INT130/TERM143) and every later cleanup attempt. No raw journal/status
is retained; all executable behavioral/proof results below remain missing.

| Case / requirement | Real seam, preconditions and action | Expected observable result | Failure detection / intended level |
| --- | --- | --- | --- |
| H45 original MESSAGE framing | stop_completed consumes one MESSAGE containing both expected lines separated by LF, NUL embedded outside keyword, tab/CR/control content, invalid UTF8/binary array, missing/wrong MESSAGE type, duplicate keys or truncated JSON/LF framing | Decoder refuses before closed completion result; no six-site removal/shutdown/aggregate admission; all cleanup attempted with original exit | Text normalization creates two receipts or strips NUL before validation; SOURCE/STATIC only; behavioral/red-green/mutation/repetition/executable proof UNEXECUTED/SUSPENDED |
| H46 distinct pair and unrelated records | Current-bound distinct cursors each contain one whole invocation1/2 MESSAGE; add well-formed printable unrelated messages, then independently duplicate/extra completion-like receipt or cursor | Valid pair and unrelated printable records may pass only with producer/decoder exit0 and MainPID0; malformed/duplicate/extra matching records refuse | Substring extraction or receipt count alone admits malformed records; saved status/all cleanup retained; SOURCE/STATIC only; executable proof UNEXECUTED/SUSPENDED |
| H47 genuine empty peer inventory | First negative-start peer_inventory_absent: current live peer start ticks before/after, same consumed Running/Self hostname/current node/valid IPs and present Peer null or {} | Only otherwise valid current empty inventory admits startup.process_absent and tsnet_admission_absent after the independent unit/PID/marker assertions; original reset/staging/capture/restore/denial/positive order remains | Empty/null inventory without valid same-response current identity accepted; SOURCE/STATIC only; executable proof UNEXECUTED/SUSPENDED |
| H48 unavailable or incomplete peer inventory | First negative start: {}, missing Peer, invalid Peer scalar/list, null/nonobject entry, missing/nonstring/empty/control HostName, malformed UTF8/JSON/duplicate keys, or failed producer with plausible valid output | Refuse both startup success records before progression; retain initiating nonzero and every cleanup attempt | Missing/unclassifiable entry treated as absent, producer status suppressed or separate good status blesses consumed bad data; SOURCE/STATIC only; executable proof UNEXECUTED/SUSPENDED |
| H49 stale peer or present sidecar | First negative start: changed node/hostname, missing/stopped backend, invalid Self IPs, failed/drifting/zombie process ticks; valid map contains home-sidecar-ci followed by malformed entry | Refuse admission regardless of separate prior successful peer status; validate every compared hostname; actual sidecar presence refuses | Stale/current identity mismatch or early comparison masks malformed later entry; saved status/all cleanup retained; SOURCE/STATIC only; executable proof UNEXECUTED/SUSPENDED |
| H50 reachable sidecar with valid framed Stop | At each of six negative sites, distinct current Stop pair succeeds but real peer nc succeeds | Reachability veto still refuses; failed nc remains inconclusive and needs independent Stop/current peer evidence; original exit and all cleanup attempted | New decoder overrides actual reachability or invents a remote refusal; SOURCE/STATIC only; executable proof UNEXECUTED/SUSPENDED |


## Diagnostic extension — THR228seq444 / sole root TASK11161

Retain all79 prior rows at their historical scope. The new D rows apply only to
the local diagnostic delta; none executed. Each row supplies behavior, actual
seam with input/preconditions, expected observable result and defect detection.
Intended venue: retained Go supervisor cases and a separately authorized bounded
collector/native/systemd venue; no Python-unit replacement. Present evidence for
every row is SOURCE/STATIC authoring only; behavioral/assertion/red-green/mutation/
repetition proof UNEXECUTED/SUSPENDED. Integration/actual callback SKIPPED24342.
See the package guide's prepared seq444 diagnostic contract for finite reasons, bounds and
identity/loss details. Current seq455 allocation is separate and manager-owned; no worker publication/run authority follows.

| Case / behavior | Real seam, preconditions and action/input | Observable expected outcome | Defect detected when execution is authorized |
| --- | --- | --- | --- |
| D01 each terminal branch | runConnectorSupervisor/superviseConnector; invoke each existing setup, Wait, timer, protocol, state, sidecar and notify refusal | Accurate fixed local reason; original return/notify/cleanup sequence unchanged | Missing/wrong label or changed decision; enumerate all fixed labels in maker branch inventory |
| D02 first then stop/exit/restart | Child starts with owned generation; malformed frame then ctx cancellation/admission refusal/Wait; next invocation new generation | Exactly one first; later consequences distinct, no original overwritten by restart | First replaced/repeated, Wait lost or invocations concatenated |
| D03 invalid input | scanChildHealth; empty/invalid/noncanonical/partial/frame oversize; wrong version/generation/sequence/state on existing seam | Fixed safe rejection class; last accepted sequence/owned generation only; validation unchanged | Raw rejected bytes/error retained or rejected generation/sequence copied |
| D04 deadlines | Absolute startup/freshness timer; query/notify result arrives late, cancellation races publication | Startup/freshness/cancel distinguished; no READY/WATCHDOG or refreshed deadline after refusal | Generic/wrong reason or renewed deadline/positive publication |
| D05 process gone | Pre-start window+boot captured; journal labels survive MainPID0, or replacement PID/invocation | Window-attributed events retained by invocation; original pin unavailable; live gates remain null | Replacement diagnose/process attributed to original; vanished PID suppresses journal |
| D06 identity loss | Journal worker; wrong/missing unit/boot/invocation/PID/time/cursor; duplicate/noncanonical fields, generation/sequence drift | No bad event; parse/attribution loss explicit, original null/window_only | Bad label joins timeline, fabricated original or missing loss |
| D07 noise/oversize/tail | Arbitrary/sensitive lines, huge frame, raw/frame/event/byte cap at journal read/project seam | Exact grammar filters before retention; journal slot precedes live scan; partial events with lost tail | Sensitive prose or extra bytes/records retained; live/noise exhausts causal slot |
| D08 capture failure/reap | Worker/query launch failure, timeout, late output, descendant-held pipe, parse/write failure; original exit37 and later cleanup | Closed loss and tail false; bounded group ownership/reap, original37 and subsequent attempts survive | False observed/empty, unreaped child falsely confirmed, status replacement or skipped section |
| D09 inactive before binding | final cleanup with inactive sidecar/PID0; bind_stop_instance then unchanged Stop/virtual_removed | virtual_stop_binding_unconfirmed; removal/aggregate refuse | Historical generic label claimed actual residue/removal or missing tuple accepted |
| D10 real reachability | Same-instance Stop receipts/current peer; nc reaches target, or target/peer absent | Positive dial vetoes; missing target/current peer remains unconfirmed | Negative dial/dead peer/missing address accepted as removal |
| D11 no storm | Repeated ctx.Done/stopChild loops with refused admission; duplicate states; normal healthy polls | One first, each distinct observation/consequence once; no per-poll healthy output | Unbounded labels, first repeats or healthy polling noise |
| D12 compatibility | Producer fixed grammar -> connector-journal worker -> failure-snapshot; parse maintained docs/case keys and bound ledger | Exact names, null original attribution,82s/32256B accounted unchanged split; old allocations remain spent; only seq455 guard/parity updated | Grammar/cap/doc drift, acceptance ledger changes or old allocation renewed |

## H22 current allocation scenarios — THR228seq455 / sole root TASK11205

These details refine the existing H22 record; the91 P/M/H/D records and their
four answers are retained. Requirement: one manager-published exact reviewed
synchronize, with both guard boundaries and fresh prepublication reconciliation.
For each input below the real seam is ci.yml job selection → pre-build Python
refusal → after-SHA checkout/proof subject. Observable expectation and defect
detection are stated below; level is SOURCE/STATIC inspection now, intended
environment is the single manager-owned disposable hosted Ubuntu24.04 N3. No
scenario has executable/assertion/red-green/mutation/repetition proof here.

- Correct input: event pull_request/synchronize, repo/head repo t-benze/happyranch, PR800, task/TASK-6475/main, before112f90ec93177a22f85332f84d8d223cbb14cf28, distinct40-lowercase-hex after equal actual head, attempt1, manager fresh reviewed receipt. Expect selection and pre-build admission using that after; denial or another source subject is a defect.
- Spent input: before590768ff or any earlier allocation head. Expect refusal; either guard retaining an old before or admitted build renews a spent allocation.
- Second update: before equals the first seq455 after instead of112f90ec. Expect refusal; admission would allocate a second run.
- Duplicate/rerun/manual: same selected run attempt2+, workflow_dispatch, reopen/open, push/main or unrelated event. Expect refusal/no build; missing job or pre-build attempt/action check is a defect. Identical no-change push creates no synchronize; transport uncertainty requires manager readback, never blind repeat. This guard does not claim a new durable event-deduplication mechanism.
- Unrelated subject: change each repo, head repo, PR number, head branch or base independently. Expect refusal; any bypass of the corresponding predicate is a defect.
- Invalid/mismatched after: empty, missing, nonstring, non40-hex, uppercase/nonhex, equal before or different from actual head. Expect no build; empty/equal/mismatch are excluded by selection, Python's canonical SHA check additionally refuses invalid shapes before build. A job selected on a malformed nonempty matching value is not execution admission.
- Prepublication drift/ancestry: remote PR head differs from112f90ec, integrated main changes, reviewed after/tree changes or nonforce ancestry fails. Expect manager to withhold push and re-integrate/renew actual full review/QA/static gates; publication on stale receipts or blind retry after transport loss is a defect. No Git SHA constant pretends to supply these manager gates.

D01–D12/H01–H50 and P/M unaffected acceptance remains unchanged:40 fixed
Go categories; first local decision distinct from consequences; after-exit
window_only/null-original attribution and explicit loss;14s/4096B split and
82s/32256B actual section ledger; redaction, original nonzero exit and every
cleanup attempt; same-instance Stop/current-peer/real-dial predicates never
weaken. Necessary ordinary main integration retains lint-only PR/main/local
checks and daily-only19UTC Product E2E. Inspecting source is not behavioral or
test-authoring PASS. Missing staged compatibility, durable commit, credential-free
restart, malformed installer recovery, N6 and full virtual/final cleanup remain
unproved. Historical REQUEST_CHANGES and failures stay attributed to their heads.
