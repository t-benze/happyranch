# Managed embedded-tailnet sidecar

This Go module transports opaque TCP bytes to the loopback connector. It has no
HTTP or daemon-bearer knowledge. `tailscale.com v1.78.0`, the module identity and
the existing dependency pins are shared by Linux and Darwin. Use Go 1.23.12
(the Mac toolchain pin), with CGO disabled.

Linux adds an explicit `Profile: "linux-managed-identity-v1"`. In that profile,
omit `ExpectedPeer` and `ConnectorAddr`: the endpoint is fixed at
`/run/happyranch-connector/ingress.sock`. Each accepted tailnet connection calls
LocalClient WhoIs with its remote IP:port, requires `Node.StableID`, opens one
Unix connection, and writes `HRID` + version byte `2` + big-endian uint16 UTF-8
byte length + StableID + uint16 UTF-8 label byte length + label. The display label
comes from the same WhoIs response (`Node.Hostinfo.Hostname`, else
`Node.ComputedName`, else empty); it never supplies identity or authorization.
The connector also accepts legacy v1 frames without labels. The complete frame
is at most 1024 bytes, including the nine-byte framing overhead. Empty StableID,
invalid UTF-8 in either field, CR/LF/NUL in StableID or oversize frames close without
forwarding; failed/absent WhoIs has no hostname/IP fallback. HTTP bytes follow
unchanged and connections are never pooled. WhoIs and the initial frame write
have five-second bounds.

This profile keeps enrollment/durable consumption, private-only configuration,
connector probe, listener, notify/watchdog and systemd BindsTo gates. Empty peers
are READY; health selects no single expected peer and emits no peer-specific
ping. Identity metadata grants no access: pairing/pin enforcement is a later
slice. Identity framing itself changes no CA, TLS, DERP or control trust behavior; the
separate home transport below owns those changes. The connector's
matching `managed.profile` is `linux-managed-identity-v1`; it runs as the fixed
`happyranch` service UID and requires a 0700 runtime directory and 0600 socket.
The renderer's matching `ConnectorUnitSpec.ingress_profile` adds only
`RuntimeDirectoryMode=0700`; AF_UNIX is already allowed. The existing installer
still admits legacy configs. Slice 3b-1b-3 adds Python-only generation of an
**inert staged bundle**: fresh IDs, the explicit identity profile, fixed Unix
ingress and the bundled deny-default policy. It omits `ExpectedPeer`,
`ConnectorAddr`, `ControlURL` and `control_tls`, including any placeholder URL.
All six files use the existing atomic writer; the generated v1 provision record
contains actual fixed-file digests and phase `provisioned` (files staged only),
never secrets or paths. No transaction is generated or consumed.
Final or pending transaction inventory refuses all legacy installer operations;
it supplies no recovery or helper action. Install/uninstall (including purge),
check-drift/unit publication and alternate supervisor install/uninstall/start/
stop/restart/enable/disable/upgrade/rollback/run entries refuse the modern profile
before unit writes, backup creation/restoration or service calls: `home control
binding not provisioned; requires slice 3b-2`. Inventory remains unchanged.
Legacy omitted/empty profiles retain upgrade/rollback behavior; read-only service
status and diagnostics remain available. The Go sidecar is unchanged
and never starts from this staged bundle. Render-only `managed_units` selection
from the generated connector adds only the existing `RuntimeDirectoryMode=0700`
line; legacy and sidecar unit bytes remain unchanged, including
`SidecarUnitSpec.home_transport=False`. Installed modern-unit proof awaits 3b-2.
The operator CLI and fixed helper's method selection remain unchanged;
replacement/recovery/rotation and helper/package H2 wiring remain 3b-1c/3b-1d.
Home `control_tls` generation and `home_transport=True` selection belong to
slice 3b-2. No automatic migration or deployment occurs in either sub-part.

The first 3b-1c sub-part adds Python-only installer inventory and stop/quiescence
prerequisites. They inspect fixed managed paths, exact unit bytes and loaded
fragments/drop-ins, with bounded shallow reads and safe metadata; unknown/unsafe
residue or transaction records yield `manual-install-conflict` naming paths only.
Safe unrecorded config subsets, including interrupted generated writer prefixes,
are known failed/partial after each present artifact is validated. Complete
unrecorded legacy configs accept 0600/0640/0644, with unchanged owner, no-follow,
regular-file, single-link and group/world-write checks; recorded provisioning
retains exact 0640. Intact manual installs require migration even when stopped.
The stop API refuses those installs/conflicts before effects; recognized installations stop the target,
connector, sidecar and both verifier units, prove dead inactive/failed states,
no queued jobs, zero service PIDs and no service cgroups, then re-inventory.
All files remain intact on stop/proof failure. Inventory reads no release-home
token and never interprets opaque trust/tsnet state. These APIs grant no cleanup
or activation authority and have no CLI/helper/package selection. Journaled
replacement, Disable cleanup and authenticated token rotation remain fail-closed;
the Go sidecar and `home_transport=False` are unchanged. TODO (3b-2): owned local
Headscale stop/cleanup, with no callable seam implemented in this sub-part.

Linux home transport (THR-327 1b) adds the optional protected config record
`"control_tls":{"dial_ip":"192.168.1.10","ca_sha256":"<64 lowercase hex>"}`
to the identity profile. It requires the canonical control URL
`https://control.happyranch.invalid:8443` and an RFC1918 IPv4. That home URL
cannot use the legacy transport. Slice 3b owns generating this configuration,
the home CA/server leaf and the private DERP map; this module generates none of
those persistent objects. Existing non-home configs retain their transport.

Before constructing tsnet, Linux admits the fixed no-follow root-owned
`/etc/happyranch/control-ca.pem` (single-link, at most 64 KiB, one valid CA with
the exact DER fingerprint, root:happyranch and exact mode 0640) and
`/etc/happyranch/empty-ca-directory` (root:root, empty and exact mode 0755).
Fixed parents must be real protected root-owned directories. Setup failure is
an `ErrConfiguration` startup error before tsnet construction or a remote dial;
the sidecar preserves that classification and does not consume enrollment.
Only home-profile units (`SidecarUnitSpec.home_transport=True`) supply exactly
those `SSL_CERT_FILE` and `SSL_CERT_DIR` paths. The flag defaults to false:
legacy/non-home units remain byte-identical, with no SSL_CERT environment
overrides, preserving OS system-root lookup. Slice 3b must set the flag when it
generates the home profile. These lines locate the protected home trust files;
the explicit upstream home-only verifier remains the authentication boundary.
Inherited upper/lower `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`,
`SSLKEYLOGFILE`, `GODEBUG` and every `TS_` variable refuse startup without
echoing their values. Refusing the whole `TS_` namespace covers the pinned
TLS/proxy/DERP/debug and authentication knobs, including future spellings.

The process creates an in-memory issuer/leaf and holds a new `127.0.0.1:0`
CONNECT listener. Only `control.happyranch.invalid:8443` and `:443` are accepted;
both dial the admitted literal `dial_ip:8443`. Normal upstream TLS verifies
only the admitted home CA, fixed DNS SAN, time and server usage, before 200.
Local TLS then re-presents the ephemeral leaf and relays opaque duplex bytes.
Absolute HTTP (including Noise's HTTP/80 attempt), other targets/methods,
malformed headers and headers over 8192 bytes close. Setup/handshakes have a
ten-second bound; at most 128 raw local/upstream sockets are owned at once.

Setup sets `GODEBUG=x509usefallbackroots=1` before the single
`x509.SetFallbackRoots` call with only the ephemeral issuer, then installs the
owned HTTP/HTTPS proxy and in-memory fixed-name resolver before clients. No
host DNS or trust-store change occurs. Upstream and local root pools stay
separate; pinned baked roots never authenticate a remote leaf. Listener,
verifier or upstream dial failure closes the listener and owned streams;
engine cancellation/shutdown closes both transport and tsnet. Malformed local
TLS after an accepted CONNECT also seals admission; EOF or a local TCP reset
from a cancelled controlhttp/DERP connection attempt closes only that attempt.
Closed home engines reject new listeners, including after pinned tsnet's own Close. The dedicated
process cannot initialize another home in place: address/home change requires
a supervisor-owned process restart. Canonical URL/node/consumed identity never
includes the random port or local issuer. Darwin home transport remains slice 5b.

`hometransport_linux_test.go` uses synthetic temporary certificates and separate
processes for real pinned `/key`/Noise registration and DERP region clients,
including matching-name/wildcard public-equivalent chains, same-CA leaf renewal,
redirects and receipt counters. An independent loopback TCP decoy detects direct
fallback to the resolved control/DERP address, including with the proxy down.
`hometransport_lifecycle_linux_test.go` composes synthetic temporary trust with
the real tsnet server and shipping engine lifecycle: /key, Noise registration,
map, Running and private DERP, then cancellation/shutdown/listener/verifier/local
TLS failure and refusal to reopen admission. The lifecycle cases first reset a
real local TCP handshake, then wait for the fixture's real DERP receipt within
the existing Up context before triggering
teardown; Running alone does not establish DERP readiness. Separate simultaneous
processes use distinct local ports/issuers; a restart on a renewed endpoint retains
node identity and exact consumed-marker bytes, drops the enrollment key and observes
the new resolver mapping without requests to the old endpoint.

Run from this module:
`GOTOOLCHAIN=go1.23.12 CGO_ENABLED=0 GOFLAGS=-mod=readonly GOWORK=off go test -count=1 ./...`.
The module's language declaration remains Go 1.23.1; execution uses 1.23.12.
These component compositions do not execute successful protected-path admission
through TSNetEngine.Start on an installed system. Exact root-owned installed
paths, full packaged lifecycle/MDWE, generated Headscale map, packet-wide absence
of public/DNS fallback traffic and native Darwin acceptance require their
authorized venues and remain NOT RUN here.

Only an omitted profile or an empty JSON string retains the legacy Linux and
Darwin behavior below. Non-string values (including null) and unknown strings
refuse at configuration admission, before state or credential handling, with
command exit 2. This Linux profile on Darwin also refuses.

`ConnectorAddr` must use literal `127.0.0.1`; literal port 8765 is always
refused. Optional JSON `ExcludedDaemonPorts` is a list of integers in 1..65535;
validation refuses any matching connector port. Omission preserves existing
validation, including for Mac configs. Linux deb provisioning writes this list
and connector `excluded_daemon_ports` as the sorted unique union of 8765, the
configured daemon port and any extra operator exclusions. It also requires
`ConnectorAddr` to equal exactly `127.0.0.1:<managed.bind_port>` before staging,
install/start and drift verification. Raw TCP must reach the connector ingress,
where pairing and policy are enforced, before the connector forwards to the
daemon. A direct daemon hop could expose loopback `/auth/bootstrap`.

Linux health uses the single-source local `happyranch/tsnethealth` module. After
successful startup it emits `tsnet-health ` plus one compact JSON record on
stderr at join, every five minutes, and on rate-limited status changes.
If `RUNTIME_DIRECTORY` is set, the latest record is atomically published as
`health.json` with explicit mode 0644; otherwise only the journal is used.
The expected Mac hostname is included. See the Linux release handbook for
fields, cadence, cached path semantics and runtime-directory preservation.

Linux uses the existing `NOTIFY_SOCKET` READY/WATCHDOG/STOPPING protocol and
`WATCHDOG_USEC`/`WATCHDOG_PID` cadence. systemd owns restart and the injected
read-only credential copy. The new readiness flags are refused on Linux.

On Darwin, run with `--config /absolute/config.json`, the existing one-use
`--credential-file /absolute/enrollment.key`, and the required
`--readiness-file /absolute/private-state/sidecar.readiness.json`. Optional
`--watchdog-interval 30s` sets the maximum interval without a completed healthy
publication; it must be positive. This bounds startup, publication and shutdown
as well as the healthy loop. launchd KeepAlive owns restart after exit 75; there
is no in-process restart. Systemd watchdog/notification environment is unused by
this lifecycle; `CREDENTIALS_DIRECTORY` is ignored for enrollment semantics.
Darwin always requires a current-user 0600 one-use file, commits the existing
consumed marker, deletes the input and resumes from durable state with no key.

The readiness path must be absolute and clean, with no symlinked ancestors.
New directories are 0700; the writable parent must be current-euid owned 0700.
Each publication uses a pinned parent, exclusive no-follow 0600 temporary file,
file fsync and atomic rename. Replacing a leaf symlink replaces the link itself.
The record is exactly C3's JSON object: `pid`, `process_started_at` (Unix wall
seconds), `ready`, `last_healthy_wall_time` (Unix wall seconds or null),
`sequence` (increasing positive integer), and `reason`. It is observation only.
The existing Python `launchd_readiness._read_record` accepts it unchanged.

Before engine startup/Server.Up, readiness is false/`starting`. All existing
redemption, durability, expected-peer, connector-probe and listener gates must
pass before true/`healthy`. Successful publications refresh at half the
watchdog interval, capped at 100 ms. Failed startup is false/
`waiting-for-readiness`; listener loss is false/`readiness-or-listener-lost`
followed by nonzero exit. SIGTERM/SIGINT publish false/`stopping` before teardown.
C6-loss also checks TCP reachability of the configured loopback `ConnectorAddr`
every `min(1s, watchdog_interval/4)`, with dial timeout
`min(250ms, watchdog_interval/8)` (both floored at 1ns). This avoids a dial on
every 100ms publication tick and detects loss in at most 1.25s with the default
30s watchdog, plus Go scheduling and publication/teardown time. For interval w,
the cap-aware combined budget is min(1s,w/4)+min(250ms,w/8), each term floored at
1ns (w=3s gives 750ms+250ms=1s). One failure publishes
false/`readiness-or-listener-lost` and exits 1. Successful probes close the socket
without sending data; TCP reachability does not prove protocol health.
Connector-loss recovery on macOS is sidecar exit + launchd KeepAlive, without a
second supervisor. Linux continues to rely on systemd BindsTo.

Failed publication closes service admission and exits nonzero; a stalled
publication or teardown is bounded by the independent monotonic watchdog.
The watchdog needs Go scheduling; host sleep/runtime freeze recovery is unproven.

From this module directory, these are the build/check commands. Long checks use
the durable jobs workflow. A Linux cross-build compiles Darwin tests without
executing them; native Mac execution requires separate authorization.

```sh
export GOTOOLCHAIN=go1.23.12 CGO_ENABLED=0 GOFLAGS=-mod=readonly GOWORK=off
GOOS=linux GOARCH=amd64 go test ./...
GOOS=linux GOARCH=amd64 go vet ./...
GOOS=darwin GOARCH=arm64 go build ./...
GOOS=darwin GOARCH=arm64 go test -c -o /absolute/scratch/sidecar.test .
GOOS=darwin GOARCH=arm64 go test -c -o /absolute/scratch/command.test ./cmd/happyranch-tsnet-sidecar
GOOS=darwin GOARCH=arm64 go vet ./...
python3 tools/generate_inventory.py --target linux-amd64 --check
python3 tools/generate_inventory.py --target darwin-arm64
python3 tools/generate_inventory.py --target darwin-arm64 --check
```

The generator defaults to the unchanged Linux files under `third_party/`.
Darwin has a separate inventory and notices under `third_party/darwin-arm64/`.
Darwin reconciles the executable command graph, existing sums and source
licenses; C6-loss includes the already-pinned command import
`github.com/mdlayher/sdnotify@v1.0.0` and its MIT notice. Linux retains its
historical library inventory byte-for-byte. The exact first-party local health
module replacement is excluded from third-party notices; external modules
remain checksum and license verified. Darwin adds
`github.com/google/uuid@v1.6.0` and
`github.com/prometheus-community/pro-bing@v0.4.0` to its build inventory relative
to Linux; both already exist in go.mod/go.sum. External module pins and sums
are unchanged. Distribution must include the matching notices; this slice makes
no binary-distribution claim. Python sidecar handoff/launchd installation is delivered by C6b: Darwin
`python -m runtime.remote_access.installer install` takes explicit
`--sidecar-binary`, `--sidecar-config` and first-use `--enrollment-key-file` inputs.
It stages/enables two agents without starting; installer start/stop/restart/status
operate both. See the maintained remote-access contract for paths, enrollment
states and removal. App bundling/frozen entry and native acceptance remain pending. Swift's existing
HomeConnector is untouched.
