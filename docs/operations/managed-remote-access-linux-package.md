# Managed remote access: Linux package operations (N3)

**Current PR800 allocation — THR228seq641 / sole ROOT TASK-12916.**
Founder seq641 authorizes **ONE publication and ONE automatic attempt1** of a
**PARTIAL installer + same-boot diagnostic** campaign, as proposed in seq638.
The new allocation starts **publication0/1 and automatic attempt0/1**; seq621's
publication1/1 and run1/1 are spent by actual FAILED run40056485462 at
`c8fdc845a50fc7c6f2740d87480b3dec0a47548f`. Every older grant remains exhausted;
that failure, its original AttributeError, empty records/diagnostics, terminalnull
and cleanupUNPROVED remain unchanged. The manager alone owns publication and
collection, and must report the actual result before any further repair,
publication or rerun. No retry or consultant three-run allowance is granted.
The one update must synchronize same-repository PR800 `task/TASK-6475` to `main`
from verified public before `c8fdc845a50fc7c6f2740d87480b3dec0a47548f` to a
distinct valid40hex event after equal to the actual PR head, checkout and proof
subject, on attempt1 only. Manual, reopen, main, rerun and later-update events
cannot renew this grant. TASK-12919 performs LOCAL source/static preparation;
FULL independent final-head review and same-head SOURCE/STATIC QA precede the
manager's publication. The automatic run must build that corrected subject's
actual wheel, frozen connector and package; old package bytes are not its proof.
The PARTIAL ceiling is conditional installer, startup and same-boot diagnostics;
fresh PID after a crash is SAME boot. Preserve all original assertions, strict
evidence and cleanup requirements and the unconditional incomplete/nonzero exit:
this campaign cannot yield complete N3/PASS. Missing startup/consumed-state,
barrier/stop-wins, rollback/full-OLD, credential-negative, diagnostic, measured
engine-close and physical-owner/keeper proof remains outstanding. Complete N3
still requires genuine reboot, both writer orders, rate/backoff/qualified reset
and intentional-stop-after-reboot evidence, original deadlines, successful
cleanup and complete terminal/validation. Reboot testing AND venue procurement
are expressly **DEFERRED until a working flow is demonstrated**, not waived.
Selected614's BOTH-positive-absence-only trust default leaves all other admission
gates intact, including actual installed AND loaded authority. Source/static
approval proves no sealing, installer, self-heal, complete N3, N6 or release
acceptance. Earlier allocation/hold passages retain historical scope; seq641
supersedes only the one publication/automatic-attempt hold stated here. No merge
or deployment is authorized. Required lint/static gates and separate Product E2E
policy remain unchanged. These banners describe the grant, not runtime success.

**Current managed-access precedence — sole ROOT TASK-12232.** LOCAL incomplete delivery; all publication/run grants are exhausted. Publication, execution and cleanup remain **HELD**. See [the current LOCAL lifecycle2 contract](#current-local-lifecycle2-source--root12232--task12399). Earlier owner, allocation and lifecycle1/H1045 passages below are historical snapshots; their last-slot instructions grant no continuation, readiness or cleanup.

Current lifecycle2 source renders BOTH services with `TimeoutStartSec=120`, `TimeoutStopSec=35` and `TimeoutAbortSec=35`; the target has `Wants` and `After` both services, services have mutual `PartOf` (also the target), and the sidecar has `Restart=no`. [Current renderer and loaded-reader contract](#current-managed-renderer-directives) takes precedence over historical omitted-start/stop10/Requires/BindsTo/independent-sidecar-restart descriptions. These source facts are not observed loaded authority or timing proof.

The historical THR-097 seq566 baseline comprised four redacted categories: `credential_input`, `engine_start`, `network_join`, and `durable_commit`. The current THR-228 seq396 local source contract extends that baseline to seven category/phase pairs: `credential_input/input_acquisition`, `engine_start/engine_initialization`, `network_join/peer_establishment`, `durable_commit/receipt_commit`, `connector/connector_probe`, `listener/listener_admission`, and `configuration/configuration_validation`. Configuration diagnosis covers direct `Start` validation; main pre-validation remains unchanged. These are source-only diagnostic mappings, not observed startup or restart acceptance. Evidence binds the mapped phase, actor/unit, failed terminal outcome, and completed assertion, and rejects missing, duplicate, prose-only, unmapped, inconsistent, incomplete, or secret-bearing receipts. This describes the current packaged diagnostic seam; the credential-delivery redesign remains future work.

**Bounded connector acceptance (THR-228 seq402/421).** `Sidecar.Start` anchors one 75-second acceptance deadline at entry; caller cancellation or an earlier caller deadline wins. The engine receives this enclosing context, so its private 80-second readiness ceiling cannot renew or escape the total. Connector probing uses at most 10 seconds and the remaining total, at most one second per dial, cancellable 200-millisecond spacing and no attempt-count cutoff; retries use the available phase deadline and remaining total. Immediate success has no retry delay. Only `errors.Is` connection-refused identity or an actual per-attempt deadline with deadline-error identity retries while the caller, phase and total remain live; arbitrary timeout text or a generic `net.Error` does not qualify. Cancellation, enclosing expiry, late success, nil successful connection and any probe close failure refuse. Every returned nonnil probe, including connection-plus-error, closes exactly once before a retry decision. Credential reading/redemption and engine startup occur once, with consumption commit in its original position before connector work. Listener failure, a late listener return or a Stop that wins admission closes the returned listener before engine teardown and cannot establish READY. Accepted serving retains the caller lifetime after the temporary startup context ends; main rechecks caller cancellation/deadline before READY publication.

These are acceptance deadlines, not a hard wall-clock limit on contextless initialization, `Listen`, close or cleanup. **Historical lifecycle1/H1045 renderer snapshot:** rendered units omitted `TimeoutStartSec` and retained `TimeoutStopSec=10`; the actual runner effective start timeout, systemd version and per-attempt credential staging were not captured. [Current lifecycle2 renderer/reader values](#current-managed-renderer-directives) are start120/stop35/abort35 for BOTH services; the historical timeout description does not govern lifecycle2. The connector supervisor's separate 80-second timer remains unchanged. The one-shot connector-race hypothesis remains unmeasured; marker/inventory/unknown diagnostics prove neither a unique attempt, completed durable commit nor sole historical cause. Credential custody, staged-input refusal, retirement/reload and historical receipts remain unchanged. Retained cases are authored source only: behavior, red/green, mutation, repetition and executable QA remain UNEXECUTED/SUSPENDED; general integration is SKIPPED THR243seq42 and former Python units remain RETIRED.

**Bounded N3 system-manager preflight (THR-228 seq409).** Before the execution ledger or product work, one owned `systemctl is-system-running --wait` query has a 120-second absolute observation deadline, including complete stdout, exit and final validation. Pinned upstream systemd v255 waits while initializing/starting, returns running with status 0 and degraded with status 1; only those exact LF-terminated byte/status pairs qualify. No decoding or whitespace/NUL normalization can admit malformed output. At most 32 bytes plus one overflow sentinel are held in memory; stderr is discarded. Timeout, unavailable/query failure, unknown, malformed/unexpected or contradictory output and nonready states refuse. Timeout/refusal/cancellation kills an unfinished query and reserves at most one additional second to confirm reaping; an unconfirmed reap refuses. Catchable shell HUP/INT/TERM before the child wait completes forwards TERM to the owned preflight and confirms cleanup; after a completed wait the handler exits synchronously, including the final flag-decision/trap-removal window. Both paths preserve exit 129/130/143 and fixed cancellation evidence (best effort if writing fails); traps are removed before subsequent harness work. Before the later cleanup trap exists, `system-manager-preflight.txt` in the existing always-uploaded diagnostics retains only fixed finite state/reason tokens and the observed numeric query status (or `not_observed`), alongside the unchanged bootstrap subject. Shell cancellation additionally retains fixed `shell_reason=cancelled` and its original numeric signal exit, including cancellation after the query has already completed. Failure retains exit 1 and the original fixed system-manager-unavailable message; no raw query bytes or stderr are persisted. This fits inside the existing 20-minute hosted job and adds no system-manager repair or privilege/provisioning. The prior run38180885346/attempt1 failed without state evidence; early boot remains a hypothesis. Readiness, timeout/cancellation, red/green, mutation and repetition behavior remain unexecuted/suspended; this document is no executable proof.

**SYSTEMD staged credential compatibility and mode capture (THR-228 seq428).**
The connector selects SYSTEMD from nonempty `CREDENTIALS_DIRECTORY`, then
configured `credentials_directory`, then the ordinary File provider. Generated
`LoadCredential=daemon.token` and the unchanged `credential-capability`
ExecStartPre own exact staged-directory, component-symlink, regular-file and
service-observable read-only checks. SYSTEMD `read_bearer` now refuses every
owner/group/other write bit (`mode & 0o222`), admitting read-only staging such
as `0400` and systemd ACL-mask `0440`. Its missing/unreadable/empty/non-file and
final-symlink refusals remain; this provider has no UID check. The ordinary
File provider retains `0077` refusal. Sidecar staged input retains its narrower
`0022` mask, which does **not** reject owner writes; its ordinary input retains
exact `0600` and current ownership. No sidecar policy change follows.

Pinned [systemd v255 write_credential](https://github.com/systemd/systemd/blob/v255/src/core/exec-credential.c#L150-L199)
removes writes and adds service-user read ACL permission; ownership fallback
requires the documented read-only remount capability. This supports ACL-mask
compatibility, not blanket privacy for arbitrary `0440` files or the historical
failure diagnosis. The previous run has no staged-mode or internal-gate evidence.

The two failure-only snapshots add `connector.credential_mode`, containing only
fixed location `systemd-daemon-token`, namespace `connector-service-root`,
four-digit numeric octal mode, microsecond observation time and a closed loss.
The existing original-MainPID worker uses metadata only through
`/proc/PID/root/run/credentials/happyranch-connector.service/daemon.token`, never
opens/reads the token. Root/mount view, file metadata and original process
identity are checked for ordinary drift. Both complete process samples must
agree apart from observation times; the enclosing boot/invocation/MainPID and
process-start binding applies. No raw ACL, UID or path is serialized. Missing
root/staging view is `unavailable_namespace`; missing final location is
`unavailable_location`, not proof of credential absence from the running
service. `unsafe_type`, query/launch/parse error, timeout, truncation and
attribution loss keep mode/time null. Mode alone establishes neither service
readability nor the last evaluated credential gate. All five internal gates
remain null/`unavailable_internal`, and original listener attribution remains.

Metadata shares the existing two-second process samples and 11-second section,
13-second command, one-second kill grace and 4096-byte output reservation;
there is no new section, budget, process connector, readiness API, privilege or
sandbox grant. Late/lost capture preserves the initiating exit and all cleanup
attempts. [Finite four-answer cases](managed-remote-access-staged-credential-cases.md)
are SOURCE ONLY: behavior/red-green/mutation/repetition/executable QA remain
UNEXECUTED/SUSPENDED, Python units RETIRED, general integration SKIPPED243seq42.
Seq428 allocation was consumed by run38259906794/attempt1 at590768ff,
which failed. Its published credential/mode source remains unchanged in this
harness-only seq435 correction; it supplied no actual staged-mode acceptance.
The reviewed seq435 update is historical and consumed; the then-separate seq498 allocation below is also historical and exhausted; [current ROOT12232 status](#current-local-lifecycle2-source--root12232--task12399) governs.

**Original connector observation (THR-228 seq421).** The two failure-only snapshots reserve a separate 13-second command allowance plus one-second kill grace for `connector`, after the existing sidecar-first channels and before teardown. The helper admits four observations under one 11-second section: named unit properties (one second), original process tree (two seconds), repeated unit properties (one second), repeated process tree (two seconds), each with at most one second for owned group termination/reaping. Reads and iterations are capped: 4096-byte unit output, 128 threads per process, at most eight descendants/four levels, 512 descriptors per process, 262144-byte TCP table per process, 2048-byte process projection and 4096-byte retained section. Boot, invocation, MainPID, process start ticks, parent relations and exact unit cgroup bind the existing Go supervisor and its actual descendants, including the onefile launcher's Python worker. Repeated process, child and socket sets must agree. Only a matching owned socket inode in LISTEN at the harness-configured literal `127.0.0.1:18443` yields `listening=true`, its actual `listener_pid` and `bound_port=18443`; a complete stable scan without it yields false/null. This is sampled process-attributed listening evidence, not atomic readiness or a hard bound on kernel filesystem stalls. Missing MainPID is `failed` for a failed unit, otherwise `absent`; unavailable child/process, query/launch/parse failure, attribution loss, timeout and truncation remain explicit, with null listening and binding. Unknown StatusText is reduced to `unclassified`; raw logs, tokens, identities, argv, paths, requests, trust state and credentials are never retained. Systemd's fixed aggregate `composite healthy`, stopping and exited statuses are observations, not gate-level cause. All five original-instance readiness gates are `ok=null/loss=unavailable_internal`: the existing private child health pipe publishes only aggregate states. The separate `diagnose` CLI constructs a new supervisor, readiness evaluation and adapter; it is never called or labeled original process state. No new inspection API, listener or initialization is added. Evidence failure neither admits readiness nor changes the initiating exit or remaining cleanup attempts. Collector cases are source specifications with executable coverage unavailable after Python-unit retirement; no behavioral PASS follows.

**Historical single N3 publication selection (THR-228 seq435, consumed).** The PR800 candidate integrates current-main lint-only `Source Boundary Lint` and `Web Lint (Node 24)` plus the existing Managed N3 package/harness/evidence job. N3 selects only a `pull_request` `synchronize` for PR800 on same-repository `task/TASK-6475` targeting main, with payload `before=590768ff22e41dd7e519011589b784d4bea7ee3d`, changed `after` equal to the event head, and `run_attempt=1`. Checkout and proof subject use that `after` SHA. Other PRs, opening/reopening, subsequent updates, main pushes, manual runs and reruns cannot renew this allocation. Current-main hooks/local `all` remain lint/static only; no general build/test lane is restored. Product E2E remains daily-only 19:00 UTC from default branch. The manager published112f90ec after independent review and scoped QA; run38347173460/attempt1 FAILED. Seq402, seq409, seq421, seq428 and seq435 are exhausted (1/1 each), with all historical failures preserved. Historical seq444 authorizes local source preparation only, no publication/run.

**Historical single N3 allocation (THR-228 seq455, consumed).** TASK11205 published `86ec1dca0262186500457069e1e6740c8ed084b1` after full independent TASK11213 APPROVE and same-head SOURCE/STATIC TASK11217 QA PASS. Automatic run `38412983176` attempt1 FAILED waiting for connector READY; Source Boundary Lint and Web Lint passed. Seq455 is1/1 consumed. All three initial start jobs were done; later both services were inactive/status0. The retained supervisor invocation records `sidecar_unhealthy`, first local `context_cancelled`, then shutdown consequences, with `original_invocation=null` and `window_only` attribution. It does not establish the original activation, global initiating cause or absence of other defects. `virtual_stop_binding_unconfirmed` and `virtual_removal_unconfirmed` are missing proof, not demonstrated residue. Full lifecycle/final cleanup remain unproved; original artifacts, failures and predicates are preserved.

**Historical single N3 allocation (THR-228 seq464/471, consumed).** Completed TASK11354 published `b3df7794c84866f1e3e5ccfee6a8804bfee0001f` after independent full APPROVE and same-head SOURCE/STATIC QA. Automatic run `38518279270` attempt1 FAILED at the staged credential provenance query after initial READY; seq464 is consumed1/1. The exact OS error was not captured. Snapshot health establishes initial startup only, not sustained lifetime or full acceptance. `virtual_removal_unconfirmed` is missing proof, not demonstrated residue. Seq402/409/421/428/435/455 and every older allocation remain consumed; all failed runs, reviews and receipts remain historical at their actual sources. The managed-target correction below remains in the candidate; no allocation is transferred or renewed by a follow-on task.

**Historical first N3 candidate (THR-228 seq498; sole root TASK11641).** Seq491 is historical and consumed1/1: COMPLETED11547 published a750114baf651b5d7688baf6e03e0b4c92138b77; run38689824548 attempt1 FAILED after initial READY and both staged credential checks. Its missing-marker assertion discarded start status and inferred success from a non-inactive state. Exact assertion-time state is unknown; later SubState is unknown/parse_loss, not established auto-restart. Neither product correctness nor sustained startup acceptance follows. Original failures/reviews/jobs/cleanup gaps remain preserved.

**Historical pre-third-run LOCAL N3 candidate snapshot (THR228seq498; sole root TASK11641; superseded by its terminal closeout).** Slot1 run38775630461 attempt1 FAILED at fe76f1d18d2c584e4fb36a3e3e7ec4fa5f7b9248 after seven startup records with unavailable stopped baseline; exact query/parser rejection and GC cause remain unknown. Slot2 run38782221731 attempt1 FAILED at9219f4c6e168b5529f59503e34c498bf23c9fe9f: held live/stopped baseline predicates passed, but subsequent preparatory offline-peer acquisition refused before marker removal/start. Exact peer query/process/parser/state cause is UNKNOWN; delayed propagation was not observed. Initial READY is not sustained/lifecycle acceptance; cleanup unconfirmed is missing proof, not demonstrated residue. Seq498 is2/3 consumed,1 remaining. Both allocation boundaries pin only the final ordinary synchronize from before=9219f4c6e168b5529f59503e34c498bf23c9fe9f to distinct canonical after=actual head, attempt1, with unchanged PR800/same-repo/task6475/main and package/proof binding. This candidate stays LOCAL before fresh full independent review, same-head SOURCE/STATIC QA and root evaluation/reconciliation; children have no publication/execution authority. Root alone owns publication/observation/actual-result reporting and stops on success, product defect, necessary out-of-harness work/uncertain protected scope or unconditionally after the third run. No fourth update/post-third repair, general policy or daily-E2E change. Historical failures/jobs/reviews/source remain preserved. All local assessments are SOURCE/STATIC ONLY; behavior/test-authoring proof remains unestablished, units/behavior/red-green/repetition UNEXECUTED/SUSPENDED, Python units RETIRED and integration/actual callback SKIPPED24342. Normal hooks remain required.

**Historical recovery snapshot (THR-228 seq514/523; 2026-10-18; superseded).** [Current ROOT12232 status](#current-local-lifecycle2-source--root12232--task12399): LOCAL incomplete, grants exhausted; publication/execution/cleanup HELD. The owner and last-slot directions in this paragraph record the historical snapshot only. The preceding paragraph preserves the pre-third-run observation. TASK11641/seq498 subsequently ended with all 3/3 attempts FAILED, including run38788101249 attempt1; that allocation is exhausted. TASK11746 is terminally FAILED. Sole delivery root TASK11824 recovers its preserved local candidate under THR-228 seq523 (approving seq520), carrying the SAME seq514 two-run ceiling, not resetting it. The predecessor checkpoint recorded 0/2 publications/runs. ROOT11824 subsequently published ef586038; run39124834332/1 FAILED after14 completed observations, with terminal=null and incomplete product/fixture cleanup proof. Its actual result was reported/read back at THR-228 seq526: binding observed, real probe failed_timeout, sync observed, journal query_error and independent peer parse_loss; removal refused, MainPID check unreached. Snapshot consumption was 1/2, no reservation, remaining1 CLOSED. FULL11864 and QA11865 cover ef586038 SOURCE/STATIC only. This conditional LOCAL correction requires NEW FULL exact-head independent review and SAME-head static QA before ROOT considers publication. Both conditional final-update guards bind only before=ef586038b9fc1ba6bb830b36a6998165fab41dae to distinct canonical after=actual PR800 head, attempt1, retaining same-repo/task6475/main/synchronize and checkout/package/proof subject binding; local guard source grants no slot or publication. See [the historical case and allocation snapshot](managed-remote-access-staged-credential-cases.md#stop-receipt-acquisition-and-refusal-diagnostics--thr228seq514). TASK11824 alone owns publication, reconciliation, observation and per-run actual-result reporting. Stop on success, product defect, necessary unauthorized scope/uncertain protected change, or the second run; no post-second repair/publication. This documentary parity grants no execution, charter rebind, merge or deployment authority.

**Private pre-attempt offline acquisition.** Pinned Tailscale1.102.3 Online is a control-plane connection observation; Headscale0.25.1 disconnect/status-update/notification is separate from local systemctl stop completion. Only a structurally valid SAME enrolled peer still Online=true may settle BEFORE marker removal/start: at most four observations,0.5s between valid online observations, accepted only within12s and before40s from original holder launch. Each observation revalidates the full original stopped tuple (including result, invocation, nonzero READY, main-execution history, main/control PIDs, restart count and inactive-exit history), boot and same held client/owner/object. Missing/duplicate/nonboolean/malformed/query/timeout/process/identity/history/reference errors immediately refuse, including after an earlier valid online read; later success cannot erase failure. Offline must actually be observed within the bounds; exhaustion refuses. Query status and fixed parser/predicate categories are retained without raw peer JSON/keys/tokens. Holder lifetime120s/outer125s/+1s stays unchanged; ordered optional prepare-1..4 fence pairs never reacquire or reset history. The original negative-attempt interval, failed-job/start-status/history/readiness/process/admission vetoes and independent teardown remain unchanged. No post-start settling/online forgiveness follows; subsequent lifecycle/restart/installer proof remains missing.

**Private retained-history acquisition.** Before the live tuple, the existing harness helper opens one persistent system-bus connection and calls RefUnit for the existing sidecar. It never reconnects/reacquires. Original manager unique owner, exact unit object and same-client Refs membership are checked on that connection; boot/run-bound ordered acknowledgements fence BOTH sides of every existing and bounded preparatory state read through the final stopped-history fence. A disconnected one-shot client, lost/duplicate own reference, changed owner/boot, malformed command, query failure or expiry refuses. RefUnit prevents normal GC while held; it does not waive missing InvocationID, zero/changed ActiveEnter, history resets, main execution, restart counts or same-attempt failed-job proof. Unit query statuses and a fixed parser-refused category are retained without raw property/error dumps. The holder has a120s absolute lifetime,2s bus-call timeout and125s outer timeout/+1s kill grace; each shell acknowledgement is bounded8s. Ordinary return releases/unrefs/closes/reaps before marker restore/reset, preserving any initiating nonzero status. Cancellation cleanup independently closes/reaps it and continues all existing teardown attempts; timeout/OS connection closure releases a failed holder. It installs no unit/helper/dependency and changes no shipping credentials, restart policy, sandbox or public contract. This acquisition is source-supported by the actual runner image's systemd255.4-1ubuntu8.17 source; actual candidate behavior remains unexecuted.

**Same-attempt missing-consumed-state oracle (THR228seq498).** The shell retains safe bounded metadata in missing-consumed-state.log. It captures a known nonzero live ActiveEnter baseline and enrolled identity, then stopped-unit/process/offline baseline before moving the marker. Under unchanged shipping Restart=on-failure it retains the actual bounded start status and pre-stop state, independently stops the target AND both services, and fences history before any marker restore/reset/new start. Acceptance requires status1, one trusted PID1/UID0 failed sidecar start job in the same boot/monotonic interval, a new stable invocation, unchanged nonzero ActiveEnter, zero main-execution timestamp/PID and current main/control PIDs, and NRestarts=0 through final stop/peer observation. A pending auto-restart may satisfy these combined predicates; state alone never proves refusal. systemd255 resets ExecMain history on EVERY start, while stop retains NRestarts until next start; an enqueued retry or changed/lost baseline therefore refuses. This detects brief READY via retained active-entry history and pre-READY process/admission via the single-start main-execution proof, rather than post-stop absence. Boot/invocation drift, unloaded/reset/lost history, query error/timeout, malformed/duplicate/unknown data or ambiguous transition refuses with no completed assertion. The existing enrolled peer must be affirmatively offline under the same live observer/identity; offline alone proves no historical admission. Unconfirmed peer propagation remains missing evidence. Marker restoration occurs only after stopped proof, and credential-free restart remains a distinct required stage. The finite sweep also corrects both ordering-barrier queries: a target-name-only match could be the opposite existing job; the bounded query now requires exactly one target job of the requested start/stop type in waiting state, with malformed/changed queues refusing. No product/unit/credential policy, public observation contract, evidence schema or independent teardown change follows. Static review establishes no runtime correctness.

**Post-READY staged inspection (THR-228 seq491).** `check_staged_credential` resolves only the existing fixed connector `daemon.token` and sidecar `enrollment.key` paths with `sudo readlink -f --`, so root-private directory traversal does not depend on runner access. Acquisition must succeed and the canonical result must equal the fixed path; regular-file, non-symlink and C-locale owner/group/mode/type metadata predicates remain. Separate `sudo -u happyranch` readability and file/directory nonwritability assertions still decide service capability: root inspection supplies no service-access proof and reads no token contents. The finite later-stage audit found no additional attributable inspection privilege mismatch: `test_false`/`absent` already invoke sudo (including their `-u happyranch` form), barriers use `sudo test`, journal/peer private-socket queries are privileged, installer traversal/digests use `sudo find`/`sudo sha256sum`, and unit metadata uses the system bus. Explicit-stop/watchdog/restart/ordering/shutdown/installer assertions, same-instance Stop/current-peer/real-dial proof, failed-query refusal, original exit and all teardown attempts remain unchanged. These are source findings, not executed lifecycle acceptance.

**Historical lifecycle1/H1045 managed-target correction (THR-228 seq464 approving463).** [Current lifecycle2 graph and directives](#current-managed-renderer-directives) supersede the Requires/BindsTo snapshot in this paragraph. `render_composite_units` omits `StopWhenUnneeded=yes` from `happyranch-managed.target`: a standalone explicit target start must remain active without another active unit requiring or wanting it. Target `Requires` and `After`, both service `PartOf` links and sidecar `BindsTo` remain unchanged; explicitly stopping the target still propagates to both services. No restart, identity, notification, credential, security or cleanup contract changes. The systemd255 documented mechanism and consultant259 throwaway reproduction support this correction, but neither proves the sole historical cause or excludes another defect. Sustained READY, actual reached lifecycle stages and authentic cleanup require the root manager's allocated hosted N3 evidence; source/static inspection alone supplies none of those results.


Both separate services retain `NotifyAccess=main`. Systemd starts them concurrently, without a connector/sidecar readiness-ordering cycle. The connector Go MainPID starts the frozen Python child with a private health pipe and without `NOTIFY_SOCKET`; canonical versioned, generation-bound, strictly monotonic records are necessary but not sufficient for READY/WATCHDOG: the sidecar unit must also be active, which means its enrollment, ExpectedPeers, listener, and connector-reachability gates completed. The sidecar adapter derives one caller-bounded readiness context, capped by its private 80-second ceiling and clipped by the enclosing 75-second acceptance deadline, across `Up` and expected-peer polling; it never refreshes on progress. Its contextless initialization cannot be interrupted, so immediately after it returns the adapter re-checks the original caller context and original absolute deadline before beginning `Up`, `Status`, or peer work (the AuthKey reference is released immediately after initialization returns); an expired caller maps to `network_join` without a renewed budget. After READY, only a complete current connector-plus-sidecar health observation refreshes liveness. Each post-READY window keeps an absolute freshness deadline: the sidecar query is bounded by its remaining time and the existing one-second query cap, with expiry re-checked after result delivery and immediately before WATCHDOG publication. A completed healthy result delivered after expiry cannot emit WATCHDOG or renew the window, even if its pending timer event has not been selected. Only a timely successful publication establishes the next configured window; notification delivery does not restart elapsed time. Expiry latches terminal readiness even when sidecar admission removal is refused or unknown, without authorizing child cleanup. Both pre-READY composite health observations are bounded by and re-checked against the original absolute startup deadline, so a health query that only completes after that deadline can never refresh or extend it; expiry, failure, cancellation or any stop decision permanently latches terminal readiness and suppresses later READY/WATCHDOG independently of the separate sidecar-admission permission that gates connector child cleanup. Cancellation is re-checked at each positive publication boundary, so an already-completed health query whose result delivery is released after the supervisor context is canceled can never emit READY or WATCHDOG; the terminal STOPPING notification uses one at-most-once latch shared by the stop and child-exit paths, so a refused admission stop followed by a spontaneous child exit emits exactly one STOPPING while the failed exit classification and the independent child-cleanup permission are retained. A completed child Wait and its authoritative reaped state are fenced before every positive READY/WATCHDOG publication boundary, so a completed healthy observation held across a spontaneous child exit can never publish after that child was reaped, while the single Wait owner, the failed exit classification, and the admission-before-child-TERM rule are preserved. The supervisor-owned health-reader scanner uses cancellation-aware delivery and is closed and joined within a bounded lifecycle on every return path, so a decoded record pending on the unbuffered channel cannot outlive supervisor return. Missing/stale, malformed, partial, duplicated, reordered, unknown, exited-service, or lost composite health stops watchdog publication and fails closed.

On a failing positive shipping-unit start, the N3 harness makes a bounded, category-only pre-teardown snapshot of closed systemd states, numeric status, scoped jobs and credential-presence booleans. Its first observation owns an independent three-second, 32-line, 4096-byte allowance and reads only the sidecar unit's current-boot journal in plain-text mode over the exact positive-start window. An in-stream exact filter runs before both caps and admits only schema-valid closed `diagnostic_receipt=...` records, `readiness_unavailable`, and `watchdog_unavailable`; malformed receipt-like text and unrelated journal text cannot consume the retained-output allowance and are never serialized. The plain channel records `observed`, `empty`, `query_error`, `timeout`, `truncated`, `parse_loss`, or `launch_failure` without borrowing from later reads. The existing JSON channel remains supplemental and adds invocation attribution when available. Both channels precede connector, target, and credential observations. The snapshot records the pre-start microsecond window, compact boot identity, independent reserved budgets, and closed loss terms rather than turning unobserved data into confirmed absence. It retains no other journal prose, paths, credentials or command arguments. Observation helpers restore the caller's shell options, so a bounded capture cannot re-arm errexit under the EXIT handler: an independently failing stop, disable, or reset cannot abort the remaining teardown, and EXIT/INT/TERM share exactly one cleanup pass. A failed/late snapshot or cleanup never overwrites the initiating exit status; ordinary local fixture success remains distinct from Managed N3 acceptance.

**Credential input and pinned logging correction (THR-228 seq390).** Enrollment input strips only surrounding Go `bytes.TrimSpace` whitespace (including LF/CRLF); clean and interior bytes are unchanged. Empty/whitespace-only input refuses before engine work. Existing owner/mode/symlink checks, consumed-marker refusal and one-use commit order remain. The entire owned file-read buffer is cleared on every read result; startup clears the independent normalized buffer after every engine result. The tsnet adapter drops its AuthKey string reference immediately after every returned initialization result, including errors or an expired caller, before any readiness work. This clears owned byte buffers and releases the string reference; it does not promise erasure of upstream/Go runtime copies.

The pinned Headscale 0.25.1 fixture explicitly requests `log.format: json` and `log.level: info`. Source at `ade7f2a2b1795768ce146913e25011cd11a1a012` nevertheless hardwires zerolog ConsoleWriter in `cmd/headscale/headscale.go`; the JSON-output switch in `cli/root.go` is commented out. Effective JSON output is therefore **unverified and unsupported by that source**, and bounded console parsing remains necessary. The fixed event allowlist binds exact producer message, level and caller suffix for `hscontrol/noise.go` and `cmd/headscale/cli/serve.go`; JSON uses zerolog 1.33.0 `level`, `caller`, `message` fields, while actual console records use RFC3339 time, abbreviated severity and caller. Extra error/key/SQL fields never leave the collector. Unknown events remain `unclassified`; malformed or incomplete records remain `parse_loss`, with timeout/unavailable/truncated states preserved. Registration refusals in `auth.go`/`noise.go` emit no dedicated event at the configured level, and database logging is silent without debug. JSON configuration or valid event counts establish neither an auth-refusal discriminator nor the sole historical cause. No live registration experiment or new diagnostic run is authorized here.

**Bounded diagnostic detail (THR-228 seq322).** Only `network_join` adds fixed `sub_reason`: `unclassified`, `context_cancelled`, `deadline_exceeded`, `up_backend_error`, `up_no_ip`, `up_error_unclassified`, `up_status_unavailable`, `up_not_running`, `peer_status_error`, `peer_status_unavailable`, `peer_not_running`, `peer_wait_deadline`, or `expected_peer_missing`. These describe observed adapter stages; generic upstream errors remain unclassified. Embedded tsnet uses tailscale.com v1.78.0, distinct from the harness peer v1.102.3. Safe error text, category/phase/exit status, the adapter’s shared readiness context and consumption decisions are preserved; its 80-second ceiling is now clipped by the seq402 enclosing 75-second acceptance deadline. Both receipt channels reject duplicates, extra fields, invalid types/enums and nonfinite values, preserving old field omission; the plain channel reserializes allowlisted fields before its retained-output caps.

The two failure-only snapshots also read the existing Headscale fixture before any teardown. `headscale` exposes only process state, bounded fixed log-event counts (`unsupported_client`, `missing_upgrade`, `initialization_error`, `server_shutdown_error`, `unclassified`) and exact synthetic peer/sidecar count/state projections. Log events are fixture history without authenticated attribution to a sidecar invocation. A complete successful node query can establish empty/absent; unavailable, failed, timed-out, truncated or malformed observations cannot. Partial valid records retain counts with unknown completeness/state; exact `name` is required, with no given-name fallback. The pinned Headscale v0.25.1 omitted `online` boolean means false. These observations establish no credential usability, listener readiness or historical cause.

Headscale PID/read/parser stages each reserve one second plus one second for group termination/reaping; node query separately reserves three seconds plus one second, and its parser separately one plus one. Reads/query output are bounded at 64 KiB (log has one overflow detector), log parsing at 256 lines, nodes at 64 and each safe parser output at 2048 bytes. Budgets are independent of sidecar, systemd and credential sections. Dedicated Linux capture owns and reaps its child groups, including adopted pipe-holding descendants; inability to establish that ownership launches no observation. Raw observations stay in private memory and never enter uploaded diagnostics. Closed losses distinguish observed, empty, unavailable, launch_failure, query_error, timeout, truncated, parse_loss and unattempted. Capture failure preserves the initiating exit and once-only complete cleanup. Local shell fixtures remain causal verification, not real-systemd Managed N3 acceptance.

**Bounded review correction (THR-228 seq331).** Each observation keeps its original absolute deadline through launch, writes, selector/read/EOF delivery, exit observation and final positive acceptance. A successful result delivered after expiry is timeout with no payload or inferred empty/count/state; its separately reserved termination/reaping still runs. Independently timely sibling observations retain their evidence.

Sidecar shutdown retains one listener-first teardown owner, closes active connections, drains proxies and closes the engine exactly once. An accept-error caller that loses ownership returns so the owner can join the accept loop; it cannot wait on the teardown that is waiting for it. Every concurrent or repeated Stop waits for teardown and accept-loop completion and reports the winning owner's same safe error. In-flight Start/Listen resolution and no post-stop admission remain unchanged.


N3 composes the portable Python connector and embedded Linux tsnet sidecar into one reproducible root-owned artifact. In system-service mode its payload root, binary directory, and binaries are mode `0755`, so dedicated non-root system-service users can traverse to and execute the binaries while ownership remains root. The installer selects that contract only with explicit `system_service=True`; optional no-root installation keeps those directories and binaries owner-only (`0700`). It does not provision a control plane, deploy a service, enable managed access by default, or begin macOS work.

```sh
cd app/linux/tsnet-sidecar
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -buildvcs=false -o ../../../dist/happyranch-tsnet-sidecar ./cmd/happyranch-tsnet-sidecar
python tools/generate_inventory.py
cd ../../..
uv build --wheel --out-dir dist
uv run python app/linux/package/build_connector.py --wheel dist/happyranch-0.1.0-py3-none-any.whl --output dist/happyranch-connector
uv run python app/linux/package/build_package.py --sidecar dist/happyranch-tsnet-sidecar --connector dist/happyranch-connector --wheel dist/happyranch-0.1.0-py3-none-any.whl --version 0.1.0 --output dist/happyranch-linux-amd64.tar
```

The manifest hashes every payload. `share/sbom.cdx.json` and `share/THIRD_PARTY_NOTICES.md` couple to the exact checksum-pinned N1 inventory; composition refuses a module missing from the notices. No dependency resolution occurs during composition.

`build_connector.py` installs the real wheel into an isolated build target and freezes that installed code with the repository-pinned PyInstaller build dependency. The archive therefore carries an explicit self-contained Python executable; it never relies on a source checkout, ambient `python`, or an inert wheel as launch proof. The wheel remains bundled as an exact receipt.

Before publication, the shared credential-capability preflight classifies each unit's root-custodied `0600` source as absent, wrong type, unsafe symlink, wrong custody, staging-incompatible, or valid using category-only output. At start, each unit requires the exact `/run/credentials/<unit>` provenance and exact basename and invokes the same preflight against only its own systemd-staged copy: the connector checks `daemon.token`; the sidecar checks `enrollment.key` and never receives the daemon bearer. Staged validation is ownership-neutral because systemd determines staged ownership; it accepts only a regular, non-symlink credential that the service can read but cannot open for writing. First enrollment comes exclusively from `$CREDENTIALS_DIRECTORY/enrollment.key`, staged by a transient owner-only systemd drop-in; staged files are never copied or unlinked by service users, and `StateDirectoryMode=0700` is explicit. Only after READY and durable consumption does the elevated helper remove the drop-in and successfully reload the manager before retiring the source. Its privileged preflight repeats the reload before deleting a retained source or marker-authorized `.retiring` residue, including when interrupted unlink already removed the drop-in: absence does not prove successful reload. Failed reload preserves the source/residue and consumed marker for an explicit retry. Both retirement commands return exit 1, empty stdout and exactly `error: enrollment_source_retirement_failed` followed by a newline on stderr, without upstream text or a traceback. Ordinary, crash, and stopped-service restarts therefore use durable state without requesting the retired credential, while a missing marker fails closed. Fresh re-enrollment requires an explicit stopped-service operator action: install a new root-owned `0600` source, invoke `prepare-fresh-enrollment` to replace consumed state and publish/reload the transient drop-in, then start the composite. `prepare-fresh-enrollment` performs the marker/drop-in mutation and the daemon reload only after one bounded successful `systemctl show` observation whose explicit named properties affirmatively prove the sidecar unit is loaded and stopped (`LoadState=loaded`, `ActiveState=inactive`, `SubState=dead`). The query is an owned child whose stdout is read incrementally under one absolute five-second deadline shared by reading, exit observation and final affirmative acceptance after the strict property validation; at most 4096 accepted bytes plus one overflow-detection sentinel are retained (maximum 4097), an over-cap producer is stopped and reaped, and the owned child/pipe are closed on every path; after the observation deadline expires, a separate explicitly bounded kill/reap allowance (which never restarts reading or authorizes a refused observation) confirms the owned child is reaped before the query returns, and an unconfirmed reap refuses category-only rather than being silently ignored. Raw record framing is validated before any normalization: only LF-delimited named records are accepted (optional single trailing LF), while CR/CRLF, VT, FF, FS/GS/RS, NEL, LS/PS, embedded invalid UTF-8 and control bytes are refused. A nonzero query status, timeout after an actual subprocess entry (the child is killed and reaped), missing executable/manager/unit, unknown, missing/empty/duplicate/extra/malformed/oversize/unsupported-framing/contradictory record, and every active/transitional/failed state refuse with the existing category-only nonzero status and a completely unchanged source/marker/drop-in/directory snapshot, no `.new` or other publication residue, and zero daemon reloads. Each MainPID owns only its unit READY/WATCHDOG. Connector watchdog refresh additionally requires a bounded-fresh complete systemd state for both units; stale, stopped, failed, restarting, unknown, timed-out, malformed, or missing-service observations cease composite health. Removal is tri-state: only confirmed absence permits downstream cleanup, confirmed presence triggers bounded stop/re-observation, and unknown fails closed. Target stop removes sidecar admission before connector cleanup.

The dedicated `Managed N3 Real systemd (Ubuntu 24.04)` CI job is fail-closed and zero-skip. It qualifies PID 1, the system manager, passwordless sudo, and transient units; verifies native Headscale v0.25.1 sha256 `d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b` and the reused CI-only Tailscale 1.102.3 peer tarball sha256 `36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5`; then requires the packaged production tsnet sidecar to see `synthetic-peer-ci` through `ExpectedPeers` before systemd READY. Both artifacts are BSD-3-Clause and already covered by the existing CI-lab artifact policy; this unit introduces no new distributed package input or notices duty. The genuine peer probes the virtual TSNet `:443` listener directly; host port 443 and connector port 18443 are not admission evidence. Explicit systemd barriers force both orderings. The packaged process calls production `Sidecar.Stop` twice on the same instance and emits PID/invocation-scoped receipts. Every fresh publication fault proves payload, units, markers, backups, stages, and temporary parents absent before successful same-root re-entry; upgrade rollback/re-entry remains required. Cleanup probes virtual admission gone while the peer is alive, then reaps all processes and proves all listeners, MainPIDs, units, package/config/state/runtime/log paths and transaction residue absent. Diagnostics live outside the cleaned work root. Only a successful run finalizes the versioned execution artifact; it binds exact git head, package digest, unique run, unique phase/observation records, completed assertions, zero skips/fakes, terminal completeness, and a whole-document digest. CI independently validates and always uploads it and diagnostics. N3 does not exercise or claim admitted HappyRanch requests or authorization-negative behavior; those proofs remain N6.

Cleanup and shipping reset require affirmative absence from each observation: the full, unfiltered `list-unit-files --full --no-legend --no-pager` inventory must return 0, every UNIT FILE/STATE/PRESET row must be valid, and none may name exactly `happyranch-managed.target`, `happyranch-connector.service` or `happyranch-tsnet-sidecar.service`; unrelated and prefix/suffix lookalikes do not count as those units. Named no-match exit 1 is not used as absence evidence because query errors share it. Empty, malformed, duplicate or failed/partial inventories refuse; raw inventory and stderr are not retained. `find` must return 0 with empty output; both services' MainPID must be exactly `0` from a successful systemctl query (status0), including unloaded units under pinned v255 SHOW_PROPERTIES. The managed target has no MainPID property: shipping reset requires its common LoadState/ActiveState/SubState to be exactly `not-found`/`inactive`/`dead` at status0, together with absent exact unit files and drop-in paths under `/etc/systemd/system` and `/run/systemd/system`, and the successful full-inventory check. Missing target MainPID is not queried; missing or empty service MainPID still refuses; only pgrep status 1 means no matching process. Empty or valid-looking output from a failed query, an empty or malformed MainPID, and pgrep errors remain unknown and refuse successful reset or residue-free finalization. Cleanup still attempts the remaining teardown exactly once and preserves an initiating exit 37, INT 130 or TERM 143. Refusals emit only `n3-cleanup:<phase>:<predicate>` with fixed `shipping`/`final` phases and a closed vocabulary in the harness; `inventory_query_failed`, `inventory_malformed` and `unit_file_residue` distinguish failed enumeration, invalid rows and exact owned residue. Other identifiers name the existing reload, fixture, unit-state, path, listener, process, stage, MainPID, work and evidence predicates without values or upstream text. Each pair emits at most once per run (at most 46 lines and less than 4096 bytes), cannot abort remaining teardown, and does not replace the initiating status. The shipping barrier cleanup removes only its four owned markers before `rmdir`; an unexpected child is preserved and refuses cleanup, while adjacent durable state retains its bytes and mode.

The installer validates duplicate-free exact archive/manifest membership against a closed path set, normalized paths, strict non-boolean schemas, architecture, count, hashes, and modes before any write. Inventory and CycloneDX fields (coordinate, purl, SPDX, go.sum and license checksum) plus structured notice coordinates/SPDX/checksum/content match one-to-one.

Install publication is one bounded transaction governed by a single owner-only, atomically-published marker. The record is schema version 2 and carries only the minimum ownership facts needed to classify recovery: the exact attempt identity bound to the root (the attempt is the canonical 32-hex token and the stage name must be its exact `.happyranch-stage-<attempt>-<8 chars>` product, with whole-string attempt and suffix validation and the exact recorded path spelling; trailing LF, CR, CRLF or TAB and normalized path aliases are refused, so a bare prefix is never ownership), recorded prior existence and a complete recursive identity for the payload tree (per-entry type, SHA-256 and mode, plus directory modes), each unit and the enrollment drop-in, the recovered backup inventory (SHA-256 and mode for every copied backup), the intended NEW payload/unit/drop-in identity used to classify a torn or published NEW artifact, the newly-created parents restricted to the finite source-derived parent plan (`opt`, `etc`, `etc/systemd`, `etc/systemd/system`, and the drop-in directory when the run publishes one), the publication intent/progress, and the authoritative commit distinction. No record contains credentials. The first valid record is written before any prior (OLD) byte is mutated; each progress update is an atomic temp-create/write/chmod/replace that preserves the previous valid record on a partial failure. Every exception-rollback or recovery decision is made from the last record that actually reached disk, never from an in-memory phase whose replacement failed.

Preparation never mutates OLD, and an incomplete backup never authorizes replacing or deleting an original. Full prior bytes and modes are preserved for the payload tree, each rendered unit, and any pre-existing enrollment drop-in (including its directory and unrelated siblings); a prior drop-in or unit that was absent is recorded as absent, never inferred from a missing backup. A restore consumes a backup by atomic rename, so recovery recognizes an already-restored artifact from its recorded identity rather than demanding the consumed backup again; a present-but-mismatched backup, or a missing backup whose active tree does not match the recorded OLD identity, is refused unchanged before any mutation. After the entire intended NEW publication and modes complete, the record advances to `committed` **before** any old backup is deleted; a failure before that replace restores OLD, a failure after it retains complete NEW. A `committed` record is treated as authoritative only when the complete active payload tree, every published unit and the applicable enrollment drop-in (or the unchanged recorded prior drop-in) equal the exact intended NEW identities recorded in that same marker; a contradictory, missing, or partial active NEW composition returns `transaction_state_invalid` unchanged before any cleanup deletes an OLD backup, and commit validity is never inferred from a missing backup. Cleanup then removes only exact recorded owned paths (never a prefix or glob), removes recorded newly-created parents only while they are still expected and empty, and unlinks the final marker last. A cleanup failure after commit keeps NEW authoritative and is retried on re-entry — it never reconstructs a mixture from incomplete old backups.

Recovery is state-driven and classifies before it mutates. An interrupted uncommitted transaction restores OLD (uncommitted exception rollback and interrupted recovery are separate routes through the same real operations); a committed transaction preserves NEW and resumes cleanup. Genuine owned transactions recover after a transient fault is removed and re-enter successfully; they are never permanently refused. A fresh-payload rollback is resumable: once the record has advanced to `rolling_back`, a partially removed tree whose surviving entries are each an exact member of the recorded NEW identity is owned deletion progress and the remaining members are removed, rather than being wrongly refused as a foreign tree; while the fault persists the partial state is preserved, and once it clears recovery reaches full OLD/fresh absence and two clean installs succeed with no owned residue. A record with a wrong root/attempt identity, a stage whose exact `.happyranch-stage-<attempt>-<8 chars>` shape does not match or which is missing/null (the writer always records the concrete allocated stage, so a missing identity is never treated as a reachable stage-absent state), a created parent outside the finite plan, traversal/escape paths, unknown version or phase, duplicate or missing/extra/wrong-typed keys (including non-string list elements, which are classified rather than leaking `TypeError`), a digest-mismatched or missing backup, or contradictory prior/commit/progress facts is preserved unchanged and refused with the existing `transaction_state_invalid` category. Unsafe symlinks or unexpected types at the root, marker/temp, stage/backups, payload, unit, drop-in, any ancestor below the root, or any ancestor above the selected root are refused before a target is written or deleted, so an ordinary write never follows a link: expected leaves must be regular files or real directories, so a FIFO, socket, device, or link reaches the preflight category instead of a filesystem operation. An unrecorded preparation residue — an orphan stage or backup, or a lone `.happyranch-install-transaction.json.tmp` — is likewise preserved and refused.

**Legacy disposition.** Every schema-v1 package transaction marker — including the formerly accepted `prepared`/`payload_retained`/`payload_published`/`units_publishing` compositions — is refused unchanged, because it lacks sufficient prior-presence, drop-in, and complete-backup ownership facts. This intentionally narrows package-marker acceptance under the seq246 conservative-marker authority; it is insufficient ownership evidence, not necessarily malformed data, and it does not change runtime v0/v1 compatibility, credential policy, or SQLite.

The connector supervisor observes the sidecar unit's health through exactly one `systemctl show happyranch-tsnet-sidecar.service` invocation per observation, requesting the four named properties `ActiveState`, `SubState`, `Result`, and `MainPID` (never `--value`). Output is parsed as named `Key=Value` records: each key must appear exactly once with a nonempty value, and missing, duplicate, empty, unknown, malformed, or control-bearing records fail closed to `unknown`. Classification preserves the existing predicate order — `inactive`/`dead`/PID 0 with a nonempty `Result` is absent; `active`/`running`/`success` with PID > 1 is healthy; `active`/`activating`/`deactivating`/`failed` with PID 0 or > 1 and nonempty `SubState`/`Result` is present-unhealthy; anything else, including a non-numeric, negative, or overflow PID, is `unknown`. A non-success `Result` is not malformed. Admission and downstream cleanup keep the same tri-state behavior.

The real-systemd job exercises a fresh plain generated shipping unit at the exact PR head and package digest, with no harness-created AF_NETLINK drop-in. Its separate bounded denial-matrix probe explicitly permits `AF_INET AF_INET6 AF_UNIX AF_NETLINK` and measures address-family, capability, device, writable-path, and control-plane dimensions with closed secret-free results. The downstream shipping-unit proof preserves composite readiness, production `ExpectedPeers` observation, peer-to-virtual-listener reachability, lifecycle/recovery ordering, custody, quiescence, cleanup/residue, zero-skip/fake, terminal, and digest-bound receipt validation.

Install/upgrade/uninstall functions take an explicit fixture root and validate the manifest before mutation, enabling tests without touching the host. Production installation, service enablement, deployment, signing, and launch remain gated.

THR228seq390 regression scope (authored, **UNEXECUTED / SKIPPED**): the retained Go cases drive credential files through `Sidecar.Start` and inspect the actual production AuthKey factory before substituting the existing deterministic server. Clean, LF, CRLF, ASCII/Unicode edge whitespace and interior whitespace/NUL/invalid-UTF8 bytes must reach the factory exactly as specified. Success, initialization failure and readiness failure must clear the owned normalized buffer and release the adapter key reference; only success may create the consumed marker/remove the ordinary source, and reintroduced input after success must refuse. Existing empty, mode, symlink and consumed-marker cases remain, with a whitespace-only refusal added. Owner validation remains unchanged and source-audited; no wrong-UID OS experiment was executed.

Finite collector regression cases for later authorized coverage are: each of the four exact level/message/caller producers in pinned JSON and RFC3339 console form; the same records with secret canaries in error/key/SQL fields yielding only fixed category/count; wrong level/caller/message yielding `unclassified`; empty-message structured events yielding `unclassified`; duplicate JSON fields, invalid UTF-8, malformed JSON and incomplete final lines yielding `parse_loss`; byte/line overflow retaining explicit `truncated`; and unavailable/timeout/query-error observations remaining unknown rather than empty. These cases are specified, **not executable behavioral evidence**. The former Python collector suite is retired and absent; no new Python-unit or replacement E2E gate is added. The hosted actual CLI callback and fresh-install N3 remain separate from these cases, and malformed installer recovery remains source-only/unverified.

THR228seq396 local diagnostic correction: startup receipts map `ErrConnector`
(including failed/nil dial and failed probe close) to `connector/connector_probe`,
`ErrListener` (state guard, failed/nil listen and stopping race) to
`listener/listener_admission`, and direct `Start` configuration validation to
`configuration/configuration_validation`. Matching uses wrapped error identity,
never cause text. Main's earlier configuration validation still emits
`configuration_invalid` and exits 2; failed `Start` still exits 1. Existing
credential, engine, network and durable-commit categories, network-only finite
subreasons, and safe `unknown/unknown` fallback remain. Both capture and ledger
allowlists accept the same additions; unknown attributed receipts still retain
parse loss, while plain unit-window receipts have weaker attribution. Output
bounds and loss reporting are unchanged. The retained Go diagnostic cases are
**UNEXECUTED** under the THR228 unit hold; collector cases remain specified with
no retained authorized executable owner after Python-unit retirement.

A consumed-marker presence snapshot does not prove completed durable commit:
the marker is created before write, file sync/close and directory sync complete.
The engine's `Durable` flag records its successful Running/expected-peer boundary
with persistent tsnet configuration; it is not a separate persisted-state fsync
or restart-validity measurement. Restart reconciliation handles retained
source/retiring residue when the drop-in is absent; a present drop-in makes it
return without retirement. Systemd credential staging precedes ExecStartPre, so
source/drop-in presence and a later absent staged-directory snapshot do not
establish what each attempt received. No credential lifecycle or restart policy
change is part of this diagnostic correction.

**Harness absent-unit correction (THR-228 seq435; sole root TASK11067).**
Both post-negative-leg `reset-failed` requests and the missing-credential stop
request are best effort, including valid unloaded units. They are never absence
proof. Successful state queries, numeric MainPID, staged-path checks and expected
peer inventory remain independent assertions before progression. Negative state
checks accept only successful exact inactive/failed output; positive waits still
require active. Genuine failed, empty or malformed queries refuse. Privileged
false-test results are encoded inside the command so a sudo failure cannot
become absence; diagnostic presence maps privilege failure to unknown/query_error.
Substitution producers are checked before PID/empty-path comparisons. Conditional
setup propagates every failure explicitly; shipping and final cleanup record
mutation failures and attempt all remaining actions, preserving original exit
and once-only EXIT/INT/TERM teardown. Source restore, refusal, one-use lifecycle,
SYSTEMD0222/File0077, sidecar policy, sandbox and capture budgets remain unchanged.
Successful full process/listener inventories replace negative kill/TCP errors in
cleanup. Negative virtual `nc` attempts remain inconclusive; the SAME manager's
TASK11067 step3 correction requires independent phase-bound Stop completion or
the strict initial NEVER-STARTED conjunction below before removal admission.
Filesystem probe limitations and unexecuted cleanup/lifecycle proofs remain gaps.
The [finite cases](managed-remote-access-staged-credential-cases.md)
are source specifications only. Seq428 run38259906794 at590768ff failed after
the expected negative ledger, without a positive-start/mode/listener result;
reset-abort attribution is supported by retained logs/source, while historical
sole-cause and credential-fix causality remain inference. Seq428 is consumed1/1.
Seq435 was consumed1/1 by the manager publication at112f90ec;
run38347173460/attempt1 FAILED. No allocation remains for this local successor.


**Receipt and assertion acquisition correction (TASK11084/11090/11099, SAME TASK11067).**
Stop receipt acquisition checks the complete journal/decoder pipeline before
admitting a closed result; a successful no-match still fails the required pair.
SAME TASK11067 step4 corrects R4 using existing JSON journal output: each original
MESSAGE and distinct cursor is decoded from strict UTF8, LF-terminated records
before Bash receives only `complete`. Missing/invalid types, binary arrays,
LF/NUL/control MESSAGE content, duplicate JSON fields/cursors/completions,
extra completion-like messages, invalid instance context and truncated frames
refuse. Well-formed printable unrelated MESSAGE records are explicitly allowed;
they cannot supply a receipt. A nonzero journal or decoder status refuses even
with valid-looking output. No raw journal is retained. Before/after rollback digests, staged readlink
and metadata, payload/source custody, PID1 and package provenance are acquired
with explicit status checks before comparison or reporting. Matching or partial
stdout from a nonzero producer refuses. Capture counters, file reads and cap
pipeline statuses are checked even when the capture function is called under a
conditional or cleanup with errexit suppressed; failures retain existing closed
loss values, unknown/empty projections, section budgets and remaining teardown.
These are source corrections with executable proof UNEXECUTED/SUSPENDED.
TASK11084's FAILED partial return is preserved. SAME TASK11067 step3 resolves
R3 with the existing successful Stop completion observations. Before each known
instance's stop, the harness binds exact active PID, nonzero InvocationID and
start-monotonic context, rechecks the tuple and pins boot plus a pre-stop wall
cutoff. Checked journal/decoder acquisition selects that unit/PID/boot/
invocation since the cutoff and requires two distinct original canonical
completion records, with no extra, duplicate or malformed completion records. MainPID0 excludes a running
replacement. This applies to final cleanup, later shipping cleanup, health loss,
Stop barrier, readiness loss and shutdown; shutdown uses one shared acquisition.
The Stop-barrier check precedes queueing the next start and releasing its latch.
Readiness timestamps/order and connector/watchdog checks remain mandatory.

Sidecar.Stop calls listener-first shutdown, joins work and returns stopErr;
stopTwice emits each receipt only after nil Stop. Thus the receipt supports local
listener teardown as a SOURCE inference. It is not a typed remote connection-
refused result or network-health proof. Every real peer attempt with a target is
retained, actual reachability vetoes removal, and failed nc alone cannot pass.
Known phases require nonmissing target plus successful current peer process-start
identity and local status/node identity checks; query/parse/liveness/receipt
failure refuses. No raw journal/status or new evidence fields are retained.
Step4 also corrects R5 before the first negative-start process/admission records:
process start ticks must match the established live peer before and after the
consumed status; that SAME status must have Running backend, current Self
hostname/node/IP shape and a present Peer field. Pinned Tailscale v1.102.3 emits
null for a nil Peer map and {} for an empty map; either is accepted only within
otherwise valid current status. Every nonempty map entry must be an object with
a nonempty printable UTF8 HostName; all entries are validated before absence.
Missing/malformed inventory or hostname, stale identity, duplicate JSON fields,
invalid encoding or any failed producer/parser refuses. A separate good status
cannot bless malformed consumed data. The expected credential diagnostic, both
resets, staging/absence checks, completed-stop capture, source restoration,
denial then positive-start order and saved status/all cleanup remain required.

The first reset_shipping_unit precedes every managed start, and package install
only writes payload/units. Every start attempt, including a failed/background
start, permanently revokes NEVER-STARTED before calling systemd. Initial shipping
cleanup requires independent successful exact unit/PID absence, full owned-unit
inventory, process census, owned/credential/transaction/staging path absence,
host-listener absence and valid Headscale inventory without the owned sidecar.
Together in the isolated fixture, no start plus no owned running process/unit,
state or registered sidecar excludes an owned virtual listener. Missing address
contributes no evidence; this branch emits no peer observation. Later no-address
cleanup refuses. Initial absence is not reusable after a failed start.

Final cleanup retains the peer until the checked teardown, real probe and current
peer-process/node identity conjunction finishes. Missing prerequisites (including
failure before any successful start) refuse the virtual and aggregate cleanup
claims without inventing a Stop receipt; every later cleanup attempt and original
nonzero exit remain. Barrier refusal still releases both latches in teardown.
This unpublished source candidate requires fresh FULL independent review and
same-head SOURCE/STATIC QA for local source preparation; seq435 is consumed1/1. No
behavioral/red-green/mutation/repetition/test-authoring PASS is claimed.
SYSTEMD0222/File0077, production sidecar/supervisor/lifecycle, sandbox, CI guards,
original failure and all cleanup attempts remain unchanged. No whole-harness
acceptance or original staged-mode/installer/restart/N6 proof follows.


**Historical local observational correction (THR-228 seq444; sole root TASK11161).**
Seq435 is consumed1/1 at112f90ec/run38347173460/attempt1 (FAILED). All earlier
allocations remain consumed. TASK11067 is completed history. This successor is
LOCAL UNPUBLISHED only: no push/publication/new PR, workflow change/trigger/rerun,
N3/build/live experiment, merge or deployment. Daily-only19UTC E2E is unchanged.
Fresh full independent review then same-head SOURCE/STATIC QA are required.

Go stderr adds only exact `connector_supervisor phase=... reason=... generation=...
sequence=...` lines. Context marks invocation start; observation marks aggregate
pre-READY sidecar state or pipe EOF; first marks the first local stop/exit decision;
consequence marks later distinct decisions/admission/notify/TERM/scanner loss.
The first reason appears once and each other phase/reason once per supervisor.
The generation is supervisor-generated; sequence is last accepted, never rejected
input. Setup before generation uses unavailable/0. Fixed reasons distinguish
startup/freshness expiry, cancellation, clean unexpected/error/after-stop child
exit, framing/read/version/generation/sequence/state rejection, regression,
sidecar unknown/absent/unhealthy, notification failure and admission refusal.
Unknown sidecar status or child exit never names a Python gate or OS signal.
No health protocol, return value, Wait, terminal latch, notify order, readiness
refresh, admission-before-TERM or Stop predicate changes. The original five
Python gates remain unavailable after exit. A new diagnose is a new evaluation.

The existing connector reservation is split, never expanded: journal command4s
plus1s kill grace/2048B first, then live process command8s plus1s kill grace/2048B.
Total14s/4096B remains; live internal opportunity is reduced from11s to6s and
incomplete samples refuse. Sidecar text, state, jobs and receipt ordering stays
first. Journal worker owns an absolute3s read/parse/exit window plus1s group reap,
64KiB raw plus one detector,8192B frames,256 frames and at most6 retained events.
Only closed labels pass before retained caps; no raw prose/payload/error is kept.
All journal events require canonical unit, boot, positive bounded PID, nonzero
invocation, unique cursor and trusted realtime/monotonic journal timestamps in
the pre-start window. Generation/PID/sequence/monotonic drift within an invocation
refuses. Events retain separate invocation IDs; the original invocation remains
null/window_only because a surviving restart cannot establish the lost original.
Missing/invalid attribution is explicit, never replacement-process evidence.
Earlier accepted events survive parse/query/timeout/truncation loss; tail_complete
is false on loss. A successful empty stream is empty; a nonzero grep/query remains
query_error. No retained label proves the ultimate root cause or complete history.

The source section ledger totals82s scheduled allowance and32256B accounted
section bytes both before and after this split (sidecar text6s/4096B; sidecar
state8s/1024B; jobs8s/8192B; receipts8s/8192B; connector14s/4096B;
Headscale17s/4096B; connector/target properties16s/2048B; presence5s/512B).
Legacy45s/24064B metadata omitted existing sections; it was never an enforced
aggregate cap. Those existing keys remain for artifact readers and are flagged
legacy_aggregate_metadata=true alongside the actual unchanged ledger; setup/kernel/output latency is not a guaranteed wall-clock bound and
snapshot JSON wrappers are outside accounted section bytes. No larger bound is
claimed or installed. Capture write/launch/query failure preserves originating
exit and remaining once-only teardown; outer interruption can lose worker partial
output and records timeout separately.

Final cleanup labels now distinguish virtual_stop_binding_unconfirmed from
virtual_removal_unconfirmed. The same active-instance pre-Stop tuple, two
same-instance Stop receipts, MainPID0, valid current peer and unsuccessful real
virtual dial remain required. Positive reachability vetoes removal. Missing
address/binding/receipts/dead process alone never proves removal or residue.
There are23 shipping plus24 final possible deduplicated refusal pairs (<4096B);
only the final legacy residue pair splits. Run38347173460 retains no final
virtual-removal/aggregate cleanup receipt: INCOMPLETE/UNCONFIRMED, not observed
residue. Existing artifacts/failed receipts remain immutable.

D01-D12 extend the maintained79 cases. All behavioral/unit/red-green/mutation/
repetition/assertion/executable-QA proof remains UNEXECUTED/SUSPENDED, nonblocking
for source preparation; integration/actual callback SKIPPED THR243seq42. Python
units remain RETIRED. Static receipts establish source properties only. Proposed
original-process gate transitions, durable last-transition record and doctor
command are PROPOSAL ONLY, with no private protocol/state/API implementation.


## Current LOCAL lifecycle2 source — ROOT12232 / TASK12399

**Current delivery status:** sole ROOT TASK-12232 remains LOCAL and INCOMPLETE; all publication/run grants are exhausted. Publication, execution and cleanup are HELD. Historical owner/last-slot directions do not renew authority.

This uncommitted, unpublished H1045-based candidate consolidates the source work
of TASK12246/12251/12252/12253/12254/12255/12261 and TASK12272 R1–R5 SOURCE
corrections to independent FULL review12267 REQUEST_CHANGES, plus TASK12285
applied R6 historical-preservation remedy. TASK12302 applies the ordinary CR1
actual-destination ownership and CR2 managed operator readiness corrections from
independent FULL review12292 REQUEST_CHANGES and manager revision23. Groups1–10
have maker SOURCE dispositions for root completeness assessment; historical
REQUEST_CHANGES results remain unchanged and final independent closure is pending.
TASK12392 applies B1–B3 ordinary SOURCE corrections from B-only independent
review12389/manager revision39: finite onefile helper roles, owned stop inhibition
and explicit completed-stopped crossboot reconcile. TASK12399 corrects B2-R1/B4/
B5/B6 from B-only review12396/manager revision41: actual first-worker retirement
publication, inert retirement duplicates, every-path final boot census/role fence,
and consumed-backoff provenance for retained equal carry charges. Historical
slice verdicts remain bound to their original subjects and require renewed coverage.
Implementation acceptance remains pending fresh FULL review and same-subject QA.
Historical checkpoint claims below the older published contract are superseded
by this LOCAL description only. Original task outputs, failed runs, receipts,
approval documents and their historical headings remain unchanged. This source
has no implementation approval, independent QA or behavioral acceptance. Sole
ROOT12232 must assess completeness, obtain fresh FULL immutable public-base-to-final
independent code review and same-final-subject SOURCE/STATIC QA, then report.
Publication and execution remain HELD.

Authority is THR228seq541 (A/B),553/556 (LOCAL proposal/scope),558/561/562 and
566/569 (exact nonsecret custody),575/579 (selected observed-reset acceptance),
with manager revision13 acceptance of independent TASK12260 CASE/DESIGN PASS
before dependent edits. The unchanged revision11 proposal and revision12 approval
addendum control the reset addition; their original headings/hashes are historical
and preserved. Seq571 IPC is unselected. DESIGN PASS proves no implementation or
behavior. No new public API, query family, signal meaning, record, unit, privilege,
dependency, credential algorithm or deadline carrier is introduced.

Founder THR228seq585 approves BOTH seq581 decisions in the exact revision20
proposal (SHA256 d335381cca60c92c0f0ed330317449485c9a77af0633632a6c7368198135f8ed);
manager revision21 accepts that corrected affected design before these edits.
This separately authorizes the eight-file/eleven-span LOCAL remedy and continuing
retention of the exact delivered historical closure and its six existing helper
uses/three legacy supervisor calls. Edit authority ends at ROOT12232 delivery or
completion/failure/cancellation/supersession or Founder revocation; retention after
successful delivery continues until revocation or separately authorized replacement.
No new caller is admitted. Only the restored unselected H1045 helper is excepted
from internal-StopUnit removal. The exact retained historical test-reached closure
and existing callers are excepted from the test-only-path restriction.
Selected managed retirement still prohibits internal StopUnit. Independent
TASK12277 REVISE remains historical; its sole lifetime correction is applied in
the approved proposal. Design/authority acceptance is no implementation verdict.
Seq585 also permits itemized internal corrections within approved files without
new privilege, unit, credential/data access, interface, disclosure or behavior.
Publication, execution and cleanup remain HELD.

The compatible declaration remains
`{"lifecycle":2,"child_health":2,"recovery_diagnostics":2}`. Legacy/incompatible
readers and unknown transaction/cleanup state refuse. Policy separates configured
enable/desired stop from current-boot runtime ownership. Acknowledged supported
stop/retirement survives reboot/reinstall; raw manager stop cancels current-boot
jobs but is not durable configured-stop acknowledgment. The LOCAL three-unit
graph uses connector-only on-failure retry/RestartSec30, RestartMode normal,
RestartPreventExitStatus2..255, explicit StartLimitIntervalSec0, sidecar Restart=no,
mutual component PartOf and target Wants/After both, with normal boot enablement.

### Current managed renderer directives

SOURCE inspection of `runtime/remote_access/linux_package.py`,
`render_composite_units` and `_installed_managed_contract`, selects BOTH
`happyranch-connector.service` and `happyranch-tsnet-sidecar.service` with
`TimeoutStartSec=120`, `TimeoutStopSec=35` and `TimeoutAbortSec=35`.
The loaded reader requires the corresponding `TimeoutStartUSec=2min`,
`TimeoutStopUSec=35s` and `TimeoutAbortUSec=35s`, alongside exact installed
rendered bytes and the complete loaded graph. The target `Wants` and `After`
both services; each service has `PartOf` the target and the peer service.
The sidecar has `Restart=no`; only the connector owns on-failure/30s retry.
Legacy lifecycle1/H1045 omitted-start/stop10, target Requires/sidecar BindsTo
and independent-sidecar-restart descriptions are historical, not current authority.

These unit settings do not replace Sidecar.Start's enclosing75s, wrapper80s,
private adapter80s, or the once-latched terminal bounds R=T+10/child<=T+20/
D=T+30/scanner2s inside D, all clipped by earlier deadlines. Renderer/reader
source agreement establishes no actual loaded authority, timing, behavior or
cleanup proof and grants no publication, execution or new allocation.

Selected managed retirement uses no StopUnit or second retry scheduler; the
unselected historical helper has only the exact seq585 exception described above.

The immutable admission policy and configured enable intent are distinct. Root's
existing paired enable writer may change false->true or repeat true->true with
new revision/operation while the SAME live generation survives. Health/reset/
retirement accept only exact policy or that closed enable-only successor: same
installation/run/no-hard-block, newer revision/different operation and enabled
true, plus all existing generation/boot/transition/current physical fences.
Stop/disable/restart inhibit first; later run reentry requires stopped absence
and cannot reuse the live generation. Qualification/debt are not erased by
metadata enable. Command replies still bind their exact selected policy and
operation; concurrent supersession refuses, and no old run receipt is replayed.

Independent installed AND actually loaded-PID1 readers bind exact fixed units,
exec/pre/post/credential mapping, graph/restart/kill/deadline/notify/hardening
properties, drop-ins, current process/executable/invocation and jobs. Neither a
helper success bit, copied environment, readable file nor pinned PID supplies
missing authority. Unknown/missing/late evidence refuses. Both startup orders
retain viable owned startup roles; qualification alone additionally requires both
services active/running, ControlPID0/no job and the current active target, with
matching bound MainPID/invocation/cgroup at both manager observations. Startup
tolerance cannot create qualified running credit. A valid clean rate-only
no-claim sidecar waiter is positively classified before its ORIGINAL10s expires:
exact self, paired no-generation/clean/policy/boot/transition plus full current
installed+loaded/process/job census. It cancels without engine/Listen and main
still physically Stops/joins the SAME object; final cooperative publication
repeats those proofs INSIDE that same original10s. The preliminary terminal call
claims no close and does not poison clean debt with unknown. Timer expiry alone,
a racing generation, failed/late proof or missing join still refuses78/unknown.
External inhibition retains the ordinary halted physical-close path.

The fixed boot-helper census admits only the current ControlPID connector
launcher, at most one direct same-UID onefile connector worker and at most one
Go managed-run-check directly under the actual body (worker or launcher).
Root pre/post hooks and service-UID credential prehook keep their exact rendered
argv, fixed executable inode, invocation/cgroup/job and retained proc/pidfd/parent
proofs. Every path repeats complete bounded membership, including MainPID-only
and empty/no-helper routes, plus main exact argv/executable and retained role
proofs, before the final manager observation within the original bound. This is
not an atomic guarantee after that observation.
Siblings, extra depth, unknown members, reparent/reexec/UID or current-field drift
refuse. Both startup orders retain the same bounded census and main-role pins.

Only the approved source-derived nonsecret manifest, four named share files,
three fixed rendered units and optional fixed path-only enrollment drop-in use
0644. Share and active generated drop-in directory use0755; binaries keep0755,
root keeps writes. The same approved payload class may remain readable at the
existing generated stage and retained backup. Actual private OLD0600/share0700
and actual public OLD0644/share0755 restore exact bytes AND modes. Unit/drop-in
backup containers remain0700. Private state, transactions, credentials, temporary
custody, enrollment .new and nonmanaged modes keep their protection. Arbitrary
input is never certified nonsecret by filenames, hashes or membership. Approved
version/dependency/configuration/credential-source-path disclosure is intentional.

Package root admission precedes preparation and every root-aware recovery,
restore, cleanup and record-write entry. Only canonical / selects managed
ownership; parent-component spellings, links, known fixed destination/inode aliases,
overlaps and unresolved ownership refuse before effects. Positively separate
staging roots keep their existing route and modes. No alias is normalized into
permission to traverse or adopt installed bytes. Concrete package source AND
destination checks additionally exclude BOTH fixed state trees and ambiguous
regular inodes with multiple links, including a planned unit hardlinked to either
lifecycle.json with system_service=False. Admission precedes preparation and
content access, and repeats in lower read/write/copy/rename/record helpers,
recovery, restore, error cleanup and uninstall. Canonical parents, expected types,
known single-link file custody and missing-tail parent proof are required;
dangling links, inaccessible/unknown metadata and special files refuse. Internally
sealed anonymous archive descriptors retain their existing handoff after regular
unlinked-inode and immutable-seal admission; this certifies no arbitrary incoming
nonsecrecy. Only literal / enters the original independent managed holder/context
and installed AND loaded-authority route; local exclusion grants no authority.

On the existing Linux-amd64 package's supported Python 3.12–3.14 runtimes,
sealing constant exports depend on interpreter build headers. Package creation
and descriptor verification use native exports when present, otherwise the
Linux UAPI values for `F_ADD_SEALS`/`F_GET_SEALS` and the exact
`WRITE|GROW|SHRINK|SEAL` mask. The two creation flags remain
`MFD_CLOEXEC|MFD_ALLOW_SEALING`, with the same native-or-Linux-ABI selection.
The real `os.memfd_create` operation is required; unsupported platforms,
missing memfd support and rejected creation or sealing refuse with the existing
`package_input_invalid` category. No ordinary-file substitute is accepted, and
partial creation failures close the owned descriptors before any handoff.
This compatibility handling supplies no kernel, installation or self-heal proof;
source compilation and static inspection cannot establish those outcomes.

The existing inventory traversal also supplies metadata-only preflight before
tree effects, without reading contents or creating a new scanner. Default full
schema2 byte/mode identities, whole-tree OLD retain/restore and commit classifiers
are unchanged. OLD symlinks remain link objects for inventory/unlink, never file
content sources. Actual private/public OLD images retain exact bytes AND modes;
unknown cleanup custody retains residue without broadening permissions.

Admitted managed package preparation, mutation/recovery/restore/rollback/uninstall
and final validation share continuous SAME-OFD B through actual child quiescence. Bootstrap
holds the future stable inode before publication. Fixed retained executable
images/context validation do not permit arbitrary commands. Stopped enrollment
owns its body guard before observer/credential/path/marker/drop-in/reload work,
including direct calls and error unwind; fixed run helpers retain their existing
algorithms under current ControlPID/executable/activation proof. CLI routing and
all three direct enrollment bodies share the same selection: the canonical fixed
source/marker/drop-in tuple requires its body guard; parent-component spellings,
links, existing file/directory inode aliases, mixed tuples and unknown ownership
refuse before observer, credential/state reads or effects. Positively separate
nonmanaged tuples retain their route and modes. Metadata selection is exclusion
only, never loaded authority or race-free filesystem proof. Original entry and
inherited deadline minima, actual query/reload reap and error custody remain.
Generic mutations
separate the writer destination from the system-manager selection. Before staging,
backup or a manager effect, _require_nonmanaged_owner accepts only one bare,
fully-qualified unit name with a supported unit suffix and a literal ASCII name
(or a named template instance). Abbreviations, paths (./, absolute or parent
components), patterns, escapes, uninstantiated templates, option-like and other
ambiguous specifications refuse as noncanonical; they are never forwarded for
systemctl suffix completion or pattern selection. Writer-directory and fixed
system-directory checks are separate from actual manager selection. One bounded
selected-unit show reads exactly Id, Names, LoadState, FragmentPath, SourcePath,
UnitFileState and NeedDaemonReload through the existing capped query reader.
Only the requested canonical Id, complete distinct canonical Names and known
loaded metadata can admit generic lifecycle verbs. Aliases from /run, vendor or
generator paths and currently loaded aliases refuse independently of whether a
hypothetical /etc writer file exists. The three fixed system names in Id/Names,
and fixed or resolved protected fragment/source identities, cannot enter generic
lifecycle even when disk markers are missing. Metadata never redirects an alias
into managed lifecycle or grants its authority. Unrelated canonical user-manager
units and named instances retain their separate route and destination checks.
New unrelated install/write/restore and idempotent uninstall explicitly allow
only a complete not-found observation: requested Id and sole Name, empty fragment,
source and file state, NeedDaemonReload=no and status0 or exact no-such-unit4.
A failed show alone never proves absence; lifecycle verbs still require loaded.
Missing/extra/duplicate/malformed/invalid UTF8/oversize/unknown/transitional,
unavailable or late metadata refuses before dependent effects. The existing4096B
cap plus one overflow sentinel and original5s/earlier inherited deadline bound
observation and final acceptance, including destination metadata and validation.
The existing reap helper receives that same deadline without renewed cleanup
grace; an unfinished refused query must still be joined, which is not a hard
OS-I/O/reap-time guarantee. Lower backup/restore/fallback repeat admission;
uninstall rechecks after caught stop/disable errors before unlink. Supervisor's
first staging guard explicitly uses the new unrelated-install disposition.
Canonical protected services/target and resolved file/inode aliases retain owner
exclusion, independently of the writer or backup destination.
Writer and backup/restore destination checks separately resolve equivalent unit
directories, parent-component spellings, links and existing inode aliases. Fixed
directory aliases retain the fixed unit markers and BOTH independent lifecycle
markers under /var/lib/happyranch-connector and /var/lib/happyranch-tsnet-sidecar.
Missing tails require a proven existing directory; dangling links, marker/parent
lookup errors and unresolved parent components refuse as unknown before effects.
Metadata identity does not certify installed or actual loaded authority from
readability or prevent concurrent out-of-band filesystem replacement. Fixed state
custody never becomes generic because lifecycle/unit markers are absent. This
exclusion precedes fixed/local unit-name classification, including each fixed-named
USER unit with complete unrelated not-found metadata and no destination or local
markers. Both protected roots, ambiguous multiple-link inodes and unknown ownership
refuse independently of manager selection. Generic install/upgrade separately admit
the actual parent before mkdir, leaf before write and required single-link regular
leaf before chmod; absent install never depends on a skipped backup. Uninstall
rechecks its actual leaf before unlink after caught lifecycle errors. Backup/restore
retain independent source and destination admission, including direct and fallback
calls and post-mkdir backup copy checks. In the selected USER manager's ordinary
canonical ~/.config/systemd/user directory, a fixed-named leaf or sibling alone is
not a local managed-package marker: absent install reaches post-write0600 and
reload, then selected enable when requested; existing leaves retain later lifecycle,
backup/restore and uninstall admission under fresh selected-manager observations.
Post-write custody checks remain; system=False does not exempt arbitrary writer,
backup or fragment/source directories from local markers. Default classifier
callers and genuinely managed local/fixed markers retain conservative refusal.
Unrelated canonical system/user/named-instance routes retain their existing modes
and independent actual-manager alias checks; fixed SYSTEM managed selections
remain excluded. These are SOURCE dispositions, not executed proof or atomic
filesystem guarantees. Direct
supervisor _install_managed preflights BOTH fixed state trees against the root AND
all six planned children: policy.json, config.json, trust-state.json, its .anchor,
and the two corresponding .stage-tmp names. This precedes FIRST mkdir/chmod,
source/target state validation, staging and ownership effects. Root-only exclusion
is insufficient: an unrelated canonical selected manager and unrelated real root
cannot authorize a policy/config/state/temp child linked into either protected
tree. Each concrete staging target requires a canonical no-link path, a proven
existing parent and an ordinary file or directory of the expected type. Fixed
roots, ancestors, descendants, equivalent parents and known directory/inode
aliases refuse; dangling links, inaccessible/unknown metadata, special files and
regular inodes with multiple links refuse without searching protected contents.
The original positive unrelated canonical system/user routes retain0700 root,
0600 staged files and existing ownership. Path admission never grants managed
execution, holder/A/B, adoption or installed AND actual-loaded authority.

Direct _stage_state_pair and _chown_tree repeat independent actual-manager and
destination admission. Pair staging preflights BOTH final names and BOTH temps
before source reads or writes; concrete checks precede temp open/chmod, each
rename and finally/error-path unlink. Changed/unknown cleanup custody preserves
residue. Source trust validation, authoritative monotone snapshot/anchor comparison,
two-temp flush/fsync BEFORE either rename, snapshot-first/anchor-second publication
and partial-pair refusal remain unchanged. System ownership preflights every
existing recursive target before the first install effect and again before any
chown, including nested descendants; local metadata traversal never follows links,
refuses inaccessible entries and is capped at4096 existing targets/64 levels.
Each chown repeats destination admission with follow_symlinks=False. No protected
tree enumeration, content inspection or global scanner is added. Backup and
fallback restore independently admit BOTH the concrete source and destination:
generic _latest_backup checks its directory before selection and its selected
source before return; shared _restore_backup repeats source admission before any
read and destination admission before write/chmod for public, direct and fallback
callers. _backup_current admits the unit source, backup directory and destination
before mkdir/copy, then rechecks before chmod. Stable source/parent symlinks into
either state tree, dangling/unresolved paths, unknown types and regular multiple
links refuse independently of actual manager selection. Metadata refusal becomes
the existing ServiceManagerError/fallback-failed disposition, with no restoration
success or later reload/restart inferred. Unrelated canonical system/user/instance
selections and ordinary single-link backups keep their route and0600 modes;
install checks its backup before creating the unit parent. Checks are local observations, not a
race-free namespace or arbitrary mount-provenance guarantee: concurrent parent/
inode replacement and OS-I/O/reap/timing limits remain. These are SOURCE-only
dispositions; concrete manager/ownership behavior and concurrency are unexecuted.
Unknown residue refuses; a timeout does not release exclusion over a live writer.

Managed OPERATOR readiness requires BOTH the unchanged five-gate report and the
existing read-only lifecycle status ready proof: current strict records/lease/
generation plus installed AND actual loaded-manager authority. Stopped, disabled,
halted, absent, blocked, deferred/rate-deferred, mismatched, unknown or late proof
cannot yield ready=true or exit0. The final Boolean and exit use that conjunction
within the ORIGINAL managed request deadline; no deadline renewal or status write.
The public ready/gates shape and five gate categories stay unchanged, with no
lifecycle details disclosed. Internal worker admission still uses only its five
gates BEFORE composite READY; nonmanaged CLI keeps its original five gates.

The credential gate uses the selected provider's actual checks. Nonempty
`CREDENTIALS_DIRECTORY` selects SYSTEMD, then configured `credentials_directory`,
then ordinary File; a selected SYSTEMD failure never falls back to File.
Ordinary File follows its selected path (`is_file`/`stat`/`read_text`) and refuses
any followed-target `0077` bit; a readable nonempty regular target can pass even
through a final or ancestor symlink. SYSTEMD refuses a final symlink and every
`0222` write bit, including owner write; it does not check symlinked ancestors.
Neither provider adds UID custody validation. Missing, unreadable, empty or
provider-specific loose-permission input still refuses. Managed source admission
and the separate `credential-capability` body own component-path symlink refusal,
applicable source custody, exact current staged unit/name/directory provenance,
and service-observable read-only capability. The admitted managed connector
worker invokes that body; ordinary direct File readiness does not. Sidecar staged
input retains `0022` (owner write is not rejected by that mask), while ordinary
sidecar input retains exact `0600` and current ownership. These SOURCE distinctions
change no provider policy, readiness path or installed/loaded-authority proof.

A completed stopped pair from a previous boot remains inhibited automatically.
Only explicit reconcile with expected installation/revision and its stable
operation ID may establish a NEW current stopped boundary: independently valid
compatible complete old policy/pair, clean idle plus halted closed/unstarted,
no block/failure, actual current fixed service/target/process/job absence and
installed AND loaded authority are repeated under the common lock/original45s.
Configured enabled/desired intent is preserved; new runtime roles/health/leases
are empty and no predecessor cooperative joins, B or monotonic clock is adopted.
Every spend is conservatively carried to the new boot's sampled clock, retaining
count and at least the count-derived or larger recorded backoff (including an
existing backoff with no spends). Only current-boot time can age this debt.
Both records carry matching fresh transition/source links; sidecar-first partial
publication still refuses, and repeated IDs require the actual current stopped
result. Same-boot explicit repair keeps its existing strict-record/absence route.
Status, start/enable, autonomous boot and package/enrollment guards never repair
old stopped records; after successful reconcile their existing current-boundary
routes apply. Disabled intent stays disabled, retired/hard-block/failed/malformed
or torn sources and unknown current absence remain refused.

Original operation bounds remain: supported stop45s with acquisition/publication/
ack A<=10s, unlock BEFORE manager stop<=35s; package180s INCLUDING preparation,
mutation and unwind; stopped enrollment60s; wrapper startup80s, sidecar75s, actual
PID1 activation120s. Supplied earlier minima can only shorten them. Earliest
terminal T fixes R=T+10/D=T+30, child phase<=T+20 and scanner join<=2s insideD;
repeat Stop/fallback/late result cannot renew them. Existing operation-specific
request/inherited-holder/environment/activation carriers constrain their owned
writers. Blanket arbitrary initiating-CLI-to-independent-autonomous-PID1 transport
was a derived overconstraint, reconciled by manager revision13; no new carrier is
required. Reaping systemctl proves neither PID1 cancellation nor service joins.
Future50/60/75/195/750s observation caps add no operation time.

A valid advancing health receipt may observe A's paired stopped/halt/health-null
publication before the manager signal. Only a coherent CURRENT OWNED inhibition
with the same installation/boot/transition/generation/roles, later different-operation
stopped policy and no block/failure is classified as intentional. It enters the
SAME once-latched retirement owner and original deadlines; it does not set sticky
failure merely for lost run eligibility. Real typed/sticky failures, unknown or
late proof and mismatched policy/roles still refuse. Inhibition proves no physical
cleanup, and neither debt nor genuine failure is cleared by that classification.
A valid first waiting/ready receipt may authenticate the actual worker even when
stop wins delivery selection. The same retiring owner publishes only that pinned
worker under current generation/role/original-terminal fences before child TERM;
no worker is invented or supplied by a first stopping record. Missing, unauthenticated,
late or mismatched worker proof refuses clean publication. Signal-first and
health-first use the same stream, original receipt bounds and retirement deadlines.

The wrapper owns one terminal decision, one actual Wait/scanner and all MAIN/rate
writes. Retirement withdraws first and proves exact sidecar process/manager/cgroup
absence BEFORE commanded child TERM. Cooperative current worker stopping, actual
successful Wait observed before the original clipped childDeadline, scanner
completion and final owned absence are required for clean retry. Slow first-worker
publication or cohort proof cannot admit worker/descendant/launcher TERM at or
after that child fence; each signal boundary rechecks absolute time after proofs.
Later Wait-channel availability proves no historical completion time. A positively
retained timely Wait observation survives subsequent scanner/cleanup work, while
late or unproved Wait stays sticky78. Every refusal still observes the ONE owned
Wait/scanner only within original remainingD; scanner<=2s and finalD can veto clean.
Forced/spontaneous child loss, available failure or unknown joins never become
clean from later absence. The SAME Sidecar Stop keeps listener-first active
flow closure, accept/proxy/monitor/watchdog joins and exactly-once engine close.
Proxy ownership cleanup covers returned sockets and registered membership even
when the post-registration authority proof refuses. An orderly copy EOF with a
successful supported write-half-close lets the reverse response drain; a copy
error or failed/unsupported half-close closes both flows. Explicit Stop closes
both registered sockets and joins both copies; contextless I/O has no hard wall
bound from these source joins.
A known engine-negative sample latches existing local veto/failure/cancellation
BEFORE guard.Check can wait for the common lock. Existing withdrawal, failure
publication and pinned-peer notification retain original bounds. Guard construction,
Admission, Check and Forward retain the SAME supplied context and original worker
health lease under the SAME lock/generation. After slow current-authority proofs,
and at each positive action, binding/admitted publication and result boundary,
they use existing Err-first/absolute-clock startupContextError and fresh existing
kernel-monotonic LifecycleNow. Equality with the original ValidUntilNS expires;
query availability and a live context never renew or restamp that lease. Original
current-record, policy, installed+loaded manager and identity/role gates remain.
Expired context or lease refuses dependent listener/proxy/READY/watchdog admission;
an action, postproof or write finishing late cannot yield positive success.
Admission still observes an action error; Forward still returns its action error
directly. Local veto/failure is rechecked at positive boundaries. No new budget,
clock seam or carrier is introduced. These are source admission observations, not
hard OS-I/O preemption, atomic multi-clock certification or race-free authority.
A timely admitted notification or write already completed cannot be recalled by
later refusal. This local latch is not immediate cross-process communication.

Managed child-health2 requires exact canonical JSON followed by LF, at most1024
bytes per physical record including that LF. The SAME bounded scanner retains
raw framing and rejects CRLF, stray CR, malformed records and an incomplete final
v2 frame before receipt clocks or delivery. Newline-terminated EOF is distinct
from incomplete framing; a framing failure remains a final sticky retirement
veto, even after an earlier valid stopping record. Historical nonmanaged v1 keeps
its ScanLines LF/CRLF/unterminated-EOF framing, canonical decoder and size limits.

Worker v2 leases remain anchored to actual scanner receipt before channel/proof
work, valid for at most12s and clipped by the OLD lease and actual earlier bound.
Wrong identity/generation/policy/boot/transition, zero/skipped/overflow sequence or
late proof refuses. The live handler, withdrawal/first-worker handling and both
retirement consumers share the same finite positive duplicate/old accepted-worker
classification. Such duplicates never renew a lease, advance sequence/qualification,
restamp receipt time or supply stopping cooperation. Strict advancing/overflow
checks and authentic stopping received after commanded TERM remain mandatory.
Cooperation requires a successful actual pidfd TERM and an original scanner wall
receipt strictly after the conservative boundary sampled after that successful
send returns. Preparation, attempted/failed signals and receipts before or equal
to that boundary (including during identity validation or send/return) supply no
cooperation and retain sticky78 even through later genuine stopping and cleanup.
This proves receipt ordering only, not causal production or an atomic send/clock.
The separate early command latch keeps the ONE Wait owner's exit election; it
never supplies cooperation. Every pinned signal rechecks the caller's existing
cancellation and absolute deadline AFTER identity work immediately before send:
removal uses original R<=T+10; worker/descendant/launcher use child<=T+20, each
clipped by D/earlier bounds; sidecar-to-wrapper notification retains its original
terminal cleanup context. Equality/elapsed time refuses even before timer delivery;
failed or late attempts withhold clean success, with no numeric PID fallback or
new allowance. A completed kernel call cannot be preempted or recalled.
Every nonduplicate, including stopping/failed, passes managed envelope, original
receipt and terminal-role admission before live dispatch. Wrong version, generation,
identity, sequence or receipt, failed, and stopping outside the commanded retirement
role retain sticky refusal78 through the SAME physical retirement. A first/stale
terminal cannot pin a worker or supply cooperation; later genuine stopping and
successful Wait/scanner/current proofs cannot erase that refusal or certify clean
idle/backoff or stopped0. Receipt bounds are rechecked after slow identity proofs.
Periodic checks add
no sequence, lease or watchdog credit. Qualification starts only after acknowledged
composite READY, requires >=300s AND >=26 distinct advancing accepted observations,
no gap>12s, and the SAME current context. READY acknowledgment is anchored once;
later qualification elapsed/gap credit uses original receipt stamps, not proof
completion. Observed transition/unknown running-role proof clears qualification;
startup roles still allow the original handshake.

At final SAME-lock reset, repeat installed+loaded authority, strict active roles,
current exact pair/generation/lease/sequence and terminal/signal/failure fences.
Check the positive boundary immediately before empty MAIN publication, then
independently refresh/re-prove after the write before acknowledgment. Late/lost
write acknowledgment is ambiguous publication: visible empty bytes are not refunded,
called old debt or represented as successful reset. Sidecar writes SIDECAR only.

The selected reset is healthy-AS-OBSERVED accounting. A current communicated
negative before the final boundary vetoes reset. An actual engine/peer/auth loss
not yet communicated to the wrapper MAY permit clearing ALL THREE spends and
backoff. Existing sidecar monitor cadence is cooperative, not instantaneous
physical continuity or a guaranteed end-to-end1s delivery. Actual engine,
credential/trust/listener/admission/READY/watchdog gates remain mandatory. There
is no at-most-one-extra-restart or one-restart-per-five-minutes guarantee.

SAME case8 retains ring[0,40,110] and acknowledged READY120:

| Selected history | Required accounting under otherwise valid fresh proofs |
| --- | --- |
| P, advancing sequences120..420, qualified reset420; actual clean460 | Empty ring; not_before490; a fresh eligible scheduled claim may spend count1. |
| N1, communicated interruption410; actual clean440 | Debt retained; not_before560; no fourth before600. |
| N2, duplicates never renew lease132; actual clean160 | Debt retained; not_before280; no fourth before600. |
| N3-known, current negative/latch before locked reset; actual clean450 | No empty publication; debt retained/not_before570; no fourth before600. |
| N3-uncommunicated, actual loss420 but wrapper observations positive; actual clean450 | Empty reset MAY occur; not_before480 only with actual cleanup/PID1 scheduling and every independent fresh gate. |

Conditional480/520/590 admissions illustrate possible erased debt, not measured
runtime, a schedule promise or a one-extra limit. Unknown cleanup or available
failure refuses even with an empty ring. Case13 retains conservative carried
boot debt0/1/2/3, no RTC/old monotonic/lease/join authority and reset-publication
loss distinctions; B never crosses boot. Cases9/10 retain exact custody and
independent installed+loaded checks,12 independent boot writers/both orders and
first/second/final-write losses,5/6/11 SAME-OFD/stop/retirement/actual-join safeguards.

Every current clean rate-deferred reader has one original entry+2s decision bound.
Final complete current proof decides deferred/eligible/refused BEFORE returning.
Entry599.5/proof600.1 before601.5, or489.5/490.1 before491.5, continues remaining
ORIGINAL entry+80 startup with released pair and fresh claim/Spend/full authority.
Only genuinely current clean defer returns1 without spend/worker/engine/listener/
READY/watchdog or prior-owner cleanup claim. Unknown/late/inhibited/failure/nonclean
proof refuses78/current actual stop semantics. After genuine independent boot preparation, the SAME full current proof also
classifies carried debt BEFORE claim, within the remaining ORIGINAL80s startup
bound (and earlier supplied minima), without minting a new2s window. Conserved
debt1/2/3 keeps30/60/120s backoff; three equal carry stamps can require600s.
After count2 carry backoff expires, the permitted third spend retains the two
equal charges and their matching source/end provenance, plus its actual fresh
stamp. The already consumed backoff is retained unchanged only while that equal
prefix remains; no charge clock or window is renewed. At exact600s the equal
prefix expires together. Existing strict duplicate/source/partial-pair validators,
subsequent clean retirement/backoff and independent current readers still apply.
Only rate alone with clean empty ownership exits1 for PID1's next reader. A real
claim failure after startup is not retroactive deferred success. No renewed window, sleep, indefinite reproof,
permission reservation across unlock or competing scheduler follows.

Diagnostic2 stays separate from unchanged v1: closed category-only canonical
records, <=512B each, at most16 events/8KiB per invocation, one initiating decision,
no raw errors/paths/PIDs/credentials. Routine unchanged clean rate-only no-claim
readers emit neither context nor terminal transition events; status derives from
the existing ledger. Real boot/preparation, claim, health/reset, withdrawal and
retirement events and genuine refusal/failure diagnostics remain. Settled signals
and the original absolute deadline/final-proof fences precede suppression: a late
refusal still emits its blocked terminal consequence. Boot/carried-debt transitions
retain their rate-terminal event. Preclaim context leaves policy unbound; actual
claim or a proven final boot defer supplies its revision. Run diagnostics retain the actual
immutable admission-policy revision through live enable. Only an authenticated
final stopped inhibition may project a newer policy revision after actual
cleanup. The v2 reader allows that closed monotone final transition for the SAME
nonnull generation/invocation/PID/boot/sequence/order, rejects later events for
that invocation and retains attribution loss for all other revision movement.
This is source attribution, never observed journal success or cleanup proof.
Boot preparation is not readiness;
publication observation is not old fsync/helper success. A reset diagnostic requires
a changed acknowledged publication. Diagnostics supply no cleanup/authority.

Current E2/E6/E7 LOCAL source corrections (manager revision60/TASK12505) align
only the fixed installed path-only10-enrollment-credential.conf assertion with
root:root0644 and its named generated directory with root:root0755. Credential
sources remain0600; private state/OLD/.new/temp containers retain their custody,
root retains writes and binaries remain0755. Approved version/dependency/configuration/
credential-source-PATH disclosure stays explicit; arbitrary configuration remains
outside that nonsecret class. External ledger/denial JSON refuses duplicate members
and nonfinite values, validates closed object/list/scalar types, lowercase fixed-width
hex, bounded true integers (excluding bool), actual UUID run IDs, purpose identifiers
and Boolean fields before use. Pretty JSON+LF and schema/version/key/PHASES/denial
contracts remain; terminal-null failed history is preserved. Completed-job capture
uses its existing closed decoder, preserving prior valid rows and explicit parse_loss.
Within E2/E6/E7 no selected assertion is removed and no new production seam or
coverage loss is intended; independent assessment remains outstanding.

Current E1/E3/E4/E5 LOCAL source correction (TASK12522, manager revision63,
accepted revision62/TASK12516 prospective design; Founder THR228seq585/590)
keeps this campaign incomplete and unsuccessful. Earlier N3 full-proof,
rollback/barrier and credential-negative descriptions are preserved historical
requirements/results, not capabilities or acceptance of this LOCAL candidate.
E2/E6 source corrections above are retained; E7 receives the narrow corrections
below while its earlier duplicate/attribution behavior remains.
No new production seam, privilege, unit, credential/data read, interface,
disclosure, self-heal behavior, evidence schema4/PHASES or run grant is added.

The FIRST post-reset lifecycle attempt uses the installed fixed connector
`start --config /etc/happyranch/connector.json` under the existing owner,
actual I/r selection/SAME holder, preserved Enabled and original45s bound.

Selected F01 amendment (Founder THR228seq614 selecting A at seq611) changes
ONLY the strict connector-pair absence rule of historical proposal125. At EVERY
eligibility call, BOTH configured expanded connector snapshot and companion
anchor positively absent under safe literal-ancestor/leaf lstat observations
permit the unchanged store's configured default: configured connector identity,
empty device/pending-pairing maps and initial pairing/revocation epochs. This
same current-state rule applies before and after credential consumption in the
SAME Start, repeated Admission/Check/Forward/health, independent status/stop,
clean retry/new generation and either otherwise eligible automatic-boot writer
order. There is no default-origin cache, history carrier or inherited allowance.
A valid present pair remains authoritative through unchanged full envelope,
anchor, generation, digest and identity validation. Partial, corrupt, mismatched,
unreadable, aliased (including dangling symlinks or hardlinked leaves), wrong-type,
unsafe or observably moving paths refuse78, never default. Only genuine ENOENT
means absence; missing directories are not created. Metadata bookends consume
the original clipped deadline without retry; these sampled checks supply no
atomic race, rollback or history guarantee. Success remains exit0 without output.

Total loss of a former pair deliberately permits that CURRENT default, with
former paired devices, pending grants and pairing/revocation history UNKNOWN,
not recovered or certified fresh. Absent devices fail current authorization;
this does not preserve lost epochs or prove former streams were closed. No
automatic trust save, mandatory pairing, repair or provenance interface follows.
Persisted TSNET identity/replay safeguards remain separate: consumed input still
requires real valid persisted network identity and absent enrollment input;
missing/invalid identity or reintroduced input refuses before engine/listener.
Fresh enrollment still requires actual fresh credentials. Both boot writers
independently require consumed identity, enabled/run policy, current installed
AND loaded authority/census, linked transitions and conservative carried debt.
Current generation, policy, rate3/600, backoff30/60/120, qualified300 observed-health
reset (including selected579 possible ALL3-debt refill), accepted stop inhibition,
unknown-cleanup refusal, listener-first withdrawal and actual Stop/Wait/scanner
joins retain their original owners and deadlines. Absence, replacement, crash,
nonclean owner or preparer loss proves no historical joins, reset or owner adoption.

Setup/reset supplies no authentic trust snapshot+anchor. BOTH-absence alone no
longer proves all downstream positives unreachable on this corrected SOURCE.
Actual fresh credential, compatible installed+loaded config/policy, service UID
and custody, current lifecycle/admission, real engine receipt/ExpectedPeers,
worker/lease, connector probe and listener gates must still pass independently.
Earlier package/setup failure or any supported refusal retains its original
nonzero status, capture and once-only EXIT cleanup. Starting is not READY. No
trust bootstrap, private-record read, synthetic diagnostic, successful skip or
continuation after nonzero/late/stale/ambiguous/typed/nonclean/unknown outcomes.
Controlled downstream stops/starts still need physical completion within original
deadlines; raw loss stays raw loss. Restore, reset-failed, absence and a cleaner
reread cannot clear debt or acknowledge Stop. The SAME five-operation denial
probe stays after actual current READY without creating its StateDirectory.
ALL retained downstream bodies remain UNEXECUTED conditional specifications:
connector/sidecar READY and ExpectedPeers, staged-credential provenance/custody,
source/drop-in retirement, denial matrix, interrupted retirement, explicit
enrollment, virtual admission/production peer, watchdog health/cessation,
automatic PID1 fresh retry/composite gates, readiness withdrawal,
revocation/sequential shutdown/SAME Stop/no-residue, fresh recovery and ordinary
stopped package reentry/residue/current admission. Source reachability is no
execution or coverage proof. E remains incomplete/nonzero/terminal-null/noPASS,
pretransaction with no rollback or equivalent keepers for removed barriers and
destructive negatives; all historical adverse records and C1/C2 gaps remain.

Both invalid managed fault programs and three rollback proof emissions are
removed. Missing OLD/checkpoint evidence remains absent: five managed durable
checkpoint oracles (payload_old_retained, payload_published, and each of three
unit_published boundaries), SAME dedicated injected exception, full OLD payload/
unit/optional-drop-in bytes/types/exact modes, residue and supported reentry.
Normal stopped install/residue is not rollback. Both wrong-boundary credential
negatives, exclusive shell helper chain/cleanup reference release and exact
marker-restored credential-free restart are retired. Lost without equivalent
keepers: reached source/consumed credential phase, SAME activation/completed-job/
original live-baseline-attempt-final history, noExec/noREADY/noRestart/SAME-peer
proof and exact restored-marker restart. The capture negative-unit-reference
interface remains unchanged and unselected. Both90-ci-barrier arms/directory/
latches/jobs/trap releases and their proof emissions are removed. Lost without
equivalent keepers: entered start-before-stop, cancellation at that boundary,
stop-before-start/queued start behind Stop-post and overlap stop-wins. Sequential
completion and C/D source tests supply none of those shipping ordering oracles.
A genuine no_double_close receipt is not a measured engine-close count.

Initialized evidence stays terminal=null/nonzero; mandatory missing observations
and diagnostic stay ABSENT under unchanged schema4. The final shell path fails
directly before cleanup, with no PASS echo/complete/zero/skip. EXIT/INT130/TERM143
retain the first initiating status and one cleanup owner. Earlier initialization
failure is real failure, never a synthetic null artifact. Existing finalization
still validates all mandatory evidence before writing complete; old run records,
hashes and failures are unchanged.

Journal delivery is one compact closed UTF8 envelope INCLUDING finalLF<=2048B
on ordinary, initial, error, cancellation and shell fallback paths. Within it,
whole v1/v2 arrays stay<=512/896B, metadata<=640B and counts<=6/16. Actual complete
candidate size controls admission; administrative406/1810/238 arithmetic is not
runtime certification. First capacity refusal latches only that grammar, keeping
its earliest whole accepted prefix without refill/holes/eviction; attribution,
cursor/decision/terminal automata and the other grammar continue on the SAME
bounded stream. Safely identifiable complete v2 projection failure is local;
unidentifiable malformed JSON/UTF8/shape/duplicates/nonfinite and genuine raw/
frame/count/query/timeout/reap losses affect both tails. Each tail needs actual
EOF, successful query, confirmed reap and its own unlost tail. Valid delivered
prefixes survive real loss with actual non-success; killed/missing/invalid final
output cannot reconstruct them or claim observed-empty. Both projections exist
on all fallbacks with explicit closed losses/false tails. Framing is validated
before shell interpolation inside the SAME4s command, with no extra query/time
allowance. Original fields/boot/window/raw65536/frame8192/frames256/internal3s/
shell4s/kill1/reap, original_invocation=null/window_only, genuine final stopped
revision and E7 attribution are preserved. Reduced retained history and possibly
omitted terminal have NO equivalent keeper; no terminal or v1/v2 authority is
synthesized or borrowed.

The accepted29-case record E1a–f/E3a–h/E4a–e/E5a–j retains observable contract,
credible regression, nearest-owner/distinct gap and NO new test-only seam.
Every explicit coverage loss has NO equivalent keeper or executed keeper proof.
This is maker source only: fresh complete E/affected A–D/F, actual FULL final
independent interaction/coverage review and SAME-final SOURCE/STATIC QA remain
root-owned. All product parse/import/compile/format/lint/test/behavior/timing/
executable-QA/RED-GREEN/mutation/restoration/selection/collection/repetition/
keeper proof is UNEXECUTED/SUSPENDED/nonblocking, never PASS. Python units are
RETIRED; integration INCLUDING actual product callback is SKIPPED24342.
Publication, execution and cleanup remain HELD; UNKNOWN12372 is not cleared.

TASK12534/manager revision65 corrects independent12528 E5-R1/E7-R1/E7-R2
within the same accepted cases; independent renewed acceptance remains owed.
Closed decoding rejects both signed exponent overflow and named nonfinite
constants anywhere, including nested ignored members, before job attribution.
Duplicate refusal and ordinary finite decoding stay separate from true typed
job-ID/result/PID1/UID0/boot/window checks. Per-frame recursion refusal records
parse_loss and continues within original bounds, retaining preceding valid jobs
and permitting later valid distinct jobs. Malformed-first/after/between-valid
and all-invalid versus empty/observed keep existing loss precedence. Shared
receipt/plain refusal stays category-only; other existing decoder callers retain
their parse-loss boundaries. No malformed sample or decoder was executed.

Final journal serialization into the worker buffer is provisional. The existing
wrapper owns cancellation through import, serialization, validation and actual
private safe-file publication, preparing both validated normal and timeout/
both-false-tail variants from the SAME safely captured prefixes. Nested handlers
retain first delivered cancellation. Exclusive private staging, full write,
flush and successful close precede replacement; original shell-file ownership
and0600 are retained for its next read. Catchable signals are blocked only over
this finite publication section and observed pending AFTER actual replacement:
that observation is the completed-delivery cutoff. Pre-cutoff cancellation
selects the existing timeout/false tails for BOTH projections and actual signal
status, with at most ONE replacement by the prepared cancelled variant. Signals
after completed delivery do not retroactively invalidate it. Simultaneous blocked
signals coalesce without observable kernel arrival order. There is no unbounded
reserialization, new query/window/allowance, signal+I/O atomicity, fsync/crash
durability or hard OS-latency promise. Publication failure refuses the file;
GNU timeout124 may retain only a completed timeout/both-false delivery, while
SIGKILL137, missing/invalid/uncommitted bytes cannot reconstruct output. Shell
interpolation and snapshot retain that admitted envelope despite nonzero status;
the initiating campaign failure and once-only cleanup remain unchanged. Existing
E5d/E5h/E5i and E7 four-answer/loss records own these delivery/decoder edges;
no new coverage loss, test-only seam, execution or behavioral PASS follows.


TASK12544/manager revision67 corrects independent12541 E7-R3 within the
existing malformed-frame/prefix cases, pending renewed independent acceptance.
Receipt and completed-job timestamp admission reuse the journal's positive
ASCII decimal1..18-digit domain before conversion, with conversion refusal
owned per frame as parse_loss. Prior receipts/jobs/seen IDs remain; rejected
frames cannot add IDs and later valid distinct rows continue. Inclusive window
endpoints, boot/unit/original receipt invocation and PID1/UID0/job/result checks
remain separate. Invalid-first/after/between/all-invalid versus empty/observed,
duplicate-after-invalid and parse_loss precedence retain the existing contract.
No raw timestamp/error disclosure, decoder/shell/cancellation change, new seam,
query/window/allowance or execution follows; all proof remains suspended.

Groups1–9 retain LOCAL SOURCE dispositions for root assessment, with R1–R5
source corrections preserved and R6 remedy applied as source; group10 now supplies
coherent source completeness/custody for mandatory fresh independent gates:
installed/loaded authority; bootstrap/SAME-OFD; package B; CLI/body/helpers; boot;
records/sticky failures; retirement/joins; health/reset/rate/diagnostics; owned
bounds. This is a maker source disposition, not independent review/QA or proof of
runtime feasibility. At the seq585 R6 snapshot, the separately recorded exact historical-retention
exception retained main_test/health_consumer assertions on the unselected legacy
consumer and restored H1045 PATH command helper: only four comments changed;
executable test bodies, inputs, assertions and bounds then remained unchanged.
Later seq590-authorized D1–D4 internal corrections (manager revision57/TASK12489)
register scanner stop/actual-done rescue before fatal waits and check records
closure without an unbounded receive; distinguish controlled child exit from
teardown and keep no-TERM observation open through the actual supervisor result
in all three negative rows, refusal/single-STOPPING and terminal/refused-exit
callers; preown stop/cancel plus actual query/release/cancellation-helper result
joins on abort, latching consumed results; and latch early supervisor code/finished
before Fatal while retaining any already-delivered child handle. Shell show-done
and stop-failed bytes are emissions, not completed consumer observations. Actual
teardown still cuts off attribution before test-owned kill. All53 Test declarations
and table dimensions remain, with original product bounds and v1/v2 framing
unchanged. No test removal, new production seam/caller or managed migration is
selected. The exact305B helper/six uses/three legacy callers and its seq585 lifetime
remain separate from the seq590 factory exception. No new coverage loss is intended;
execution/RED-GREEN/mutation/repetition/keeper proof remains suspended. This retained
test-only production path is explicitly excepted, not evidence of managed
lifecycle2 behavior or test-authoring PASS. Every selected managed source contract and missing behavioral/physical/
timing proof remains owed. No extra launch route is introduced. Compilation/product parse/import/formatter/build/lint/tests,
behavior/timing/executableQA/red-green/mutation/repetition remain UNEXECUTED/
SUSPENDED/nonblocking, never PASS. Python units RETIRED; general integration
INCLUDING actual callback SKIPPED THR243seq42. Daily-only19UTC Product E2E and
lint/static-only merge checks remain unchanged. No new test/fault program or run
allocation. Original39128125220/1 FAILED15/terminalnull/causeUNKNOWN/cleanupUNPROVED,
CONSUMED missing_boundary/acquisition_success=false/payload0 and exhausted grants
remain unchanged. FULL independent review, same-subject SOURCE/STATIC QA and
root's substantive report remain owed. Publication/runs/cleanup HELD.

### Current LOCAL C3/C4 correction — ROOT12232 / THR228seq590

Founder590 and manager revision52 accept the exact joint revision50 design.
The local source removes shutdown's unused acceptCaller branch and its five
shipping false arguments. Autonomous errors still hand off to an external
shutdown owner; deliberate Stop retains nil/ErrEngine precedence. Listener
withdrawal, actual accept/proxy/monitor joins, one engine completion and the
C1/C2 proxy corrections remain. The two former artificial owners now use actual
successful Sidecar.Start-owned Accept registration, independent one-shot late-error
gates and real acceptWG/Stop results. The eight owner-table dimensions and eight
same-object Stop callers, late-success resource and repeat/error observations
remain; main.stopTwice stays sequential. Synthetic post-teardown registration
coverage is retired, without an equivalent shipping-state keeper claim.

The adapter uses actual time.Now, fixed200ms polling and real
server.LocalClient().Status; clock/poll/client overrides are removed. Only the
existing newServer factory, six-method tsnetServer (Start,ClearAuthKey,Up,Status,
Listen,Close), stored server, production server wrapper and default constructor
mapping remain as the exact YES test-only factory exception. Existing consumers
are main's constructor, its retained main_test consumer, testTSNetEngine and the
retained adapter owners in engine_tsnet_test; no new production caller/interface
is admitted. Edit authority ends at ROOT12232 delivery/terminal state or Founder
revocation. After successful delivery only the exact delivered closure/enumerated
uses may remain until revocation or separately authorized replacement; retention
grants no later edit authority. The distinct seq585 historical305B helper and its
existing consumers/lifetime remain unchanged; managed retirement never uses it.

Test source retains fifteen failure shapes, ten delivered-terminal shapes and
all eighteen whitespace input/stage rows. Reached Status cases use the same
original750ms earlier parent at fixed200ms, failing starved setup; contextless
Start crosses its original future deadline with parent delivery still pending.
Both Up cancellation owners have matching positive peers. The renamed
DeadlinePollArbitrationRefusesTerminalReceipt uses the original10ms parent:
zero Status/deadline_exceeded is an earlier fence, not select-entry proof;
Status terminal admission requires deadline_exceeded; peer_wait_deadline labels
the actually selected Done case. No unique-select/no-poll guarantee.
Finite cleanup owns releases, endpoints and actual Start/Stop/observer results
before launch and settles them once; a timeout is failed cleanup, not completion.

The C5 correction (manager revision54, Founder590) uses the existing
startupContextError: delivered Err first, actual absolute deadline second.
Adapter entry refuses elapsed pending-delivery callers before factory work.
All dependent admission uses the SAME effective readyCtx/min(caller,private80),
including before factory, after allocation/before Start, after contextless Start,
after Up/Status, before Status and immediately before each positive receipt.
An allocated server refused before Start clears its key reference; every returned
Start still clears first, and a returned Start error retains ErrEngineStart even
if late. A late successful Start admits no Up. Late Up/Status success, error,
nil/nonrunning/missing-peer shapes refuse with zero receipt and terminal category.
The existing early-terminal owner retains delivered rows and adds pending entry
and delivered-cancellation precedence. Its existing contextless-Start owner now
covers13 factory/Start/Up/Status pending-result rows at the original750ms bound;
these adapter assertions are independent of Sidecar's later success fence.
Timely positive Up/Status,15 producer/10 delivered-terminal/18 whitespace rows,
Sidecar75/private80 and the actual fixed200ms ticker remain. No new seam, budget,
unique-select guarantee or contextless-work interruption/hard OS timing claim.

Eight explicitly approved losses/consolidations remain: nil_factory, nil_server,
raw LocalClient acquisition error, raw downstream Status error, exact returned
Status pointer, closed acquisition mapping coupling, closed delegation mapping
coupling and deterministic unique-select/no-poll producer. Raw3/null2/select lack
complete keepers; mapping2 retain category/consumer coverage only. Default AuthKey
mapping, real ClearAuthKey METHOD and fake upstream clear CALL are separate;
original read-buffer clearing and real upstream initialization/key lifecycle
remain unproved. No behavior, keeper or test-authoring PASS follows.

All product parsing/import/compilation/formatter/build/lint/tests, timing,
RED/GREEN/mutation/restoration/collection/repetition/executable QA/keeper proof
remain UNEXECUTED/SUSPENDED. Integration including product callback is
SKIPPED24342; Python units RETIRED. C1/C2 finite TCP/resource/half-close/Stop-plan
and genuine post-Forward installed+loaded authority/phase/venue proof remain OPEN.
Accepted13, A/B, original deadlines, custody and self-heal/reset579 behavior are
unchanged. Publication/execution/cleanup HELD; UNKNOWN12372 and all original
failure/consumed evidence persist. Root still owes renewed complete independent
immutable-final review, same-subject SOURCE/STATIC QA and substantive delivery.
