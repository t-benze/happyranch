# Shared tsnet health measurements

Partial A2a boundary: this local module implements the map-only STUN prober and
refresh guards. The closed v1 record builder and its privacy/bounds tests remain
pending a manager scope/size decision. No consumer is wired yet.

The future consumers can require `happyranch/tsnethealth` with a local
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

No mapped address survives the probe. Empty measurements must become
`derp_ms: {}` and `derp-unreachable`; v1 must emit `portmap: unknown` because no
port mapper is contacted. Home DERP must come from Status.Self.Relay. Captive
detection is omitted. The running tsnet backend's own magicsock netcheck is
unchanged. DNS uses the host in the supplied map when neither IP is valid.

Run from this module with Go 1.23.12, `GOWORK=off`, `CGO_ENABLED=0`, and
`GOFLAGS=-mod=readonly`: `go vet ./...` and `go test ./...`. All probes in tests
use loopback. `go list -deps .` must contain neither
`tailscale.com/net/netcheck` nor `tailscale.com/net/captivedetection`.
