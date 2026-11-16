# One-click remote access and one invite (THR-327)

Status: **design proposal; no implementation or host change authorized by this file**. TASK-16631,
founder seq487. Baseline: `9a6844b61df3b64d3f6627e7b0e53c0872a3438f`. Authority: seq421 chose
TASK-16298 revision 2's middle option: separate `happyranch` system user, system units and all
hardening, with desktop buttons behind fresh polkit authentication. TASK-16362's C1/D improvements
are approved in principle. This proposal makes them part of pairing; C2 and the Go rewrite remain
deferred. Founder-reserved questions below are unresolved gates.

## 1. Outcome, assumptions and current seams

Linux: **Turn on remote access** → fresh polkit system-password dialog → generated configuration,
enrolled sidecar, verified release daemon, ready to invite. **Invite a device** → its own fresh
polkit dialog → one copyable code/QR; explain first that Linux approval will also be required.
Mac: **Connect** accepts that invite, joins and pairs, waits for Linux approval, then shows
**Connected**. **Approve device** on Linux raises another fresh polkit dialog showing the actual
redeemed device, target runtime, stable-ID fingerprint and comparison phrase also shown on Mac
(TASK-16298/seq419). **Turn off remote access** and **Revoke device** each raise fresh polkit dialogs
at those actions. No authentication grants later actions. Turn off removes this installation's
remote-access configuration and authority. Everyday desktop setup needs no JSON or terminal/sudo
commands; B's separate server-owner bootstrap prerequisite is described in §9.

Assumptions: one release owner per Linux installation; Headscale/private DERP remains the control
plane (R1 selects Linux-local control or the existing remote deployment); a healthy packaged release
daemon is available. The root helper derives
the owner from authenticated bus credentials, never an app-supplied username. It uses a validated
existing root-owned release record, otherwise passwd-home + `.local/share/happyranch`. An arbitrary
custom XDG home cannot be accepted from the desktop environment; unsupported locations fail visibly
pending an approved protected binding flow. The founder-host default `.deb` layout is supported; DEV
8765/`~/.happyranch` is always excluded. Linger remains a separate opt-in; remote enable never sets
it.

Read before drafting: TASK-16298 `design.md` rev2; TASK-16362 `architecture-review.md`; the [current
contract](../../agent-guides/contracts/remote-access-and-remote-jobs.md) and
[RUNBOOK](../../../deploy/remote-access/RUNBOOK.md). Baseline code facts:

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
driving agents. Both home services keep the same UID: compromise of either is within that trust
boundary; this proposal does not claim isolation between those two services.

Keep every existing connector/sidecar system hardening directive, including `NoNewPrivileges`, empty
capability/ambient sets, `ProtectSystem=strict`, `ProtectHome=yes`, private tmp/devices,
kernel/control-group protections, address-family restrictions, SUID/realtime/personality
restrictions, `MemoryDenyWriteExecute`, native syscall architecture and `UMask=0077`. Keep
notify/watchdog, restart/start-limit and target/bind/uphold relationships. The verifier retains its
separate existing root capability budget and read-only home/proc proof. No user-mode substitution,
broad filesystem access, new connector LAN listener or weakening for Python is permitted. Item D (bundled
Python PT_GNU_STACK/MDWE) is an **external prerequisite in the parallel packaging PR**; this spec
neither designs nor edits it.

## 3. Fixed helper and fresh authorization

Proposed root mechanism: system-bus activated `ai.happyranch.RemoteAccess1`, fixed object
`/ai/happyranch/RemoteAccess1`, packaged helper at
`/opt/happyranch-desktop/bin/happyranch-remote-access-helper`. It runs no shell, imports no user
Python/config and accepts no command-line settings. Bus/polkit uses system Python `-I` and
already-required `python3-gi`/Gio; installer work uses only the fixed bundled Python `-I` entry, not
user site packages. A new dependency needs approval. D-Bus methods below have **zero input
arguments**; reject extra arguments, FDs, environment overrides and unknown methods before effects.
This is the polkit variant of the middle option; no second pkexec dialog.

| Exact method / polkit action suffix | Effect |
| --- | --- |
| `Status()` / none | Bounded redacted phase, readiness, device summaries and selected device; no secrets or mutation. |
| `Enable()` / `enable` | Validate package/owner, generate fixed configs, enroll home, install/reload/start exact system units, verify READY. Already-ready is inspect-only. |
| `ReplaceFailed()` / `replace-failed` | Replace only a recognized failed/partial installation, with the explicit loss-of-pairings warning and transaction below. |
| `Disable()` / `disable` | Stop admission, revoke devices, stop/remove owned remote units/config/state/credentials; never stop/delete the release daemon or package. |
| `IssueInvite()` / `issue-invite` | Invalidate previous pending invite; obtain one one-use join key and fresh pairing secret; deliver once to this bus caller. |
| `ApprovePending()` / `approve-pending` | Approve the sole redeemed pending identity captured before authentication, then atomically pin and issue its credential. |
| `RejectPending()` / `reject-pending` | Permanently reject that captured pending identity and consume its invite. |
| `NextDevice()` / none | Cycle a helper-owned per-bus-caller cursor over the protected device list; select nothing supplied by the caller. |
| `RevokeSelected()` / `revoke-selected` | Revoke only the selected protected record captured before authentication; close that device's live streams. |

Action IDs are exactly `ai.happyranch.remote-access.<suffix>` for authenticated methods. Defaults:
`allow_any=no`, `allow_inactive=no`, `allow_active=auth_admin`; never `_keep`, wildcard rules,
retained capabilities or general systemctl grants. Root calls `CheckAuthorization` on the actual
unique system-bus sender, with interaction allowed only for an explicit mutation. No bearer-based
shortcut. Enable, invite issuance, redeemed-device approval, turn off and per-device revoke each
authenticate at their own action; replacement and rejection also use their listed fresh-auth actions.
Policy/message files are root-owned package files. The helper supplies trusted
`polkit.message` details: verb, fixed control host/release owner, replacement effects, or actual
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
| `/etc/happyranch/{connector,sidecar,policy}.json` | root:hr 0640 under root:root 0755; helper generates IDs, socket settings, fixed private control URL and bundled deny-by-default route artifact. No ExpectedPeer in the new Linux profile. |
| `/etc/happyranch/{provision,install-transaction}.json` | root:root 0600; helper records layout/version/digests/phase, not secrets. No arbitrary path manifest. |
| `/etc/happyranch/release-daemon.json` | root:root 0644; exact existing verifier fields, derived owner/home and packaged fragment. |
| `/etc/happyranch/daemon.token` | hr:hr 0600; helper copies only the validated fixed release-home token; existing file provider, no change to daemon bearer custody. |
| `/etc/happyranch/enrollment.key`, reserved `/etc/credstore/happyranch-tsnet-enrollment` link | root:root 0600 source and existing exact-target link; helper stages home-only key, existing LoadCredential handoff deletes source/link after durable enrollment/READY. |
| `/etc/happyranch/invite-issuer.json`, `invite-issuer.credential` | root:root 0600 under protected parent; R1-dependent local Headscale binding, full Headscale API key (B) or scoped machine credential/trust anchor (C). Zero-input bus methods never carry secret bytes; B's separately reserved fixed entry path and transient exposure are described in §9. |
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
No key is renewed automatically. This route-policy renewal mechanism remains R2-reserved.

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
    participant I as Approved join issuer (reserved)
    participant M as Mac client
    L->>H: IssueInvite() + fresh admin authentication
    H->>I: Fixed home, single-use join, <=5 minute expiry
    H->>C: Persist invite digest (private root-peer IPC)
    H-->>L: One code + QR (one delivery)
    L-->>M: User transfers invite
    M->>I: Join Headscale with one-use pre-auth key
    M->>C: POST /pair/invite (secret); sidecar attaches WhoIs identity
    C-->>M: Pending + private completion handle + comparison phrase
    L->>H: ApprovePending()
    H->>H: Fresh polkit dialog shows actual node + matching phrase
    H->>C: Approve exact captured pending generation/node
    C-->>M: Credential after durable pin/epoch commit
    M->>C: Credential + ordinary request (same pinned node)
    C-->>M: Allowed product response; Mac shows Connected
```

Redeem atomically consumes the invite into `pending-approval`, binds the actual WhoIs identity, and
creates a random completion handle; it does **not** authorize product routes. Linux shows the
escaped WhoIs device label, control-plane label, stable-ID fingerprint and comparison phrase also
shown on Mac. Name alone is insufficient. Approval/rejection operates only on that immutable pending
tuple. Approval commits pin + credential digest + monotonic epoch before delivery; completion is
one-use, same-node and handle-bound. Lost credential delivery after successful network join consumes
the invite: Mac explains **“Authorization did not finish. Create a new invite on Linux”**. The user
re-issues an invite and repeats Linux approval; restart burns unfinished pending/delivery state.
Reuse a still-valid, same-node persisted network membership with empty-key reopen only for joining,
never to recover the spent invite or old grant. The issuer removes abandoned partial nodes, or
records pending operator cleanup if unavailable; valid same-node membership may instead be reused
for the new ceremony. Race/replay/expiry/store-corruption cases deny, never restore an invitation.

Every HTTP request, WebSocket upgrade and SSE admission loads current anchored trust, verifies the
per-device credential digest/epoch/expiry/revocation **and** exact control-plane/stable-node pin;
then applies unchanged route/readiness and release-verifier gates. No other paired device is
displaced. Stream registrations carry internal device ID and epoch; per-device revoke persists
invalidation and closes that device's existing streams before reporting success, preserving the
existing incomplete-revocation failure semantics. Other devices stay connected. Ordinary node-key
rotation keeps StableID; re-enrollment/new StableID needs fresh approval. Never auto-repin from
matching hostname, address or old credential. Existing unpinned managed devices become
`re-pair-required`, never grandfathered.

## 6. Invite, issuer dependency and secret handling

One encoded string, `hrinvite1:` + base64url of strict JSON (≤1500 bytes decoded,
≤2048 bytes encoded including prefix, one QR symbol or issuance refuses): `v`, `control_url`,
`control_plane_id`, `home_endpoint`, `home_id`, `connector_id`, `invite_id` (128 random bits),
`join_key`, `pair_secret` (256 random bits), `issued_at`, `expires_at`. QR contains exactly that
string; no short lookup code, web redirect, public paste service or separate key/code entry. Fields
are untrusted until validated; HTTPS control URL, no userinfo/query/fragment, fixed tailnet port
443, supported version, exact fields and expiry ≤5 minutes. Mac shows the target control host before
connecting; invite receipt is not an independent proof of an honest home/control plane. No
end-to-end trust-root claim beyond the shipped tailnet model is introduced.

The join key is non-reusable, non-ephemeral and expires with the invitation; Headscale enforces its
one registration. Connector's existing cross-process exclusive transaction enforces one redemption
of the secret digest and one approval/credential issuance. At most one active invite/pending attempt
per home; issuing another cancels the old, with remote-key expiration best-effort recorded. Persist
digests/deadlines/state, never raw secrets. Join-key issuance uses a helper-generated idempotency
ID; uncertain issuer response is not retried with a new ID. Expiry/deny/restart consumes locally
even if remote cleanup is unavailable. An unused key may join until expiry after cancellation, but
cannot pair; a successful join does not expire the node, so rejected/unpaired nodes must be removed
by the issuer, or reported as pending operator cleanup. No claim of instant network revocation when
Headscale is unreachable.

**Missing shipped capability:** the RUNBOOK uses operator Headscale commands; there is no protected
desktop join-key issuer or machine-entitlement bootstrap. **FOUNDER-RESERVED R1** in §9 compares
local home-network minting (A), a root-only full Headscale API key on Linux (B), and a scoped issuer
beside remote Headscale (C). No option is selected here. All require fixed tenant/control/tags/TTL,
separate home-enroll and device-invite purposes, idempotent issuance/expire/cleanup and no general
admin API exposed to agents or devices. A mints locally as root; B uses the protected API key;
C keeps Headscale admin material server-side and must enforce scope itself, not assume its current
API credential is narrowly scoped. Remote issuer transport uses pinned HTTPS/mTLS. The helper
generates the pairing secret itself; asks the connector to persist only its digest; returns the
combined invite to the initiating desktop caller only after fresh authentication. Agents get no
unauthenticated mint API or access to the stored issuer credential; B's transient key-entry exposure
is explicitly disclosed in §9.

R1 must also choose the option's authenticated GUI bootstrap/recovery: local setup for A, the
separate server-owner bootstrap and fixed helper key-entry path for B (§9), or machine entitlement
with fresh human server-side approval for C (never a reusable browser session or silently approved
code). Zero-input app IPC carries no issuer secret. B does not promise secrecy from founder-UID
agents during user-owned desktop/clipboard entry. Endpoint/trust metadata is packaged or
operator-provisioned root-only, never caller-selected. **Until the founder picks and approves that
provisioning mechanism, initial enable/invite is gated; fresh no-terminal desktop setup depends on
completed server-owner bootstrap.** Shipping admin/reusable join keys in the app/package or an
agent-readable key pool does not solve this gap. Everyday desktop setup cannot require terminal work.

Raw invite/join/pair secrets, completion handles, device bearers, daemon token, issuer credentials,
private keys and their request/response bodies never enter argv, env, URLs, journal, analytics,
crash breadcrumbs or diagnostic exports. Disable HTTP body/header logging for pairing and broker
transport; use closed error codes, not raw library exceptions. Diagnostic redaction covers
structured fields and known prefixes with synthetic canaries, including malformed inputs. No secret
hashes/prefixes or public node keys in public logs. Device fingerprints are privileged approval UI
only. Keep the existing no-logtail/private-DERP policy. Desktop/Mac drafts are memory-only, no state
restoration; clear on completion, expiry/cancel. Clipboard copying is explicit and best-effort
cleared on expiry; screenshots/clipboard history/agent memory reads remain a disclosed residual.
The current Mac device bearer is memory-only in `ClientBridge.swift`; relaunch requires re-pairing.
`EnrollmentKeyStore.swift` uses Keychain only to stage/delete join-key material, not persist the
device bearer. **Proposed saved-device custody, R2-reserved / slice 5:** after durable Linux approval
and credential delivery, save the bearer in a separate non-synchronizing Keychain item scoped to the
signed HappyRanch app, with app-restricted access, no shared access group or plaintext fallback.
Bind its lookup to the saved home/control-plane and stable-node identity; never select by label.
Locked/unavailable storage fails visibly with unlock/retry, never silently loses a saved credential.
For a still-valid saved grant, app restart reopens the same owner-only tailnet state with no join key,
loads that item, rechecks the pin and reaches the authorized runtime without a new invite. Disconnect retains the saved item;
logout or removal of the saved computer closes the session and clears both Keychain and memory,
reporting deletion failure rather than claiming removal. Linux per-device revocation or turn off
invalidates the bearer immediately at admission and closes streams even if the Mac is offline;
the Mac clears its saved item on learning of revocation. Local erasure alone is not Linux revocation.
Keychain custody does not defeat a compromised unlocked app/session; secrets stay out of diagnostics.
These are new requirements, not current behavior; R2 must approve them before slice 5 ships.

## 7. Mac, installation recovery and upgrades

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

Enable first inventories only fixed managed destinations, exact unit fragments, drop-ins and package
files, with no effects on unknown objects. Successful modern records resume idempotently; existing
healthy manual installs require explicit migration, never infer failure from a stopped unit. For
FAILED/partial manual installs under `/etc/happyranch`, `/opt/happyranch` and the fixed service
state paths, `ReplaceFailed()` offers one truthful destructive replacement prompt. Recognize only
bounded known prior unit/config shapes + safe ownership or valid provisioning provenance; never
execute old config/argv. Stop target, both services and verifier; prove quiescence before changing
files. Journal intentions and fsync before each fixed-path rename/removal; retain root-only
quarantined old configs briefly for diagnosis, never reuse keys, trust or old control settings.
Generate fresh IDs/config/enrollment and require re-pairing. Never recursively remove `/opt`, the
dpkg prefix, foreign files, custom units or unknown drop-ins. Unsafe/unrecognized residue produces
`manual-install-conflict` with precise non-secret paths; it needs operator recovery, not a terminal
recipe for the user. Interrupted replacement stays off; next authenticated ReplaceFailed resumes
only the exact journal generation. Do not roll back revoked credentials into service.

Disable closes admission/revokes first, confirms both services/verifier stopped, then removes only
positively owned units, configs, enrollment sources/links, socket/runtime files and secret/trust
state; reloads systemd and confirms absence. Remote issuer cleanup is tracked as pending if offline.
Remove a helper-created service account only when no other installation/files/processes use it;
otherwise retain the locked nologin account and say so. Keep package/helper, non-secret audit and
unrelated data; destroy quarantined secrets after successful replacement or disable. Any
stop/ownership uncertainty aborts removal and reports partial state.

Upgrades preserve protected installation IDs/pins and revocations, validate the old version, stop
admission, migrate under the same trust lock, reload exact system units and verify loaded
fragments/drop-ins/READY before reopening. Unknown or torn state refuses; no trust reset. V1
unpinned records require re-pairing. Refresh a rotated daemon token only from the recorded validated
release source after explicit authenticated Enable/repair, never a new app-selected path.
[#1562](https://github.com/t-benze/happyranch/issues/1562): before normal desktop start, detect
`NeedDaemonReload=yes`, run own-user daemon-reload once, reread and show a bounded error/retry if it
fails. Existing restart-only reload is insufficient. Never attach an old/unverified daemon or poll
“starting” indefinitely; no root iteration over arbitrary user managers. Coordinate this user-unit
startup change with the parallel `build-deb.sh` work; no packaging edit in this design PR.

## 8. Sliced delivery and evidence (implementation requires approved briefs)

Dependency order is 0 → 1 → 2 → 3a → 3b → 3c → 4 → 5 → 6; R1 may be designed alongside 1/2, but production
enable/invites cannot ship before it is approved and provisioned. No new required CI check or
automatic M1/M2 trigger; use existing authorized manual hosted acceptance. Every implementation
slice needs independent review, scoped QA, normal hooks and the two required lint/static checks at
its exact head.

| Slice | Main file ownership / dependency | Verification required |
| --- | --- | --- |
| 0 prerequisite | Parallel Python PT_GNU_STACK/MDWE PR; no design here. R1/R2 decisions below. | Owner's packaged runtime evidence; never remove MDWE to pass. |
| 1 protected ingress | `app/linux/tsnet-sidecar/{sidecar,engine_tsnet,health}.go`, `runtime/remote_access/managed_provider.py`, `systemd_unit.py`; C1/D frame and explicit new Linux profile. | Supported Go tests and real temporary Unix sockets: forged HTTP identity, wrong UID, malformed frame, absent WhoIs; empty-peer READY and loss/watchdog. Native Mac hosting profile unchanged. |
| 2 trust ceremony | `pairing.py`, `authorization.py`, `state_store.py`, request/stream admission; no desktop or installer edits. | Retained standalone connector venue with synthetic state: two concurrent redeemers/approvals → exactly one grant; wrong node with valid bearer denied; A revoked closes A streams while B works; restart/torn/rollback/legacy cases refuse. |
| 3a fixed helper | New helper module, system-bus/polkit definitions; status and guarded approval/revoke, no installation yet. | Standalone IPC plus disposable polkit: extra args refused, agent can prompt but not silently approve, actual target immutable across auth, fresh auth twice, wrong-UID and disconnect refusal. |
| 3b generation/install | `installer.py` `_input()`/`_read_file()` source-UID/same-FD validation, generated config/transaction owner, package/helper wiring; after 3a and parallel packaging PR. Coordinate renderer after 1; R1-B adds fixed key entry/replacement/removal. | Temp-root/fake-manager clean/known FAILED/partial/foreign fixtures, foreign-owned 0600 token and parent/leaf swap or metadata-change refusal on enable/repair/replacement; safe `.deb` payload access, stop failure, interrupted replacement/disable, token rotation and exact unit/hardening parity. If B selected: entry redaction, atomic replacement and removal/pending server expiry. |
| 3c invite issuance | R1-selected local mint/API client or scoped issuer client + connector private invite IPC; after approved/provisioned R1, 2 and 3b. New server source/deployment requires its own assigned-repository brief. | Synthetic issuer/idempotency/uncertain-response/cleanup tests, fresh-auth mint, QR size/round-trip, no standing user mint capability, redaction canaries and expired/replayed invitation refusal; B admin-key expiry refuses minting, with no automatic replacement. |
| 4 Linux UI/upgrade | `packaging/linux/desktop/happyranch_window.py`, package wiring only after parallel packaging PR; #1562. | Existing standalone desktop checks; synthetic reload-needed/failure/timeout; native GTK off/starting/ready/conflict/error/approval/revoke screenshots. Separate fresh polkit prompts at enable, invite, actual-device comparison/approval, turn off and revoke; route-policy renewal paused message + retry. B setup/replacement/expiry/removal states if selected. No host service actions for maker. |
| 5 Mac single invite | `app/mac/Sources/HappyRanchApp/{RemoteConnectionView,AppDelegate}.swift`, supervisor managed model/ClientBridge, `app/mac/tsnet-bridge` only as needed; #1563. Proposed app-scoped Keychain bearer custody requires R2 approval. | Supported Swift/Go checks, unsigned build, native join→pending→Connected and TLS-close/deny/expiry/cancel/retry. Record memory-only baseline; if R2 approved, verify save/restart without invite, locked Keychain, logout/removal, Linux revocation/turn off, join-success/delivery-loss fresh-invite recovery and partial-node cleanup/reuse; secret-free diagnostics. #1564 stays queued. |
| 6 hosted acceptance/docs | `scripts/ci/m1_install_ready_uninstall.sh`, `m2_tailnet_daemon.sh`, related fixtures/docs; builds on 1–5. | M1 uses helper-generated configs and no ExpectedPeer; clean plus known FAILED-manual fixture, exact hardening/READY/loaded units, disable residue, interrupted recovery and route-policy renewal failure closed. M2 keeps real random-port daemon/verifier and squatter negative; adds two dynamically named stable-ID devices, one combined invite each, race/replay/expiry/wrong-node/revoke-survivor and join-success/delivery-loss recovery cases. |

Hosted harness fixtures may provision a root-owned local test issuer and a test-side polkit
authentication agent on disposable runners; production helper has no test flag, bypass action or
alternate caller-supplied settings. Hosted authentication simulation is not proof of the real
desktop dialog; separate disposable desktop/native UI acceptance remains required. Retain existing
secret artifact filters, private pipes, source/runtime receipts and bounded teardown. No founder
host, live Headscale or production service is used. M3/M4 shared drivers need
alignment/requalification after these changes; their older receipts do not establish this protocol.
No Product E2E or retired Python/Web units here. Each affected test follows the test-authoring
evidence gate, using its supported venue and honest NOT RUN for unavailable native or sensitivity
evidence.

Doc parity in the owning implementation slices: current remote-access contract;
`deploy/remote-access/RUNBOOK.md` (replace operator steps for desktop users, keep historical server
provisioning separate); `app/linux/tsnet-sidecar/README.md`; `app/mac/README.md`;
`docs/runbooks/linux-release-handbook.md`; normative `2026-08-26-managed-remote-access-contract.md`;
M1/M2 workflow comments and M3/M4 runbooks; `docs/local-ci.md` for supported commands. No bundled
remote-access skill was found in the repository; inspect published skill consumers before
implementation and update any owning remote-install instructions alongside it. Do not change agent
permissions, jobs/jenkins skill authority or test cadence.

[PR #1555](https://github.com/t-benze/happyranch/pull/1555), THR-362, inspected at
`6722fbb64ec648da9c6955d9ac5eb1165f4ccb6d`, adds only its handoff design document: no present file
collision. Its local MCP/app credentials must never serve as remote-install, invite or approval
authority. Keep its proposed `/desktop/mcp` and connect routes remotely denied. Coordinate any later
Linux settings/window edits with its owner before slices 4/5; do not merge both UI flows implicitly.

## 9. FOUNDER-RESERVED gates and explicit exclusions

- **R1 — issuer choice and home bootstrap: FOUNDER-RESERVED.** EM asked the founder
  in THR-327 seq496; this document does not answer it. A's home-network topology
  was already founder-approved at seq354; choosing the product mode and its
  bootstrap/recovery remains reserved. No code proves a no-terminal fresh bootstrap today.

| Option | Custody, reach and agent threat model | Effect on slices |
| --- | --- | --- |
| A — home-network mode | Headscale on the Linux box gives local control of coordination; fixed helper mints locally as root. An already-approved client may retain cached peer keys/maps and its bearer and still reach the box while away through a reachable public WireGuard endpoint. Away operation is unsupported/unreliable, not prevented. Agents have no local issuer/admin access or silent mint authority; fresh authentication, actual-device approval and protected pins remain. Root compromise controls local Headscale. | 3b adds local Headscale setup; 3c uses root-local minting; 4/5 disclose the away-operation limit; 6 checks local operation and cached-client reach without asserting away denial. 0–2 and 3a are unaffected; no LAN enforcement is proposed. |
| B — root-only Headscale API key on Linux | Separate server-owner bootstrap creates an expiring full admin key; authenticated desktop intake places it in root custody (§9 details below). Helper mints single-use join keys valid for ≤5 minutes. Agents cannot read the stored copy or silently mint, but may observe entry through the user-owned app/clipboard. Linux root compromise grants full tailnet administration; this key is not scoped. Existing remote Headscale supports away access. | 3b adds fixed key entry/custody/replacement/removal; 3c uses the fixed Headscale API and handles admin-key expiry; 4 adds prerequisite/expiry/replacement/removal states; 6 checks stored-key isolation, mint bounds and failure/cleanup. 0–2, 3a's fresh-auth boundary and 5 are unaffected. |
| C — scoped issuer beside Headscale | New infrastructure on `neihoumacau-hk`; server term ends **2026-12-03**, so continuity/cost needs a founder decision. Linux holds only a scoped home entitlement; agents cannot read it or silently mint. Linux root compromise gets that scope; issuer/Headscale compromise retains tailnet-admin risk. Supports away access. | 3b adds entitlement/bootstrap custody; 3c needs the scoped service and its own infrastructure brief; 6 adds broker scope/idempotency/offline acceptance. 0–2, 3a, 4 and 5 are unaffected. |

**B remains an option, not the selection.** Its complete proposed lifecycle is:

- **Server-owner bootstrap, separate from everyday desktop setup:** on the user-owned Headscale
  server, its administrator runs `headscale apikeys create --expiration 90d` (illustrative finite
  lifetime from the [v0.25.1 remote-CLI guide](https://headscale.net/0.25.1/ref/remote-cli/)); the
  selected lifetime/expiry must be recorded and shown before acceptance. The output is displayed
  once. The Linux desktop user supplies this credential after server bootstrap; normal enable and
  invite creation thereafter need no server terminal commands. No HappyRanch-operated issuer is added.
- **Entry boundary:** the key passes through the user-owned desktop app / clipboard before root
  custody; an agent running as the founder can observe that transient value. Root-only storage
  protects the stored copy, not this path. The smallest proposed intake is the fixed helper reading
  its own secret-entry prompt after fresh operation-bound polkit authentication, directly through a
  private stdin pipe; never a user-owned file, argv/env, logged input or zero-input bus secret argument.
  Clear app/prompt buffers and best-effort clipboard state after handoff; history/capture may remain.
  R1 must explicitly approve this fixed entry path; it grants no general stdin/FD or IPC command API.
- **Expiry/replacement:** the admin key expires at its server-set deadline (90 days in the example),
  or earlier if expired by its owner; it is never auto-renewed. Show **“Headscale admin key expired.
  Replace it to create invites”** with a replace action; deny minting, never extend the deadline or
  reuse a key rejected by Headscale. Existing approved devices retain their bearer/pin until their
  own expiry/revocation; admin-key expiry alone does not revoke them. Replacement uses a newly created
  server key and the same fresh-polkit fixed intake, validates it against the protected control host,
  atomically replaces root custody, erases the old local copy and asks the server owner to expire the
  old key with `headscale apikeys expire --prefix "<PREFIX>"`; failed/uncertain replacement stays gated.
- **Removal/turn off:** fresh authenticated removal disables minting and erases the protected key;
  turn off also invalidates invites/device authority as specified in §7. Local deletion does not
  expire a server key. The server owner expires it; unavailable server cleanup remains visibly pending,
  never reported as complete. No automatic key renewal or replacement is authorized by the timer.

All options retain separate UID, hardening, verifier, credential-plus-pin checks and fresh auth.
R1 must approve the selected bootstrap/recovery and provisioning. B additionally needs the founder's
explicit exception to normative invariant 2(9), which currently prohibits home-held Headscale API
credentials, and matching normative parity in its owning implementation slice; no exception is made here.

- **R2 — additions beyond the seq421 install wrapper: FOUNDER-RESERVED.** Approve the specific
  fixed bus-helper lifecycle, approval protocol/store version, transactional
  failed-install replacement/purge semantics and fixed root-owned **route-policy renewal** timer
  (same shipped policy only, no inputs, fail closed with the paused message + retry; no automatic key renewal).
  C1/D are approved in principle; this document does not grant implementation or
  production migration authority. A no-input cursor is proposed to satisfy
  per-device revoke without caller-supplied peer IDs; native usability is unproven.
  The proposed app-scoped Keychain device-bearer store and restart/logout/removal/revocation and
  lost-delivery recovery behavior in §§5–7 also require this decision, in slice 5; none is shipped today.
- **Not selected; founder-only if reopened:** dropping the separate UID/system
  services, trusting Headscale alone, loosening any hardening/verifier proof,
  new network/agent containment, public relay/control fallback, connector-daemon
  merge/plain daemon socket, Go rewrite, connector LAN ingress, independent end-to-end
  device crypto, alternate custom-home authority or new top-level dependencies.
  None is necessary to the proposed approved-boundary path.

This spec PR changes one document only. Behavior, hosted M1/M2, native dialogs, Mac acceptance and
founder-host failed-install replacement are **NOT RUN**.
