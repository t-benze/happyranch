# Shared tsnet health

The package provides map-only STUN measurements, refresh guards, and the closed
v1 health record (A2a-i/ii). The Linux tsnet sidecar consumes it for journal
records and its atomic runtime health file.

Call `BuildRecord(at, trigger, status, expectedKey, ping, derpMap, probe, age)`
with real v1.78.0 Status/PingResult/DERPMap snapshots and Refresh's result/age.
The nonzero expected node public key selects exactly one Status.Peer entry;
only its HostName is retained. Ping.IP must match that peer's TailscaleIPs.
Nil/failed/unrelated pings produce path `none`; CurAddr is never authoritative.
Successful Endpoint takes precedence over DERP and is reduced to direct_kind:
RFC1918/link-local IPv4 => lan, 100.64/10 => cgnat, IPv6 => ipv6, else public.
No address, key, AuthURL, other peer name or raw health text is retained.

The returned immutable Record supports `json.Marshal`; construction and
serialization refuse records exceeding 2048 bytes (including oversized names),
and zero Records refuse serialization. `at` is UTC, dated back by nonnegative
probe age; no extra age field is emitted. Unknown handshake age is zero.
Triggers are join/periodic/change/on_demand. Unknown backend states become
NoState. Running without a control warning is connected, login/machine-auth
states are needs_login, otherwise unreachable. Rendered Status.Health messages
map to six fixed, unique codes in fixed order; unknown text becomes other.
DERP measurements use short region codes from the supplied map, choosing at
most eight in ascending RegionID order; malformed codes are omitted and first
duplicate wins. Self.Relay supplies derp_home. Region labels accept 1–16 ASCII
lowercase letters/digits/hyphens, starting with a letter. Portmap is always
unknown, the approved narrowing that avoids portmapper traffic.

The Linux sidecar requires `happyranch/tsnethealth` with a local
`replace happyranch/tsnethealth => ../../shared/tsnethealth`. Both Linux payload
and Mac app scripts export the full source tree, preserving that relative path.
Keep the existing `tailscale.com v1.78.0` pin in all three modules.

Retain one `Runner` per tailnet. Pass its current DERP map to `Refresh`; a join
bypasses the ten-minute cache window, while every call shares one in-flight
probe. Cached values are copied and returned with age/availability. `Probe` is
the uncapped measurement primitive. Schedule periodic pings in the consumer
(Mac: visible window only, every minute; sidecar: every five minutes); the ping
adapter must send one disco ping and honor the three-second context. Reserve
change records with `AllowChange`, at most once every thirty seconds.

No mapped address survives the probe. Empty usable measurements become
`derp_ms: {}` and `derp-unreachable`; nonempty measurements with UDP false add
no-udp. Home DERP comes from Status.Self.Relay. Captive
detection is omitted. The running tsnet backend's own magicsock netcheck is
unchanged. DNS uses the host in the supplied map when neither IP is valid.
Replies are received and validated while subsequent map hosts resolve and send;
each RTT starts at its own send. Partial measurements survive the overall
five-second deadline and enter the runner cache. Tests supply a synthetic
`net.DefaultResolver` transport and restore it afterward, without real DNS.

Run from this module with Go 1.23.12, `GOWORK=off`, `CGO_ENABLED=0`, and
`GOFLAGS=-mod=readonly`: `go vet ./...` and `go test ./...`. All probes in tests
use loopback. For the concurrent in-flight contract, run
`CGO_ENABLED=1 go test -race -run '^TestRefreshConcurrent$' ./...`.
`go list -deps .` must exclude netcheck, captivedetection and portmapper.
