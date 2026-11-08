# Superseded Jenkins Mac signing source

Use the [Mac release handbook](../../../docs/runbooks/mac-release-handbook.md)
for the current GitHub Actions build and owner-local signing flow. The Jenkins
signing job stays disabled; no Jenkins app-build job is installed. The following
attended setup, grants and commands are preserved historical source notes and
must not be followed as current release instructions.

<details>
<summary>Historical attended signing source — superseded, not operational instructions</summary>

Original delivery TASK-14158 continues under sole current manager root TASK-14353
and Founder THR-104 seq612 (seq609 retains the Jenkins choice).
TASK-14346 is failed historical provenance.
This source implements the accepted TASK-14160 design/cases (SHA256
`d6886d89cc38cc55274964f071adddb26d72d71c7486952af9856af1ff0a4704` /
`43dc973a9f12efb5b98568fa63df929b59de408191109ce5441d23d77ab573d4`),
independently assessed DESIGN-ONLY PASS by TASK-14163 and accepted by the
manager. This source delivery creates no job, node, credential, owner session
or native result.
No owner action is due in this source leg.

`config.xml.in` is a core Freestyle template, initially **disabled**, with no SCM,
automatic trigger, remote trigger token, downstream action or caller script/path.
It selects only `happyranch-mac-signing-owner`, disables concurrency and uses
installed build-timeout **1.41**, absolute 20 minutes and AbortOperation only.
Controller executors remain zero; the old `mac-mini` and unrelated jobs stay
unchanged. The7Nov2026 root observation has the general node online/idle with three
executors; neither it nor the historical LaunchFailed evidence establishes
current UID, owner-M4 mapping, Keychain access or isolation.

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

After guarded merge, root stages the six workload files from the **full actual
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

The existing installed job remains disabled at the accepted PR1268 bytes.
This renderer produces an **offline replacement candidate only**. A later update
needs its own exact live permission, preserved prior/current config hashes,
readback and unrelated-state comparison. This source delivery never creates,
updates, enables or runs that job. A lost write reply remains uncertain: read
back the exact item and reconcile; never repeat the write blindly. A401/403
holds without credential/CSRF changes. GET200 grants no Configure/Build rights.

## Attended owner setup and current authority

Founder THR104seq612 approved the exact source-only DEV-I03 amendment from
seq598/att443, SHA256 `fbf219a5c51dce31a4086c2c193b624639548f9ccfaa5ba4b46c2c957c110bfa`.
The installed amendment covers this continuation and expressly delegated source
corrections; current TASK14353's TASK14356 brief supplies the exact HIGH
five-file correction radius on the retained TASK14349 source/PR1281.
`owner-session.sh` is now source, subject to independent FULL review, separate
scoped QA, normal hooks/static checks and manager guarded merge. **Source
acceptance is not native acceptance.** No live operation below is authorized by
that amendment. The existing installed disabled job remains unchanged until a
separate exact live configuration permission.

The selected existing GUI owner remains unknown. No historical UID, username,
M4 mapping, macOS26.5, Xcode or private-key access is asserted. The owner's
reported selector is `C9C7371D6FBFDB86BB847CC1DE181EAF2E3A4BD9`, Team `LS2C558RKS`,
Developer ID Application: Shenzhen Danze Internet Information Service Co., Ltd
(LS2C558RKS), expiry2027-12-05. Existing login Keychain and `HappyRanch-notary`
remain the proposed custody, with per-operation owner-controlled prompts.

After the merged package is supplied and **local nonsecret inspection alone is
separately permitted**, this exact command in the active owner's Mac Terminal
collects actual hostname, console user/UID/DirectoryServices HOME, Java21 path,
OS/Xcode and named tool availability. It performs no SSH, Keychain operation,
agent connection, app execution or Apple request:

```bash
/bin/bash -p ./owner-session.sh inspect
```

Run it from the extracted reviewed source directory. Wrong/root/non-GUI owner,
unsafe HOME or non-arm64 refuses; missing Java/Xcode/tools are reported as
unavailable. Inspect requires the existing stock Perl/Bash tools. Returned paths
are the selected paths, not a request to invent JSON. Keychain/profile usability,
hardware-to-reported-M4 mapping, existing SSH login and host-key trust still need
root disposition. Missing Java/native tools are dependencies, never auto-installed.

One concrete setup candidate after inspection and exact later permission:

1. Root packages the **actual merged source** and all six literal file hashes,
   and stages owner-private0700 release directories and0600 source files
   (shell entries0700). On Mac the exact release is
   `$HOME/Library/Application Support/HappyRanchSigning/release/$SOURCE`, with
   pinned controller JAR at `$HOME/Library/Application Support/HappyRanchSigning/agent.jar`.
   `SOURCE` is the full verified merged commit, never a caller-selected branch.
   The JAR is not a repository dependency: 1,406,408 bytes, SHA256
   `4ae5bbd00b0f79f4453d20fbb6b70dcac4d0a9a4d21d85b18b2c72dd9d117aae`,
   Implementation-Version `3355.3357.v931d3c992987`, Main-Class
   `hudson.remoting.Launcher`, observed7Nov2026. Any controller/JAR drift holds
   for root reconciliation; no latest download or automatic pin update.
   Before launching, attended setup prepares and validates both exact0700
   owner-owned directories `agent/` and `agent/remoting/` under the private state
   root, checking every parent. Missing normal directories may be created;
   aliases, non-directories, wrong ownership or insecure permissions refuse.
   Pinned remoting931d3c992987 `WorkDirManager.initializeWorkDir` checks the
   work directory and default internal `remoting` directory before creating its
   caches/log components. Both must exist for the retained
   `-failIfWorkDirIsMissing`; that fence is never removed for first use.
2. Root separately prepares the same verified six files plus `config.xml.in` on
   the controller at `/home/benze/HappyRanchSigningController/$ROOT/release/$SOURCE`,
   with private0700 parents and0600 files. This explicit private staging path
   avoids shared-writable runtime ancestry. It creates no service. Existing
   controller CLI path is `/home/benze/projects/happyranch/.venv/bin/happyranch`;
   its matching Python supports the XML comparison. Root validates these existing
   tools and real mapping before live setup; source tasks do not install them.
3. Separately provision exactly inbound node `happyranch-mac-signing-owner`,
   EXCLUSIVE/one executor/self-label only, remoteFS
   `$HOME/Library/Application Support/HappyRanchSigning/agent` from inspected HOME.
   Controller stays zero executors. Source performs no provisioning. General-node
   distinct UID/no-owner-Keychain-access proof is still unavailable: this
   candidate conservatively requires other executor nodes offline and no other
   running/queued build. The controller reads the exported Jenkins2.568.3
   `labelExpression` scalar. An enabled Freestyle job restricted to another
   simple label is eligible under that
   placement rule; owner-label, unrestricted/null, compound or missing/malformed
   placement holds. Complete typed counts, Booleans, labels, job/node inventories
   and executor/one-off arrays are required; missing/null facts never mean idle.
   Root resolves held placement under separate exact authority. Idle or a
   configured service path cannot satisfy isolation. No unrelated workload is
   killed or reconfigured.
4. Establish the actual mapping and **existing** SSH host/login trust for the
   selected candidate `benze@192.168.18.174:22`. A different mapping is returned
   to root, never guessed. The source uses only that destination, stock trust
   checks and existing identities, no ssh config, forwarding of an agent, new
   key/account, host-key bypass, SSH service change or LAN HTTP secret fallback.
5. In its genuine current native manager task/SID, root explicitly authorizes
   the session by invoking the shipped source command below. `$ROOT` and `$SID`
   must be its **actual current** root and SID; a historical number does not
   grant permission. The command verifies native allocation through supported
   `happyranch details`, current status/owner/SID and Pause. It creates an
   exclusive nonsecret grant under `.../$ROOT/grants/session.json`. **Only a
   separately authorized live root may invoke it**; allocation/source approval
   alone is not permission. No maker/session fixture is a production grant.

   ```bash
   /bin/bash -p "$CONTROLLER_RELEASE/owner-session.sh" controller-grant "$SOURCE" "$ROOT" "$SID" session - - - "$INSPECTED_MAC_HOME"
   ```

6. The owner runs this ONE attended setup, using the root's supplied exact
   merged SOURCE/current root/current SID values:

   ```bash
   /bin/bash -p "$HOME/Library/Application Support/HappyRanchSigning/release/$SOURCE/owner-session.sh" attend "$SOURCE" "$ROOT" "$SID"
   ```

   It checks GUI owner, home/host, Java21, private paths, six release pins,
   JAR digest/manifest, no other owner-UID Java process, fresh controller state
   and native manager allocation. After explicit `ATTEND`, it starts only
   SSH `127.0.0.1:18081 -> controller127.0.0.1:8081`, then prompts for the **new**
   inbound secret through no-echo Terminal input. Obtain that secret only after
   separate permission via normal Jenkins UI over the tunnel. Never copy the
   default `-secret VALUE` command into a shell. Empty, invalid, EOF, interruption
   or60s input timeout refuses without launching Java. The memory pipe supplies
   Java21 `@/dev/stdin`; Java OS argv/environment/files/logs contain no supplied
   secret. Launcher output is discarded, JUL disk logging/dumps/attach disabled,
   core limit zero and JVM performance-data files disabled; no raw launcher diagnostic is retained. Same-UID/root memory
   access, OS memory handling and uncatchable termination remain trust limits;
   source clearing does not promise forensic memory erasure. The selected native
   JVM/remoting combination must still be verified with synthetic data first.

The attended window lasts at most30minutes, with no automatic reconnect/relaunch.
`stop`, signals, input failure, timeout and child failure close admission and
settle only original owned children. An unsettled group, partial stage, failed
receipt or timeout remains FAILED_OR_UNKNOWN and retained.
After a leader is reaped or original-child ownership is lost, no further TERM
or KILL is sent to its numeric group, including between cleanup signals.
Inherited streams or surviving descendants remain unresolved and cannot admit
work; observing a group ID is not authority to signal it. SESSION, consent,
permission and native ACTIVE/run/attempt records are not age-unlocked or deleted.
A later window needs explicit root process/state reconciliation; do not manually
edit a marker to obtain admission. Owner locks the existing login Keychain in UI
after positive settlement; no automatic policy/ACL/unlock change.

For each of the same six stages, root chooses one fresh REQUEST_ID, exact action,
RUN_ID and intended numeric build (preflight RUN is `-` in this command). Before
its ONE separately authorized upload/build, it creates a fresh stage grant:

```bash
/bin/bash -p "$CONTROLLER_RELEASE/owner-session.sh" controller-grant "$SOURCE" "$ROOT" "$SID" "$REQUEST_ID" "$ACTION" "$RUN_OR_DASH" "$BUILD" "$INSPECTED_MAC_HOME"
```

In the original attended Terminal the owner enters `consent REQUEST_UUID` for
that one selected request. This is local consent, not manager authentication.
At actual gate time `refresh` obtains fresh controller observations over the
existing trusted SSH session, checks the genuine current native manager SID,
all-job placement, node EXCLUSIVE1/self-label/remoteFS, queue/running workload,
controller JAR and **actual running build** action/request/run/queue/node and
FileParameterValue class/name. An intended build number is never sufficient.
The exact uploaded filename comes from core's actual executor environment;
original size/hash are expected policy pins, independently checked against real
executor bytes before extraction/key use. JSON API does not export the original
filename in every core version. No stale/root-written expected scalar substitutes
for that current actual association. Controller readings are bounded and
nonmutating; unknown/missing/oversized/changed facts refuse. Each native recheck
repeats these observations; local records never make old isolation fresh.

`owner-session.json` uses closed canonical `happyranch-signing-owner@2`, with
actual `host,user,uid,home,node,agent_pid,supervisor_pid`, current `root,session`,
`source,started,expires,isolation_checked,isolation,profile_checked`.
`permissions/REQUEST.json` uses closed `happyranch-signing-permission@2` with
`delivery_root=TASK-14158` as historical provenance, genuine current
`root=task,session`, and existing `source,action,request,run,job,build,queue,
issued,expires,upload,log_review`. Permissions expire within both stage budget
and original attended window; isolation is at most120s old at each gate. Sorted
compact UTF8 plus one newline is mandatory. @1 records remain history and do not
admit a new current continuation. Records are **private nonsecret prerequisites,
not authentication or proof of human presence**; same-UID/root interference
remains a trust limit. No bearer/netrc is copied to Mac or embedded in a grant.

The optional existing-profile check is a separately authorized native operation.
Root uses the same grant command with action `profile-check`, RUN `-`, BUILD `-`
and fresh request. In the attended Terminal owner enters `profile REQUEST_UUID`.
Only then does the source perform bounded120s `xcrun notarytool history
--keychain-profile HappyRanch-notary --keychain "$HOME/Library/Keychains/login.keychain-db"
--output-format json`. Complete exit0/settlement produces the current local
profile prerequisite; raw history stays private. No profile/password creation,
secret extraction, signing or new submission occurs. Apple stages continue to
refuse absent that real result. Public preflight is not that result.

Finalize additionally needs root's actual complete-log inspection/warning
resolution: current grant directory `REQUEST.review.json` is the existing closed
`sha256,submission_id,inspected_complete,warnings_disposition,safe_to_publish`
record. Root derives it from the real complete log, never asks an owner to
fabricate it. `REVIEWED_LOG_SHA256` alone remains insufficient.

The **exact later permission request**, after independent source gates and
inspection, must name: actual inspected host/user/UID/HOME/Java/native tools;
private merged release/JAR/controller-grant staging; the one dedicated inbound
node and required isolation dispositions; verified existing SSH mapping/trust;
the finite loopback tunnel; new inbound-secret acquisition and memory-pipe agent
launch; owner-controlled per-operation login-Keychain prompts; and any separately
selected profile check or exact one native stage/upload/build/config enablement.
No source approval authorizes that request's execution. Keychain ACL/partition
list/Always Allow/Trust/unlock-policy, new accounts/keys/profile, password storage,
key export/import, app rebuild, public delivery and installation remain excluded.

## Whole-file input and final collection

Root already holds the private original; no new Founder download/reconstruction
is needed. After all independent source gates, disabled job readback and live
prerequisites, manager uses the existing **fixed-origin netrc-reference** API at
`http://127.0.0.1:8081`. Never read/copy/embed its credential contents, add an auth
client, follow redirects or use an advertised URL as an authenticated target.
No controller API operation runs from this source leg.

Before the whole-file upload, independently establish at least **1GiB free on
BOTH the Mac volume** holding private workspace/inputs/runs **and the
controller upload storage** (including its temporary upload area). Unknown or
insufficient free space on either volume means refusal/hold before POST. The
runner's Mac free-space check does not establish controller capacity; root must
obtain that separate observation under later exact live permission. This source
leg performs no live disk check.

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
key access, Apple status, native trust, controller admission or native acceptance of
the new owner/session producer. No repository test/seam is added.
TASK14178 extends that same case record for C4 current-time freshness (including
the old-permission/stale-isolation counterexample), and C5/C8/C9 failure journal,
validated status/UNKNOWN, publisher and marker boundaries. Its task-only probes
and old-source comparisons are portable evidence, not whole-stage/native proof.

Required automatic checks are exactly **Source Boundary Lint** and **Web Lint
(Node24)**, static only, with normal hooks/nonforce publication. Python units are
RETIRED; general integration including callback/lifespan/SessionTracker is
SKIPPED THR243seq42. No Product E2E or app rebuild is allocated. Independent FULL
code review, separate scoped QA and manager guarded merge remain required.
Root owns the merged-byte package, ONE later attended owner setup and THR104
attachment. Exact job update/readback, whole-file transport and native operations
remain separately authorized steps.
Actual Mac/Jenkins/native/Apple execution, signed final bytes, Finder/install,
independently downloaded quarantine behavior, macOS14 and public release remain
NOT RUN/open. Source delivery grants no live enablement or native-stage authority.

</details>
