# One-click remote access and one invite (THR-327)

Status: **design proposal; no implementation or host change authorized by this file**. TASK-16631,
founder seq487. Baseline: `9a6844b61df3b64d3f6627e7b0e53c0872a3438f`. Authority: seq421 chose
TASK-16298 revision 2's middle option: separate `happyranch` system user, system units and all
hardening, with desktop buttons behind fresh polkit authentication. TASK-16362's C1/D improvements
are approved in principle. This proposal makes them part of pairing; C2 and the Go rewrite remain
deferred. Round 4 records founder seq499/502: **R1 resolved to A, home-network access**; R2 and the
separate advanced-input R3 below remain FOUNDER-RESERVED. No implementation authority follows.

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
commands. Advanced **Use my own server** adds operator-issued key input (§6); its server-side
manual issuance is a test prerequisite, never an extra user pairing/terminal step in the apps.

Assumptions: one release owner per Linux installation; default Headscale/private DERP is Linux-local;
advanced mode uses the existing neihoumacau-hk deployment; a healthy packaged release daemon is
available. The root helper derives the owner from authenticated bus credentials, never an
app-supplied username. It uses a validated existing root-owned release record, otherwise passwd-home + `.local/share/happyranch`. An arbitrary
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
driving agents. Connector and sidecar keep the same UID: compromise of either is within that trust
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
This is the polkit variant of the middle option; no second pkexec dialog. R3 proposes only the
separately gated URL/key intake below; the default no-input contract is unchanged.

| Exact method / polkit action suffix | Effect |
| --- | --- |
| `Status()` / none | Bounded redacted phase, readiness, device summaries and selected device; no secrets or mutation. |
| `Enable()` / `enable` | Default home-network mode: derive LAN address, install/configure local Headscale/private DERP, locally mint Linux join key, enroll and verify exact system units/READY. Already-ready is inspect-only. |
| `RefreshHomeAddress()` / `refresh-home-address` | Re-derive LAN address from kernel inventory, renew the leaf/SAN/config with the same private CA, verify readiness; no supplied address or new CA accepted. |
| `EnableOwnServer()` / `enable-own-server` **R3-reserved** | Helper-owned URL/Linux join-key intake, then fresh auth bound to the displayed URL; enroll only, no mint capability. No local Headscale installed. |
| `ReplaceFailed()` / `replace-failed` | Replace only a recognized failed/partial installation, with the explicit loss-of-pairings warning and transaction below. |
| `Disable()` / `disable` | Stop admission, revoke devices, stop/remove owned remote units/config/state/credentials; never stop/delete the release daemon or package. |
| `IssueInvite()` / `issue-invite` | Home mode: locally mint one-use join key and pairing secret after fresh auth; invalidate old invite, deliver once. |
| `IssueOwnServerInvite()` / `issue-own-server-invite` **R3-reserved** | Intake a fresh operator-issued Mac join key for the protected server, fresh auth, then the same one-invite ceremony. |
| `RetryPolicyRenewal()` / `retry-policy-renewal` **R2-reserved** | Fresh auth retries only the fixed shipped-policy renewal; all readiness gates must pass before access resumes. |
| `ApprovePending()` / `approve-pending` | Approve the sole redeemed pending identity captured before authentication, then atomically pin and issue its credential. |
| `RejectPending()` / `reject-pending` | Permanently reject that captured pending identity and consume its invite. |
| `NextDevice()` / none | Cycle a helper-owned per-bus-caller cursor over the protected device list; select nothing supplied by the caller. |
| `RevokeSelected()` / `revoke-selected` | Revoke only the selected protected record captured before authentication; close that device's live streams. |

Action IDs are exactly `ai.happyranch.remote-access.<suffix>` for authenticated methods. Defaults:
`allow_any=no`, `allow_inactive=no`, `allow_active=auth_admin`; never `_keep`, wildcard rules,
retained capabilities or general systemctl grants. Root calls `CheckAuthorization` on the actual
unique system-bus sender, with interaction allowed only for an explicit mutation. No bearer-based
shortcut. Enable, invite issuance, redeemed-device approval, turn off and per-device revoke each
authenticate at their own action; replacement, rejection, address refresh and policy Retry also use
their listed fresh-auth actions. Advanced enable/invite authenticate after input validation, showing
the normalized server URL; a cancelled prompt discards the input without enrollment or invitation.
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
| `/etc/happyranch/{connector,sidecar,policy}.json` | root:hr 0640 under root:root 0755; helper generates IDs, socket settings, derived local or R3-authenticated protected control URL and bundled deny-by-default route artifact. No ExpectedPeer in the new Linux profile. |
| `/etc/happyranch/{provision,install-transaction}.json` | root:root 0600; helper records layout/version/digests/phase, not secrets. No arbitrary path manifest. |
| `/etc/happyranch/release-daemon.json` | root:root 0644; exact existing verifier fields, derived owner/home and packaged fragment. |
| `/etc/happyranch/daemon.token` | hr:hr 0600; helper copies only the validated fixed release-home token; existing file provider, no change to daemon bearer custody. |
| `/etc/happyranch/enrollment.key`, reserved `/etc/credstore/happyranch-tsnet-enrollment` link | root:root 0600 source and existing exact-target link; helper stages home-only key, existing LoadCredential handoff deletes source/link after durable enrollment/READY. |
| `/etc/happyranch/invite-issuer.json` | root:root 0600; mode, local Headscale binding/CA fingerprint or R3-authenticated remote URL/system trust. No API/admin credential, key pool or standing remote mint capability. |
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
authority would be the R2-reserved fixed root timer, with no inputs or IPC arguments; it cannot revive
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
    participant I as Local Headscale / supplied remote key
    participant M as Mac client
    L->>H: IssueInvite() + fresh admin authentication
    H->>I: Local mint OR R3 fresh manual key intake, never remote admin key
    H->>C: Persist invite digest (private root-peer IPC)
    H-->>L: One copyable invite (one delivery)
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
shown on Mac. Name alone is insufficient. Approval/rejection operates only on that immutable pending
tuple. Approval commits pin + credential digest + monotonic epoch before delivery; completion is
one-use, same-node and handle-bound. Lost credential delivery after successful network join consumes
the invite: Mac explains **“Authorization did not finish. Create a new invite on Linux”**. The user
re-issues an invite and repeats Linux approval; restart burns unfinished pending/delivery state.
Reuse a still-valid, same-node persisted network membership with empty-key reopen only for joining,
never to recover the spent invite or old grant. In home mode the helper removes abandoned partial
nodes locally. Advanced mode cannot delete nodes/expire keys remotely: record operator cleanup
pending. Each new Add a device still requires a fresh key, even if valid same-node membership is
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

One text string, `hrinvite1:` + base64url strict JSON, ≤4096 decoded / 5500 encoded bytes including
prefix; refuse oversize, never truncate. Exact fields: `v`, `mode`, `control_url`, `control_plane_id`,
`control_tls`, `home_endpoint`, `home_id`, `connector_id`, `invite_id` (128 random bits), `join_key`,
`pair_secret` (256 random bits), `issued_at`, `expires_at`. `control_tls` is a closed union: system
HTTPS trust, or local CA DER + SHA-256 fingerprint. No private key/admin token enters an invite.
Versioned mode/trust metadata supports later Services-issued keys in the same envelope/ceremony;
unknown versions/fields refuse. Text transfer is sufficient; QR display is deferred if this larger
certificate-bearing envelope cannot fit one symbol. No redirect/lookup service or separate pairing
code. The Mac validates HTTPS URL, no userinfo/query/fragment/path except `/`, fixed tailnet runtime
port 443, times and exact schema before connecting; shows the control host and release runtime.
Trusting an invite is trusting its sender for the target CA/home, not independent host attestation.

**Default: home-network access (R1 resolved, seq499/501).** Enable installs bundled Headscale,
creates the private control plane and locally mints the Linux node's ≤5-minute, single-use,
non-ephemeral key. Add a device mints an equivalent Mac key through the root helper's fixed local
admin socket operation; no Headscale admin/API key is created or stored anywhere in either app or on Linux. Headscale's
protected DB/socket are local administrative authority; root compromise controls this tailnet.
The helper creates the pairing secret; connector stores only its digest, then the freshly
polkit-authenticated initiating caller receives the complete invite once. No unauthenticated mint.

Address discovery is carried by the invite: helper reads kernel interfaces/routes, derives the
single usable default-route RFC1918 LAN IPv4 address and uses `https://<LAN-IP>:8443`. It shows that
address in Linux status; no user hostname/port input, mDNS daemon, router DNS or Internet lookup is
required. Multiple equally eligible/no LAN addresses refuse with a useful network-state error;
this milestone does not silently pick an interface or accept an app-selected address. The Mac
uses the URL from the invite, not tailnet DNS (unavailable before join). Saved devices retain it.

Local HTTPS uses a per-install private CA and an IP-SAN leaf, generated in protected helper custody.
Invite carries the CA DER and its SHA-256 certificate fingerprint; Mac verifies their agreement,
certificate validity, chain and URL IP SAN using that CA in an app/profile-scoped trust pool, plus
the saved fingerprint on every reconnect. Linux sidecar uses the same scoped CA. No OS-wide trust
installation, HTTP fallback, trust-on-first-network-contact, `InsecureSkipVerify` or TLS bypass.
This needs real control and private-DERP transport support in both pinned tsnet adapters; current
constructors set ControlURL only. Slice 1/5 must prove every control/relay TLS path uses the scoped
trust without disabling verification; if unavailable, stop for a separate feasibility decision,
never weaken TLS. Headscale supports configured cert/key paths ([TLS reference](https://headscale.net/0.25.1/ref/tls/)).

On address change, Linux shows **Home address changed — Refresh home address**; fresh auth runs
RefreshHomeAddress, re-derives address and issues an IP-SAN leaf under the same CA. Old invites are
invalidated. Linux creates a new invite; accepting it explicitly updates the saved endpoint only
for the same home/CA. With a valid saved grant this is metadata recovery only: do not redeem the
new pair secret or issue a credential; leave its unused key to local expiry/cleanup. Normal join and
Linux approval apply if the grant/node is no longer valid. Ordinary app
restart never needs an invite. Slice 1/5 must explicitly migrate the versioned protected control-URL
binding/consumed marker on both endpoints, preserving same-node identity; no blind marker rewrite
or hostname repin. If identity cannot be retained, require new enrollment/approval. Leaf renewal
uses the same authenticated refresh action and CA, needs no invite for unchanged address, and adds
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
Bind HTTPS/control + embedded private DERP only to the derived LAN IPv4:8443, STUN to that IP:3478/UDP;
metrics to 127.0.0.1:19090 and gRPC to 127.0.0.1:50443 with insecure mode off; no wildcard/IPv6/admin
LAN listener, ACME/80, router forwarding or firewall edits. Refuse occupied ports. Load only the
embedded private DERP region; empty public map URLs and no automatic public fallback/update checks.
Exact renderer/ports follow the [versioned config](https://raw.githubusercontent.com/juanfont/headscale/v0.25.1/config-example.yaml);
these are proposed bindings, not current host facts. WireGuard UDP underlay remains tsnet-managed,
may use wildcard/ephemeral sockets and reachable public endpoints; it never exposes plaintext HTTP.
Turn off stops/removes the owned Headscale unit/config/DB/CA/leaf/Noise/DERP state and local nodes;
keeps inert package payload/notices. Never remove an unrelated Headscale installation. Cleanup
uncertainty stays visible; release daemon/org data remain. Re-enable creates fresh authority.

**Advanced: Use my own server (seq502–504; R3 input contract reserved).** Operator keeps Headscale on
neihoumacau-hk and supplies its HTTPS URL and one ≤10-minute, single-use, non-ephemeral Linux join key
at Enable; no local Headscale. Each Add a device requires another such Mac key; helper places it in
the same invite with a fresh pairing secret. Invite lasts ≤5 minutes and never beyond the supplied
key's operator-declared deadline. Manual issuance is an explicit operator prerequisite outside the
app journey, using the server's admin console/authorized operator channel; no commands for users.
Linux has no admin key, entitlement or credential that can mint/expire keys/delete remote nodes.
Use system-verified HTTPS for this server and its private relay, no public relay fallback. Wrong,
expired or already-used keys give **Ask the server operator for a fresh single-use key**. If joining
consumes a key before authorization finishes, preserve only valid same-node membership, invalidate
the attempt and require a new invite/key and Linux approval as in §5; failed Linux enrollment with
no durable state requires a replacement Linux key. Unused superseded keys expire server-side;
abandoned remote nodes remain pending operator cleanup. Never claim Linux revoked their membership.

**R3 proposed intake, not an exception granted here.** EnableOwnServer/IssueOwnServerInvite retain
zero-input bus signatures; a fixed packaged helper-owned prompt collects URL/key only into private
memory, followed by fresh operation-bound polkit showing normalized URL/purpose/target generation.
URL is entered only at Enable; Add a device reads that protected URL and accepts key/deadline only.
Changing server requires authenticated Turn off/Enable; invite intake cannot retarget it.
Never accept app-owned files, argv/env, general stdin/FDs, caller config, shell or key-bearing bus
arguments. Reject duplicate/extra fields, controls/whitespace, oversized URL (>2048 bytes), non-HTTPS,
userinfo/query/fragment/non-root path. This advanced test accepts a DNS host on 443 resolving only
to public unicast addresses; pin validated resolution through the dial, deny redirects/proxies and
loopback/link-local/private/metadata targets to prevent root SSRF/rebinding. For v0.25.1 require the
[48 lowercase hex join-key format](https://raw.githubusercontent.com/juanfont/headscale/v0.25.1/hscontrol/db/preauth_keys.go),
not an API key; format cannot prove single-use/expiry, so the operator must attest those properties
and deadline. No admin query is added to verify them. Freeze inputs before auth; revalidate after;
changed/expired/disconnected attempts clear buffers and refuse. Persist only protected URL/trust and
the existing root-owned one-time Linux enrollment handoff; Mac key stays memory-only until invite
delivery. Never user-owned files/logs. Same-session input/clipboard capture remains possible: an
agent observing a join key can use that key until consumed/expired, but it cannot mint more keys.
This input path **does not satisfy “no caller-chosen inputs/settings”** in seq421/506: fixed methods
alone do not fix it. R3 needs a separate explicit founder decision for slices 3a/3b/3c/4/5/6 before
advanced implementation; do not represent seq502's product choice as approval of root intake.

All modes enforce single-use invite redemption/approval in the existing cross-process transaction,
one active invite/pending attempt per home, no raw invite at rest, digests/deadlines only. Local
minting journals a helper-generated operation ID; uncertain mint outcome never issues under a new
ID automatically. Expiry/deny/restart consumes locally; a network key/node may survive remote
cleanup failure but cannot pair or gain runtime access. No standing user/agent-readable key pool.

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

Disable closes admission/revokes first, confirms connector/sidecar/verifier and owned local
Headscale stopped, then removes only positively owned units, configs, enrollment sources/links,
socket/runtime files and secret/trust state; reloads systemd and confirms absence. Advanced remote membership cleanup is operator-pending;
local runtime revocation is immediate and does not depend on remote cleanup.
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

Dependency order is 0 → 1 → 2 → 3a → 3b → 3c → 4 → 5 → 6. R1 is resolved; R2 gates its
helper/store/replacement/timer/Keychain additions, R3 gates the advanced input path. Home mode need
not wait for R3; neither flow bypasses R2 or native trust feasibility. No new required CI check or
automatic M1/M2 trigger; use existing authorized manual hosted acceptance. Every implementation
slice needs independent review, scoped QA, normal hooks and the two required lint/static checks at
its exact head.

| Slice | Main file ownership / dependency | Verification required |
| --- | --- | --- |
| 0 prerequisite | Parallel Python PT_GNU_STACK/MDWE PR; no design here. R1 resolved; R2/R3 decisions below. | Owner's packaged runtime evidence; never remove MDWE to pass. |
| 1 protected ingress | `app/linux/tsnet-sidecar/{sidecar,engine_tsnet,health}.go`, `runtime/remote_access/managed_provider.py`, `systemd_unit.py`; C1/D frame and explicit new Linux profile and scoped control/DERP CA trust on Linux. | Supported Go tests and real temporary Unix sockets: forged HTTP identity, wrong UID, malformed frame, absent WhoIs; empty-peer READY and loss/watchdog; valid/forged/expired local CA, wrong SAN/pin and relay TLS refusal without verification bypass. Native Mac hosting profile unchanged. |
| 2 trust ceremony | `pairing.py`, `authorization.py`, `state_store.py`, request/stream admission; no desktop or installer edits. | Retained standalone connector venue with synthetic state: two concurrent redeemers/approvals → exactly one grant; wrong node with valid bearer denied; A revoked closes A streams while B works; restart/torn/rollback/legacy cases refuse. |
| 3a fixed helper | New helper module, system-bus/polkit definitions; status and guarded approval/revoke, no installation yet. | Standalone IPC plus disposable polkit: extra args refused, agent can prompt but not silently approve, actual target immutable across auth, fresh auth twice, wrong-UID and disconnect refusal; reserved R3 prompt input freeze/URL display, no files/FDs, SSRF/rebinding negatives. |
| 3b generation/install | `installer.py` `_input()`/`_read_file()` source-UID/same-FD validation, generated config/transaction owner, package/helper wiring; after 3a and parallel packaging PR. Coordinate renderer after 1; bundle pinned Headscale/notices, protected local CA/DB/admin socket, exact LAN bindings; R3 adds fixed advanced intake. | Temp-root/fake-manager clean/known FAILED/partial/foreign fixtures, foreign-owned 0600 token and parent/leaf swap or metadata-change refusal on enable/repair/replacement; safe `.deb` payload access, stop failure, interrupted replacement/disable, token rotation and exact unit/hardening parity. Local CA/address recovery, port conflicts, full Headscale cleanup; R3 key-entry redaction and pending operator cleanup. |
| 3c invite issuance | Local root-only Headscale mint + connector private invite IPC; after 2/3b/R2. R3 adds operator-supplied per-device key, never API/admin minting or server deployment. | Synthetic local mint/idempotency/uncertainty/cleanup; fresh auth, text envelope/CA size and round-trip, no user mint capability, redaction/replay/expiry; R3 consumed key replacement, unverified TTL disclosure and no remote admin authority. |
| 4 Linux UI/upgrade | `packaging/linux/desktop/happyranch_window.py`, package wiring only after parallel packaging PR; #1562. | Existing standalone desktop checks; synthetic reload-needed/failure/timeout; native GTK off/starting/ready/conflict/error/approval/revoke screenshots. Separate fresh polkit prompts at enable, invite, actual-device comparison/approval, turn off and revoke; route-policy renewal paused message + retry. Home card/address refresh; R3 URL/key intake and per-device replacement states; no admin-key prompt. No host service actions for maker. |
| 5 Mac single invite | `app/mac/Sources/HappyRanchApp/{RemoteConnectionView,AppDelegate}.swift`, supervisor managed model/ClientBridge, `app/mac/tsnet-bridge` only as needed; #1563. Scoped control/DERP CA trust; proposed Keychain bearer custody requires R2 approval; R3 advanced invite metadata after approval. | Supported Swift/Go checks, unsigned build, native join→pending→Connected and TLS-close/deny/expiry/cancel/retry. Record memory-only baseline; if R2 approved, verify save/restart without invite, locked Keychain, logout/removal, Linux revocation/turn off, join-success/delivery-loss fresh-invite recovery and partial-node cleanup/reuse; CA/SAN/pin mismatch and renewal/address recovery, not-home/offline distinctions and secret-free diagnostics. #1564 stays queued. |
| 6 hosted acceptance/docs | `scripts/ci/m1_install_ready_uninstall.sh`, `m2_tailnet_daemon.sh`, related fixtures/docs; builds on 1–5. | M1 uses helper-generated configs and no ExpectedPeer; clean plus known FAILED-manual fixture, exact hardening/READY/loaded units, disable residue, interrupted recovery and route-policy renewal failure closed. Separate home and neihoumacau-hk app acceptance below; M2 keeps real random-port daemon/verifier and squatter negative; adds two dynamically named stable-ID devices, one combined invite each, race/replay/expiry/wrong-node/revoke-survivor and join-success/delivery-loss recovery cases. |

Hosted harness fixtures may provision bundled local Headscale and a test-side polkit
authentication agent on disposable runners; production helper has no test flag, bypass action or
alternate caller-supplied settings outside any explicitly approved R3 intake. Hosted authentication
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
| Outside-home, Linux home + Mac on a genuinely different network through neihoumacau-hk, after R3 | Operator records ≤10-minute/non-reusable issuance evidence without secret artifacts, supplies Linux/Mac keys; advanced app enable/Add a device → one invite → Linux approval → release web UI; capture direct/private-DERP route evidence separately. Retain this server until the test settles; no removal authorized. |
| Both flows: restart either app/daemon; add second Mac; revoke first; turn off/re-enable | Saved grant/membership reconnect without new invite after ordinary restart; daemon offline/restarting shown; A revocation cuts requests and live streams while B works; off invalidates both/invites, preserves release/org data; re-enable needs new invites. |
| Both: cancel/expiry/replay, key consumed before approval/delivery, Keychain locked, policy renewal fails | Clear safe retry/new-key/invite actions, no restored spent authority; approval still required; locked storage visible; expired policy pauses closed with Retry. |
| Local trust/address recovery; cached approved client away | Wrong CA/SAN/expired cert refused, same-CA renewal/restart reconnects; changed LAN endpoint needs explicit refreshed invite, CA replacement reapproval; unavailable LAN state shown honestly; away cached success never advertised as supported outside-home acceptance. |

**Timeline / risk, as of 2026-11-16:** target decisions/security + trust/packaging feasibility by
11-18; slices 1–3c through 11-23; Linux/Mac slices 4–5 through 11-27; separate home-network acceptance
by 11-28 and outside-home app/revocation test by **2026-12-01**, leaving 12-02 for authorized fixes.
The neihoumacau-hk term ends **2026-12-03**; outside-home acceptance must finish before that date,
not merely join or start by then. This is a proposed schedule, not a delivery commitment: R2/R3,
scoped tsnet CA/DERP trust, packaging prerequisite and native/security reviews are unresolved.
**The slice plan cannot currently guarantee that deadline.** EM must confirm feasibility by 11-25
and flag a miss immediately if the 11-18 gate or integration milestones slip. Do not call same-LAN
success outside-home readiness or silently extend the server term, skip gates or deploy to recover.

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

## 9. Recorded decisions, remaining gates and exclusions

- **R1 RESOLVED — A, home-network access.** Founder seq499, EM seq500 and Product seq501 select local
  Headscale/local root minting now. No admin key stored anywhere. Seq502–504 retain the advanced
  manual-key neihoumacau-hk test; they do not reopen A or approve an input-contract exception.
- **Production target, deferred:** HappyRanch Services sign-in → user token → authorized service-side
  join-key issuance; Headscale admin credential remains service-side. Preserve the versioned invite
  and host-approval journey. No Services account/infrastructure dependency for this milestone.
- **Rejected for this milestone:** B (full Headscale admin key on Linux) and C (new scoped issuer
  beside neihoumacau-hk). No B bootstrap/expiry/replace flow remains. NOTE N2 is removed with B:
  a full admin key captured by a same-UID agent would permit direct Headscale administration until
  server expiry/revocation, regardless of fixed-helper restrictions. No such key is proposed here.
- **R2 FOUNDER-RESERVED, awaiting reply to seq505/506:** fixed bus-helper lifecycle, approval
  protocol/store version, transactional failed-install replacement/purge, fixed root **route-policy
  renewal** timer (identical still-shipped policy, no inputs, fail closed with paused + Retry), and
  slice 5's app-scoped Keychain/restart/logout/revocation/lost-delivery requirements. No automatic
  key renewal. C1/D approval in principle does not approve these additions or production migration.
  New local Headscale packaging/CA custody/trust details in §6 require security/Product review in
  the approved implementation briefs; R1's mode choice alone is not approval of those details.
- **R3 FOUNDER-RESERVED — advanced root intake:** §6's URL/key prompt is caller-selected data and
  therefore outside the literal no-input/settings contract. Request this narrow exception explicitly;
  it affects 3a (prompt/auth), 3b (protected URL/Linux enrollment), 3c (Mac key/invite), 4 (UI), 5
  (validation/recovery), 6 (outside-home acceptance). Do not silently broaden R2 or weaken default
  fixed actions. If declined, advanced enable/invites stay unavailable; do not fall back to files.
- **Not selected; founder-only if reopened:** dropping separate UID/system services, Headscale-only
  authorization, weaker hardening/verifier, network/agent containment, public relay/control fallback,
  connector-daemon merge/plain daemon socket, Go rewrite, connector LAN ingress, independent device
  crypto, arbitrary custom-home authority or other new top-level dependencies. Headscale bundling
  is the explicitly selected local-mode dependency; no additional dependency is authorized here.

This spec PR changes one document only. Behavior, hosted M1/M2, native dialogs, Mac acceptance and
founder-host failed-install replacement are **NOT RUN**. Return the revised flow and independent
security findings for Product review before finalizing; source review never establishes acceptance.
