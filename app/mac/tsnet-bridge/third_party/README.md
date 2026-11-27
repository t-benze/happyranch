# Client-only Tailscale patch

Founder THR-327 seq739/746 selected in-memory PeerAPI for Mac and Linux
Clients. Only the `happyranch/tsnet-bridge` main module replaces Tailscale.
The separate Host sidecar and shared-health module retain their original
manifests. This slice removes PeerAPI's kernel bind; the remaining client
transport/listener work is owned by the other slices in
[the merged design](../../../../docs/specs/2026-11-27-client-listener-removal.md).

## Pin and source delta

- Upstream: `tailscale.com v1.78.0`, tag `v1.78.0`, commit
  `0267fe83b200f1702a2fa0a395442c02a053fadb` in
  `https://github.com/tailscale/tailscale` (module proxy `.info` Origin).
- Pristine module h1: `h1:/UF5EituVa1QMhojqOObFumTarVTcZ14nW1KIVVwbLw=`.
- Patch: `tailscale-v1.78.0-peerapi.patch`, SHA-256
  `5bbd46d04b423af475df37e07eda36e1dcfc845dd5fda460e81de67f5625a211`.
- Patched `ipn/ipnlocal/peerapi.go`, SHA-256
  `32119b9f85363942cc551da3deeb8ac96944461887f1d7e16916e61290838273`.

The one condition extends Android's existing fake-listener branch to Darwin
and Linux **when `sys.IsNetstack()`**. Netstack continues to intercept and serve
PeerAPI flows, using the existing fake port. Non-netstack behavior, other OSes,
peer identity checks and all request handlers remain upstream. The host module
does not inherit a main module's `replace` directive.

We commit the patch and preparer instead of a full vendored tree. The preparer
uses the existing Go downloader/cache, independently recomputes Go's module zip
h1, verifies the exact patch SHA-256, and applies it with the existing Git tool.
It retains the verified upstream zip and generates an ignored tree under
`third_party/tailscale.com`. Verification compares every generated file to the
authenticated zip, except the single pinned patched file; extra, missing,
changed or symlinked files refuse. LICENSE, PATENTS, AUTHORS and Linux's
conventional nested `licenses/` originals retain their upstream bytes.
The patch has zero context so its literal context prefixes do not trigger
Git whitespace checks; the exact upstream h1 and patched-file hash bind its
application location and result.

The checked-in `go.mod` replacement always points at the generated tree.
An unprepared checkout fails a normal bridge build. Both shipping build scripts
run preparation before compiling the bridge and stop on any failure. Neither
shipping build falls back to the upstream module. The notice collectors admit
only this client main module, exact path, version, h1, patch hash and verified
tree; every other third-party replacement refuses.

## Prepare and qualify

From the repository root (Go 1.23.12 and Git must already be available):

```sh
python3 -I app/mac/tsnet-bridge/third_party/prepare_tailscale.py --go /absolute/path/to/go
python3 -I app/mac/tsnet-bridge/third_party/prepare_tailscale.py --verify
cd app/mac/tsnet-bridge
GOTOOLCHAIN=local GOPROXY=off GOWORK=off CGO_ENABLED=1 go build -mod=readonly ./...
GOTOOLCHAIN=local GOPROXY=off GOWORK=off CGO_ENABLED=1 go vet -mod=readonly ./...
GOTOOLCHAIN=local GOPROXY=off GOWORK=off CGO_ENABLED=1 go test -mod=readonly -count=1 -run '^TestClientPeerAPIListeners$' -v .
GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOWORK=off go build -mod=readonly tailscale.com/ipn/ipnlocal tailscale.com/tsnet
GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOWORK=off go vet -mod=readonly tailscale.com/ipn/ipnlocal tailscale.com/tsnet
```

Preparation supports `GOPROXY=off` with the pinned zip in the existing cache.
On a clean packaging machine it uses the configured Go proxy/checksum policy;
the fixed h1 is checked independently in either case. Generated files do not
enter the source export. Re-run preparation after checkout or a pin change.
Package-level `go vet` also loads the upstream packages' test dependencies. If
those already-pinned modules are absent from the cache, run that command with
the configured GOPROXY first, retaining `-mod=readonly`, then repeat offline.

The Linux regression runs in an isolated process, with memory-only control/Noise
and two real tsnet nodes. The isolated child uses normal tsnet UDP lifecycle,
waits for the target's advertised service in the caller's map and verifies an
encrypted ping before HTTP. Relay request/upgrade
counts diagnose fixture readiness without logging node identities or payloads.
The fixture normalizes the DERP dialer's `tcp4` to memnet's accepted `tcp`
network after checking the exact IPv4 destination; it never delegates an OS dial.
Its `/proc` census matches only process-owned socket
inodes, detects deliberate IPv4/IPv6/filesystem Unix positive controls, and then
requires zero TCP LISTEN/filesystem Unix sockets with both nodes running. UDP
is reported separately. A real in-tailnet PeerAPI HTTP 200 greeting proves the
service still runs. It never contacts real Headscale or a home.

For the attributable pristine failure, copy go.mod/go.sum to a task scratch
`pristine.mod`/`pristine.sum`, use `go mod edit -modfile=/absolute/pristine.mod
-dropreplace=tailscale.com@v1.78.0`, and set the first-party health replacement
to its absolute repository path with `go mod edit -modfile=... -replace=...`.
Run the same test with `-modfile=/absolute/pristine.mod`; require the nonzero
exit to name the PeerAPI listener census, with the HTTP positive control still
succeeding. Re-run normally for the patched pass. No production file needs a
temporary mutation.

From the repository root, also run the existing portable notice verifier:

```sh
python3 -I packaging/verify_manifest_notices.py --scratch /absolute/new/task-scratch
```

Long checks and local CI use the documented durable jobs workflow. Native Mac
cgo archive compilation, packaging and runtime census require the later Mac
build-machine qualification; pure-Go Darwin build/vet is only source-level
cross-platform qualification. Whole Client GUI/CLI listener absence is outside
this dependency slice. Required CI stays lint/static only.

## Future upgrades

A version change requires a new approved dependency task. Acquire the pristine
new tag/commit, verify its module h1 and retained notices, re-evaluate whether
upstream has removed the kernel bind, and re-apply only the minimal guard if
still needed. Refresh the patch, exact hashes, replacement and notice association
together. Never broaden the exception to arbitrary versions or paths. Repeat
the pristine failure/patched pass, in-tailnet positive control, Linux build/vet,
Darwin pure-Go build/vet, native Mac cgo build/census, both notice consumers and
the required exact-head local CI. Independent review and scoped QA own acceptance.
