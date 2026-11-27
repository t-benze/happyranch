# Client listener inventory and removal design

> Status: proposed replacement; source inventory, not implemented or qualified.
> Authority: Founder THR-327 seq739; root TASK-19545, docs-only TASK-19546.
> Current sources: [remote-access contract](../agent-guides/contracts/remote-access-and-remote-jobs.md),
> [managed security contract](2026-08-26-managed-remote-access-contract.md),
> [native bridge design](2026-11-17-web-native-bridge.md).

## Scope and explicit assumptions

On a **Client** machine, neither the GUI nor CLI fallback may expose a network
listener through which terminal tools can talk to the remote runtime. The target
design has no connectable client runtime listener, including a filesystem Unix
socket. A 0600 runtime relay is not an observation socket. Host Headscale
8443/3478, the Host connector, Host daemon loopback and home tsnet sidecar retain
their existing boundaries.

This inventory is pinned to fetched `origin/main`
`9763083271f111ac9e763aa9f9c458d848e951b4`. All file/line references below refer
to that immutable base. Assumptions and limits:

1. The supplied port-51297 observation belongs to the founder's v9 build; source
   matching is not a PID/FD attribution. Neither founder machine was inspected.
2. Existing GUI Main/Connection UX, selected-owner admission, saved access,
   native consent, cancellation and partial/unknown effects remain requirements.
   CLI recovery remains available, with reachability rather than loaded-UI claims.
3. Existing pinned Go/tsnet, Swift/WebKit and Python/GI facilities are the design
   budget. No new dependency, entitlement, system trust installation, daemon auth
   change, route-policy expansion or infrastructure change is assumed authorized.
4. Ordinary encrypted WireGuard/STUN UDP sockets are transport machinery, not a
   terminal-callable runtime HTTP service. Record them in socket evidence. This
   does not declare the whole client process socket-free or authorize a new proxy.
5. The control/DERP transport seam described below is **not yet proven feasible**
   against pinned tsnet. This note is not permission to weaken its home-only trust
   boundary. Stop implementation if a supported listener-free seam cannot preserve it.
6. Source inspection establishes reachable code paths, not native/package results.
   All fail-before/pass-after and native Mac execution here are planned, NOT RUN.

## Listener inventory

Paths are repository-relative. “GUI” includes the helper process tree; a
custom URI scheme alone does not make that tree listener-free.

| Owner file:line | Roles / startup path | Bind | Serves | Can curl / terminal tools reach the remote runtime? |
| --- | --- | --- | --- | --- |
| `app/mac/Sources/HappyRanchSupervisor/ClientBridge.swift:471` (`NWListener` at 486) | Mac foreground CLI: `ConnectionCLI.swift:202` → `ManagedHomeInviteSession.open` (`HomeInviteSession.swift:235`). Legacy bridge API also supports DIY/managed lanes. Current GUI uses direct app transport instead. | TCP `127.0.0.1:<ephemeral>`; optional requested port, 480–481 | Local session bootstrap (`/auth/bootstrap`, `/api/v1/auth/bootstrap`); forwards other requests to home with device credential. Comments promise HTTP/SSE/WS byte relay; they are not proof of full framing correctness. | **Yes**, while paired/authorized. Browser Authorization is stripped; knowing the port suffices to send requests through the owning session. No Host/Origin gate exists in this receiver. Remove the listener API from client paths. |
| `packaging/linux/desktop/client_runtime.py:352–359` (`RuntimeProxy`) | Linux foreground CLI: `client_cli.py:245` → `ClientSession.open_runtime` (`client_session.py:476–482`). Current GUI selects `RemoteDaemon`, not this proxy. | TCP `127.0.0.1:<ephemeral>` | Session bootstrap, framed HTTP, streamed SSE and validated duplex WS upgrade | **Yes**, while authorized. Exact Host and optional Origin checks at 396–397 accept ordinary curl using the correct URL. Remove rather than conceal the port. |
| `app/mac/tsnet-bridge/home_transport.go:75` (`newHomeTransport`) | **Both client platforms, GUI and CLI**. Mac `--home-bridge-v2`: `home_helper.go:185`; Linux `--home-session-v1`: `home_session.go:252`; both call `prepareHomeProcess` (`home_startup.go:34`). | TCP4 `127.0.0.1:<ephemeral>` | Fixed-target HTTP CONNECT followed by process-local TLS → verified home control/DERP TLS. `home_startup.go:73–75` sets helper HTTP(S)_PROXY to it. | No direct runtime route: it targets admitted home IP:8443 control/DERP. It **is a connectable client proxy**; GETs return 400. Remove it for the listener-free client design, preserving trust and endpoint confinement. This is separate from the home-sidecar forwarder below. |
| `app/mac/tsnet-bridge/home_helper.go:280–299` (`homeListener`), runtime relay 302–311 | **Both client platforms, GUI and CLI**. Called by `home_helper.go:227` and `home_session.go:300`. Parent consumers: Mac `HomeBridgeProcess.swift:129`; Linux `client_session.py:348`. | Filesystem Unix `<private session directory>/dial`, 0600 in 0700 parent | Fixed pinned home peer:443 relay; sends an initial four-byte big-endian result, then duplex HTTP/runtime bytes; no arbitrary target argument | Default curl does not parse the binary status prefix. A same-user terminal program can consume it and send runtime HTTP to the connector; connector device authentication still applies and the socket does not inject a credential. **Runtime-capable, not observation-only; remove.** Mode 0600 does not satisfy the ruling. |
| Pinned `tailscale.com@v1.78.0/ipn/ipnlocal/peerapi.go:72–125`, `local.go:4663–4754`; entered by client `home_session.go:153,177` / `managed.go:143` | Embedded tsnet client engines; startup depends on assigned netmap addresses | PeerAPI TCP port: deterministic attempts then ephemeral. Netstack branch uses empty IP, potentially wildcard IPv4/IPv6 kernel bind; later addresses reuse its port. A failed kernel bind can fall back to a fake userspace listener. | Tailscale PeerAPI/Taildrop/DNS, not HappyRanch forwarding | **No runtime forwarding identified.** Still include in process TCP census and investigate/remove client TCP admission for the proposed zero-TCP-listener target; do not silently exempt it or report zero listeners after removing only our wrappers. Native occurrence is unmeasured. |
| Pinned `tailscale.com@v1.78.0/tsnet/tsnet.go:120–124` (`Server.Port`), client constructors `home_session.go:153`, `managed.go:143`, `bridge.go:64` | Embedded client engines; port left zero | Engine-selected UDP sockets (including IPv4/IPv6 as supported) | Encrypted WireGuard transport / discovery | No runtime HTTP service or application proxy identified. Retain required encrypted transport and classify sockets explicitly; there is no blanket zero-bound-socket assertion. |
| `app/mac/tsnet-bridge/home_startup.go:90` (`fixedHomeResolver`); pinned tsnet `tsnet.go:643` / `LocalClient` at 191 | Client helpers / embedded engine | `net.Pipe` DNS and `memnet` LocalAPI, **no kernel address or filesystem socket** | Fixed-name resolver; native in-memory LocalAPI/health | **No.** Neither is connectable by a separate terminal process. Preserve this property. |
| Pinned tsnet `tsnet.go:212–230` (`Server.Loopback`) | Optional SOCKS5/HTTP/LocalAPI facility; **no production call site** in `app/mac/tsnet-bridge` or client native owners | Would be TCP `127.0.0.1:0` if called | Tailnet SOCKS/HTTP proxy and LocalAPI | Not started by inspected client paths. Do not introduce it as a replacement. `RunWebClient` is also unset/false in the client constructors. |
| `packaging/linux/desktop/app_scheme_webkit.py:1302` | Linux GUI SSE response bridge | Anonymous `socket.socketpair()`, **no bind/listen address** | Native-to-WebKit response body and cancellation wakeup | **No.** Connected inherited/owned descriptors are not a connectable listener. |

The dependency references above are from the locally available pinned module
source, selected by `app/mac/tsnet-bridge/go.mod` (`tailscale.com v1.78.0`).
They are conditional source findings; process evidence must establish actual
addresses/owners. No client call to `Server.Listen`, `ListenPacket`, `Loopback`
or a separate WebSocket listener was found. The runtime TCP/Unix relays above
are the application forwarding exposure.

### Confirmed Host-only owners and URL seams

| Owner | Role evidence / disposition |
| --- | --- |
| `app/mac/Sources/HappyRanchSupervisor/HomeConnector.swift:208–221` | Its API itself has no role parameter. The sole production constructor is `AppDelegate.swift:1348`, inside `startHomeConnector`, guarded by `.home` at 1317. Switching preference to `.client` stops it at 1231–1233. It binds the validated home tailnet address/connector port and injects the home daemon credential. No supported Client startup call was found; leave Host behavior unchanged. |
| `app/linux/tsnet-sidecar/hometransport.go:63` | `Sidecar` config validation requires `RoleIdentity` starting `home-sidecar-` (`sidecar.go:63`). `engine_tsnet.go:61` → `hometransport_linux.go:158–194` prepares the home-only control forwarder. Client packaging instead builds/launches **app/mac/tsnet-bridge** (`packaging/linux/build-payload.sh:162`, `client_session.py:254`). No Client path into this sidecar was found. Do not edit its forwarder, encrypted tailnet listener (`engine_tsnet.go:196`) or 0600 observation-only socket (`observe_linux.go:119–123`). |
| `RuntimeTransport.swift:17`, `PortReader.swift:40` | URL construction and reading the **local daemon** port; neither creates a listener. `AppDelegate.swift:45`'s 127.0.0.1 gate is reached after the client transport branch at 42–43. It selects the local Host daemon transport, not a ClientBridge Host/Origin check. Do not remove Host port discovery. |

### Port 51297 and the HTTP 400 observation

The strongest source match is the **dedicated client helper's CONNECT proxy**,
`home_transport.go:157–174`, not the GUI's `ClientBridge.start` listener.
`readHomeConnect` at 217–255 requires CONNECT to
`control.happyranch.invalid:8443` or `:443`, HTTP/1.0 or 1.1, no body/transfer
encoding, and a consistent explicit Host. An ordinary GET on **any path** fails
the method/target check and gets a fixed zero-body 400 before any upstream dial.
This is not an Origin check. Native GUI startup already uses
`ManagedHomeInviteSession.openApp` (`HomeInviteSession.swift:253–260`) and
`AppDelegate.connectToRemote` (`AppDelegate.swift:1434–1460`) without calling
`ClientBridge.start`. `ClientBridge`'s local 400 branches at 1033–1047 concern
UTF-8/request-line parsing; a valid bootstrap GET should instead return 200.

Thus “all paths returned 400” does not establish a safe runtime bridge or
prove which PID owned port 51297. Exact attribution still needs the v9 build
identity plus native PID/FD evidence on an authorized build-machine reproduction.
No claim of inspecting the founder's Mac or Linux Host is made.

## Replacement

### GUI runtime path

Keep Main at `happyranch-app://app/`, serving bundled release assets locally.
On Mac, retain `AppScheme`'s WKURLSchemeHandler (`AppScheme.swift:283`) and
`AppSchemeScripts`' admitted document/message and fetch-body channel. The
selected `AppAPITransport` calls the native peer transport directly. On Linux,
retain WebKitGTK custom URI registration (`app_scheme_webkit.py:509–512`),
`app_scheme.RemoteDaemon` (`app_scheme.py:440–495`) and `RemoteCall`.
`happyranch_window.py:1436–1441` already attaches the Client owner to Main.
This extends the existing GUI route; it does not add a browser-accessible URL.

Replace the helper's filesystem `dial` listener with a versioned, bounded
multiplexed stream protocol over its **existing inherited parent pipes**.
Launch/ready/state frames and runtime open/data/credit/close frames need distinct
closed types and stream IDs; only the immutable admitted home peer:443 can open.
Native transport objects represent logical streams rather than accepting a new
local connection. Preserve independent cancellation, backpressure, current-peer
revalidation, byte order, deadlines, stream limits and parent-EOF teardown.
Never put credentials in argv/env, paths, logs or JavaScript. Validate helper
bundle/protocol compatibility; mixed versions refuse without a listener fallback.
No user CLI command spelling or external compatibility contract is changed here.

Framed HTTP must retain bounded headers/bodies, reject ambiguous framing and
smuggling, handle fragmented and binary content correctly and finish exactly one
response. SSE must deliver incrementally, including quiet streams, with abort,
target retirement, revocation and slow-reader cancellation closing the same owned
remote stream. Keep existing upload/download handling and do not buffer SSE to EOF.
Preserve document/generation admission, remote configuration policy, mutation
send fences and unknown-effect handling; neither a transport error nor locale
change may replay a mutation or switch the selected target.

The home connector remains the authority for current device proof, pairing,
allow/refuse policy and stream revocation. It alone injects the **raw daemon
token on the final home loopback hop**. That token never leaves home. Any
legacy bootstrap compatibility response is session-only and handled natively;
neither it nor a JavaScript Authorization header grants home authority. Existing
native-only device credentials stay in their approved custody.

### Control/DERP path: feasibility gate before implementation

Removing GUI/CLI runtime TCP listeners is insufficient: both helpers still start
`homeTransport`. Replace its OS CONNECT listener with helper-local transport
connections while preserving exact admitted home IP, name, CA fingerprint,
certificate verification, fixed resolver, no ambient/system/public roots or
proxy fallback, private process custody and failure-triggered teardown. Preserve
the current process-local issuer layering if that is required to keep these
properties. Never merely unset HTTP(S)_PROXY and let tsnet use default networking.

The pinned tsnet `LocalClient` is in-memory and `Server.HTTPClient()` is a
tailnet request client; neither by itself proves a seam for **all control and
DERP TLS dials**. `home_startup.go` currently wires environment proxy URLs, not
an injectable listener-free dialer. Before implementation, establish a supported
seam in the existing dependency for both paths with negative trust/destination
fixtures. A `http.DefaultTransport` override alone is not a demonstrated solution.
Also resolve tsnet's conditional PeerAPI TCP admission in the native inventory.
**These feasibility points remain open; no working replacement is claimed.**
If they require a dependency upgrade/fork, new dependency/entitlement, broader
trust, an access-check change or loss of the signed-off UX, STOP and return the
exact choice to the engineering manager/founder. This docs task authorizes none
of those changes and provides no test-only hook or reflective bypass as a solution.

### WebSocket

A custom URI response handler is not a WebSocket upgrade transport. Neither
existing scheme adapter supplies a WebSocket facade. Moreover,
`runtime/remote_access/route_policy.json:50–51` has **zero remotely allowed
WebSocket templates**, and no WebSocket consumer was found in `web/src` at this
base. Preserve that refusal; do not add a WS route or a loopback WS bridge.

If a separately approved UX later requires WS, it needs an admitted
document-scoped message channel: native open/send/close commands, bounded
text/binary frames and credits, status/error/close events tied to target and
document generation, native-only credentials and the same connector policy and
revocation gates. Both WKWebView and WebKitGTK would use their existing native
message handlers and the inherited helper stream channel. This is a different
approach from URI-scheme HTTP forwarding, not an implemented capability. Any
required remote WS allowlist expansion needs separate security-contract approval.

### CLI fallback on both platforms

Limit the foreground connection to connect/recover and observing access or
revocation. Keep established consent, pairing, custody and cleanup semantics;
local saved-access removal must not claim Host revocation. Mac stops calling
`HomeInviteSession.open`/`ClientBridge.start`; Linux stops calling
`ClientSession.open_runtime`/`RuntimeProxy`. Call the same native authenticated
health path used by the GUI (`checkAccess` / `ClientSession.health`) internally,
then continue bounded access observations until cancellation or denial.

Return closed reachability/effect categories; do not create, print, persist or
retain a usable client port/URL. Successful reachability does not claim that
the release runtime UI loaded. Removing only the URL text leaves the exposure
and fails this requirement. No CLI route opens a browser runtime.

## Verification plan (implementation work; NOT RUN here)

1. Add a process-level listener census in the supported native/helper venues.
   Pin source/artifact identity and parent PID, include every helper/WebKit child,
   inspect both IPv4 and IPv6 TCP listeners, UDP binds and filesystem Unix sockets
   from launch through join, approval, connected GUI/CLI, reconnect, cancellation,
   failed approval, revocation and teardown. Capture continuously or instrument
   listen creation so a short-lived startup proxy cannot escape a single snapshot.
2. On the unchanged base, the same zero-client-runtime-listener oracle must fail:
   GUI exposes the helper CONNECT TCP and Unix runtime relay; CLI additionally
   exposes ClientBridge/RuntimeProxy. Verify the oracle detects a deliberate
   synthetic TCP and Unix runtime listener, so a missing PID/permission is never
   mistaken for absence. Do not expose real credentials while probing.
3. After replacement, require no connectable runtime TCP/Unix/WS/SOCKS/HTTP proxy
   at any lifecycle phase, no client port in CLI output/state, and no helper
   CONNECT listener. Resolve every extra TCP socket, including PeerAPI; an unknown
   owner/address fails the census. Classify required encrypted UDP, true private
   observation-only sockets and anonymous pipe/socketpairs separately with their
   owners/protocols. Check descriptor and stream cleanup after abort/revoke.
4. Use `lsof` PID/process-tree TCP/Unix observations on the **Mac build machine**
   and `ss`/process socket tables on an authorized disposable Linux Client, with
   exit/error handling. Exact commands belong in the approved native harness,
   not an ad hoc run on either founder machine. A 400, 403, refused curl or empty
   process list alone cannot prove absence. Synthetic direct behavior checks must
   exercise the actual owners, not a list of forbidden source strings.
5. At the reviewed implementation head, qualify native Mac WKWebView and packaged
   Linux WebKitGTK: release assets/deep reload, framed/binary HTTP, incremental
   SSE/quiet stream/abort, upload/download, switch/disconnect/reconnect, saved and
   session-only flows, credential non-disclosure, forbidden requests, revocation,
   no mutation replay and both locales. WS stays denied under the current policy.
   Native Mac compilation and actual process census require the authorized Mac
   build machine; this Linux docs task proves neither.
6. Independent security/code review precedes implementation acceptance. Retain
   normal hooks and lint/static `scripts/local_ci.sh all`; required hosted checks
   remain Source Boundary Lint and Web Lint (Node 24). No retired Python unit or
   Web Vitest suite, ad hoc Product E2E, new CI trigger, live install or deployment.

## Docs/spec/plan parity and bilingual impact

This is a **prospective parity list**, not a claim that current binaries have
changed. The active firewall task TASK-19534/TASK-19542 owns overlapping contract,
one-click spec, runbook and local-CI edits; this docs-only change leaves them alone.
Coordinate these specific updates with that owner before implementation lands:

| Maintained source | Required coordinated update |
| --- | --- |
| `docs/agent-guides/contracts/remote-access-and-remote-jobs.md` | Record seq739 for both GUI and CLI; replace client loopback/Unix runtime relay and control-proxy descriptions, retain Host boundaries and all token/pairing/revocation rules. |
| `docs/specs/2026-08-26-managed-remote-access-contract.md`, `2026-11-16-one-click-remote-access.md` | Align client topology only; retain connector-only raw bearer custody, explicit remote allowlist and home-only control/DERP verification. |
| `docs/specs/2026-11-17-web-native-bridge.md`, `app/mac/README.md`, Linux release handbook/client CLI documentation | Remove the retained foreground RuntimeProxy/ClientBridge exception and usable client URL; document internal health and inherited helper transport, actual native evidence and compatibility. |
| `docs/operations/remote-access-runbook.md` and founder plan-v9 | Replace client-port curl evidence; keep Host firewall, port, release ownership and revoke checks. No new on-host audit installation, endpoint or credential access is implied. |

The handed-off **plan-v9** is the immutable task artifact
`dev_agent/output/TASK-19463/plan-v9.md` (retrievable with task recall), not a
repository plan file. Do not overwrite it or disturb the founder's running case.
The manager's next plan delta must revise steps **9–10**: drive authenticated
allowed/refused requests through the admitted app transport in an authorized
native qualification harness; take the security evidence from the **Host
connector allow/refuse audit/decision categories** correlated to those requests,
alongside actual GUI runtime load and the Client listener census. Do not use
Mac curl as an allowed-runtime test. If the installed audit cannot provide that
evidence, record NOT RUN and arrange an approved evidence venue; do not invent an
audit command/endpoint or expose a temporary client port. Read-only policy probes
must preserve the plan's prohibition on POST shutdown. Steps 8 and 13 also lose
their printed-port/repeated-curl assumptions: use native/CLI authenticated health,
Host revocation evidence and stream closure. Historical v8/v9 receipts stay historical.

No app-owned strings change in this PR. The implementation copy delta includes
removing both `Runtime reachable: <url>` lines (`ConnectionCLI.swift:208`,
`client_cli.py:246`) and any help/runbook port instructions. Proposed replacement
meaning: **“Runtime reachable.” / “运行时可连接。”** and **“Observing access;
Ctrl-C disconnects.” / “正在检查访问权限；按 Ctrl-C 断开连接。”** Retain the
reachability-versus-loaded-UI distinction in English and Simplified Chinese.
These are design candidates, not shipped translations. Update the relevant native,
CLI and Web catalogs/copy together with the implementation; preserve existing
saved-access warnings, native prompts and recovery language. Verify affected
GUI states in en/zh-CN at the required sizes without locale-triggered requests,
transport restart or lost drafts/focus/selection. Seq736 approval-timeout,
pending ambiguity and inviteState follow-ups remain out of scope.
