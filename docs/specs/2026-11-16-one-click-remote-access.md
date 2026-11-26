# One-click remote access and one invite (THR-327)

Status: **design proposal; no implementation or host change authorized by this file**. TASK-16631,
founder seq487. Baseline: `9a6844b61df3b64d3f6627e7b0e53c0872a3438f`. Authority: seq421 chose
TASK-16298 revision 2's middle option: separate `happyranch` system user, system units and all
hardening, with desktop buttons behind fresh polkit authentication. TASK-16362's C1/D improvements
are approved in principle. This proposal makes them part of pairing; C2 and the Go rewrite remain
deferred. Round 5 records **R1 resolved to A, home-network access** (founder seq499/502) and
**R2 APPROVED** (seq508, scope explained in seq506). **The custom-server option is removed** (founder seq526/529, 2026-11-17);
future outside-home access uses a fixed operator server (§6). Round 7 (TASK-16744), addressing TASK-16733 F1, supersedes Round 6's direct-DNS
recommendation: a matching-name public-root chain still passes its independent Let's Encrypt pool.
**Recommend B, a same-process strict home-only TLS CONNECT forwarder on pinned v1.78.0, for both
Linux and unsigned Mac; security/Product decision and implementation qualification pending.** R2 is not reopened.
The revised trust/invite and unsigned-Keychain details require renewed review. This
document itself authorizes no implementation or host operation.

## 1. Outcome, assumptions and current seams

Linux: **Turn on remote access** → fresh polkit system-password dialog → generated configuration,
local Headscale plus enrolled sidecar, verified release daemon, **Ready to invite**. The card explains
the existing keep-running choice and whether closing the desktop window will make the release
runtime unavailable. The main-window
**Home-network access** card opens settings/devices. **Add a device** → its own fresh polkit dialog
→ one copyable invite; explain first that Linux approval will also be required. Text first; QR later.
Mac offers **Run HappyRanch on this Mac** / **Connect to another computer**; opening/pasting an
invite enters the latter directly. It joins and pairs, waits for Linux approval, loads the selected
release runtime web UI, then shows **Connected**. **Approve device** on Linux raises another fresh
polkit dialog showing the actual redeemed device, target runtime, stable-ID fingerprint and comparison phrase also shown on Mac
(TASK-16298/seq419). **Turn off remote access** and **Revoke device** each raise fresh polkit dialogs
at those actions. No authentication grants later actions. Turn off removes this installation's
remote-access configuration and authority. Everyday desktop setup needs no JSON or terminal/sudo
commands. Outside-home access is a separate future journey using a fixed operator server (§6);
there is no custom-server option, URL field or key input in the apps.

Assumptions: one release owner per Linux installation; default Headscale/private DERP is Linux-local;
outside-home access will use an operator-provided fixed server, private testing now and production
later, subject to §6's open gates; a healthy packaged release daemon is available. The root helper derives the owner from authenticated bus credentials, never an
app-supplied username. It uses a validated existing root-owned release record, otherwise passwd-home + `.local/share/happyranch`. An arbitrary
custom XDG home cannot be accepted from the desktop environment; unsupported locations fail visibly
pending an approved protected binding flow. The founder-host default `.deb` layout is supported; DEV
8765/`~/.happyranch` is always excluded. Linger remains a separate opt-in; remote enable never sets
it.

THR-327 seq613: the packaged Linux daemon launcher creates its release data
folder (`$XDG_DATA_HOME/happyranch`, otherwise `~/.local/share/happyranch`, using
the existing absolute-XDG resolver) explicitly at 0700 before daemon exec can
publish lock/PID/discovery/readiness state. GUI-first startup applies the same
creation before desktop.lock. The final leaf is created and opened without
following a symlink, then verified by descriptor as a real directory owned by
the current UID; a new folder must be exactly 0700. Only that new verified
inode may have owner bits restored after umask filtering. Existing folders
are never chmod/chown repaired: a symlink, non-directory, wrong owner or
group/world-writable folder logs its path/problem and that it must be private
to the user. This adds no daemon-startup veto for an existing unsafe folder;
the existing Enable refusal remains `release_home_invalid`. Shared dev/home
creation, daemon file modes, systemd UMask and every helper-owner validation
predicate stay unchanged. Creating an XDG release folder does not extend the
protected-binding policy described above.

Enable admission/capture failures retain class/stage-only local syslog records.
The activation child's Enable-effect failure records only exception class,
innermost shipped source-relative file:line and the fixed enable step, including
through closed product wrappers. It never formats messages, arguments, locals,
user paths or credentials; delivery failure preserves the original closed exit
and uncertainty. The [runbook](../operations/remote-access-runbook.md#local-home-enable-failure-diagnosis)
names the helper journal and read command. Founder THR-327 seq643 assigns this
milestone's home-network acceptance to founder-run local Linux testing.

Read before drafting: TASK-16298 `design.md` rev2; TASK-16362 `architecture-review.md`; the [current
contract](../agent-guides/contracts/remote-access-and-remote-jobs.md) and
[RUNBOOK](../operations/remote-access-runbook.md). Baseline code facts:

- `installer.py` provisions operator JSON, then separately installs; it refuses
  existing destinations and has no replacement journal. `--layout deb` uses
  `/opt/happyranch-desktop/python/bin/python3 -I -m` and the bundled sidecar.
- `release_verifier.py` proves the random loopback listener and exact established
  connection belong to the packaged user unit before HTTP/bearer transmission.
  Keep this proof, root verifier, no-drop-in rule and all exclusion checks.
- `sidecar.go` requires `ExpectedPeer`; `engine_tsnet.go` checks hostname visibility
  at startup, then the proxy copies bytes over loopback TCP without `WhoIs`.
  `pairing.py` already has multi-device credentials and durable revocation.
- Mac `ClientBridge.swift` has managed join and pairing; `RemoteConnectionView`
  still has a separate Home-mode `HomeConnector`. `launchd_installer.py` is the
  separate macOS hosting lane, not Linux installation or Mac client pairing.

## 2. Threat-model delta and unchanged boundary

The founder's UID includes every agent. The desktop app is an untrusted requester. An agent can read
an invite displayed to it, imitate app IPC, change app-owned state, raise genuine polkit prompts,
disrupt its release daemon and modify its data. `allow_any=no` does not exclude agents in the active
desktop session (host fact in the brief, MEM-656; also TASK-16298 H1). Never rely on app identity,
an app confirmation, the daemon bearer or a recent administrative grant.

After this change, without fresh human authentication an agent cannot use the official channel to
install/retarget services, mint a new invite, approve a device, change pins/policy, undo revocation,
or copy the home tailnet state. It can steal/race a displayed invite, join its own node and cause a
pending approval/denial of service. That node gets no product access without approval of its actual
identity. Prompt spam and approving an unexpected prompt remain risks. Same-session screen/input
compromise, password capture, local admin rules granting automatic authorization and root compromise
exceed this boundary; fresh `auth_admin` does not attest a human gesture or defeat a compromised
desktop. Agents can already read the local daemon token and open an unofficial outbound tunnel. This
design protects official trust/policy/audit, not agent containment.

Headscale attests node identity; its compromise can forge that identity and may expose credentials.
Pins supplement the device credential; neither Headscale membership nor a hostname alone authorizes
traffic. A stolen unlocked paired Mac still has the shipped remote product privileges, including
driving agents. Connector and sidecar keep the same UID: compromise of either is within that trust
boundary; this proposal does not claim isolation between those two services.

Keep every existing connector/sidecar system hardening directive, including `NoNewPrivileges`, empty
capability/ambient sets, `ProtectSystem=strict`, `ProtectHome=yes`, private tmp/devices,
kernel/control-group protections, address-family restrictions, SUID/realtime/personality
restrictions, `MemoryDenyWriteExecute`, native syscall architecture and `UMask=0077`. Keep
notify/watchdog, restart-on-failure and target/bind/uphold relationships. The managed-identity
connector start-rate limit is the later exception described in
[the contract’s renewal unit upgrade section](../agent-guides/contracts/remote-access-and-remote-jobs.md#thr-327-renewal-unit-upgrade-task-19228);
sidecar and legacy/other connector limits stay unchanged. The verifier retains its
separate existing root capability budget and read-only home/proc proof. No user-mode substitution,
broad filesystem access, new connector LAN listener or weakening for Python is permitted. Item D (bundled
Python PT_GNU_STACK/MDWE) is delivered in **merged PR #1568, `1a8efca4`**; installation and packaged
acceptance remain separate. This spec neither designs nor edits that fix.

## 3. Fixed helper and fresh authorization

Proposed root mechanism: system-bus activated `ai.happyranch.RemoteAccess1`, fixed object
`/ai/happyranch/RemoteAccess1`, packaged helper at
`/opt/happyranch-desktop/bin/happyranch-remote-access-helper`. It runs no shell, imports no user
Python/config and accepts no command-line settings. Bus/polkit uses system Python `-I` and
already-required `python3-gi`/Gio; installer work uses only the fixed bundled Python `-I` entry, not
user site packages. A new dependency needs approval. D-Bus methods below have **zero input
arguments**; reject extra arguments, FDs, environment overrides and unknown methods before effects.
This is the polkit variant of the middle option; no second pkexec dialog. The no-input contract
is unchanged; outside-home key delivery remains an open founder/security gate (§6).

| Exact method / polkit action suffix | Effect |
| --- | --- |
| `Status()` / none | Bounded redacted phase, readiness, device summaries and selected device; no secrets or mutation. |
| `StatusV2()` / none | Additive bounded read-only status with separate setup/runtime/invite/address/policy/cleanup observations; Status v1 stays byte-compatible. |
| `Enable()` / `enable` | Default home-network mode: derive LAN address, install/configure local Headscale/private DERP, locally mint Linux join key, enroll and verify exact system units/READY. Already-ready uses the existing owned-unit restart/verification path and reconciles the scoped F3 UFW plan below. |
| `RefreshHomeAddress()` / `refresh-home-address` | Deferred to B13: no helper method exists and the CLI verb remains unavailable. Planned behavior: re-derive LAN address from kernel inventory, renew the leaf/SAN/config with the same private CA, verify readiness; no supplied address or new CA accepted. |
| `ReplaceFailed()` / `replace-failed` | Replace only a recognized failed/partial installation, with the verbatim loss-of-pairings warning and terminal confirmation in section 7 below. |
| `Disable()` / `turn-off` (polkit action `disable`) | Stop admission, revoke devices, stop/disable/unlink owned remote units, then clean owned activation config/state/credentials. Renewal units remain package payload; install-scope policy/provision survive. Never stop/delete the release daemon or package. |
| `IssueInvite()` / `create-invite` (polkit action `issue-invite`) | Public on the helper in I5a; Linux CLI delivered in I5b, bridge delivered in I5c (source only; I6 native/live acceptance pending). Fresh auth mints one-use join key and pairing secret, invalidates old invite, and delivers once in the sole terminal `(s)` JSON reply after B16 settlement within the original 120 seconds; no prior accepted reply. |
| `CancelInvite()` / `cancel-invite` | Public on the helper in I5a; Linux CLI delivered in I5b (no v1 bridge command). Fresh auth invalidates the current protected invite in the connector, then expires its key; report network cleanup separately. |
| `RecoverInvite()` / `recover-invite` | Public on the helper in I5a; Linux CLI delivered in I5b (no v1 bridge command). Fresh auth proves no usable key remains and consumes the old ceremony; only a later IssueInvite creates fresh authority. |
| `RetryPolicyRenewal()` / `retry-policy-renewal` **R2-approved** | Public on the helper in I5a; Linux CLI delivered in I5b (no v1 bridge command). Fresh auth retries only fixed shipped-policy renewal under the original 120 seconds and B16 intent/outcome, with final deadline validation after audit. No connector restart; all readiness gates must pass before access resumes. |
| `ApprovePending()` / `approve-pending` | Approve the sole redeemed pending identity captured before authentication, then atomically pin and issue its credential. |
| `RejectPending()` / `reject-pending` | Permanently reject that captured pending identity and consume its invite. |
| `NextDevice()` / none | Cycle a helper-owned per-bus-caller cursor over the protected device list; select nothing supplied by the caller. |
| `RevokeSelected()` / `revoke-selected` | Revoke only the selected protected record captured before authentication; close that device's live streams. |

I5d-2 wires clean-install Enable to the existing installer owners: omitted-input
provisioning, inert Headscale preparation, managed-fragment publication, protected
@2 invite-config publication, then forward activation. No operator JSON,
ExpectedPeer or enrollment-key input is needed. UID/B8 binding, pre-auth protected
inventory capture, under-lock CAS, fresh auth_admin/no-keep, B16 and the single
original 480-second budget cover the whole composition. Complete staging and the
proven post-Disable shape reuse their owners' exact rules; Ready stays inspect-only.
Native/package/cross-device acceptance remains separate from the temp-root tests.

I5e renews the helper mount namespace after every settled lifecycle operation
(Enable, ReplaceFailed, Disable, RetryPolicyRenewal, IssueInvite, CancelInvite
and RecoverInvite), including terminal refusal. Once the accepted/terminal reply
and B16 settlement attempts are done, admission is sealed; the helper waits for
inflight work and any StatusV2 worker, releases its well-known bus name, then
exits through the idle/close path. Racing calls receive an existing non-success
error. The next call auto-activates a fresh Type=dbus helper, so immediate Disable
can clean service directories created after Enable's helper started. Status and
device methods otherwise keep their 60-second idle behavior. Before lifecycle
capture/polkit/effects/B16 intent, `statvfs` must show each existing optional
Headscale/connector/sidecar state/runtime path writable in this namespace.
Absence is allowed; read-only or unavailable observations refuse `unavailable`
with zero authorization/effects/B16 intent and schedule exit. No service directory
pre-creation, unit hardening, auth or budget changes follow. Temporary-root and
private-bus checks do not claim installed namespace acceptance; I6a managed-M1
owns the clean Enable → READY → immediate Disable → complete Off confirmation.

I5b delivers the seven Linux CLI lifecycle/invite mappings above. Every mutation
requires a desktop and fresh native authorization. CLI timeouts are 482 seconds
for Enable/ReplaceFailed, 302 for Disable and 122 for invite/policy methods.
Create-invite requires `/dev/tty` before dispatch, validates the exact I4a terminal
frame and displays the invite only there; stdout JSON carries only closed nonsecret
action fields. Six methods acknowledge `accepted` before effects and send one
unicast LifecycleCompleted(s) receipt before helper release, correlated to the
original method/call serial and authenticated helper owner. CLI subscribes before
the single send and consumes only that original receipt inside its original
480s Enable/ReplaceFailed, 300s Disable or 120s invite/policy budget. Their sole
method reply remains admission-only accepted. The closed 512-byte signal carries
failed/completed and an optional failed category; Enable may also carry optional
closed `firewallWarning` display advice (see F2 below). Enable preserves B9's typed
preparation categories, including both dial spellings; Disable preserves its
partial_cleanup/failed/uncertain categories. The four CLI-only methods report
replaceFailedCompleted, cancelInviteCompleted, recoverInviteCompleted or
policyRenewalCompleted with exit 0; known failure reports lifecycleFailed with
failed/refused/partial_cleanup and exit 1. All retain effect unknown. Uncertain
work maps to outcomeUnknown for the four CLI-only methods; valid Enable/Disable
receipts preserve typed uncertainty, while a lost/invalid/late receipt or transport
loss retains outcomeUnknown/exit 1.
The caller fallback margin supplies no acceptance grace. No polling, replay,
new window command/event or cleanup authority follows. Private worker
outcomes/residue alone remain insufficient without the authenticated receipt.
The private exit-1 preparationFailure frame is bounded to
256 bytes. No paths, addresses or raw diagnostic text are exported.
CLI consumes once: enablePreparationFailed/category/unknown/nativeRecovery
returns exit 1; enableCompleted/unknown/refresh returns exit 0. Neither proves
effect, Ready, Off or cleanup. Invalid/late/wrong-correlation/unsupported or
absent frames retain outcomeUnknown. No lookup, replay, broadcast or redispatch.
Turn off uses fresh system authorization without an additional CLI prompt.

Invite capture without an activation receipt first reuses strict Enable
recognition, then refuses not_applicable before authorization/effects. Device
actions with unavailable connector status use the existing strict absence proof
only to refuse: proven Off is not_applicable; other unverified state remains
unverified. A display snapshot never admits a mutation. CLI revoke refuses a
fresh known Off status before selection/dispatch. These no-effect refusals use
the existing hostStateUnrecognized/category envelope and bilingual copy.
The existing pre-write/pre-invalidation/pre-mint issuer rate fence reports the
closed private result rate_limited, projected as invite_rate_limited/none with
English and zh-CN copy. No other child refusal is relabelled as this cause.
Status's short helper read timer uses timeout in that envelope, while helper
unavailable or D-Bus NoReply uses the closed status_unavailable category, with
English and zh-CN GUI parity. Neither names invite/policy expiry or changes the
unknown-effect rule for a dispatched mutation. The Linux client preserves
homeUnavailable and protocol errors; only explicit revocation responses prove
revoked, and identity_denied remains ambiguous accessUnavailable.
An IssueInvite refusal with no invite-state effect preserves an earlier issued
observation with its original age, expiry and generation guards. Fresh-helper
journal observation and dispatched uncertainty remain unchanged; no new
privileged read or invite delivery is added.

I5c / bridge slice 2b delivers only host.enable, host.turnOff and host.createInvite
in the Linux packaged window, using the same validator and fixed zero-input
Enable/Disable/IssueInvite methods with 482/302/122-second transport deadlines.
Enable/Disable acceptance leaves effect, host denial and host/network cleanup
unknown; the persistent dedicated bus and later Status observations own state.
Subscribed Main documents additionally receive a native operationId and one
host.enableFinished event. A plain EN/zh-CN preparation reason survives read
loss; a fresh post-terminal status read owns displayed state. Observation ends
with its original document/context/listener. Completed never confirms effects
or state; older peers retain status-unclear.
IssueInvite returns only closed nonsecret issuance/expiry metadata. The complete
invite stays in a native GTK sheet with explicit Copy, local absolute expiry and
m:ss countdown; expiry/close drops the text and best-effort clears only our
unchanged clipboard content. Closing does not cancel the invite; the Linux owner
still approves the device. Missing desktop refuses before dispatch. Loss, late
reply or cancellation never authorizes replay. Replacement, invite cancellation/
recovery and policy renewal remain CLI-only; refresh remains B13-deferred and
Mac host closed. Source delivery is not I6 native/live acceptance (NOT RUN).

The remaining engineering details are recorded in the contract's
[Home-network engineering settlements B1–B18](../agent-guides/contracts/remote-access-and-remote-jobs.md#home-network-engineering-settlements-b1-b18-thr-327-seq577);
these settlements preserve the approved behavior and UX and do not claim activation delivery.

I1 added StatusV2 to the then-existing Status, NextDevice and three
device-mutation methods; I5a's seven public lifecycle/invite additions are listed
below, with unchanged admission, Linux CLI consumers in I5b and bridge consumers
in I5c. RefreshHomeAddress remains absent and B13-deferred. StatusV2 retains the
same bus-owner gate and no polkit prompt for reads, returns the canonical B15
bounded JSON within a 2-second status budget, and leaves unsupported observation
dimensions unknown. Setup comes from a read-only protected HomeActivationStore snapshot through the fixed bundled Python
`-I` child; stopped connector alone means runtime unavailable. After replacement
closure, Status and StatusV2 can report Off only after the unchanged read-only
Enable capture proves clean, complete staged-inert or post-Disable inventory:
no activation/transaction/invite authority, matching owner/provenance and
quiescent services. No polkit, slot claim, repair or effect runs for this proof.
If the connector is unavailable and this proof fails, reads refuse
`host-state-unrecognized` (D-Bus suffix `host_state_unrecognized`). CLI and both
Host views map that refusal and the existing owner-binding refusals to
`hostStateUnrecognized`: “Remote access files or the release runtime owner could
not be verified. Check the local installation.” Main uses Status, whose proven
Off projection is known/offline with no devices, so the existing Turn on action
is offered. StatusV2 reports setup off/runtime unavailable; a recognized live
receipt still retains its own setup observation when the connector is down.
The refusal keeps `hostStateUnrecognized` and optionally carries only the closed
category residue, owner, provenance, custody, inventory, quiescence, timeout
or unknown through child capture, HomeLifecycleClient, D-Bus, native protocol,
CLI JSON, Main relay and localized EN/zh-CN copy. Legacy owner-binding errors
map to owner; absent/invalid categories map to unknown, with no host metadata.
Status v1 successful serialization shape, status deadlines and mutation admission
stay unchanged.

THR-327 restoration permits only H1 bounded sidecar health diagnostics
(`health.json` / `.health-N`) under inactive/quiescent absence proof; sockets,
other runtime/state and unsafe custody still refuse. Staged-inert/post-Disable
capture and inert pre-renewal publication defer ONLY expiry, preserving exact
shipped policy and provenance. The existing `headscale_start_verify` effect,
AFTER its durable intent and under the SAME flock, calls fixed fresh-auth renewal
and then requires currentness BEFORE its reload and before ANY unit enable/start.
The live-config owner accepts only the renewal's exact policy-digest provenance
update; other bytes/custody stay bound. All later checks/admission/runtime remain
strict. Keep the later managed-services renewal and timer-before-target order.
The EFFECTS tuple, journal names/format/shape and old-intent I3a recovery semantics
remain unchanged; recovery never renews. Ready Enable retains its existing restart/verification path plus the scoped F3 UFW reconciliation below.
An absent-journal founder-shaped retained staging host with empty state/verifier
directories and the expected account reports Off and proceeds on Turn on without
other host actions. This is synthetic fixture coverage, with installed acceptance
still separate. Health files never establish Ready: the live SO_PEERCRED
observation and current activation/node binding remain mandatory.

At I1, the B16 writer bracketed the three device effects
with durable intent and fixed outcome/uncertainty records; the lifecycle/invite
audit composition follows below.
The fixed helper source contract already reflects B8/B9's optional compatibility UID
pin and installed root Type=dbus packaging. I3a adds the lifecycle/auth/audit
composition below; I5a exposes Enable, ReplaceFailed, Disable, IssueInvite,
CancelInvite, RecoverInvite and RetryPolicyRenewal on the public helper.
Linux CLI consumers are delivered in I5b; I5c wires only the existing bridge
enable/turnOff/createInvite commands. RefreshHomeAddress has no helper method
and remains deferred to B13. I5b switches CLI consumers; I5c adds exactly those three existing v1 bridge mappings.

I3a wires the serving supervisor's optional B10 reserve/invalidate/revoke-all
capability to the existing InviteControl and its one current pinned pairing
manager. It privately mounts Enable, ReplaceFailed and Disable through B9 capture,
real polkit CheckAuthorization and the fixed bundled-Python activation/installer
child. Every call captures and revalidates the kernel sender UID, B8 owner,
protected activation generation/operation (including absence), and applicable
Enable-config or B17 inventory/journal generation. B6 also checks the operation
under its receipt lock. B16 durable intents precede effects, with succeeded,
refused or uncertain settlement. Admission returns before the worker; the single
slot remains held through all effects under the original 480/300-second budget.
The already-designed three action definitions retain auth_admin/no-keep defaults.
I5a includes all three in public dispatch/introspection; Linux CLI consumers are delivered in I5b; bridge
consumers are delivered in I5c, with native/live acceptance still pending.

I3b privately mounts CancelInvite and RecoverInvite through that same helper
capture/auth/worker slot and fixed bundled child, with their exact auth_admin,
no-keep actions. Their original 120-second monotonic budget includes capture,
authentication, journal, B10 invalidation and key cleanup. The child uses the
existing ready/commit framing on its pipes to call the Gio parent's live UID,
B8 owner and protected generation/operation revalidation hook; no new connector
verb, listener, pairing owner, store or minter is introduced. It derives the
issuer/config/CA and connector generation from protected current state, selects
the current journal entry without any app ID, and holds the existing invite-user
lock. Journal transactions end before IPC and minter calls. B16 intents precede
effects. B11 tuples pass through unchanged: partial_cleanup/cancelled/uncertain
means invalidation was acknowledged but key cleanup was not proved. Lost or late
replies stay uncertain. StatusV2 uses only the existing invite/cleanup fields,
bound to the authenticated UID and activation G/O and aged by B15; restart or
binding drift leaves them unknown. I5a exposes these methods on the public
helper; Linux CLI wiring is delivered in I5b (no v1 bridge commands).

I4a privately mounts zero-input IssueInvite with the exact fresh issue-invite
polkit action (auth_admin, never _keep), B8 UID/owner and protected config/CA/
generation capture, the same helper slot and existing issuer/minter/serving B10
owner. It preserves one attempted issue per minute and invalidates the old
ceremony before mint. Connector unavailable refuses before mint; lost reserve
replies leave the existing journal uncertain for B11. Only IssueInvite waits
for the worker and B16 settlement before its single reply; the original
120-second deadline includes capture/auth and is checked again after audit.
The slot remains held through reply; other lifecycle calls keep accepted first.
Terminal success, both child frame and compact D-Bus `(s)` JSON, is exactly
`{"result":"complete","invite":"<text>","expires_at":<int>}` with the exact
IssuedInvite text/Unix expiry. Child ready/commit frames stay unchanged; only
this child exchange uses read_frame(limit=6144), with default4096 unchanged
elsewhere. Parent refuses duplicate/extra/missing keys, invalid single
hrinvite2: prefix/base64url alphabet, >5500 text bytes and noninteger/Boolean
or out-of-range expiry (must be in (now, now+300]). Oversize is never truncated.
Lost/late/malformed/expired post-dispatch results and lost delivery are uncertain;
no second reply, fetch, polling or alternate delivery channel exists. Text is
absent from StatusV2, B16, issuer journal, logs/errors and stderr (DEVNULL);
StatusV2 carries only existing phase/deadline, and B10 only digest/deadline.
The pinned wire-size/TTL limits remain unchanged. B14 I4b-1 delivers the fixed
renewal transition, executable, inert package units and connector pickup.
I4b-2 delivers P3/P5 Enable renewal/timer wiring, Disable stop/disable/unlink and
renewal-journal recovery, private Retry and existing-field policy/timer status.
Enable validates both renewal units' loaded package identity and protected exact
renderer bytes before any mutating manager call. Disable accumulates renewal
residue while still attempting the owned remote and receipt-bound Headscale
safety stops, including after failed revoke. Refused journal recovery retains
the journal; any renewal residue or stop/quiescence uncertainty prevents B6.
Failed revoke alone does not: after all remote and renewal stop/quiescence
proofs, timer-link absence and journal recovery succeed, B6 still runs, deleting
the Headscale DB live members while retaining connector trust/sidecar state
and evidence requiring revoke success. Disable reports `partial_cleanup` naming
that un-revoked trust state. Complete requires revoke success AND all proofs
AND B6 success AND no residue (I3a R4/R7b, I3c C3/C5).
All ownership and quiescence gates remain required.
Ready Enable also reconciles the scoped F3 UFW plan below; fresh-auth activation recovery remains
verify/publication-only without renewal or timer replay. I5a opens the helper
public gate; Linux CLI consumers are delivered in I5b and bridge consumers are delivered in I5c (source only; I6 pending). B18 artifact-changing
upgrade repair remains separate.

Disable holds that slot throughout → existing revoke-all (burn ceremony, durable
denies and stream closure) → stop/disable recognized connector/sidecar/verifier
and stop the receipt-bound local Headscale, with native quiescence proof → B6
with authenticated-store expectations. B6 owns the exact Headscale-link disable.
Stop still runs if revocation fails; uncertain stop prevents B6. **R7 corrects R4:**
I3c carries the helper's trusted revoke-success fact into the existing B6 owner.
Only successful revoke permits fixed connector trust and persisted sidecar state
retirement. Complete still requires revoke success, confirmed quiescence, B6
success and all admitted residue retired. Failed revoke reports partial_cleanup
and names retained un-revoked trust/sidecar state. CA staging and B7/B11/minter
evidence follow I3c's fixed custody and proof/audit retirement; any remainder is
incomplete. No new executor, purge reuse or B17 reuse for Disable. One changed-generation
re-read/retry is allowed within the original 300-second budget. Successful revoke
leaves no admissible ceremony/device after restart/rotation before stop.

Action IDs are exactly `ai.happyranch.remote-access.<suffix>` for authenticated methods. Defaults:
`allow_any=no`, `allow_inactive=no`, `allow_active=auth_admin`; never `_keep`, wildcard rules,
retained capabilities or general systemctl grants. Root calls `CheckAuthorization` on the actual
unique system-bus sender, with interaction allowed only for an explicit mutation. No bearer-based
shortcut. Enable, invite issuance, redeemed-device approval, turn off and per-device revoke each
authenticate at their own action; replacement, rejection, address refresh and policy Retry also use
their listed fresh-auth actions.
Policy/message files are root-owned package files. The helper supplies trusted
`polkit.message` details: verb, derived/protected control URL/release owner, replacement effects, or actual
device label + stable-ID fingerprint + comparison phrase. These details are supported by the [polkit
authority
API](https://www.freedesktop.org/software/polkit/docs/latest/eggdbus-interface-org.freedesktop.PolicyKit1.Authority.html).
Labels are bounded escaped text, never markup or authority.

Capture operation, caller, selected record and store generation before the dialog; release locks
while waiting, then revalidate all of them and current ownership under the mutation lock.
Changed/expired/disconnected requests refuse; authorization is spent on that operation only. An
agent cannot swap the target after the dialog. One pending pairing globally, at most 32 paired
devices, one mutation/dialog in flight, no queued surprise prompts, 120-second dialog deadline.
Issue is rate-limited to one/minute; failures return fixed categories. All calls must match the
protected release UID (fresh Enable derives it from the bus); status/cursor grants no trust
mutation. The helper communicates with a fixed private connector control socket as root; the
connector checks kernel peer credentials before any trust mutation.

## 4. Generated inventory and custody

TASK-19401 daemon outage retry: only the managed Linux identity connector's
`StartLimitIntervalSec` changes from 300 to 0. Restart-on-failure, one-second
restart delay, each 120-second start attempt, the watchdog, hardening and full
pre-listener readiness remain. Daemon absence is never readiness or permission
to serve; after restoration the connector retries without a button and its
unchanged Upholds/BindsTo sidecar dependency recovers. Allow the existing two
startup budgets plus polling/delay (250 seconds) with all other gates healthy;
installed-systemd validation is separate.

For an already-READY d05afaac/98a87688 host, package upgrade alone retains the
old connector unit and old failure behaviour. The new package's configure-only
postinst reloads the system manager after replacing packaged renewal fragments
(also when the old package had no postinst), without touching user managers,
unit activation or state. It tolerates reload failure; strict
`NeedDaemonReload=no` admission is unchanged. After a successful package reload,
fresh-auth Turn on again republishes only its exact previous rendering through
the existing pinned-parent unit publisher under ready receipt/G/O/custody/deadline checks, reloads, then
uses the existing reset/start/joined path. No receipt or transaction rewrite,
mint, replay or new recovery admission. A renewal-unit property mismatch still
refuses, now using syslog to emit only the fixed unit kind and property name.
TASK-19410 proved package replacement left `NeedDaemonReload=yes`; no helper-side
pre-inventory reload or stale-property admission is added. Acceptance of the
postinst's installed upgrade behavior remains in the manager's VM lane.

All persistent writes use literal no-follow parents, bounded regular single-link files, exclusive
temporary writes, fsync + descriptor-relative rename. Existing unsafe metadata refuses, never
chmod/chown a foreign tree into acceptance. `root:hr` means root:`happyranch`; `hr:hr` means
`happyranch:happyranch`.

On enable, authenticated repair/token rotation and failed-install replacement, the opened fixed
release-home token inode must have `st_uid` equal to the authenticated/recorded release UID. Require
exact 0600 mode, regular type, one link, nonempty UTF-8 and a bounded read (at most 64 KiB).
Traverse every parent and the leaf descriptor-relatively with no-follow; compare named/opened
device + inode identity and recheck named/opened identity, owner, mode, link count, size, mtime and
ctime after reading. Copy only bytes read from that same validated FD, never reopen for copying;
foreign ownership or changed/unsafe metadata refuses before publication or service effects, without
copying its bytes. Slice 3b must change `ManagedInstaller._input()` → `_read_file()` in
`runtime/remote_access/installer.py`: `_input()` currently leaves `owner=None`, so fixed paths and
the existing 0600 check do not establish source UID ownership. Preserve TASK-16298 rev2's H2 rule.

| Fixed object | Owner / mode; generation and writer |
| --- | --- |
| `/opt/happyranch-desktop` payload, helper, system-bus and polkit definitions in `/usr/share` | root:root, dirs/executables 0755, definitions 0644; dpkg only, never copied from the app or a checkout. |
| `/etc/happyranch/{connector,sidecar,policy}.json` | root:hr 0640 under root:root 0755; helper generates IDs, socket settings, derived local control URL and bundled deny-by-default route artifact. No ExpectedPeer in the new Linux profile. |
| `/etc/happyranch/{provision,install-transaction}.json` | root:root 0600; helper records layout/version/digests/phase, not secrets. No arbitrary path manifest. |
| `/etc/happyranch/release-daemon.json` | root:root 0644; exact existing verifier fields, derived owner/home and packaged fragment. |
| `/etc/happyranch/daemon.token` | hr:hr 0600; helper copies only the validated fixed release-home token; existing file provider, no change to daemon bearer custody. |
| `/etc/happyranch/enrollment.key`, reserved `/etc/credstore/happyranch-tsnet-enrollment` link | root:root 0600 source and existing exact-target link; helper stages home-only key, existing LoadCredential handoff deletes source/link after durable enrollment/READY. B7 rollover also retires the abandoned generation's source/link through the same installer primitive before prepared publication; section 7 defines refusal/retry semantics. |
| `/etc/happyranch/invite-issuer.json` | root:root 0600; closed `home-invite-config@2`: format, release_uid, home_id, control_plane_id, connector_id, ca_der_sha256, dial_ip, control_url. Stable identity only; generation and endpoint come from B1/B2 receipt. Initial protected-source publication belongs to `ManagedInstaller.publish_invite_config()`; exact-byte reuse only, no rewrite. No API/admin credential, key pool or standing remote mint capability. |
| `/etc/happyranch/control-ca.pem`, `/etc/happyranch/empty-ca-directory/` | Home mode only: root:hr 0640 PEM bundle containing ONLY this home's CA; root:root 0755 empty directory prevents Go's default CA-directory loading. Root helper writes/checks these; hr cannot replace them. No system trust-store mutation. |
| `/etc/happyranch/headscale/`, `/var/lib/happyranch-headscale`, `/run/happyranch-headscale` | Protected local config, TLS and DB/Noise/DERP state/socket; dedicated locked `happyranch-headscale` user, dirs 0700, files 0600. Root retains CA signing key separately, server gets only its leaf key; socket owner is this service, root helper is its sole administrative client, no hr/founder access. |
| Existing five connector/sidecar/target/verifier unit files in `/etc/systemd/system` | root:root 0644; shared renderer, exact unit names, helper only. Add separate bus-helper unit/definition with bounded lifecycle; no authority added to connector units. |
| `/var/lib/happyranch-connector/trust-state.json`, `.anchor`, `.lock` | hr:hr 0600, parent explicit 0700; extend existing transactional trust store with pins and pending invitation digests. Connector writes only, helper invokes fixed operations over private control IPC. |
| `/var/lib/happyranch-tsnet-sidecar` | hr:hr 0700; persistent non-ephemeral tsnet identity and consumed marker, existing enrollment rules. |
| `/var/lib/happyranch-remote-access` | root:root 0700, files 0600; helper's issuer request IDs, expiry/cleanup state and transaction recovery records; no raw invite at rest. |
| `/run/happyranch-connector/{ingress,control}.sock` | hr:hr 0600, parent hr:hr 0700; ingress verifies hr peer, control verifies root peer. Founder/agents have no access. |
| `/run/happyranch-release-verifier/control.sock` | existing root:hr 0660, root-owned parent; resolve/confirm protocol unchanged. |
| Audit and public status | protected root/hr journals contain allowlisted event codes, internal operation/device IDs and outcomes; existing public health file remains secret-free. |

Pins live **inside the anchored trust-state transaction**, not a user-owned JSON list or second
independently updated file: control-plane ID + `WhoIs.Node.StableID`, internal device ID, credential
digest, epoch, expiry and revoked flag. Only root and hr can modify it; the founder UID cannot.
Explicit 0700 state/runtime modes strengthen the existing renderer defaults; preserve all other
hardening. **Route-policy renewal** preserves freshness: helper wraps the package's exact shipped
route-policy artifact at enable/upgrade; the fixed root-owned route-policy renewal timer/action
may re-stamp only that identical, still-shipped policy before its existing max-age boundary. Its
authority is the R2-approved fixed root timer, with no inputs or IPC arguments; it cannot revive
withdrawn policy or widen routes. Failure expires closed and stops remote access. The Linux card
shows **“Remote access paused: policy could not be renewed”** with **Retry**; retry rechecks renewal
and readiness, reopening only after successful fixed-action renewal and all existing gates pass.
No key is renewed automatically. Founder seq508 approved this route-policy renewal mechanism.
Automatic renewal cannot revive expiry; only a fresh-auth
fixed-action retry may re-stamp an expired identical shipped artifact. Linux I4b-1
ships the fixed executable and both unit files at `/usr/lib/systemd/system/`
without enabling them. Pickup precedes readiness inside the existing connector
lifecycle lock and updates the single consumer without restart/stream closure;
cached expiry still follows the existing readiness-loss teardown. P3 Enable/Disable
wiring and P5 Retry remain I4b-2. Turn off stops/disables/unlinks the units while
their package files remain installed. A changed shipped artifact needs B18
repair/upgrade rewrapping before renewal can succeed.

## 5. Identity transport, pairing and every-request checks

For the new Linux managed profile only, replace TCP sidecar ingress with the protected Unix socket.
Accept → `LocalClient.WhoIs(remote IP:port)` on the tsnet connection → require a nonempty
`Node.StableID` → connect Unix socket and prepend a versioned, length-bounded (≤1 KiB) identity
frame → unchanged HTTP byte stream. The connector requires kernel peer UID hr, exactly one frame, no
duplicates, then removes/ignores all client identity headers. Identity cannot come from HTTP. One
remote connection maps to one local connection; never pool across nodes. Unknown version, missing
identity, WhoIs failure or malformed frame closes it. Pinned tsnet v1.78.0 exposes
`WhoIsResponse.Node` and `tailcfg.Node.StableID`; verify real Headscale v0.25.1 behavior in M2. No
hostname/IP fallback if absent.

Slice 4a-0 extends this private frame for display only: HRID v2 carries uint16
big-endian StableID length + UTF-8 StableID, then uint16 label length + UTF-8 label,
after `HRID` and byte `2`. The total remains at most 1024 bytes; v1 remains accepted
with no recorded label. Select the name from the same WhoIs response, preferring
`Node.Hostinfo.Hostname`, then `Node.ComputedName`, else empty. Invalid UTF-8 or a
frame exceeding the bound closes. Python strips control/format/bidi characters,
trims and caps the display name at 96 characters before recording/projecting it.
Optional string `device_label` on pending/delivery/device records needs no snapshot
version change or migration. Helper Status falls back to `stable_node_id[:96]`
only when the name is empty. The name never participates in authorization,
pinning, matching, revocation or stream registration; fingerprint and phrase
remain the comparison authority. Real Headscale hostname behavior remains an
acceptance check, not a claim from synthetic tests.

The helper projection further bounds recorded names by their JSON-escaped byte
cost so the complete snapshot plus duplicate mutation target fits the unchanged
32768-byte helper frame at capacity. This projection may shorten a recorded
96-character name; persistence and the exact legacy fallback stay unchanged.
The per-snapshot budget arithmetic is maintained in the remote-access contract.

`ExpectedPeer` visibility is removed from **Linux new-profile** enrollment and restart readiness,
including health diagnostics' single-expected-peer assumption. Keep durable state/consumed marker,
private DERP/control, connector liveness, listener and watchdog gates. An empty paired set is
healthy and grants nothing. The Mac may still use the home endpoint in its invite to dial; that is
not pre-knowing the Mac's hostname. Legacy Mac hosting/DIY profile behavior stays versioned and
unchanged until explicitly migrated; no global lenient parser.

```mermaid
sequenceDiagram
    participant L as Linux desktop (untrusted UID)
    participant H as Root helper + polkit
    participant C as Connector + sidecar (hr)
    participant I as Local Headscale
    participant M as Mac client
    L->>H: IssueInvite() + fresh admin authentication
    H->>C: Check availability; reserve undeliverable digest to invalidate old invite
    H->>I: Local mint, never remote admin key
    H->>C: Persist invite digest (private root-peer IPC)
    H->>H: Persist B16 outcome; recheck original 120 s deadline
    H-->>L: Sole terminal reply: complete + invite + expires_at (no prior accepted)
    L-->>M: User transfers invite
    M->>I: Join Headscale with one-use pre-auth key
    M->>C: POST /pair/invite (secret); sidecar attaches WhoIs identity
    C-->>M: Pending + private completion handle + comparison phrase
    L->>H: ApprovePending()
    H->>H: Fresh polkit dialog shows actual node + matching phrase
    H->>C: Approve exact captured pending generation/node
    C-->>M: Credential after durable pin/epoch commit
    M->>C: Credential + ordinary request (same pinned node)
    C-->>M: Authorized release runtime web UI loads; Mac shows Connected
```

Redeem atomically consumes the invite into `pending-approval`, binds the actual WhoIs identity, and
creates a random completion handle; it does **not** authorize product routes. Linux shows the
escaped WhoIs device label, control-plane label, stable-ID fingerprint and comparison phrase also
shown on Mac. Use the bounded OS-derived device label for this milestone; editable labels are
explicitly deferred. Name alone is insufficient. Approval/rejection operates only on that immutable pending
tuple. Approval commits pin + credential digest + monotonic epoch before delivery; completion is
one-use, same-node and handle-bound. Lost credential delivery after successful network join consumes
the invite: Mac explains **“Authorization did not finish. Create a new invite on Linux”**. The user
re-issues an invite and repeats Linux approval; restart burns unfinished pending/delivery state.
Reuse a still-valid, same-node persisted network membership with empty-key reopen only for joining,
never to recover the spent invite or old grant. In home mode the helper removes abandoned partial
nodes locally. Each new Add a device still requires a fresh key, even if valid same-node membership is
reused; do not consume that new key unnecessarily. A spent key is never a retry credential.
Race/replay/expiry/store-corruption cases deny, never restore an invitation.

Every HTTP request, WebSocket upgrade and SSE admission loads current anchored trust, verifies the
per-device credential digest/epoch/expiry/revocation **and** exact control-plane/stable-node pin;
then applies unchanged route/readiness and release-verifier gates. No other paired device is
displaced. Stream registrations carry internal device ID and epoch; per-device revoke persists
invalidation and closes that device's existing streams before reporting success, preserving the
existing incomplete-revocation failure semantics. Other devices stay connected. Ordinary node-key
rotation keeps StableID; re-enrollment/new StableID needs fresh approval. Never auto-repin from
matching hostname, address or old credential. Existing unpinned managed devices become
`re-pair-required`, never grandfathered.

## 6. Invite, selected bootstrap and secret handling

**Round-7 approved home wire contract (PR #1577):** one text string, `hrinvite2:` +
base64url strict JSON with `v=2`, ≤4096 decoded / 5500 encoded bytes including
prefix; refuse oversize, never truncate. Exact fields: `v`, `mode`, `control_url`, `control_plane_id`,
`control_tls`, `home_endpoint`, `home_id`, `connector_id`, `invite_id` (128 random bits), `join_key`,
`pair_secret` (256 random bits), `issued_at`, `expires_at`. `control_tls` is a closed union: system
HTTPS trust, or local CA DER + SHA-256 fingerprint **and `dial_ip`**, the derived RFC1918 IPv4.
The remaining field names, size/TTL limits and one-invite ceremony stay as stated here;
`home_endpoint` retains its runtime target meaning and is not overloaded as a control dial address.
Home `control_url` must be exactly `https://control.happyranch.invalid:8443` (optional trailing `/`
normalizes away); reject other names, IP URLs, extra trust fields and mismatched mode/trust.
Version 1 home/IP profiles require a new invite and approval; never silently reinterpret old
protected URL bindings or consumed markers. Custom-server intake does not exist and is not planned;
a future fixed-server invite mode needs its own security-reviewed slice (§6 below). No private key/admin token enters an invite.
Versioned mode/trust metadata supports later Services-issued keys in the same envelope/ceremony;
unknown versions/fields refuse. Text transfer is sufficient; QR display is deferred if this larger
certificate-bearing envelope cannot fit one symbol. No redirect/lookup service or separate pairing
code. The Mac validates HTTPS URL, no userinfo/query/fragment/path except `/`, fixed tailnet runtime
port 443, times and exact schema before connecting; shows the control host and release runtime.
That fixed tailnet runtime port is distinct from the control server's HTTPS port: derived LAN 8443
in home mode. Outside-home server configuration remains subject to the open gates below.
Trusting an invite is trusting its sender for the target CA/home, not independent host attestation.

### v2 wire literals (pinned)

These representations are normative. Slice 3c's issuer MUST conform; issuer/parser
round-trip fixtures are the shared contract.

| # | Pinned representation |
| --- | --- |
| 1 | Exactly one `hrinvite2:` prefix plus canonical **unpadded** RFC4648 base64url (`A-Z a-z 0-9 - _`). Refuse `=`, whitespace, `+`, `/` and noncanonical trailing bits. The same encoding applies to inner `ca_der`, `invite_id` and `pair_secret`. |
| 2 | UTF-8 strict JSON object with exactly the 13 top-level keys listed above; field order is irrelevant. Refuse duplicate, extra or missing keys and trailing data. `v` is integer `2`; identity, endpoint and secret values are strings; times are integers. No floats, booleans or null. |
| 3 | `mode="home"`; `control_tls` has exactly `{"kind":"local_ca","ca_der":…,"sha256":…,"dial_ip":…}`. `{"kind":"system"}` is reserved and always refused in home mode / while R3 is disabled. No extra members. |
| 4 | `dial_ip` is canonical dotted-decimal RFC1918 IPv4: no leading zeros, port, IPv6, mapped addresses, CGNAT or loopback. |
| 5 | `sha256` is 64 lowercase hex characters: SHA-256 of the exact decoded CA DER, without prefix or separators. Exactly one CA DER with CA:TRUE. |
| 6 | `home_id`, `control_plane_id` and `connector_id` are 1..128 ASCII `[A-Za-z0-9_-]`. |
| 7 | `invite_id` decodes to exactly 16 bytes; `pair_secret` to exactly 32 bytes. |
| 8 | `issued_at` and `expires_at` are nonnegative Int64 Unix seconds, with zero future skew (`issued_at <= now < expires_at`) and `0 < expires_at - issued_at <= 300`. The issuer floors fractional deadlines so no response can extend the envelope deadline. |
| 9 | The connector stores SHA-256 of the exact `pair_secret` base64url UTF-8 text. The Mac sends that text raw. |
| 10 | `join_key` is exactly 48 lowercase hex characters. |
| 11 | `home_endpoint` is canonical ASCII tailnet `host:443`, without scheme, userinfo, path, query, fragment or percent-escape. |
| 12 | Total size is ≤4096 decoded JSON / ≤5500 encoded bytes including the prefix. There is no separate DER allowance. Never truncate. |
| 13 | `control_url` is exactly `https://control.happyranch.invalid:8443`, with an optional single trailing `/`. |

Row 3's R3 reference is historical: R3/own-server is superseded and removed.
`{"kind":"system"}` stays refused by the shipped home-only parsers. Any future
fixed-server trust mode needs separate security review; this gate remains **OPEN,
not decided**, consistent with G1–G3 below.

Known limitation: metadata-only recovery cannot observe invalidation of an
unredeemed old invite. This is recorded, not solved by the pinned representations.

**Default: home-network access (R1 resolved, seq499/501).** Enable installs bundled Headscale,
creates the private control plane and locally mints the Linux node's ≤5-minute, single-use,
non-ephemeral key. Add a device mints an equivalent Mac key through the root helper's fixed local
admin socket operation; no Headscale admin/API key is created or stored anywhere in either app or on Linux. Headscale's
protected DB/socket are local administrative authority; root compromise controls this tailnet.
The helper creates the pairing secret; connector stores only its digest, then the freshly
polkit-authenticated initiating caller receives the complete invite once. No unauthenticated mint.

Address discovery is carried by `control_tls.dial_ip`: helper reads kernel interfaces/routes and
derives the single usable default-route RFC1918 LAN IPv4. The kernel inventory
preserves table identity: an exactly four-byte `RTA_TABLE` overrides `rtm_table`;
when absent, the header applies. Validate every default and ECMP hop with known
interface identity first; malformed/duplicate/truncated attributes or unknown
indices refuse `inventory` even on otherwise ignorable routes. Ignore only
non-main defaults with no preferred source. Source-less main gives `inventory`;
only ignored routes left gives `no_address`. Sourced defaults in every table
remain candidates: two assigned RFC1918 sources, even identical, give
`multiple_addresses`; non-RFC1918/IPv6 sources give `dial_ip`. Down/loopback,
source-specific and nexthop refusals remain unchanged. No metric/weight ranking,
device-name/type filtering or fallback to arbitrary interface addresses.
Linux status and Mac's trust display show
that address alongside the home identity. Multiple equally eligible/no LAN addresses refuse;
no app-selected address, router DNS, mDNS daemon or Internet lookup is needed to obtain it.
The URL's DNS name is fixed; the per-home CA fingerprint and protected home/control-plane IDs,
not the shared alias or LAN address, distinguish homes. Never collapse pins across homes.

**Local trust, Round 7: strict home-only authentication required.** TASK-16720's actual v1.78.0 `/key` and DERP probes
accepted wrong-IP-SAN and no-SAN home leaves. In
[`net/tlsdial/tlsdial.go:69,91,142–175`](https://github.com/tailscale/tailscale/blob/v1.78.0/net/tlsdial/tlsdial.go#L142),
verification uses `ConnectionState.ServerName`; Go suppresses IP literals in SNI, leaving it empty.
Both OS and baked-in Let's Encrypt (LE) checks then omit hostname verification. Consequently an
LE-chained certificate for **any** domain can authenticate an IP-URL bootstrap even with a scoped
OS bundle. A LAN attacker can supply its own Noise key and receive enrollment secrets. Noise
cannot repair an attacker-authenticated bootstrap. The former “IP is not publicly certifiable”
argument is withdrawn. Mac PR [#1575](https://github.com/t-benze/happyranch/pull/1575), inspected at
branch `task/TASK-16696` at `771286347009a77045f78c04fb45df24a308cb3b`, adds `SetFallbackRoots`/`AddCertWithConstraint`: its home-root constraint
does not constrain the independent LE pool. Hold trust acceptance for 1b and 5b pending this decision.

**Decision comparison.** Effort is engineering days, including focused tests/doc parity, excluding
review queues and native/packaged acceptance. All viable choices retain separate Linux users,
every §2 hardening directive and MDWE, device pins/bearers/approval and private-only DERP. No
fork, dependency change, root helper for Mac or additional infrastructure is authorized here.

| Option | Mechanism, Linux / unsigned Mac feasibility | LAN attacker, Noise and DERP outcome | Effort and slices 1b / 3b / 3c / 5 |
| --- | --- | --- | --- |
| **A — conditional alternative, explicit founder rescope required** | Fixed `control.happyranch.invalid` DNS-SAN home leaf and process-local resolver to admitted `dial_ip`, with Linux scoped roots / Mac forced fallback roots. No root/signing/host trust-store installation on Mac. | Refuses unrelated names, but **accepts a valid matching-name public/LE chain independently of the home CA on both OSes**. Reserved-name issuance restrictions do not establish home-only authentication. This fails the unchanged requirement for `/key` and DERP; never select it without an explicit founder reduction of that requirement. | **5–8 days** across 1b/3b/3c/5, plus explicit security rescope and renewed review; lower relay/lifecycle cost purchases a weaker trust property. No rescope is supplied here. |
| **B — RECOMMEND: strict in-process TLS CONNECT forwarder** | Each dedicated Go process binds `127.0.0.1:0` before tsnet. Existing HTTP proxy paths route control and DERP to it; stdlib TLS dials only admitted `dial_ip:8443`, with explicit **home-only** `RootCAs`, fixed DNS-name/validity verification. It re-presents a leaf from a per-process ephemeral CA held only in memory and in tsnet's isolated fallback pool. Same APIs on Linux and unsigned Mac; no root/signing/trust-store install on Mac. | **Remote public chains for any name, including the matching reserved name, refuse before `/key`, registration or DERP.** Wrong/no/IP-only SAN, forged and expired chains refuse. Noise still authenticates the bootstrapped key. The unchanged baked pool sees only the local re-presented leaf; it cannot authorize the remote connection. LAN interception gives denial/metadata risk. Local forwarder/process compromise remains within the existing host trust boundary. | **8–12 days:** 1b strict verifier + CONNECT/duplex/lifecycle/startup proof (2–3); 3b protected home CA/DNS leaf/private map + refresh (1–2); 3c v2 bindings (0.5–1); 5 dedicated-process proxy/root lifecycle, recovery and native qualification (3–5). Linux transport feasibility executed below; native Darwin/full tsnet/packaged acceptance NOT RUN. No dependency, upgrade, fork or new service/infrastructure. |
| **C — upstream upgrade, founder-reserved** | Read-only survey below finds no IP-SAN fix in sampled later releases. v1.104.7 supplies `ts_omit_bakedroots`; an approved upgrade/build change must also enforce identity and home-only roots on both OSes. | An upgrade alone fails. Omitted baked roots plus a proven home-only/name verifier could meet the refusal set without B's relay; must qualify `/key` and DERP, never infer security from the version. | **1–2 days investigation; 5–10+ days qualification** across 1b/3b/3c/5; new module/toolchain/transitive/build inventory and compatibility cost. Founder approval required; no manifest or build flag changes here. |
| **D — IP URL with scoped roots: REJECT** | Keep the old direct IP URL and Linux scoped OS / Mac constrained fallback pool. | LE for an unrelated name can authenticate bootstrap and DERP; Linux also accepts wrong/no-SAN home leaves. Attacker supplies both `/key` and its Noise endpoint; later Noise authentication does not repair it. | No acceptable delivery; 1b/5 remain held. Unchanged hardening cannot compensate. |
| **E — fork or vendored verifier patch, founder-reserved** | Patch pinned tlsdial to remove baked fallback and bind the expected name for both clients, with upstream/security maintenance owned explicitly. | Could meet the property only after real bootstrap/DERP and both-platform qualification; source changes alone are not acceptance. | **2–4 days repair plus 4–8 days qualification**, recurring patch/rebase/security ownership. Explicit founder exception required; not selected. |

**B: concrete transport and custody proposal.** Keep the v2 canonical remote URL
`https://control.happyranch.invalid:8443` and `dial_ip` contract above. The root helper generates the
per-home private CA and fixed DNS-SAN server leaf in protected Headscale custody. Check exact CA DER,
CA constraints/validity and SHA-256 fingerprint before any network/root use; save the same
home/control-plane binding. The reserved name is an identity label, **never a reason to accept a
public chain**. Remote identity is established solely by the explicit home pool in the forwarder.

- **Pinned public API assessment:** v1.78.0 [`tsnet.Server`](https://github.com/tailscale/tailscale/blob/v1.78.0/tsnet/tsnet.go#L66)
  has no exported control/DERP TLS-config or system-dialer injection field; its `dialer` is private.
  `controlclient.Options.HTTPTestClient` is explicitly test-only and not exposed through tsnet;
  DERP's `SetURLDialer` explicitly excludes region clients. Do not use either, reflection/linkname,
  a hidden test hook or module-cache edits. Instead use the existing production proxy paths:
  [`controlclient/direct.go:268–273`](https://github.com/tailscale/tailscale/blob/v1.78.0/control/controlclient/direct.go#L268),
  [`controlhttp/client.go:509–522`](https://github.com/tailscale/tailscale/blob/v1.78.0/control/controlhttp/client.go#L509),
  and [`derphttp_client.go:718–730,811–872`](https://github.com/tailscale/tailscale/blob/v1.78.0/derp/derphttp/derphttp_client.go#L718).
- **Same-process routing on both OSes:** bind an ephemeral loopback-only listener before tsnet,
  generate a fresh ephemeral CA and fixed-name server leaf/key in memory, then set process-owned
  `HTTP_PROXY` and `HTTPS_PROXY` to that listener, before upstream proxy-cache initialization.
  Clear/refuse inherited upper/lowercase proxy,
  `ALL_PROXY`, `NO_PROXY`, debug/keylog and trust overrides before initialization; no caller proxy,
  ambient bypass or direct retry is allowed. These derived loopback URLs contain no credentials.
  Keep canonical URL and persistent node/consumed-marker identities remote, never the ephemeral
  port/issuer. Retain the in-memory fixed-name resolver (`dnscache.Get().Forward` and process
  resolver installed before clients) mapping only the admitted home name to `dial_ip`; it changes
  no host DNS. It grants no trust or alternate endpoint. Proxy address lookup uses literal loopback;
  remote verifier dials literal `dial_ip`, with explicit fixed `ServerName`.
- **Closed CONNECT adapter:** accept only CONNECT authorities for the exact fixed name on 8443
  (control) or 443 (pinned DERP proxy behavior). v1.78.0's DERP proxy path uses `HostName:443` even
  when the map has `DERPPort=8443`; map both allowlisted authorities to the **same admitted
  `dial_ip:8443`**, never to a client-supplied host/port. Reject absolute HTTP requests (including
  Noise's HTTP/80 attempt), other methods/targets and malformed/oversize headers. Upstream's
  existing HTTPS fallback then uses CONNECT; no HTTP/80 egress is opened. Open upstream with
  standard `crypto/tls` using a separate `x509.NewCertPool` containing exactly the admitted home CA,
  normal DNS-SAN/time/server-auth verification, no `InsecureSkipVerify`, custom acceptance callback,
  platform/public/baked roots or redirect. Complete that handshake **before CONNECT 200 and before
  forwarding any HTTP/Noise/DERP bytes**. Then terminate local TLS with the ephemeral leaf and relay
  bytes over the verified TLS stream, preserving HTTP upgrade, Noise and DERP duplex streams.
  No HTTP body parsing/logging or encrypted-map rewriting. Proxy/handshake failure closes; no
  transparent/direct fallback or public relay. Setup obeys existing attempt/request deadlines;
  owned stream cancellation/closure and resource bounds need implementation qualification.
- **Isolated local roots on both OSes:** set the process-owned `GODEBUG=x509usefallbackroots=1`
  **before** the single `x509.SetFallbackRoots` call, before any tsnet/root use, passing only the
  ephemeral CA. Go 1.23.12 checks that setting during the call; setting it afterwards leaves a
  nonempty system pool in place. Never add the home or public roots to this local pool. The
  remote verifier always passes its separate explicit home pool. tlsdial's independent LE pool
  remains unchanged; it cannot see the remote leaf because the forwarder verifies that connection
  first and re-presents only its own local leaf. This composes strict remote authentication without
  changing upstream verification or introducing a TLS bypass. One home per dedicated process;
  new process/port/issuer on restart, while durable node/home/grant IDs remain unchanged.
- **Linux 1b:** validate `/etc/happyranch/control-ca.pem` and protected empty CA directory no-follow,
  root ownership/write modes, bounded exact-one-CA DER/fingerprint, and refuse unsafe/missing/extra
  trust before creating clients. Retain the proposed fixed `SSL_CERT_FILE`/`SSL_CERT_DIR` unit
  additions; they locate/protect the home profile, **not the authentication boundary**. Forced
  ephemeral fallback roots isolate tsnet; explicit home roots authenticate upstream. Ephemeral
  keys never go to disk. Proxy/GODEBUG values are set internally before library use, not supplied
  by the desktop or inherited unit environment. Every other unit hardening/MDWE byte stays exact.
- **Unsigned Mac 5b:** dedicated Go home-bridge validates invite/saved CA/dial-IP bindings, creates
  its own in-memory local issuer/leaf and the same CONNECT/verifier/root setup. No Keychain/system
  CA installation, root helper, signing identity or entitlement is needed for this transport.
  Bearer custody still follows the separate login-Keychain requirements below. Native unsigned
  bootstrap/DERP, save/reopen/rebuild and sequential homes remain **NOT RUN**, not inferred from Linux.
- **Headscale / DERP 3b:** keep `server_url=https://control.happyranch.invalid:8443`, HTTPS/control
  and embedded DERP on actual `dial_ip:8443`, and a protected private-only map with
  `HostName=control.happyranch.invalid`, explicit `IPv4=dial_ip`, IPv6 disabled, `DERPPort=8443`,
  `STUNPort=3478`, no `CertName`/`InsecureForTests`. Use v0.25.1's
  [`automatically_add_embedded_derp_region=false` and `derp.paths`](https://github.com/juanfont/headscale/blob/v0.25.1/config-example.yaml#L102)
  if needed for the exact tuple. **No loopback map entries or per-client map rewrite:** the existing
  proxy path reaches the process-local listener on its own random port. STUN stays at the LAN IP;
  no new relay/service. Empty public maps/updates/fallback remain mandatory.
- **Lifecycle:** hold the bound listener for the process lifetime, never attach to an existing
  port. A second process gets another ephemeral port without changing the shared map. On shutdown,
  cancellation, home/address change or listener/verifier failure, close owned streams and tsnet
  admission, discard keys/caches and restart only under the existing supervisor/recovery contract.
  Never replace a failed verifier with direct A. No LAN listener, new account/service, privileged
  Mac operation or hardening relaxation. Local process/root compromise remains a disclosed limit.

The verified HTTPS **`/key` fetch** authenticates the Noise public key; the Noise handshake then
must authenticate that exact key before registration/join-key receipt. Pinned controlhttp demotes
outer Noise TLS errors and can try HTTP/80; B's separate upstream verifier rejects a bad remote
certificate even on that later connection, and its proxy rejects HTTP/80. Do not claim that
upstream changed its error policy. WireGuard protects peer payload; **DERP TLS passes the same
strict upstream home verifier independently of Noise**. Pins, bearers, Linux approval and release
route/verifier gates still authorize runtime access; Headscale membership alone never does.

**C upstream receipts (read-only, TASK-16730):** Go proxy returned releases through v1.104.7 at
observation. Sampled exact tags v1.80.3 (`tlsdial.go:144,158`), v1.84.3 (`147,161`),
v1.88.4 (`104,157,171`), v1.90.9 (`105,158,168–174`) and
[v1.104.7 `net/tlsdial/tlsdial.go:116,168–175,198–205`](https://github.com/tailscale/tailscale/blob/v1.104.7/net/tlsdial/tlsdial.go#L116).
All still derive verification identity from `cs.ServerName`; **IP-SAN binding fix NOT FOUND in
these sampled files**, not an exhaustive claim about every release. **Fallback disabling FOUND**:
v1.104.7 [`feature/buildfeatures/feature_bakedroots_disabled.go:6–14`](https://github.com/tailscale/tailscale/blob/v1.104.7/feature/buildfeatures/feature_bakedroots_disabled.go#L6)
sets `HasBakedRoots=false` under build tag `ts_omit_bakedroots`; tlsdial checks it before the baked
pool. This is a build-wide switch, not a per-home tsnet trust API or an IP-SAN fix. No upgrade,
build-flag adoption, fork or dependency/toolchain change is selected/authorized by this document.

**Round-7 spike evidence, not implementation acceptance:** TASK-16744 task-tmp adapts the
preserved TASK-16730/16720 Go harness without modifying either or the module cache. Pinned real
v1.78.0 controlclient `/key` + Noise `/machine/register` and real DERP region Connect pass valid
home DNS leaf and refuse **matching-name public-equivalent**, unrelated-name public-equivalent,
wrong/no/IP-only SAN, forged CA, expired CA/leaf and unavailable proxy (22 named scenarios across
two parent tests). Correct-key registration receives the synthetic join canary; all invalid
bootstrap cases have zero `/key`, registration and canary receipts. Wrong advertised Noise key
allows `/key` but no registration/canary. A forged outer certificate after valid `/key` also refuses
at B's strict verifier. Each child runs its own CONNECT bridge, ephemeral local issuer and forced
fallback-root setup; the fake upstream has independent request/canary counters.

The public-equivalent fixture deliberately adds a second synthetic root to tsnet's process trust
pool, modeling its independent permissive public/baked branch; the matching leaf verifies for the
**exact expected reserved name** and empty name. The unrelated leaf verifies for its own/empty
name. Neither root is in B's upstream home-only pool. No real LE private key/certificate is used,
and actual baked roots are unchanged/source-inspected. Temporarily contaminating B's upstream
pool with that public-equivalent root causes **both matching-name cases to fail their refusal
assertions**: `/key=1`, registration=1, synthetic-canary receipt=1, DERP accepted. Restoring the
spike file byte-exactly (`a428f4adb94526f3bfeac1672808b0ed6ed1c6336c4b17018e61d3de2fbe9f63`)
passes the full matrix. Command:
`GOTOOLCHAIN=go1.23.12 CGO_ENABLED=0 GOFLAGS=-mod=readonly GOWORK=off go test -count=1 -v .`
(exit 0; focused intended pool-contamination regression exit 1). Receipts: dev_agent
`output/TASK-16744/spike-{red,green}.txt`. Linux proves the two real transport paths and verifier
composition, **not native Darwin**, complete tsnet startup/proxy-failure lifecycle, protected-path
admission, generated Headscale maps, fallback-network-I/O exclusion, MDWE or packaged acceptance.
Those remain §8 gates. Round 6's unrelated-name probe alone did not prove root exclusion.

On address change, Linux shows **Home address changed — Refresh home address**; fresh auth runs
RefreshHomeAddress, re-derives `dial_ip`, updates the protected upstream dial-IP/resolver/DERP mapping and renews
the fixed DNS-SAN leaf under the same CA when needed. Old invites are
invalidated. Linux creates a new invite; accepting it explicitly updates saved `dial_ip` only
for the same home/CA. With a valid saved grant this is metadata recovery only: do not redeem the
new pair secret or issue a credential; leave its unused key to local expiry/cleanup. Normal join and
Linux approval apply if the grant/node is no longer valid. Ordinary app
restart never needs an invite when saved trust/credential remain usable. For v2 DNS profiles,
slices 1b/5 must transactionally update the versioned dial-IP metadata on both endpoints while retaining canonical control URL, protected home/control-plane IDs, consumed
marker and same-node identity; restart the dedicated process to flush old connections/DNS caches and regenerate its local issuer/proxy. Never
rewrite a v1 marker into v2 or repin from the shared DNS alias. If identity cannot be retained,
require new enrollment/approval. Leaf renewal uses the same authenticated refresh action and CA, needs no invite for unchanged address, and adds
no timer; expired/mismatched certificates fail closed. CA change/loss means **Home identity changed
— approve again**: authenticated Turn off/Turn on, fresh invite and Linux approval, never auto-repin.
If the local URL is unreachable, show **Not on your home network, or home computer unavailable**
with **Connect to your home network / Retry**; report daemon offline/restarting separately when
known, never assert the network's location from timeout alone. F2 remains: cached approved clients
may retain maps/keys/bearers and reach a public WireGuard endpoint away from home. This is unsupported
and unreliable, not prevented; no LAN containment is claimed ([cached-client behavior](https://tailscale.com/docs/reference/coordination-server-down)).

**Local packaging/lifecycle (slice 3b).** Ship the Headscale v0.25.1 compatibility target inside the
`.deb` under the protected payload, never fetch/install at Enable. Pin exact upstream tag, artifact
SHA-256 and build/dependency inventory in the owning packaging change; security/compatibility review
must qualify the pin before distribution, no `latest` or implicit version upgrade. Include its
[BSD-3-Clause license](https://github.com/juanfont/headscale/blob/main/LICENSE), copyright/disclaimer
and dependency notices in package/About; audit the exact pinned license inputs, not only main.
A system unit runs as locked nologin `happyranch-headscale`, separate from hr, with the same applicable
system hardening in §2, empty capabilities, only fixed state/runtime writes, restart limits and
bounded readiness. Its admin Unix socket is service-owned 0600 in its 0700 runtime parent; only
that service and root can access it, and only the fixed root helper acts as administrative client.
Bind HTTPS/control + embedded private DERP only to the derived `dial_ip`:8443, STUN to that IP:3478/UDP;
metrics to 127.0.0.1:19090 and gRPC to 127.0.0.1:50443 with insecure mode off; no wildcard/IPv6/admin
LAN listener, ACME/80, router forwarding or firewall edits. Refuse occupied ports. Load only the
embedded private DERP region; empty public map URLs and no automatic public fallback/update checks.
Exact renderer/ports follow the [versioned config](https://raw.githubusercontent.com/juanfont/headscale/v0.25.1/config-example.yaml);
these are proposed bindings, not current host facts. WireGuard UDP underlay remains tsnet-managed,
may use wildcard/ephemeral sockets and reachable public endpoints; it never exposes plaintext HTTP.
Turn off stops/removes the owned Headscale unit/config/DB/CA/leaf/Noise/DERP state and local nodes;
keeps inert package payload/notices. Never remove an unrelated Headscale installation. Cleanup
uncertainty stays visible; release daemon/org data remain. Re-enable creates fresh authority.
After complete Turn off, renderer-exact retained configs/fragments may be reused
only after protected provenance and absence of live/pending/cleanup residue are
proved. The retained sidecar's single old binding is stripped for provenance
verification only; the next normal activation renders generation 0 with fresh
trust/enrollment. Admission and recovery never rewrite config bytes. Partial
cleanup continues to require Retry turn off.

### Outside-home access (fixed operator server)

**Future plan, not shipped acceptance.** Founder seq526 (2026-11-17) removes the custom-server
option; seq529 retains the existing test service for the dev team only. Servers are always fixed
and operator-provided: one private test server for development/testing now, a production server
later. Server identity (control URL, relay map and trust material) comes **only from build-time /
private packaged configuration**. Internal test builds carry test configuration; public releases
can never select or reach the test environment and carry production configuration only once
available, otherwise none. No UI field, helper argument, app-owned file, environment or CLI override
can select/retarget the server. Changing server means a different build. No URL/key intake path
exists or is planned. Production join-key issuance via HappyRanch Services is deferred.

**OPEN founder/security gates — not decided by this amendment:**

- **G1:** how an operator-issued single-use, short-lived test join key reaches the Linux helper.
  The seq421/506 no-caller-chosen-inputs principle stands; no delivery mechanism is selected here.
- **G2:** TLS trust for the fixed server. No system-trust, pinning or CA model is selected here.
- **G3:** the outside-home invite mode, an additive invite-contract change in its own
  security-reviewed slice. The shipped home-only wire literals and refusals in this section stay fixed.

**Dev-only test-service controls and acceptance PLAN (product seq531; not implemented or verified):**

| Planned control / evidence | Ownership and gate |
| --- | --- |
| Approved test-device inventory recording each owner/purpose; remove obsolete devices and keep administration private. | Host steps: **founder-run, pending founder decision (seq530)**. |
| Restrict peer-network access and relay use independently; demonstrate with a DERP check that an unauthorized client cannot relay. An optional IP allowlist is not decided. | Host steps: **founder-run, pending founder decision (seq530)**. |
| Separate internal/private test configuration from public production builds; public artifacts cannot select or reach test. Keep test and production credentials separate. | Future build/packaging slice; any host/credential setup is **founder-run, pending founder decision (seq530)**. |
| Preserve Linux approval of the actual device and per-device revoke; network membership never grants runtime access. | Runtime authorization unchanged; no implementation change authorized here. |
| An outsider who knows the endpoint cannot enroll, reach peers or relay; a removed test device loses access. Record separate enrollment, peer, DERP and removed-device denial evidence without secrets. | Founder-run acceptance and all host steps: **founder-run, pending founder decision (seq530)**. |

G1–G3 decisions and founder-run hardening must precede outside-home implementation/acceptance.
No test-server identity belongs in tracked text, public artifacts or hosted fixtures. This plan
performs no host/VM step and does not claim that endpoint secrecy alone restricts access.

All modes enforce single-use invite redemption/approval in the existing cross-process transaction,
one active invite/pending attempt per home, no raw invite at rest, digests/deadlines only. Local
minting journals a helper-generated operation ID; uncertain mint outcome never issues under a new
ID automatically. Expiry/deny/restart consumes locally; a network key/node may survive remote
cleanup failure but cannot pair or gain runtime access. No standing user/agent-readable key pool.

**Effective invite lifetime:** seq495's ten minutes was a UX suggestion, not this protocol's TTL.
Home invite expiry is the earlier of issuance + five minutes and its locally minted key's expiry.
Display that actual deadline/countdown on Linux and Mac, including while waiting for approval;
joining does not extend it. Outside-home lifetime rules remain for the G3 security-reviewed slice.
At expiry consume pending/delivery authority and show **Create new invite** on Linux / **Create new
invite on Linux** on Mac. A replacement always invalidates the old invite/pending handle.

Raw invite/join/pair secrets, completion handles, device bearers, daemon token, Services tokens,
private keys and their request/response bodies never enter argv, env, URLs, journal, analytics,
crash breadcrumbs or diagnostic exports. Disable HTTP body/header logging for pairing and enrollment
transport; use closed error codes, not raw library exceptions. Diagnostic redaction covers
structured fields and known prefixes with synthetic canaries, including malformed inputs. No secret
hashes/prefixes or public node keys in public logs. Device fingerprints are privileged approval UI
only. Keep the existing no-logtail/private-DERP policy. Desktop/Mac drafts are memory-only, no state
restoration; clear on completion, expiry/cancel. Clipboard copying is explicit and best-effort
cleared on expiry; screenshots/clipboard history/agent memory reads remain a disclosed residual.
The current Mac device bearer is memory-only in `ClientBridge.swift`; relaunch requires re-pairing.
`EnrollmentKeyStore.swift` uses Keychain only to stage/delete join-key material, not persist the
device bearer. **Saved-device custody, R2 approved seq508 / slice 5:** after durable Linux approval
and credential delivery, use a separate generic-password item in the user's local **login Keychain**,
not the entitlement-dependent data-protection Keychain. Disable synchronization; no shared access
group or plaintext fallback. Service/account identifiers bind the saved home/control-plane and
stable-node identity, never a label. Use the macOS Security framework's legacy `SecAccessCreate`
ACL with `SecTrustedApplicationCreateFromPath` for the current app executable as the sole trusted
application; never grant all applications access. This supports an ad-hoc/unsigned test app without
a Developer ID or entitlement prerequisite. Reopen the item through `SecItemCopyMatching`, allowing
the normal Keychain user-consent/unlock UI. Prove create, quit and reopen with the actual unsigned
artifact; do not infer success from a signed build. Set the ACL via `kSecAttrAccess` when creating
the item with `SecItemAdd` ([Apple access attribute](https://developer.apple.com/documentation/security/ksecattraccess),
[trusted applications and confirmation](https://developer.apple.com/documentation/security/secaccesscreate(_:_:_:))).

An unchanged test binary may reopen under its ACL; a rebuilt/update binary can change code identity
and trigger a Keychain access prompt or denial. Explain **Saved access needs Keychain permission
after this update**, offer **Unlock/Allow and retry**, and never silently broaden the ACL, recreate
the item or downgrade storage. If access remains denied or the old identity cannot be authorized,
show **Saved access unavailable — create a new invite on Linux**; fresh approval saves a new item
only after explicit replacement consent, and the obsolete host grant remains revocable on Linux.
Failure to save after delivery shows **Access was not saved** with Retry while the credential is
still in memory; after process loss require a new invite. Never claim restart reconnection then.

The signed release migrates through explicit user consent: its first use reads the old local item
via Keychain's permission UI, validates the same home/node binding and current host grant, writes
and reads back a new non-synchronizing item restricted to the release's stable signed identity,
then deletes the old item. Failed read/authorization requires re-invitation; failed write/readback
keeps the old item and reports migration incomplete, failed deletion reports retained old storage.
No credential export or automatic trust of a changed executable. Signing improves future identity
stability; it is not required for this milestone.

For a valid saved grant, restart reopens the owner-only tailnet state with no join key, loads its
Keychain item, checks the pin and reaches the authorized runtime. **Disconnect** closes only the
current session and keeps saved access. **Remove saved computer** (or local logout) closes sessions
and erases local bearer, saved network/trust state and memory; report each deletion failure.
It does not revoke the host grant. Linux **Revoke device** denies and closes that device's streams
even with Mac offline; **Turn off remote access** invalidates all devices/invites. Mac erases its
item on learning of revocation. Unsigned ACLs provide no durable publisher identity or isolation
from a compromised unlocked app/session; user-approved Keychain access, same-user executable
replacement and memory theft remain limits. Concrete ACL/update/migration behavior requires renewed
security review and native tests; never claim Keychain defeats same-session compromise.

## 7. Mac, installation recovery and upgrades

I5d-0 B7 rollover retains the older failed transaction. After all existing proofs
and the unchanged-pair check, its recovery owner retires the abandoned enrollment
source/link through the existing installer primitive, repeats the unchanged-pair
and source-token checks, then publishes prepared G+1 with a new operation as the
sole commit point. Unsafe custody or removal failure retains the failed pair
without prepared publication. A crash leaving only the link or neither entry
admits fresh-auth retry with every proof repeated and idempotent removal.
READY/RESUME and half-published recovery leave enrollment untouched.
Backend Enable forwards the rollover pair to the existing
coordinator only when stable bindings match, txn is older failed_uncertain and
operation IDs differ; matching pairs keep Ruling 17 terminal handling. The existing
live-config owner first admits provision-equal staged bytes, then an exact earlier
render over protected staged provenance for this prepared rollover: connector
digest unchanged, exact sidecar insertion
for integer 0 <= g < G+1, stripped digest equal to provision. Old done home_config
also requires its live member digests and g equal to the old transaction generation.
If neither rule admits the bytes and the older txn has NO home_config record,
I5d-2 reuses the post-Disable proof: exactly one trailing insertion bound to the
protected stable IDs, any integer old g >= 0 (including g >= the new generation),
and an exact provision digest after stripping it. Its retired CA/dial are not
compared to the new receipt; repeated pre-home_config failures and rollovers use
the same proof. Older home_config intent/partial publication keeps its existing
refusal limit and never uses this rule. Foreign/pending/unsafe inputs refuse
without effects. Captured inputs are
the proven staged bytes; only the normal next home_config effect publishes the
G+1 render. Recovery never rewrites live configs or stable @2 invite configuration.
Current transaction checks, custody, receipt CAS and B2 observation remain required.

**Round-7 trust qualification:** slice 5b/#1575's fallback-root/IP-constraint proposal is not
accepted as protection against LE fallback. After approval, replace its home transport design
with §6 B's strict CONNECT/upstream home-only verifier, fixed DNS URL, v2 `dial_ip` and isolated
ephemeral issuer/proxy/root lifecycle; retain unsigned
Keychain requirements, single invite, Linux approval and every recovery state below. Do not edit
or merge #1575 on the strength of the Linux spike. Native unsigned Mac bootstrap/DERP negatives,
restart/rebuild/address refresh and home isolation remain acceptance gates.

Mac states: Idle → Validating invite → Joining network → Pairing → Awaiting Linux approval → Opening
verified session → Connected. Joined alone never says Connected. All network/native work is off the
main actor with attempt IDs, bounded deadlines (join 120s, each request 10s, approval no later than
invite expiry), cancellation and closed failure categories: malformed/expired invite, TLS/control
unreachable, join denied, home unavailable, identity unavailable, approval denied/expired,
revoked/re-pair required, daemon unavailable. Retry explains whether a new invite is required; an
already-persisted membership uses empty-key reopen, never silently reuses a spent join key. No
fallback to public Tailscale, legacy HomeConnector or an unpinned peer.
[#1563](https://github.com/t-benze/happyranch/issues/1563) belongs in this slice: show a sanitized
failure and retry action, including TLS-close.
[#1564](https://github.com/t-benze/happyranch/issues/1564) is **queued separately**: Home-mode Start
Connector is not called by this Mac-client flow; capture its crash report in its own authorized
reproduction, no launchd/Swift hosting rewrite here.

User-visible states/actions (Linux and Mac; no state below grants runtime access):

| State | Linux display / action | Mac display / action and effect |
| --- | --- | --- |
| Permission prompt cancelled/denied | **Action cancelled — no change**; return to the prior state. **Try again** starts a new fixed action and fresh polkit prompt; never queue it. | If waiting, remain **Waiting for Linux approval** until rejection/cancel/expiry; a cancelled Linux prompt is not approval. Mac Keychain cancellation shows **Saved access needs permission**, with **Retry**. |
| Invite issued / expired | **Invite expires in m:ss** using §6's effective ≤5-minute deadline; **Create new invite** requires fresh auth and invalidates the old attempt. At zero: **Invite expired — Create new invite**. | Show the same deadline while joining/waiting; at zero **Invite expired — Create new invite on Linux**. Never retry the spent invite/key. |
| Awaiting approval / user cancels waiting | Show actual OS label, stable-ID fingerprint, comparison phrase and target runtime; **Approve device** / **Reject device** each use fresh auth. Rejection consumes the attempt. | **Waiting for approval on [computer]** and comparison phrase; **Cancel** closes the local attempt and forgets its handle/secrets. Pending authority expires, is rejected on Linux, or is invalidated by **Create new invite**; no access or silent resume. |
| Join key consumed / credential delivery lost | **Authorization did not finish — Create new invite**; local partial-node cleanup is recorded. | **Authorization did not finish — Create a new invite on Linux**; reuse only valid same-node membership, then a new invite and Linux approval. Never restore a spent handle/grant. Save/update failures use the explicit Keychain recovery in §6. |
| Release daemon offline/restarting | **Remote access ready; HappyRanch offline/restarting**; open the normal runtime controls, then **Retry** after readiness/verifier succeeds. No credential reset. | **Home HappyRanch is offline/restarting — Retry** when known. If control/LAN is unreachable, use **Not on your home network, or home computer unavailable**; retry/network guidance, never **Connected**. |
| Connected / disconnect / local removal | Device remains approved until **Revoke device** or **Turn off remote access**. | **Connected** only after authorized release web UI loads. **Disconnect** keeps saved access; **Remove saved computer** erases local access only. Deletion failure shows **Removal incomplete** and what remains, with **Retry removal**. |
| Host revocation / turn off | **Revoke device** denies only that device and closes its streams. **Turn off remote access** invalidates all devices, invites and approvals, removes owned remote services/secrets, and preserves release runtime/org data. Both require fresh auth. | **Access revoked — Create new invite on Linux**; close access and erase saved material when observed. An offline Mac cannot prove local erasure, but host denial is already effective. |
| Partial cleanup / uncertain stop | **Remote access cleanup incomplete** names safe paths/components and whether host denial is confirmed; never claim **Off** or **Removed** without proof. **Retry turn off** is the same fixed Disable action with fresh auth; unknown ownership needs operator recovery. | Close local sessions; show **Local cleanup incomplete** or **Host cleanup status unknown** as applicable. **Retry removal** erases only local state; host cleanup needs Linux. |

Authenticated Turn off clears retained failed markers only for the fixed five
managed `UNITS`, after verified stop/final fragment form and before cleanup
receipts retire. Ready-restart Enable uses the same reset before starting owned
units, with its original G/O, custody, deadline and no-mint/replay fences. Each
reset requires exact owned bytes/fixed FragmentPath, no drop-ins/reload, failed
ActiveState, dead/failed SubState and empty Job. Services require
MainPID=ControlPID=0 and empty ControlGroup; the fixed socket requires
ControlPID=0 and empty ControlGroup, with no service-only MainPID. The fixed
target has no execution context. Absent fixed units also require the no-fragment,
not-found reload proof with only their supported PID fields.
Re-read must prove inactive/dead. Reset failure is fixed partial cleanup during
Turn off, never completed; real ready-restart failure keeps the Turn off advice.
All five fragments/configs stay retained and strict inventory state sets stay
unchanged. Receipt-bound B6 also resets only the fixed Headscale service after
its verified removal and final daemon reload, before retiring the original
receipts. Its absent-unit proof requires exact Id/Names, not-found LoadState,
empty FragmentPath/DropInPaths, no reload requirement, zero MainPID/ControlPID,
empty ControlGroup/Job, dead/failed SubState, original receipt/G/O/identity/custody
fences and a fresh inactive/dead after-read. The not-found Service properties
are required explicitly; missing properties refuse. Ready restart uses the same
service proof for a failed loaded Headscale unit, with exact home bytes and its
loaded FragmentPath, before the existing boot-equivalent start. The original
receipt/G/O/custody/deadline/no-mint/no-replay fences remain. Reset or start
failure keeps the existing named refusal and Turn off advice.
Status never resets; an already
stranded failed retained unit without a receipt still needs operator recovery
because this change adds no no-receipt Disable admission.

Enable first inventories only fixed managed destinations, exact unit fragments, drop-ins and package
files, with no effects on unknown objects. Successful modern records resume idempotently; existing
healthy manual installs require explicit migration, never infer failure from a stopped unit. For
FAILED/partial manual installs under `/etc/happyranch`, `/opt/happyranch` and the fixed service
state paths, `ReplaceFailed()` offers one truthful destructive replacement prompt.
The CLI warning is exactly “Replace failed remote access; existing pairings will be lost”,
followed on the controlling terminal by exactly `Continue? [y/N] ` (default No).
Only an explicit `y` or `yes` dispatches; no tty, EOF or any other answer dispatches
nothing, effect `none`. Recognize only
bounded known prior unit/config shapes + safe ownership or valid provisioning provenance; never
execute old config/argv. Stop target, both services and verifier; prove quiescence before changing
replacement files, except that PR1632's validated own-journal pending discard may precede a later
stop failure. Journal intentions and fsync before each fixed-path rename/removal; retain root-only
quarantined old configs briefly for diagnosis, never reuse keys, trust or old control settings.
Generate fresh IDs/config/enrollment and require re-pairing. Never recursively remove `/opt`, the
dpkg prefix, foreign files, custom units or unknown drop-ins. Unsafe/unrecognized residue produces
`manual-install-conflict` with precise non-secret paths; it needs operator recovery, not a terminal
recipe for the user. Interrupted replacement stays off; next authenticated ReplaceFailed resumes
only the exact journal generation. Do not roll back revoked credentials into service.

THR-327 closure retains the existing replacement-reload intent for daemon-reload
followed by reset-failed of only journal-removed fixed managed units. Fresh
not-found, empty fragment/drop-ins, zero main/control PIDs, empty job and
failed/inactive admission is required before reset; inactive is a no-op. Both v1
and v2 already-reloaded journals resume with unchanged caller/auth/custody checks.
Ordinary absent-unit inventory still requires inactive before retirement; a
remaining failed marker refuses without another retry or changed deadline.
When a lifecycle worker returns, settlement waits for its RLock, then records
completion or uncertain and releases its slot while sealing process exit, even
if completion raises. Unfinished effects retain ownership. See the maintained
[closure contract](../agent-guides/contracts/remote-access-and-remote-jobs.md)
and [formal model](formal/replacement-closure/README.md).

I2b supplies only the unmounted `ManagedInstaller.begin_replacement` first-intent
entry for B17; I3a mounts it and I5a exposes public ReplaceFailed. Linux CLI
wiring is delivered in I5b; the v1 bridge has no replacement command. It requires the
expected release UID and exact observed inventory/journal generation, captures
the full fixed inventory, validates the release owner under §1 and the same-FD
source token, then durably publishes additive `replacement-intent@2` before stop
and per-intent recovery. It admits only recognized failed complete installs,
validated incomplete fixed sets (including generated-writer prefixes/partial
legacy provenance), or the existing exact v1 recovery journals. Existing v1
journals resume without conversion. Stopped intact manual, healthy, modern
staged-inert/recorded and unknown shapes remain non-candidates.
The closed v2 record retains exact metadata/digests/link targets for the fixed
inventory, with literal `absent` for missing digests and metadata only for opaque
state. Safe unrecorded 0600/0640/0644 config modes remain admissible; recorded
provisioning stays exactly 0640. V2 preserves missing originals through quarantine
and uses a separate `replacement-stage-release` intent when the interrupted
writer never published its derived release record. Recovery closes into fresh
modern staged-inert files without activating services. All existing no-follow,
single-link, cross-config/provenance, exact-unit/loaded-fragment/drop-in checks,
64 KiB file and 64-entry directory bounds and PR1632's pending-discard exception
remain. This source capability grants no live installation or service action.

Disable closes admission/revokes first, confirms connector/sidecar/verifier and owned local
Headscale stopped, then removes only positively owned units, configs, enrollment sources/links,
socket/runtime files and secret/trust state; reloads systemd and confirms absence. Local runtime
revocation is immediate and does not depend on remote cleanup.
Remove a helper-created service account only when no other installation/files/processes use it;
otherwise retain the locked nologin account and say so. Keep package/helper, non-secret audit and
unrelated data; destroy quarantined secrets after successful replacement or disable. Any
stop/ownership uncertainty aborts removal and reports partial state.

I2a supplies the receipt-bound B6 installer cleanup executor, privately mounted by I3a; existing
operator CLI/uninstall calls retain their pre-activation scope. It requires
trusted expected identities/generation and all owned services already quiescent,
removes only the fixed per-type live-state/receipted members, and explicitly
destroys the owned CA private key. I3a mounts revocation/stop/B6;
I5a exposes public Disable; I5b delivers Linux CLI wiring, with bridge wiring following in I5c. I3c extends this same owner to the fixed
root-only CA staging files and, only with trusted successful revoke-all, the
fixed connector trust and persisted sidecar state. Failed revoke still retains
that state and reports partial cleanup. I5g admits pre-stop diagnostic residue in
the existing stop-time inventory only for `/run/happyranch-tsnet-sidecar`, using
the same H1 names, modes, exact service UID:GID, regular single-link type,
65536-byte ceiling and eight-diagnostic bound below (`observe.sock` is separate).
Inventory is metadata-only, no-follow through the literal parent FD; it never
opens/reads diagnostic contents. All other inventory refusals stay unchanged.
This permits the existing stop proof to reach B6; it grants no earlier deletion.
I5f H1 retires nonsecret runtime diagnostics
only in `/run/happyranch-tsnet-sidecar` after existing stop/quiescence proof and
before empty-only rmdir: `health.json` (0644) or `^\.health-[0-9]+$` temps
(0600/0644), at most eight entries, each regular single-link, existing service
UID:GID, at most 65536 bytes. Validate the whole set by no-follow lstat through
the pinned literal parent FD before the existing observe.sock unlink and again
before diagnostic deletion; unknown names or unsafe metadata refuse with zero
deletions in that directory. Never open/read contents. Recheck each activation
signature before dirfd-relative unlink, fsync the parent, then existing rmdir/fsync.
This applies on every B6 path reaching that pass, including failed revoke;
persisted StateDirectory retention, strict post-Disable staging admission and
legacy/uninstall preserve-across-stop behavior remain unchanged. The existing
B16 retirement audit does not cover this pass; no audit vocabulary is added.
B7/B11 transaction, uncertain journal
and home-minter evidence retire only after B6 proves all five Headscale DB,
WAL/SHM, Noise and DERP files gone in that same receipt-admitted operation, with
B16 intent/outcome audit. Nonterminal/pending issuer evidence with an active
ceremony refuses; unknown pending entries remain incomplete.

The contract's I3c M1-M7 table fixes the root:root 0700 home-minter parent and
two user directories, exact 0600 single-link record names and per-user streamed
600,000-entry bound. Validate the whole set under existing nonblocking flocks;
unlink operation reservations, user-create, fence, fence.pending, rpc.lock last,
fsync, release, rmdir empty directories and fsync parents. Audit contains only
per-user counts. Retention is within one DB lifetime; after proven DB deletion,
keeping user-create would permanently block a fresh Enable. Fresh activation
G/O CAS still blocks stale replay. Without DB proof all evidence stays, incomplete.
Unknown/unsafe entries, contention or exhaustion refuse; no recursive removal.
No-argument CLI/uninstall D and account rules A–E remain unchanged. Complete
Turn off requires revoke success, stop proof, B6 success and all residue retired;
package/helper, audit and unrelated release/org data remain.

Upgrades preserve protected installation IDs/pins and revocations, validate the old version, stop
admission, migrate under the same trust lock, reload exact system units and verify loaded
fragments/drop-ins/READY before reopening. Unknown or torn state refuses; no trust reset. V1
unpinned records require re-pairing. Refresh a rotated daemon token only from the recorded validated
release source after explicit authenticated Enable/repair, never a new app-selected path.
[#1562](https://github.com/t-benze/happyranch/issues/1562): before normal desktop start, detect
`NeedDaemonReload=yes`, run own-user daemon-reload once, reread and show a bounded error/retry if it
fails. This fix is **merged in PR #1569, `e1ff752d`**; installation/upgrade acceptance is still
separate. Never attach an old/unverified daemon or poll “starting” indefinitely; no root iteration
over arbitrary user managers. No packaging edit in this design PR.

## 8. Sliced delivery and evidence (implementation requires approved briefs)

Dependency order is 0 → 1a → 2 → 3a → 3b → 3c → 4 → 5 → 6, with **1b after renewed design/security
approval of Round-7 B and before local-CA installation or home-mode acceptance**. Slice 1a's
identity frame and explicit Linux profile need not wait for 1b; 1a merged in #1572 without CA
changes. R1 is resolved and R2 approved (seq508). The custom-server path is removed.
Round-7 trust/invite changes still await their own decision. Home-network delivery remains first;
outside-home access depends separately on G1–G3 and founder-run hardening (§6). Home
trust/Keychain feasibility and independent review remain separate gates, not another R2 decision.
No new required CI check or automatic M1/M2 trigger; use existing authorized manual hosted acceptance. Every implementation
slice needs independent review, scoped QA, normal hooks and the two required lint/static checks at
its exact head.

| Slice | Main file ownership / dependency | Verification required |
| --- | --- | --- |
| 0 prerequisite | PT_GNU_STACK/MDWE PR #1568 merged at `1a8efca4`; upgrade-reload PR #1569 merged at `e1ff752d`. | Owner's packaged runtime evidence; actual installation/upgrade acceptance remains separate. Never remove MDWE to pass. |
| 1a protected ingress (merged #1572) | `app/linux/tsnet-sidecar/{sidecar,engine_tsnet,health}.go`, `runtime/remote_access/managed_provider.py`, `systemd_unit.py` and connector Unix listener/parser; WhoIs identity frame and explicit new Linux profile only, **no CA changes**. | Supported Go tests and real temporary Unix sockets: forged HTTP identity, wrong UID, malformed frame, absent WhoIs; empty-peer READY and loss/watchdog; exact hardening parity. Native Mac hosting profile unchanged. |
| 1b Linux strict home-only transport (after Round-7 decision) | Sidecar startup/config + same-process CONNECT forwarder, explicit upstream home roots, ephemeral issuer/forced local fallback pool and immutable resolver before tsnet; fixed `SSL_CERT_FILE` + empty `SSL_CERT_DIR`; no dependency/fork or weakened units. | Execute the transport/admission matrix below against real pinned code; full tsnet proxy/root/resolver lifecycle and fail-closed teardown in addition to direct probes. Exact rendered-unit comparison allows only the two Environment lines; MDWE/all other directives identical. |
| 2 trust ceremony | `pairing.py`, `authorization.py`, `state_store.py`, request/stream admission; no desktop or installer edits. | Retained standalone connector venue with synthetic state: two concurrent redeemers/approvals → exactly one grant; wrong node with valid bearer denied; A revoked closes A streams while B works; restart/torn/rollback/legacy cases refuse. |
| 3a fixed helper | New helper module, system-bus/polkit definitions; status and guarded approval/revoke, no installation yet. | Standalone IPC plus disposable polkit: extra args refused, agent can prompt but not silently approve, actual target immutable across auth, fresh auth twice, wrong-UID and disconnect refusal. |
| 3b generation/install | `installer.py` `_input()`/`_read_file()` source-UID/same-FD validation, generated config/transaction owner, package/helper wiring; after 3a/1b and merged packaging prerequisites. Coordinate renderer with 1a/1b; bundle pinned Headscale/notices, protected local CA/DB/admin socket, DNS-SAN leaf/server_url, fixed-name private DERP map and exact LAN bindings. | Temp-root/fake-manager clean/known FAILED/partial/foreign fixtures, foreign-owned 0600 token and parent/leaf swap or metadata-change refusal on enable/repair/replacement; safe `.deb` payload access, stop failure, interrupted replacement/disable, token rotation and exact unit/hardening parity. Assert generated DNS URL + explicit LAN DERP/STUN tuple, same-CA dial-IP refresh/old-cache teardown, CA/address recovery, port conflicts, full Headscale cleanup. |
| 3c invite issuance | Local root-only Headscale mint + connector private invite IPC, v2 DNS URL + CA/fingerprint/dial_ip; after 2/3b, within approved R2. | Synthetic local mint/idempotency/uncertainty/cleanup; fresh auth, v2 exact schema/CA size and round-trip, v1/IP-URL/unknown-field refusal, home_endpoint unchanged, effective countdown, no user mint capability, redaction/replay/expiry. |
| 4 Linux UI/upgrade | `packaging/linux/desktop/happyranch_window.py`, package wiring after merged #1568/#1569; verify their installed behavior. | Existing standalone desktop checks; native GTK states/actions from §7, including prompt cancellation, countdown and partial cleanup. Separate fresh polkit prompts at enable, invite, actual-device comparison/approval, turn off and revoke; route-policy renewal paused + Retry. Home card/address refresh; no admin-key prompt. No host service actions for maker. |
| 5 Mac single invite | `app/mac/Sources/HappyRanchApp/{RemoteConnectionView,AppDelegate}.swift`, supervisor managed model/ClientBridge, `app/mac/tsnet-bridge` as needed; #1563. Dedicated-process strict CONNECT verifier + ephemeral local fallback roots/proxy/resolver, v2 parser/profile and unsigned login-Keychain ACL from §6, within approved R2; renewed security review/native feasibility required. | Supported Swift/Go checks and actual unsigned build: join→pending→Connected; §7 cancellation/expiry/offline states; save/quit/reopen without invite, locked/denied Keychain, rebuilt code identity, signed migration and deletion failure. Linux revocation/turn off, lost delivery/new invite and partial-node cleanup/reuse; all §8 transport/admission negatives on native unsigned Mac, Noise-key mismatch, isolated homes, renewal/address recovery and secret-free diagnostics. #1564 stays queued. |
| 6 hosted acceptance/docs | `scripts/ci/m1_install_ready_uninstall.sh`, `m2_tailnet_daemon.sh`, related fixtures/docs; builds on 1–5. | M1 uses helper-generated configs and no ExpectedPeer; clean plus known FAILED-manual fixture, exact hardening/READY/loaded units, disable residue, interrupted recovery and route-policy renewal failure closed. Hosted fixtures remain home/synthetic; outside-home app acceptance is separately founder-run after G1–G3 and hardening below; M2 keeps real random-port daemon/verifier and squatter negative; adds two dynamically named stable-ID devices, one combined invite each, race/replay/expiry/wrong-node/revoke-survivor and join-success/delivery-loss recovery cases. |

**Round-7 B falsifiable transport/admission matrix.** Required separately on Linux 1b and the
native unsigned Mac home-bridge 5b; client logs alone are not refusal evidence. Use synthetic
certificates/secrets, fresh subprocesses per root profile, loopback fake control/embedded DERP
with real pinned clients, then full startup/packaged venues. No live founder service.

| Case / precondition | Required observable result |
| --- | --- |
| Valid admitted home CA + fixed DNS SAN, `dial_ip` routes to fake server | Both OSes: real `/key` hit, authenticated registration and synthetic join-key receipt; DERP Connect through B succeeds. Assert strict upstream TLS and ephemeral local leaf are distinct. Linux transport spike PASS; native Mac NOT RUN. No host DNS edit or Mac root helper. |
| Public/LE-equivalent chain whose leaf **MATCHES `control.happyranch.invalid`** (including matching wildcard), valid in the baked/system-equivalent pool but outside the home CA | **Linux and Mac expected REFUSE:** zero `/key`, registration and join-key receipts; DERP refused by B before forwarding. Deterministic matching-leaf fixture must independently verify for the exact expected name and empty name in its public-equivalent pool. Linux exact-name transport case PASS; native Mac and wildcard execution NOT RUN. Actual baked LE source unchanged; no real LE private key claim. |
| Public/LE-equivalent chain for an unrelated name | Both OSes: zero `/key`, registration/canary receipts; DERP refused. Show the injected chain verifies for its own/empty name and that upstream rejects its root as well as its SAN. Linux transport spike PASS; native Mac NOT RUN. No acceptance-property narrowing to issuable names. |
| Home-CA leaf with wrong DNS SAN; no SAN; IP-only SAN for the actual dial address | Each: zero `/key`/registration, DERP refused. Assert nonempty expected DNS name; IP-only leaf must not regain the old IP-URL path. |
| Forged CA; expired CA; expired leaf; mismatched CA DER fingerprint | Each: zero registration; transport-invalid leaves give zero `/key` and DERP refusal. Fingerprint/CA admission failures occur before client construction or any dial. Matching SAN alone grants no trust. |
| Missing/unsafe bundle or parent, symlink/link/write-mode/extra-CA/PEM content; nonempty CA directory; ambient file/dir/proxy/keylog/debug override | Closed startup error before tsnet/dial; no secret echo. Linux default-directory contamination grants no trust; Mac platform/public roots never replace either isolated local or explicit upstream profile. Exercise exact protected paths in an authorized disposable venue, not merely temporary SSL_CERT paths. |
| Valid `/key`, server Noise key differs from advertised key; outer Noise cert invalid with correct key | Wrong key: `/key` succeeds but authenticated registration/join-key receipt is zero. Correct key with bad outer remote cert: B refuses its separate upstream TLS, with zero registration/receipt; document unchanged upstream nonfatal policy. Linux both cases PASS; native Mac NOT RUN. |
| CONNECT/root/resolver startup, outage and multiple profiles | Both OSes: force proxy/root setup before any client, only local ephemeral issuer in tsnet pool, only home CA in upstream pool; inspect actual upstream dials and reject wrong targets/HTTP/80/redirects/bypass overrides. Listener/CONNECT/verifier failure must close admission/owned streams, with no direct/public fallback. Full tsnet bootstrap/Noise/DERP and DNS-fallback-I/O observation remain NOT RUN; isolated unavailable-proxy transport cases PASS on Linux. Two processes use different loopback ports without map rewrite; refresh flushes old caches/issuer while retaining canonical IDs/consumed authority. Native Mac NOT RUN; unsupported path returns to EM. |
| Private embedded DERP map/config and renewal | Headscale v0.25.1 generated map has the exact §6 tuple, no other regions/CertName/insecure flags; real relay negatives above fail independently of WireGuard/Noise. Same-CA DNS-leaf renewal works, CA replacement requires approval; stale dial-IP/cache cannot silently retarget. |
| Unit/rendered installation and native unsigned Mac | Only the two fixed Environment additions versus current-main sidecar unit; every other hardening/MDWE directive identical and packaged readiness verified separately. Mac bootstrap + DERP matrix, save/quit/reopen, rebuilt unsigned identity, address refresh and two sequential homes run on Darwin without root/signing/trust-store installation. Missing venue = NOT RUN. |

Hosted harness fixtures may provision bundled local Headscale and a test-side polkit
authentication agent on disposable runners; production helper has no test flag, bypass action or
alternate caller-supplied settings. Hosted authentication
simulation is not proof of the real desktop dialog; separate disposable desktop/native UI acceptance remains required. Retain existing
secret artifact filters, private pipes, source/runtime receipts and bounded teardown. No founder
host, live Headscale or production service is used. M3/M4 shared drivers need
alignment/requalification after these changes; their older receipts do not establish this protocol.
No Product E2E or retired Python/Web units here. Each affected test follows the test-authoring
evidence gate, using its supported venue and honest NOT RUN for unavailable native or sensitivity
evidence.

**Acceptance is two separate app journeys; neither is proven by network membership or CLI joins.**

| App case / venue | Observable result |
| --- | --- |
| Home-network fresh setup and today's recognized FAILED manual-install fixture, disposable Linux + Mac same LAN | Enable installs local Headscale/connector/sidecar with full hardening; Ready to invite; one invite, actual Linux approval, authorized release web UI loads. No user terminal/JSON/separate pairing. |
| Outside-home, Linux home + Mac on a genuinely different network through the fixed operator test server; after G1–G3 decisions and founder-run hardening | Founder-run, pending founder decision (seq530): prove the seq531 outsider/DERP/removed-device denials in §6, then fixed-build app enable/Add a device → one invite → Linux approval → release web UI; capture direct/private-DERP route evidence separately. No URL/key input. Retain this server until the test settles; no removal authorized. |
| Both flows: restart either app/daemon; add second Mac; revoke first; turn off/re-enable | Saved grant/membership reconnect without new invite after ordinary restart; daemon offline/restarting shown; A revocation cuts requests and live streams while B works; off invalidates both/invites, preserves release/org data; re-enable needs new invites. |
| Both: cancel/expiry/replay, key consumed before approval/delivery, Keychain locked or update changes code identity, partial cleanup, policy renewal fails | §7's visible actions/countdown; clear retry/new-key/invite recovery, no restored spent authority; unsigned save/reopen and signed migration qualified separately; cleanup failures visible; expired policy pauses closed with Retry. |
| Local trust/address recovery; cached approved client away | Full Round-7 refusal matrix above passes; fixed-name same-CA renewal/restart reconnects; changed dial IP needs explicit v2 refreshed invite, CA replacement reapproval; unavailable LAN state shown honestly; away cached success never advertised as supported outside-home acceptance. |

**Timeline / risk, amended 2026-11-17:** target decisions/security + trust/packaging feasibility by
11-18; slices 1a/1b–3c through 11-23; Linux/Mac slices 4–5 through 11-27; separate home-network acceptance
by 11-28 and outside-home app/revocation test by **2026-12-01**, leaving 12-02 for authorized fixes.
The operator test server term ends **2026-12-03**; outside-home acceptance must finish before that date,
not merely join or start by then. These are targets, not a delivery commitment. R2 is approved;
packaging #1568 and upgrade #1569 are merged,
but installation/acceptance is unproven. G1–G3 and founder-run hardening (pending seq530),
Round 7 strict trust/invite security/Product decision, slice 1b trust
qualification and unsigned-Keychain/native acceptance remain unresolved. Slice 1a is merged;
the TASK-16720 IP-SAN/LE failure and Round-7 forwarder/lifecycle work put the 11-23 integration target at risk.
**The slice plan cannot currently guarantee that deadline.** EM must confirm feasibility by 11-25
and flag a miss immediately if the 11-18 gate or integration milestones slip. Do not call same-LAN
success outside-home readiness or silently extend the server term, skip gates or deploy to recover.

Doc parity in the owning implementation slices: current remote-access contract;
`docs/operations/remote-access-runbook.md` (replace operator steps for desktop users, keep historical server
provisioning separate); `app/linux/tsnet-sidecar/README.md`; `app/mac/README.md`;
`docs/operations/linux-release-handbook.md`; normative `2026-08-26-managed-remote-access-contract.md`;
M1/M2 workflow comments and M3/M4 runbooks; `docs/operations/local-ci.md` for supported commands. No bundled
remote-access skill was found in the repository; inspect published skill consumers before
implementation and update any owning remote-install instructions alongside it. Do not change agent
permissions, jobs/jenkins skill authority or test cadence.

[PR #1555](https://github.com/t-benze/happyranch/pull/1555), THR-362, inspected at
`6722fbb64ec648da9c6955d9ac5eb1165f4ccb6d`, adds only its handoff design document: no present file
collision. Its local MCP/app credentials must never serve as remote-install, invite or approval
authority. Keep its proposed `/desktop/mcp` and connect routes remotely denied. Coordinate any later
Linux settings/window edits with its owner before slices 4/5; do not merge both UI flows implicitly.

## 9. Recorded decisions, remaining gates and exclusions

- **R1 RESOLVED — A, home-network access.** Founder seq499, EM seq500 and Product seq501 select local
  Headscale/local root minting now. No admin key stored anywhere. Seq502–504's earlier manual-key
  test proposal is superseded by seq526/529 below; A and its no-input contract remain unchanged.
- **Production target, deferred:** HappyRanch Services sign-in → user token → authorized service-side
  join-key issuance; Headscale admin credential remains service-side. Preserve the versioned invite
  and host-approval journey. No Services account/infrastructure dependency for this milestone.
- **Rejected for this milestone:** B (full Headscale admin key on Linux) and C (new scoped issuer
  beside the operator test server). No B bootstrap/expiry/replace flow remains. NOTE N2 is removed with B:
  a full admin key captured by a same-UID agent would permit direct Headscale administration until
  server expiry/revocation, regardless of fixed-helper restrictions. No such key is proposed here.
- **R2 APPROVED — founder seq508, as explained in seq506:** fixed bus-helper lifecycle, approval
  protocol/store version, transactional failed-install replacement/purge, fixed root **route-policy
  renewal** timer (identical still-shipped policy, no inputs, fail closed with paused + Retry), and
  slice 5's saved Mac Keychain/restart/logout/revocation/lost-delivery requirements. No automatic
  key renewal. No further founder confirmation of R2 is needed. The concrete unsigned ACL/update/
  signed migration and revised trust mechanism in §6 still require security/Product review and
  implementation qualification; R2 approval is not acceptance evidence or production migration.
- **Prerequisites MERGED:** packaging PR #1568 at `1a8efca4d5d7903701276d97ee6f66d1d5b85bdf` and
  upgrade-reload PR #1569 at `e1ff752dc59a0f953f6c5784f6727738335d2fcf`. Installing them and
  fresh/failed-install app acceptance remain separate, unperformed operations here.
- **Round-7 trust recommendation — pending, not approval:** §6 B qualifies an in-scope pinned-v1.78.0
  mechanism: strict same-process home-only upstream TLS for `/key`, Noise underlay and DERP,
  re-presented through an ephemeral local issuer/CONNECT proxy on Linux and unsigned Mac. Retain
  fixed DNS/v2 `dial_ip`, private DERP and v1 re-pair policy. TASK-16733 F1 is addressed at Linux
  transport-spike/design level; native Mac/full lifecycle acceptance remains NOT RUN. Renew
  security/Product review and approve affected §8 cases before implementation, including #1575's
  replacement design. R2 stays approved; the custom-server option is removed. No changes to #1575/#1573 in this PR.
  **A is only a conditional founder rescope**, accepting matching-name public-CA authentication;
  no such approval exists. Upgrade/`ts_omit_bakedroots` and fork/patch alternatives remain
  founder-reserved with costs above. No TLS bypass, dependency upgrade, host entry, new
  infrastructure or hardening exception is selected.
- **R3 FOUNDER-RESERVED — advanced root intake (historical; SUPERSEDED 2026-11-17 by
  founder seq526/529):** the earlier URL/key-prompt proposal required an exception to the
  no-caller-chosen-inputs contract and was never approved or implemented. It is removed, not
  awaiting approval; no own-server/custom-server input path is planned. The no-input principle
  stands and G1 remains open for fixed-server test-key delivery.
- **Fixed operator server ruling — 2026-11-17, founder seq526/529:** no custom-server option,
  URL field or key input; one fixed operator test server now, production later. Seq529 retains
  the existing test IP and restricts test Headscale to the dev team. No identity is published here.
  Build/private configuration is the sole server source; public releases cannot select or reach
  test. Production Services join-key issuance remains deferred. **G1–G3 are open, not decided** (§6).
  Product seq531's device/peer/relay/build-separation controls and outsider/removed-device acceptance
  are a plan, not shipped evidence. Every host step is **founder-run, pending founder decision
  (seq530)**; no host/VM operation follows from this amendment.
- **Not selected; founder-only if reopened:** dropping separate UID/system services, Headscale-only
  authorization, weaker hardening/verifier, network/agent containment, public relay/control fallback,
  connector-daemon merge/plain daemon socket, Go rewrite, connector LAN ingress, independent device
  crypto, arbitrary custom-home authority or other new top-level dependencies. Headscale bundling
  is the explicitly selected local-mode dependency; no additional dependency is authorized here.

This docs-only amendment changes the spec and its three companion docs. Behavior, hosted M1/M2, native dialogs, Mac acceptance and
founder-host failed-install replacement are **NOT RUN**. Return the revised flow and independent
security findings for Product review before finalizing; source review never establishes acceptance.

### THR-327 F2: read-only Linux host firewall advisory

Firewall evidence never vetoes Enable. No firewall inspection runs in Enable
preflight or before setup effects. After the lifecycle outcome is settled, the
existing fixed child may supply optional display advice within the original
deadline: only when more than six seconds remain, allowing at most two seconds
for advice and preserving the remaining terminal/auth budget. Read errors
produce `unverified`; insufficient remaining budget omits the observation.
Neither advice nor its failure changes the settled outcome, authorization,
audit, receipts, publication, setup READY or runtime availability.

The fixed C-locale `/usr/sbin/ufw status verbose` observation has three named
advisory categories:

- `likely_blocked`: recognized active UFW, default deny incoming, neither an
  IPv4 user allow for 8443/tcp nor one for 3478/udp, and stock IPv4 before/after
  templates. This is a probability: live INPUT allows from initialization or
  other owners can exist even when both files are stock.
- `clear`: UFW is inactive, or recognized user allows cover both ports with
  stock templates. This says nothing about end-to-end connectivity.
- `unverified`: any active customization, unreadable/unknown evidence, partial
  allow, default allow/reject without both user allows, command failure,
  rate limit, unknown profile/interface, malformed output or timeout.

LAN-scoped allows and port lists/ranges count as possible allows; IPv6-only
allows do not cover the IPv4 home dial address. Fixed rule-file reads are
read-only, no-follow, regular-file-only and limited to 64 KiB each. Any change,
including harmless customizations, symlinks, missing files or oversized and
non-ASCII content, downgrades active observations to `unverified`. Stock hashes
never establish live rules. No iptables/nft execution or new capability is used.
Missing UFW supplies no observation; firewalld/plain nftables are not inferred.

StatusV2's optional `firewall` observation uses `state` and `age_seconds`, with
`likely_blocked|unverified|clear` plus internal `unknown|stale` freshness states.
Unknown is omitted. Status advice reserves 300 ms of the original two-second
response deadline, leaving 200 ms before the unchanged 1.9-second watchdog. It
is skipped when the resulting advice budget is at most 100 ms. Advice timeout
or error omits the field and still returns the collected V2 observations; it
never extends the original deadline. Frames without the field and original
Status v1 retain compatibility. Fresh observations (at most 30 seconds old)
project to the existing optional native host `firewallWarning`. Enable's settled terminal and
successful native/CLI result may carry the same optional closed advisory.
Absent advice never substitutes for setup/effect evidence. `clear` is carried
without warning text; English and Simplified Chinese host views and CLI say
that the **home computer's firewall probably blocks** the home ports for
`likely_blocked`, and tell the user to allow **8443/tcp and 3478/udp from the
home LAN only**, then check Status. Unverified advice is conditional on failed
connections. No rule contents, addresses, paths or stderr cross the boundary.

The remote client snapshot does not carry host firewall Status. Replacing the
client's generic home-network-unavailable explanation therefore needs a new
protocol field and remains follow-up; this change adds none to that snapshot.

The F2 inspector never changes firewall rules. Founder THR-327 seq722 authorizes
only the F3 effects and exact lock-file sandbox carve-out below. Service user,
capabilities, R2 custody, home-only trust and authentication/authorization
remain unchanged.
Installed-package/UFW and cross-device qualification remain separate from the
standalone temporary-file/native-simulation venue.

### THR-327 F3: owned home LAN UFW rules

Founder THR-327 seq722 authorizes the root helper's Enable, after joined
activation, to share F2's bounded UFW observation and add/adopt exactly two
allow specifications on installed, active UFW: from the selected home LAN
subnet to the Headscale dial IP, 8443/tcp and 3478/udp, each commented
`happyranch-home-headscale`. No caller supplies addresses, prefixes, paths,
commands or flags. The kernel prefix is retained for the same interface/address
chosen by `select_lan_dial_ip`. That selector stays IPv4/RFC1918; F3 adds no
IPv6 selection. The exact unique network must contain the dial IP, be private
RFC1918/ULA, and be no wider than /16. Missing/ambiguous, public or wider
prefixes skip the effect and retain F2 advice. Absent/inactive UFW is a no-op.
Firewalld and raw nftables receive F2 advice only: no F3 mutation through either.

The packaged root helper retains `ProtectSystem=strict`, `ProtectHome=read-only`,
`PrivateTmp=yes` and `NoNewPrivileges=yes`. Its only F3 sandbox carve-out is
`ReadWritePaths=-/run/ufw.lock`: UFW must write its own lock even to inspect rules.
The leading `-` skips a missing file at unit start; it grants no writable `/run`
directory. The package ships `/usr/lib/tmpfiles.d/happyranch-ufw-lock.conf`
containing only `f /run/ufw.lock 0644 root root -`, for systemd-tmpfiles to
create the empty lock at boot. This matches UFW's lock metadata and creates no
rule. F3 adds no maintainer script, install-time tmpfiles invocation or helper
restart. The existing configure-only postinst from the connector upgrade fix
reloads the system manager; DEBIAN contains `control`, `md5sums` and `postinst`.
Distro triggers and D-Bus activation retain their existing behavior.
If install/upgrade leaves the lock unavailable in the helper namespace, reboot
once and press Turn on again. The worker checks the existing lock without
creating it before any rule effect. If missing or read-only, both ports record
`skipped_ufw_lock_unavailable` ("skipped: ufw lock unavailable") in the journal
and Status. Enable can complete without firewall effects; Turn off has no F3
rule to remove. Repeating Enable after lock access returns can acquire the rules.
Previously acquired or uncertain ownership never becomes a skip: unavailable
cleanup remains uncertain with its intent retained. UFW startup security checks
remain enabled against the fixed `/usr/sbin/ufw` CLI path before its frontend is
constructed. No directory-wide grant, out-of-sandbox UFW execution, capability,
user or dependency change is permitted by this carve-out.

UFW's `UFWRule.match` is authoritative: 0 adopts our tagged exact allow; -2
leaves the existing differently tagged/untagged allow untouched; -1 leaves a
different action/log collision untouched and reports it as blocking. Any
collision vetoes add, even alongside an exact owned rule. Only no-match adds.
The fixed system-Python worker holds UFW's POSIX lock across fresh inspection
and execution of `/usr/sbin/ufw` with fixed-action argv in the same process,
closing the inspection/add race. UFW already supplies its Python modules; no
new dependency or privilege model is added. No shell, ufw enable/disable/reset,
insert, reorder or separate reload is issued.
UFW imports and CLI output use a binary-capable capture, including its
import-time stdout binding. Success messages such as `Rule added` cannot
prefix the worker's single strict JSON reply or turn a completed add into an
unverified activation. Command failures still refuse success and retain the
existing rollback/recovery behavior.

The existing protected activation journal has an optional nonsecret `firewall`
record: exact subnet and two ordered port states. Older journals omit it and
retain their byte encoding, effect order and recovery behavior. Each add/remove
intent is durable before its command; completion follows the result. Existing
local activation/Turn off audit shapes bracket each port with fixed nonsecret
subjects. A lost add completion retains enough evidence to adopt the tagged
rule on retry or remove it on cleanup. B7 preserves the record; a generation
rollover cleans the old plan first. Failed-Enable rollback and Turn off
cleanup delete only an allow with our comment AND the exact source,
destination, protocol and port. Missing owned rules are idempotent. Uncertain
cleanup retains its intent and refuses successful cleanup. Founder hand-added
rules, including identical untagged allows and denies, are never removed.
Unchanged repeated Enable leaves settled journal/audit bytes untouched. If UFW
later becomes inactive, ownership stays recorded for Turn off cleanup.

For an already-READY upgrade, read Status, quit the app, reinstall the new deb
with `sudo apt-get install -y --reinstall ./happyranch_0.1.0-1_amd64.deb`, reopen,
wait 15 seconds, read Status, then press Turn on again once. If that press is
refused with `ready-restart-failed-turn-off` after Status read failed/offline,
wait 30 seconds and confirm Status is ready/available. Then press Turn on again
once more: expect `enableCompleted` and both tagged LAN rules. The first refused
press must leave no untracked firewall effect; any retained effect must be
journaled and removable by Turn off. This is the known pre-existing N3/N5 case;
F3 does not repair it. A second refusal on a ready/available host is a stop
condition. Do not add a manual daemon-reload or service restart. F3 upgrade
acceptance uses this procedure; an identical archive
reinstall does not model replacement by a different build's unit-file mtimes.

StatusV2 optionally carries `firewall.ports`; native/CLI Status projects it as
`firewallPorts`, exactly `8443/tcp` and `3478/udp`. States are `added`,
`preexisting_not_owned` (already allowed by your existing rule),
`blocked_by_existing` (your existing rule blocks this port),
`skipped_ufw_absent`, `skipped_ufw_inactive`, `skipped_ufw_lock_unavailable`,
`skipped_subnet_rejected`, plus
`pending`, `add_intent`, `remove_intent`, `removed`, `unverified` during recovery.
These are recorded activation effects read from the protected journal in the
existing setup-status child. They survive a skipped or timed-out F2 advice
probe without an additional UFW child. They do not assert current live-rule presence,
packet reachability or setup readiness. Enable and cleanup verify live rules.
F2 advice and the original Status deadline remain independent; observation
failure never erases other facts. Frames without this extension remain valid.

Replace failed remains installer-only and refuses home activation before
admission; that path cannot acquire F3 rules. Use Turn off for retained F3
activation effects. This preserves the existing R2 compatibility contract.
