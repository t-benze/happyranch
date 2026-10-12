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
in the maker leg. Manager retains seq4280/1 and later publication after gates.
