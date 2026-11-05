# Inert HappyRanch macOS signing source

TASK-14158 owns this manual signing delivery under Founder THR-104 seq594.
This source implements the accepted TASK-14160 design/cases (SHA256
`d6886d89cc38cc55274964f071adddb26d72d71c7486952af9856af1ff0a4704` /
`43dc973a9f12efb5b98568fa63df929b59de408191109ce5441d23d77ab573d4`),
independently assessed DESIGN-ONLY PASS by TASK-14163 and accepted by the
manager. It creates no job, node, credential, owner session or native result.
No owner action is due in this source leg.

`config.xml.in` is a core Freestyle template, initially **disabled**, with no SCM,
automatic trigger, remote trigger token, downstream action or caller script/path.
It selects only `happyranch-mac-signing-owner`, disables concurrency and uses
installed build-timeout **1.41**, absolute 20 minutes and AbortOperation only.
Controller executors remain zero; the old `mac-mini` and unrelated jobs stay
unchanged. Its dated offline/LaunchFailed/three-executor observation establishes
no current UID, owner-M4 mapping, access right or repaired launch.

## Fixed provenance and release installation

The unsigned original is `HappyRanch-1.0.1-1.0.1-arm64.zip`, **49,089,611 bytes**,
SHA256 `85b96655a1d072328872fa8797e16ad0087e0e05aa30b8dda2a8bc78b60ac243`.
It packages source `21c63d8c1ec520764945d7b0bafb4bfe668ab81d`, tree
`b2af3a4c8e0cb1c6b58796030085dfb31680b374`; archive/notices QA12645 applies
to those unchanged bytes. Nothing here rebuilds or executes the app.

The TASK14131 owner ZIP is SHA256
`8639a792ffa7152111689ed59d09eff4ae93452d8ca9608260c6d0528d2fb11c`.
`mac-sign.sh` adapts its original script SHA256
`f65b0e9833e246c2a4617ac8250388728ae239b436874f03b204d3c5f1eb6780`.
The inventory is byte unchanged, SHA256
`16ee1fd168286ca9bbfe0ed7ea683ed98df0d8470b7916ad57de7b0907538af3`.
TASK14146 APPROVE/TASK14147 portable PASS cover the owner bytes, **not this
adaptation**. The changes add stage admission, private process supervision,
parent-owned completion publication, association-before-recovery, reviewed-log
binding and a fresh extraction during `test`. New independent full review and
scoped QA must assess this PR's immutable final head.

After guarded merge, root stages the five workload files from the **full actual
merged commit**, verifies each against that commit, and renders from those exact
bytes with `perl bounded-run.pl --render FULL_MERGED_SHA > candidate.xml`.
This offline renderer emits literal source/file pins and refuses an invalid SHA
or unresolved placeholder. Supplying a SHA is not proof the files came from
that commit: root verifies repository/object/file identities before installation.
No runtime fetch, branch selection, mutable main/latest or build-supplied source
hash/path is supported. The fixed release is
`<actual home>/Library/Application Support/HappyRanchSigning/release/<merged SHA>`.
Files are owner-only0600 (scripts0700); directories0700, without aliases,
hardlinked state or shared-writable ancestry. Source/layout changes require new
reviewed pins; existing runs require their original exact script/inventory and
source marker. Config readback must match all security-relevant fields and the
literal shell, retaining the exact candidate/readback hashes.

Root records create intent/config hash, verifies exact item404, performs one
ordinary fixed-origin create and reads back the disabled config/inventory. An
existing item refuses overwrite. A lost POST reply is uncertain: reconcile that
exact item by GET; never repeat create. No Create/Configure/Build right follows
from GET200. A401/403 holds; do not change credentials or CSRF policy.

## Held owner operation and admission contract

**DEV-I03 remains unchanged:** “You cannot modify authentication, daemon
bearer-token flow, credential handling or custody.” The missing protected source
is `owner-session.sh`: new Jenkins inbound-secret acquisition/pipe handoff and
authenticated-agent provisioning. It is deliberately absent. Any Keychain ACL,
unlock-policy/profile/custody change is also outside this PR. Founder594 and
the manager's DEV-G03 approval do not supply that immutable exception. Root
must resolve the exact authorized implementer/charter boundary before dependent
protected source or live setup. This PR neither implements another credential
mechanism nor writes owner/session/permission files to manufacture authority.

The later selected venue is an attended **existing active GUI owner** on native
Darwin/arm64, with actual console UID, Directory Services home, user and pinned
host. No historical username/UID is guessed. The separate node must be
EXCLUSIVE, one executor and its unique self-label only, using Java21 and fixed
remoteFS `<home>/Library/Application Support/HappyRanchSigning/agent`. Actual
Java, Xcode/Swift/Security/tools, host/user/tunnel mapping, profile usability,
permissions and isolation remain prerequisites, not claims. The proposed
owner-controlled loopback encrypted forward, matching pinned remoting jar and
secret-pipe launcher still require separately reviewed protected preparation.
No secret is accepted by this runner. No new account, keychain, profile, key
export, Always Allow/Trust, partition-list edit, service or unattended launch.

The held producer must create these private, closed nonsecret records under
`<home>/Library/Application Support/HappyRanchSigning` only after establishing
real current authority and observations. Control JSON uses sorted keys, compact
UTF8 encoding and one trailing newline (stock JSON::PP canonical encoding);
duplicates/alternate encodings refuse. They are **necessary local gates, not
authentication, proof of human presence or authority from a hash/old SID**.
Same-UID owners/administrators can change them; authentic production and the
controlled attended window remain external protected prerequisites.

* `owner-session.json`: `version=happyranch-signing-owner@1`, merged `source`,
  actual `host,user,uid,home,node,agent_pid`, Unix-second `started,expires`
  (at most30minutes), `isolation_checked` (no older than120s at the actual
  current gate/admission or recheck, never future; an old permission cannot
  make an old isolation observation fresh),
  `isolation=exclusive-one-owner-workload`, and `profile_checked`.
  Apple stages require `profile_checked=actual-authorized-history-exit0`, from
  the separately authorized bounded check of the **existing** profile. Public
  preflight does not perform that Apple authentication or prove private-key use.
* `permissions/<REQUEST_ID>.json`: `version=happyranch-signing-permission@1`,
  `root=TASK-14158`, genuine **current** permanent `task,session`, pinned
  `source,action,request,run,job,build,queue`, Unix-second `issued,expires`
  bounded by this stage and owner session, plus `upload,log_review`.
  `queue` comes from this numeric build's actual Jenkins API `queueId`; core
  does not export it as a parameter. A caller cannot select the task/SID/queue.
  The producer must verify current allocation/authority outside this source.
* Preflight `upload` is the observed current build's
  `{type: hudson.model.FileParameterValue, name: ORIGINAL_ZIP,
  filename: original.zip, size: 49089611, sha256: <original hash>, build: N}`;
  otherwise `upload` is null. Actual executor bytes are independently checked.
* Finalize `log_review` contains exact `sha256,submission_id`, Boolean
  `inspected_complete=true,safe_to_publish=true`, and `warnings_disposition`
  equal to `none-present` or `all-explicitly-accepted-by-root` matching the
  actual complete issues. Otherwise it is null. Root must actually inspect the
  complete real log and explicitly disposition every warning before creating
  this record. A supplied `REVIEWED_LOG_SHA256` alone cannot satisfy it.

The producer must refresh all-job-label/queue/running-build and owner-UID process
observations before each stage. The old general node stays offline unless its
actual UID is independently proven distinct without owner-keychain access.
Runner additionally observes the console owner/home/host and refuses another
owner-UID Java process. This conservative check and EXCLUSIVE1/nonconcurrency
are not an OS sandbox or an ACL against an administrator relabeling another job.
An unrelated Java workload can therefore also cause refusal. Do not resolve it
by killing someone else's process. No age-only unlock or auto-relaunch exists.

The producer must prepare private `inputs`, `runs`, `permissions`, `receipts`,
release and workspace directories on the checked volume. Minimum free space
is1GiB on Mac and independently on controller upload storage. Each stage takes
an exclusive kernel workload lock plus retained `ACTIVE` and per-run mkdir
locks. Failure/interruption retains ownership and partial state. Root reconciles
the exact processes/files/receipts before any explicit further action; no stale
lock deletion, ambiguous submit retry or partial sign/finalization reuse.

## Whole-file input and final collection

Root already holds the private original; no new Founder download/reconstruction
is needed. After all independent source gates, disabled job readback and live
prerequisites, manager uses the existing **fixed-origin netrc-reference** API at
`http://127.0.0.1:8081`. Never read/copy/embed its credential contents, add an auth
client, follow redirects or use an advertised URL as an authenticated target.
No controller API operation runs from this source leg.

Use the accepted ordinary whole-file multipart `/job/happyranch-mac-signing/buildWithParameters`
plan: core file field `ORIGINAL_ZIP=@<exact retained absolute original>;filename=original.zip`
and allowlisted scalars via `--form-string`. Curl configuration/proxies/redirects
disabled;5s connect,300s total,128KiB response, no retry,64MiB/file and65MiB
whole multipart client bounds. These are client caps, not claims about effective
server limits, controller temporary space or actual49MB admission/executor copy.
Record intent, actual HTTP status/queue Location, exact queue→numeric build and
FileParameterValue association. An uncertain upload reconciles queue/build
history without a second POST. Queue wait120s ends in a truthful queued/offline
disposition, not an automatic new build or controller-executor fallback.

Preflight alone accepts `ORIGINAL_ZIP=original.zip` as filename metadata, never a
path, and checks the actual regular private workspace file, exact size/hash and
current-build proof **before extraction/keychain queries**. It retains a verified
private copy in `inputs/original.zip`, then removes only that admitted workspace
file after successful checks. Later stages reject uploads or residual workspace
inputs. No global/node variable named ORIGINAL_ZIP may impersonate file presence.
Full hash, CRC, exact1,066-entry layout, eleven arm64 headers and plist/source pins
are checked before native signing; no ZIP member or app executable is run.

Only the explicit publisher filenames in config may be archived. Previous finite
publisher directories are retained privately under the new operation, never
adopted as current evidence. Any extra/aliased/special file refuses. Raw stdout,
stderr, original ZIP, baseline, keychains, environment and agent launch data stay
outside the publisher. Evidence aggregate2MiB; native capture256KiB (complete
notary log1MiB); final ZIP128MiB. Mandatory stage.json plus real selected-stage
exit0 are required; ArtifactArchiver's nonempty check alone proves neither.

Final collection is a separate normal bounded **whole-file** GET of the exact
numeric build and only the configured final artifact. Use the same fixed origin
and existing netrc reference,5s connect/300s total and a counting128MiB cap even
without Content-Length. Require complete response, matching Content-Length when
present, actual published size and SHA256 before exclusive local publication.
Interrupted/oversized/mismatched downloads retain failed private receipts/partials
and never count as delivered. Existing helper limits10MiB/file and50MiB aggregate
remain unchanged; that helper cannot collect this archive. No transfer has run.

## Six finite stages and completion

| Action | Parameters and completed transition | Seconds |
| --- | --- | ---: |
| preflight | Fresh REQUEST_ID; empty RUN_ID. Current whole original/context/tools/read-only exact unlocked keychain status; no signing/Apple call. |180|
| sign | Fresh REQUEST_ID and new RUN_ID. Retain original/baseline/work; sign8libraries/bundles,2frozen executables, then outer app; verify all11. |900|
| submit | Fresh REQUEST_ID, same RUN_ID. Distinct signed ZIP roundtrip; flushed archive name/size/hash/ATTEMPTED precedes exactly one submit; real returned ID saved even on nonzero. |600|
| status | Fresh REQUEST_ID, same RUN_ID; optional actual RECOVER_ID only if no saved ID. One info/log, plus bounded history for recovery; association precedes canonical ID adoption. |300|
| finalize | Fresh REQUEST_ID, same RUN_ID; REVIEWED_LOG_SHA256 plus actual root full-log/warning disposition. Accepted and exact association; staple copy, distinct ZIP and fresh extraction/full verification. |900|
| test | Fresh REQUEST_ID, same RUN_ID. Reopen/re-extract final bytes, repeat signature/ticket/Gatekeeper/non-code verification; no app/GUI execution. |600|

REQUEST_ID/RUN_ID/RECOVER_ID are lowercase UUIDs, not paths or shell. Sign/final
directories are never reused after partial work. All11 signatures require exact
identifier/team/authority/secure timestamp; runtime applies to3executables, with
no exception entitlements. No `--deep` signing; strict/deep verification is used.
Non-code bytes/modes, notices, README and plist are compared to the original.
Submission bytes remain unchanged after finalization. Final bytes get a new
measured size/hash and actual submission/log association. The final ZIP roundtrip
must pass signatures, stapler validation, syspolicy_check and spctl before completion.

Native calls have a120s ceiling, submit300s and Swift180s, clipped to the shared
stage deadline with10s teardown reserve. `bounded-run.pl` starts process groups,
drains capped private streams, records raw exit/signal/reason, sends TERM/KILL,
reaps the leader and requires the group/streams settled. The outer stage allows
its nested supervisor8s before KILL; ordinary native command supervisors use2s,
all within a10s teardown window. Escaped processes, uninterruptible OS I/O,
uncatchable kill/power loss, same-UID interference and PID reuse are residual
limits; uncertainty cannot become success. The plugin is a secondary bound and
does not certify hard termination of arbitrary Java I/O.

The child writes only `sign.ready`/`final/ready`. The parent settles it, syncs
required state, copies/verifies bounded outputs and publishes command/output/stage
receipts before the final exclusive completion marker. Capture, persistence,
deadline or teardown failure prevents completion. Caught publication failure
privately retains all partial success/final publisher artifacts and replaces
them with a FAILED_OR_UNKNOWN receipt/hold. Failure publication includes the
available bounded command journal snapshot (allowlisted labels, actual exits,
signals, reasons and settlement only), a controlled phase cause and output.json.
No argv, raw stream or parser error enters that projection. Missing command
evidence is explicitly unobserved; malformed/unavailable journal or observation
evidence is explicitly failed, never a successful evidence-publication claim.
Publisher/marker errors remain non-success, including failures after history
rename; retained journal lookup follows that rename. A failed publisher can
leave evidence unavailable and must not be reported as successfully published;
uncatchable interruption remains incomplete and Jenkins must not be treated as
successful. A marker alone is insufficient without the exact successful build
and matching durable/public receipt. Pending, Invalid, missing/oversized/malformed
logs, tool failure and ambiguous history return non-success. No blind resubmit.

For status, the native consumer saves a closed projection of the current info
response before log/Accepted checks. Only a complete successful, settled info
command with matching ID/name and an allowlisted status can supply a known
`Pending`, `In Progress`, `Invalid` or `Accepted` observation. That observation
becomes `native_status` only when it matches the saved canonical ID/name. An
unassociated recovery candidate remains UNKNOWN with its separately labelled
info status; observing it never writes id.txt. Unobserved, incomplete-command,
malformed, mismatched and unknown-status responses have explicit causes and
UNKNOWN status. A known info status does not satisfy Accepted/full-log/root
warning review or permit finalization. Later native failure, timeout, output cap,
child-settlement uncertainty or publication failure preserves the original
non-success and ACTIVE/run/attempt/hash holds.

## Source handoff and unexecuted acceptance

The existing TASK14166 output/PR case record maps actual portable commands to
C1–C10. Synthetic command/JSON exercises establish only local bounds, refusal,
persistence and association predicates. They do not establish macOS Swift/Security,
key access, Apple status, native trust, controller admission or the existence of
authentic owner/session producers. No repository test/seam is added.
TASK14178 extends that same case record for C4 current-time freshness (including
the old-permission/stale-isolation counterexample), and C5/C8/C9 failure journal,
validated status/UNKNOWN, publisher and marker boundaries. Its task-only probes
and old-source comparisons are portable evidence, not whole-stage/native proof.

Required automatic checks are exactly **Source Boundary Lint** and **Web Lint
(Node24)**, static only, with normal hooks/nonforce publication. Python units are
RETIRED; general integration including callback/lifespan/SessionTracker is
SKIPPED THR243seq42. No Product E2E or app rebuild is allocated. Independent FULL
code review, separate scoped QA and manager guarded merge remain required.
Root then owns disabled-job setup/readback, whole-file transport, protected
boundary resolution, ONE later attended owner setup and THR104 attachments.
Actual Mac/Jenkins/native/Apple execution, signed final bytes, Finder/install,
independently downloaded quarantine behavior, macOS14 and public release remain
NOT RUN/open. Source delivery grants no live enablement or native-stage authority.
