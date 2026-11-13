# M4 native macOS outside client and public VM cell

THR-327 goal C now uses a **GitHub-hosted macOS runner**, after merge. The
main-only `managed-m4.yml` workflow has `workflow_dispatch` only, `contents:read`,
pinned existing actions, synthetic state, always-cleanup and plain-log artifacts.
It is not a required check. PR triggers, required lints and daily Product E2E
are unchanged. Static verification and the VM-only premerge phases do not prove
native transport acceptance.

## Execution status and preserved failures

- JOB-6610 exited1 at outside-client join; the normalizer withheld its cause.
  Its proxy-egress TLS reset and original logs/purge failures remain in PR1449.
- JOB-6615 exited1, `tcp-connect` at90.18s, while trusted health/key returned200.
  Its aggregate classifier did not attribute endpoints or prove a root cause.
- TASK-15670's ONE report-only strace diagnostic, JOB-6618, exited1. Client
  syscalls were observed, but no parsed connect endpoints or client DNS replies
  were attributable. Host resolver comparison was198.18.0.47. **TUN-bypass
  hypothesis still unproven**, neither confirmed nor refuted. No raw trace was
  retained, no host fix was made, and that diagnostic allowance is spent.
  Logs/purge/teardown/clean-state each exited0, retained cache ledger3. This is
  not evidence of a product defect on a real Mac behind a TUN proxy.
- Native macOS C-ABI joining, route/refusal/DERP/exposure assertions and real
  GitHub encrypted-handoff coordination are **NOT RUN premerge**. VM-half
  `--premerge-vm` receipts must be reported separately at the pushed head.
- TASK-15684's ONE native attempt (run43270232585, JOB-6633 exit1) failed at
  the first empty-membership poll: Headscale returned `null`. Join and all
  installed-product checks remained unproven; the attempt stays a failure.
- Attempt2 (run43279212804/JOB-6639) failed with no macOS capacity/steps.
  Attempt3 (run43283669745/JOB-6640) built the native archive, then failed
  before observed membership/install. Its old `outside-join` phase combined
  decrypt and join; the underlying cause remains unresolved. These attempts
  stay spent. The evidence repair grants no dispatch or join allowance.
- Attempt4 (TASK-15753, run43310355906/JOB-6656 exit1) reached C init0,
  then C start returned-2 immediately; address waiting was not reached. It
  remains failed/spent. The never-created-target hypothesis is refuted by the
  existing Bridge constructor, which created the target before worker start.
  The actual cause remains unresolved; the next failure classification is
  evidence, not a new native acceptance result or run allowance.
- Signed Mac.app, Swift ClientBridge and GUI on founder hardware are separate,
  founder-assisted acceptance and are **NOT RUN**. The current GUI is BYO;
  managed enrollment currently reads a development environment key, which
  conflicts with the normative credential contract. A separately authorized
  secure managed-enrollment product change is needed before prescribing that
  GUI path. Do not put a key in its environment or substitute system Tailscale.

## Fixed cell and prerequisites

Only disposable Ubuntu24.04 VM `i-j6c1pwjqygzrccal4mzh`, cn-hongkong,
47.238.219.200 is admitted. The host checks the dedicated security group has
only443/tcp and3478/udp ingress. No80/22, SG/SSH change, restart, resize, new cost,
cloud setting or real org data is permitted. The VM stays Running. Pre-existing
sshd22/DHCP68 are left alone and are not test listeners.

Reuse the M3 VM tools under `/var/lib/happyranch-m3` (uv, Go, system Python/venv,
pip and checksum-pinned Headscale0.25.1). Public exact-SHA source/build caches
live under `/var/lib/happyranch-m4`; fresh test state lives in `cell`, never in
task output. Cloud Assistant mutations are single-issue, with bounded read-only
invocation-result polling; an uncertain RunCommand is never automatically replayed.
No Aliyun profile or cloud credential leaves this host.

The M4 host persists each original InvokeId in `invocations.json` before polling.
Before probing ownership or issuing any cleanup command, it confirms that every
prior invocation is terminal for this instance (`Finished`, `Failed`, `Stopped`).
After lost observation it uses at most60 additional seconds of read-only polling;
it does not require StopInvocation. An owned-sha marker proves ownership only.
Missing markers, empty results, polling failures and `Stopping` never prove that
a writer stopped. If settlement is unproved, no further remote command is issued:
the summary is `FAIL/UNCERTAIN`, records the original InvokeId (or null when the
issue response was lost), and publishes `clean_state_clearance:false`. Private
host scratch is still removed. Inspect and settle that original invocation before
any separately authorized recovery; never replay the original mutating command.

**Certificate ledger is3 of5 this week. No new issuance is allowed.** Every
prepare requires the existing root-owned0700 cache and root-owned0600 regular
single-link files, ledger prior2+reserved1, and cached certificate valid for
more than24h. Missing/unsafe/unusable cache stops before Headscale starts. Headscale loads
the cached combined PEM through tls_cert_path/tls_key_path with ACME disabled;
normal certificate/hostname verification stays enabled and no renewal is possible.
Only this ACME account/certificate/key/ledger cache is private allowed residue;
Headscale DB/noise/DERP state, pairing state and product material are removed.
`--remove-cert-cache` remains a separate explicitly authorized final decommission,
never part of an acceptance/premerge pass. Do not run it for this task.

## Handoff transport and residual risks

Transport is existing **GitHub HTTPS**: the runner publishes an immutable v4
artifact containing only its ephemeral RSA3072 public key and public run metadata.
The authenticated host validates exact run ID, attempt1, workflow path,
workflow_dispatch, main branch, exact SHA, artifact digest and public-key digest.
It transfers only that public key through Cloud Assistant. The VM encrypts each
one-use enrollment key (<=10min) and pairing code (<=5min) with RSA-OAEP/SHA256
before returning ciphertext. The host publishes only ciphertext/public fields
on the existing PR1449 issue-comment channel, once per phase. No new GitHub
secret, variable, setting, permission scope or inbound port is used.

The runner reads public comments without authentication; the contents:read token
is not used for messaging. It accepts only immutable operator ID4394426,
unedited single matching comments bound to run/attempt/SHA/key digest/phase and
expiry. Duplicate/stale/future/edited/mismatched envelopes refuse. All waits,
response sizes and public API reads are bounded (48 reads across the run).
Public API exhaustion/outage fails closed; this host's unauthenticated read
was rate-limited during authoring. Runner-side availability is NOT RUN premerge.
GitHub's [public-comment API](https://docs.github.com/en/rest/issues/comments)
and [immutable artifact API](https://docs.github.com/en/rest/actions/artifacts)
are the existing channels, not a new control service.

The transient RSA private key is0600 runner scratch between workflow steps; the
foreground runner loads it into memory and unlinks it before any credential
handoff. Enrollment/pairing plaintext exists only in runner process memory and
private pipes into the unchanged C ABI. Decryption uses a private-key pipe;
no plaintext output file, argv, env, workflow input, log, PR body or artifact.
Darwin ignores XDG_CONFIG_HOME: on the disposable runner a checked, absent
Application Support tsnet path is linked into private scratch, using the actual
native main executable name `m4-tsnet-host` (`tsnet-m4-tsnet-host`, with the
corresponding `tslib-m4-tsnet-host` legacy refusal). The runner exclusively creates the0700 state
target inside private0700 scratch before installing the link; Bridge checks the
target's literal directory type, owner and0700 mode before worker start.
Existing/foreign state (including dangling links and legacy tslib state) refuses;
cleanup removes only that exact link and removes state with scratch. The runner
receives no Headscale admin key, daemon bearer, cloud credential or real data.

The host observes the client's Headscale membership before install. Headscale
`null` and `[]` both project to an empty list and continue the bounded join wait.
Other JSON types, malformed JSON, missing node fields and command failures
refuse; unexpected membership still refuses. After
paired-positive controls, the client redeems a third disposable `pair-ready`
code; existing redacted `list-devices` is the host's acknowledgement to revoke
`pair-revoked`. This adds no product endpoint/seam and grants no runner write
scope. Host publishes a public revocation acknowledgement; the client then
asserts refusal and unaffected paired200. A fresh dispatch/key is required after
a failed attempt; automatic reruns cannot reuse an enrollment/ceremony.

Residual risks are GitHub availability/rate limits, public timing/run/IP/key
metadata, and compromise of the authorized operator/repository account or runner.
Binding and TTL checks constrain substitution/replay; they do not remove trust
in those accounts or GitHub. Timeouts fail and trigger cleanup. An uncertain
comment POST must be inspected rather than replayed.

## Commands and ownership

Premerge, from the clean **published** exact head, submit a persistent job
(`review_required:false`, `persistent:true`) for the single VM-half pass:

```bash
python3 scripts/ci/m4_tailnet_aliyun.py --sha FULL_PUSHED_SHA --output /absolute/task-output/vm-premerge --scratch /absolute/task-tmp/fresh-vm-premerge --premerge-vm
```

This prepares real Headscale/daemon, asserts empty membership through the shipped
nodes phase, transfers a locally generated test public
key, decrypts VM-generated enrollment and test pairing ciphertext only in memory,
then runs logs/purge/teardown/clean-state. Test pairing uses the shipped CLI with
a fixture config pointing to state inside the disposable cell; it installs no
product services, joins/redempts no client, and claims no native/route acceptance.
The summary is `VM_PREMERGE_PASS`, never a full M4 PASS.

After independent review/QA and manager merge, engineering_manager dispatches
**Managed M4 macOS tailnet**, workflow `managed-m4.yml`, main, **no inputs**:

```bash
gh workflow run managed-m4.yml --repo t-benze/happyranch --ref main
gh run list --repo t-benze/happyranch --workflow managed-m4.yml --event workflow_dispatch --branch main --limit 5 --json databaseId,headSha,status,url
```

Select the newly dispatched run and its exact main SHA; do not infer identity
from the latest run alone. From a clean checkout of that same main SHA, promptly
start the host coordinator as an authorized persistent job:

```bash
python3 scripts/ci/m4_tailnet_aliyun.py --sha FULL_DISPATCHED_MAIN_SHA --runner-run RUN_ID --output /absolute/task-output/m4-native --scratch /absolute/task-tmp/fresh-m4-native
```

It waits for the public-key artifact (max10min), prepares the VM, publishes
enrollment ciphertext, observes join, installs the real product, publishes pair
ciphertexts, observes the positive-control marker, revokes, and reads native
completion/artifacts. After positive invocation settlement it attempts owned
logs/purge/teardown/clean-state, fencing each subsequent command the same way,
checks SG and provider Running, and removes private host scratch. On failure it
requests cancellation only for that exact dispatched run. Cleanup uncertainty
remains FAIL, never PASS. VM cleanup requires the host coordinator even when the
workflow ends; the runner has no VM credential. Keep the host job alive through
cleanup and inspect its actual terminal receipt.

## Assertions and retained diagnostics

The runner builds the **unchanged** `app/mac/tsnet-bridge` with Go1.23.12,
readonly graph, `-buildmode=c-archive`, then the native C compiler statically
links that archive into `m4-tsnet-host`, as the Swift app links it into its main
executable. A C export query waits for Go initialization before the host loads
the driver's existing shared Python runtime and runs the same private JSON pipe
worker. The worker resolves the eleven existing ABI exports from the main
executable (`CDLL(None)`), without repackaging/dlopen of the Go archive. Python
home selects the driver's existing installation; no dependency is installed.
The workflow selects the runner image's existing OpenSSL3; it installs no dependency.
The existing Python protocol/HTTP parser calls the C ABI headlessly.
No product export/flag, stub transport, dependency change, Swift/GUI path or
system Tailscale is involved. Native toolchain and archive hash are recorded.

| Check | Required observation/control |
| --- | --- |
| allowed-200 | Tailnet GET real daemon's synthetic teams ->200 and exact M2 body. |
| absent/never-paired | Same transport GET ->403 identity_denied; paired200 before/after each. |
| revoked | Targeted device200 before revoke; same credential403 within30s, unaffected device200. |
| forbidden routes | Six retained M2 method/path rows ->exact403 category; paired allowed200 brackets each; synthetic org survives. Forbidden routes are never allowed as controls. |
| DERP | Bounded all-interface native packet observation plus native socket controls and a fresh exact real-daemon200 prove relay traffic with no native UDP; current-run authenticated effective identity must establish only region999 at the expected VM endpoint with no upstream map/mesh. Missing/partial evidence refuses. The existing separate allowed200 follows, then exposure probes. |
| public exposure | Verified443 health200 before/after bounded application-level probes; daemon path on control443 is404; public18765/18443/8765 yield no TLS/HTTP response. Guest crosscheck requires daemon/connector loopback controls and no forbidden public listener. No response is a bounded observation, not universal reachability proof; raw TCP through a proxy is insufficient. |
| logs/cleanup | Fresh run directory, microsecond journal boundary and shipped attribution. Installed-product purge assertions remain unchanged. Without install, assert absent product state and label not applicable. Remove transient state/CA/links/test sockets; preserve unchanged DNS hashes and only the validated cert cache. |

`managed-m4-logs-RUN_ID-1` contains only request.json and client-summary.json.
Private native filch/stderr/build logs are excluded from workflow artifacts.
The logtail delay
defers periodic drain wakes, but v1.78.0 starts an initial drain immediately;
consumed filch entries can be truncated before any upload succeeds. It does not
guarantee retention of startup evidence. The client worker is killed/reaped
without graceful logtail shutdown and scratch is removed by foreground/finally and workflow
always-cleanup. VM plain-log collection excludes cell/keys/DB/state, records
stream byte counts/hashes and <=256KiB excerpts, gzip>1MB and45MB total cap.
Guest1s memory/swap sampling and per-service peaks remain; builds use2 workers,
fixture MemoryMax600M. Sampling is not a hard instantaneous whole-host bound.

The public client summary and Actions log retain distinct `handoff-fetch`,
`decrypt`, `native-process-start`, `join-call` (C init), `native-start` (C start)
and `join-wait` (local address) phases with elapsed seconds. Private worker
progress uses fixed labels and the original join deadline; C init/start return
codes, runner return code and worker return codes before/after cleanup are
separate fields (null means not observed/exited yet; cleanup kill is not the
original failure). Before deleting scratch, the runner copies the existing
`Bridge.join_diagnostics` through a closed projection: fixed DNS/TCP/TLS/Noise/
auth/register/netmap/DERP labels, counts, deadline and elapsed time only.
These pattern observations do not establish an endpoint or root cause.
Failure fields contain an allowlisted exception CLASS and fixed stage/error
label; unknown classes/diagnostic labels become `Unknown`. Exception messages,
argv, raw native text, keys, node/enrollment/pairing material and credential URLs
are never exported. A failure before the join call has no join diagnostics.

On nonzero C init/start, the worker reads and frees the unchanged shipped
`tsnet_last_error` allocation and exports only `bridge_error_class`: `state-dir`,
`log-dir`, `executable`, `control-url`, `already-started`, `not-initialised` or
`unknown`. The Bridge allowlist projects unrecognized labels to `unknown`.
The summary and Actions log retain that label (`null` when no C error was
observed), never the accessor's raw text, paths, keys or URLs. These are pattern
classifications; an unrecognized hostname/auth/backend error remains `unknown`.
Pinned tsnet v1.78.0 puts its log config/filch files in the same state root;
there is no separate harness log-directory setting.

The acceptance gate uses an independent **native packet and socket proof**. The
old filch proof remains a closed corroborating diagnostic: Headscale0.25.1 sends
DisableLogTail before the first netmap, so Tailscale1.78.0 drops later backend
writes before filch. A missing DERP/UDP log observation cannot establish the new
proof. No logtail enablement or product change is made.

The existing macOS `/usr/sbin/tcpdump` captures `pktap,all`, including loopback
and tunnel interfaces, without a BPF, PID or endpoint filter. `-tt -nn -q -k PD`
produces private header summaries with process IDs and direction; no payload
printing is requested. A root supervisor uses the runner's existing Python via
noninteractive sudo, owns/waits for that one capture child, and applies a35s
alarm and2,000,000-byte child-output file limit after sudo (plus the fixed
19-byte supervisor marker; reaching the limit refuses). Capture also stops at4096
packets. There is no installation, persistent OS setting or network change.
The35s proof window includes startup, the old log diagnostic, two `lsof`
observations and a real native GET of the synthetic daemon teams route. The
original worker request deadline is unchanged; late results cannot pass.

The gate requires all of the following before the original subsequent200 and
unchanged exposure controls:

- The exact still-running native PID has positive TCP socket evidence and no
  UDP socket in both `lsof -nP -a -p PID -i -FpfP` observations.
- Every native IP transport header has known syntax/direction. Unattributed
  egress, native fragments/unsupported packets and malformed metadata refuse.
  Unsupported background formats, including ARP, require positive attribution
  exclusively to another process; unresolved attribution refuses.
- Zero native UDP packets on every captured interface/address/port, not only
  UDP visible at the VM. A guest capture alone would miss packets dropped by
  the unchanged security group before reaching the VM.
- Native TCP egress is exclusively to the VM's443, with positive payload bytes
  both ways on a matching native flow, and the actual unchanged bridge returns
  200 with the exact expected real-daemon body during that window.
- Capture starts positively, terminates/reaps normally before its deadline,
  has a closed supervisor stop marker, and reports zero drops. Captured and
  received counts must agree, as must parsed and captured counts. Missing,
  partial/unread or incompatible counter semantics fail closed.

Source basis: [Apple tcpdump metadata/all-interface API](https://github.com/apple-oss-distributions/tcpdump/blob/main/tcpdump/tcpdump.1),
[pinned peer transport](https://github.com/tailscale/tailscale/blob/v1.78.0/wgengine/magicsock/endpoint.go),
[pinned UDP/DERP dispatch](https://github.com/tailscale/tailscale/blob/v1.78.0/wgengine/magicsock/magicsock.go),
[Headscale map composition](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/derp/derp.go),
[effective node observation](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/derp/server/derp_server.go),
and [environment/config resolution](https://github.com/juanfont/headscale/blob/v0.25.1/hscontrol/types/config.go).

Before packet success can publish region999, the host obtains a current-run
`relay-identity` observation from the running guest. Existing Headscale0.25.1
`GenerateRegion`/`DERP Nodes[0]` startup journal records must belong to the exact
live systemd invocation/PID and name exactly one region999 and one node at the
VM hostname/443, IPv4 and STUN3478. Before launch, the harness snapshots the
pinned binary and config's digest/inode/timestamps. The observer checks the live
executable, exact argv, boot/start identity and unchanged inputs, and refuses
all HEADSCALE_*, DERP_* or TS_* process environment overrides. Unknown config
keys, upstream URLs/paths, auto-update, mesh, changed process/config or missing/
multiple/contradictory records refuse. Pinned Headscale composes an empty map
from empty sources, adds this observed embedded node, and has no mesh setup.
This establishes the singleton map under the trusted guest/input assumptions;
it does not read the client's or server's in-memory map. No debug setting or
raw config/environment/journal dump is published.

The authenticated Cloud Assistant response is checked against this source SHA
and fresh recipient key digest, then its closed label/integer projection travels
in a separate operator-authenticated, run/attempt/SHA/key/expiry-bound envelope.
The runner refuses an absent, malformed, wrong-run or older-than120s identity;
wrong/multiple regions, wrong endpoint and any upstream/auto-update/mesh refuse.
Contradictory client log regions (including [123,999]) veto otherwise-passing
packets; missing filch logs alone still do not reject the independent proof.

Strength argument: pinned Tailscale transports peer WireGuard data by UDP or
DERP. A fresh exact real-daemon200 with zero native UDP, no observed UDP sockets,
and bidirectional native TCP payload only to VM443 establishes relay traffic.
The runtime identity observation names that relay as999. Neither configuration,
join nor connection counts alone can pass. Original separate allowed200 and
exposure controls execute only after both proofs succeed.

Limits: this is a bounded observation of this process/request and relies on the
OS's packet/process metadata and counters, the unchanged authenticated fixture
and bridge, and the trusted disposable runner. It also trusts guest root and
the pinned process loading the unchanged snapshotted inputs; no hostile root
memory tampering is covered. Startup identity remains valid only while that
process and its fixed no-upstream/no-update inputs remain unchanged. It does
not certify all future UDP behavior, all network topologies, or signed-app/Swift/GUI behavior. Socket
snapshots alone would not prove the path. Native tool/metadata/counter behavior
and actual acceptance are NOT RUN premerge; conservative refusal is intentional
if this runner cannot supply complete evidence. The next separately authorized
native attempt must exercise the actual observer, request, post-proof200 and
exposure controls; offline fixtures cannot supply that acceptance.

`packet_diagnostics` reaches client-summary.json and Actions before private
cleanup and remains in the final summary. Its closed labels are `unknown`,
`capture-unavailable`, `capture-start`, `capture-budget`, `capture-deadline`,
`capture-cleanup`, `capture-incomplete`, `capture-format`, `metadata-missing`,
`udp-observed`, `udp-socket-observed`, `socket-evidence`,
`relay-endpoint-unexpected`, `relay-traffic-missing`, `request-failed`,
`identity-missing`, `identity-malformed`, `identity-stale`, `identity-region`,
`identity-endpoint`, `identity-upstream`, `identity-log-conflict`, `passed`.
Only packet/native/UDP/socket counts, TCP socket observation count and relay
transmit/receive byte counts accompany it, clamped to0..16,000,000 with real
integer types only. Unknown labels map to `unknown`; no PID, tuple, address,
path, URL, key, raw header or payload is exported. Raw header/stderr files stay
in private runner scratch and existing finally/always-cleanup removes them.
The offline UDP sensitivity fixture must refuse with `udp-observed`; enabled
UDP sockets, unexpected endpoints, partial captures and absent positive request
controls have separate failures. All earlier failure/issuance budgets remain
spent. This repair extends the guest/control drivers; its ONE conditional VM
premerge pass observes real guest identity and cleanup without a native join.
That pass does not prove native transport or close native acceptance.

For corroborating log evidence the worker emits `derp_diagnostics`, reprojected
by Bridge and again by the runner. Before private cleanup the runner writes it to
client-summary.json and Actions stdout; the final cleanup summary retains it.
`label` is one of `no-log-files`, `log-budget`, `no-decodable-entries`,
`region-missing`, `region-unexpected`, `udp-disable-missing`, `unknown`, `passed`.
Unknown labels reproject to `unknown`. Only `file_count`, `total_bytes`,
`decoded_entry_count`, `derp_connected_matches`, `udp_disable_matches` (integers
clamped to0..16,000,000; invalid types/negative values become0) and
`matched_regions` (sorted distinct integer IDs0..10,000,000, at most8) accompany
the label. Booleans/floats/strings are not integer evidence. Counts can be partial
on read failure; `unknown` also covers a worker failure before diagnostic delivery.
A pre-cleanup publication error records the fixed `derp-evidence-publication`
failure and still reaps/removes private state before final evidence publication.
No raw text, paths, addresses, keys or URLs cross this boundary. Attempt6
(TASK-15774/run43330485188/JOB-6668) remains failed/spent at derp-proof; post-proof
200 and exposure probes were not reached. Bridge/logger/ABI/dependencies remain
unchanged. This packet-proof change supplies no run/issuance allowance or native acceptance.

Before generating or publishing the recipient request, `prepare` executes that
same host with an exclusively owned malformed log config and a synthetic key.
The only successful precondition outcome is init0/start-2, `log-dir`, with the
private error matching the deliberate JSON decode failure. Pinned tsnet reaches
this branch before NewLogger/netmon/engine, so no join/enrollment/network call
is needed. The worker is reaped and the exact state link/target removed before
key generation; acceptance then creates fresh state at the same name/target.
A failure publishes only `native-precondition` and the existing closed error
class (or null), never raw error text, and no recipient request is published.
This checks executable lookup, state directory access and log-file access;
successful later logger/engine creation is not proved by the deliberate stop.

Attempt5 (TASK-15766/run43320896544/JOB-6663) remains failed/spent: init0,
start-2, `executable`. In Go1.23.12 Darwin `os/executable_darwin.go:17-20`, that
exact text means the runtime's captured executable path is empty.
`runtime/os_darwin.go:459-470` reads it by scanning beyond argv/environment;
the archive initializer captures argv before entering Go on a new thread.
Loading after Python startup can observe a modified vector; changing the
loading/initialization order addresses that harness risk. The failed runner's
actual vector mutation is inferred, not reconstructed. Static app linkage has
no demonstrated corresponding failure in the inspected app sources; app
immunity and Darwin execution remain PROOF NOT RUN. Pinned tsnet calls
`os.Executable()` unconditionally before checking `Dir`, so adding `Dir` alone
would not bypass this error. No app/Go/C ABI/dependency/workflow change is made.

The host `summary.json` always includes the `control_waits` list. VM-only
premerge and Linux diagnose-only runs record `[]`: no hosted control wait
occurred, so no wait entry, label, count or elapsed time is fabricated.
Runner runs retain the genuine `HostedRun.waits` entries for public-key handoff,
outside join, pair-ready acknowledgement and native completion: attempted
observation count, elapsed seconds, status and a fixed failure label
(`observation-failed`, `runner-completed`, `deadline`, `unexpected-membership`,
`runner-failed`) plus allowlisted exception class. Thus a completed runner and
an expired host wait remain distinguishable without retaining assertion text.
Each observed wait has `failure_label` (null on success), `observations` and
`elapsed_seconds`; these are per-entry fields, not top-level summary fields.
An empty list explicitly represents their absence when no wait occurred.
All membership, readiness, success and deadline assertions remain required.

Linux `--diagnose-only` remains solely for separately authorized non-acceptance
diagnostics because it reuses the same worker; no default/full Linux acceptance
path exists. **Do not run another diagnostic for TASK-15670.**

Verification: `scripts/local_ci.sh all` as a durable job; shellcheck-x for changed
shell drivers, separate bash-n per driver, Python compile/help, actionlint when
available (otherwise report unavailable), worktree guard and full diff check.
Required hosted lints must be green at the exact pushed PR head. Native macOS
execution is NOT RUN premerge. Signed GUI/founder acceptance and the other four
normative topology/failure rows remain separate obligations; this workflow
covers only separate-network forced private DERP.


## Observer compatibility before enrollment (TASK-15856)

Attempt7 (TASK-15847 / run43357856292 / JOB-6690) remains failed/spent at
DERP-exposure `capture-format`. Its partial13/native13, tx935/rx0 does not
identify the failed line. Raw macos-15 capture was never retained. PROVEN:
the old receive-byte accumulation ran only after a complete first pass, so an
earlier parser refusal leaves rx0 even if incoming records were present.
Whether that happened, and the exact structural refusal, remain UNKNOWN;
source-derived fixtures are not a reconstruction of the runner's output.

Apple's [metadata printer](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/print_pktap.c#L139-L218)
prints parenthesized proc/eproc IDs and `in`/`out` for `-k PD`; an ID can be
empty when a name/UUID exists but PID is unavailable. The normal quiet
[IPv4](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/print-ip.c#L343),
[IPv6](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/print-ip6.c#L249), and
[TCP](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/print-tcp.c#L264) printers produce IP/IP6 and `tcp N`;
[UDP](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/print-udp.c#L854) prints `UDP, length N`.
Link/protocol printers can also emit ARP, other protocols, indented multiline
output or truncation markers. The parser validates metadata first and counts
one packet per timestamped record. Unsupported/multiline/truncated bodies may
be tolerated only with positive, nonzero proc/eproc attribution exclusively to
other processes. Empty/zero attribution never proves another process. Native
unsupported records and all unresolved attribution refuse; kernel ingress must
match the reverse of a known native outbound TCP flow. Native IPv6 to a different
endpoint still refuses. Native payload in both directions exclusively with VM443,
zero native UDP, both socket controls, exact fresh real-daemon200, full counters,
zero drops, limits and authenticated singleton999 identity remain jointly required.
No BPF, process, direction or endpoint capture filter is added.

Apple's [stderr statistics](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/tcpdump.c#L3462-L3518)
use singular/plural `packet(s)` and omit interface drops when zero; a reported
metadata-filter drop also refuses. One captured/received/kernel counter each,
at most one interface/metadata-filter counter, and exactly one PKTAP startup
header are required. Captured=received=parsed, zero reported drops, strict35s /
2,000,000 bytes per file / fewer than4096 packets and positive child stop/reap
remain mandatory. Compression/other stderr text is counted, never exported.
These are source contracts; actual hosted binary compatibility remains NOT RUN.

`capture_diagnostics` accompanies packet evidence in client-summary.json and
Actions before cleanup and in the final summary. Its fixed `label` is `unknown`,
`passed`, `prefix`, `metadata`, `multiline`, `truncated`, `transport`, `endpoint`,
`attribution` or `counters`. Unknown input projects to `unknown`. Fixed integer
fields count startup headers, metadata, IPv4/IPv6, other-process packets,
counter-line presence and values, other stderr lines, and unparsed records by
those structural classes. Values use real integers clamped to0..16,000,000;
booleans, negative values and arbitrary fields do not pass the projection.
The existing `packet_diagnostics.label` still names the proof refusal; format
refusals retain `capture-format`. No raw line, name, PID, address, tuple, path,
URL or payload enters either projection.

After the existing native precondition and before RSA recipient generation or
publication, `prepare` runs the real observer self-test. An owned short-lived
Python child makes one exact synthetic HTTP request to an owned loopback
listener, keeps the connection open for both actual lsof controls, and stops
and is reaped with the capture supervisor. The same parser/counter/budget gates
must pass with positive child-attributed bidirectional TCP. The local endpoint
is used only by this precondition; the acceptance proof still fixes VM443.
It publishes `observer_diagnostics` with closed stage labels, packet counts and
capture shape counts on success or refusal, then removes private capture scratch.
`observer-child-start`, `observer-request` and `observer-child-cleanup` distinguish
local child failures; existing capture/socket/proof labels cover observer failures.
A refusal produces no recipient key/request and spends no enrollment.
This proves observer compatibility, not native relay acceptance.

The separate **Managed M4 observer probe**
(`.github/workflows/managed-m4-observer-probe.yml`) is manual-only, non-required,
macos-15, contents:read, pinned checkout with persist-credentials:false, and
always removes private scratch. It runs only this self-test and lsof parsing,
with no inputs, secrets, bridge build, VM/Headscale/Aliyun or PR-comment interaction.
After this workflow file exists on main, the manager may use an authorized branch
ref for later parser validation:

```bash
gh workflow run managed-m4-observer-probe.yml --repo t-benze/happyranch --ref task/TASK-15856
```

Use `--ref main` to probe the merged version. No dispatch is authorized by this
source delivery. managed-m4.yml triggers/guards, required CI and Product E2E
are unchanged. This task changes no guest driver: m4_tailnet_aliyun.py,
m4_tailnet.sh and m4_tailnet_control.py remain byte-identical; VM --premerge-vm
is NOT RUN. Native acceptance remains OPEN. Linux JOB-6610/6615/6618;
native1–7 TASK-15684/15690/15691/15753/15766/15774/15847 (attempt7 run43357856292,
JOB-6690); premerge JOB-6641/6643/6644/6647 remain failed/spent. No allowance is
replenished and no certificate issuance is authorized (last observed ledger3/5).

TASK-15875 adds `structural_skeletons` **only to the credential-free observer
probe** before format validation. Native prepare and acceptance summaries
retain their existing closed labels and bounded integer fields. The first
unrecognized-prefix lines are projected, filling any remaining slots with the
first recognized-prefix lines: at most three lines total, at most 2048 input
characters and 256 output characters per line. Only `IP`, `IP6`, `pktap`,
`proc`, `eproc`,
`svc`, `in`, `out`, `pid`, `Flags`, `length`, `UDP`, `tcp`, `udp` and the fixed
punctuation set `()[],:;<>.=+-/|*` survive. Decimal tokens become `N`,
hex/address-like tokens become `A`, all other words become `W`; whitespace
normalizes to single token separators. No raw addresses, ports, PIDs, process
names, paths, hostnames or payload enter this field. Other printable punctuation
becomes `W`; empty/control/non-ASCII input becomes `unknown`; overlong input
or output becomes `overlong`. The public sink revalidates the closed vocabulary
and bounds. This is structural evidence,
not reconstruction of raw capture, process-attribution proof or native acceptance.
Run43368317931 (TASK-15874) remains FAILED at prefix, captured18/received18/parsed0.
Probe1 run43371075839 at719ad7b31ca35cc189bacb893381400e7844b306 also
FAILED with skeleton `["unknown"]`. The actual rejected form remains UNKNOWN.
Probe2 run43372501871 at877688acd9449f196ac60ea201619287234a4008 FAILED;
its verbatim skeletons were `["unknown", "A ( proc N , out ) IP A > A : tcp N",
"A ( in ) IP A > A : tcp N"]`. Thus same-line positive proc/out metadata and
kernel ingress without proc are observed with the existing `-k PD` flags.
Ingress still requires the reverse of a positively attributed native TCP flow.
`unknown` alone does not identify the refused line. Apple's
[interrupt exit](https://github.com/apple-oss-distributions/tcpdump/blob/58aef51e406a84d34352a0a05946770037a25360/tcpdump/tcpdump.c#L3159-L3175)
adds a newline. The candidate framing fix recognizes exactly one empty terminal
line after the final packet newline; leading/internal/whitespace-only/repeated
empty lines and all unsupported packet records still refuse. The bounded integer
`terminal_separator_lines` reports that recognition in capture diagnostics;
structural skeletons remain probe-only. Captured=received=parsed, zero drops,
positive process/reverse-flow attribution, UDP silence and all existing limits
remain required. The third and final task probe must establish real compatibility;
source/fixtures and the first two failed probes cannot establish it. No capture
flag or packet filter changes. No allowance is replenished.
Native attempt8 remains unspent; JOB-6699 and all prior failed/spent runs remain
failed/spent, with no allowance replenished.

Custody boundary (engineering_manager TASK-15621): the no-raw-address/PID/tuple/payload rule applies to the packet-proof and capture-diagnostic public fields. The pre-existing reviewed pairs handoff payload synthetic tailnet address and VM service journal metadata are existing public surfaces and are outside that assertion; credentials, enrollment/pairing material and raw capture lines remain forbidden everywhere.
