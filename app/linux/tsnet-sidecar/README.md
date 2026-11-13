# Managed embedded-tailnet sidecar

This Go module transports opaque TCP bytes to the loopback connector. It has no
HTTP or daemon-bearer knowledge. `tailscale.com v1.78.0`, the module identity and
the existing dependency pins are shared by Linux and Darwin. Use Go 1.23.12
(the Mac toolchain pin), with CGO disabled.

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
Failed publication closes service admission and exits nonzero; a stalled
publication or teardown is bounded by the independent monotonic watchdog.
The watchdog needs Go scheduling; host sleep/runtime freeze recovery is unproven.

From this module directory, these are the build/check commands. Long checks use
the durable jobs workflow. A Linux cross-build compiles Darwin tests without
executing them; native Mac execution requires separate authorization.

```sh
export GOTOOLCHAIN=go1.23.12 CGO_ENABLED=0 GOFLAGS=-mod=readonly
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
Both reconcile the actual build graph, existing sums and source licenses. Darwin
adds `github.com/google/uuid@v1.6.0` and
`github.com/prometheus-community/pro-bing@v0.4.0` to its build inventory relative
to Linux; both already exist in go.mod/go.sum. No new required module or sum is
authorized. Distribution must include the matching notices; this slice makes
no binary-distribution claim. Python sidecar handoff/launchd installation (C6b),
app bundling/frozen entry and native acceptance remain pending. Swift's existing
HomeConnector is untouched.
