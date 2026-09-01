# Managed remote access: Linux package operations (N3)

N3 composes the portable Python connector and embedded Linux tsnet sidecar into one reproducible owner-only artifact. It does not provision a control plane, deploy a service, enable managed access by default, or begin macOS work.

```sh
cd app/linux/tsnet-sidecar
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -buildvcs=false -o ../../../dist/happyranch-tsnet-sidecar ./cmd/happyranch-tsnet-sidecar
python tools/generate_inventory.py
cd ../../..
uv build --wheel --out-dir dist
uv run python app/linux/package/build_package.py --sidecar dist/happyranch-tsnet-sidecar --wheel dist/happyranch-0.1.0-py3-none-any.whl --version 0.1.0 --output dist/happyranch-linux-amd64.tar
```

The manifest hashes every payload. `share/sbom.cdx.json` and `share/THIRD_PARTY_NOTICES.md` couple to the exact checksum-pinned N1 inventory; composition refuses a module missing from the notices. No dependency resolution occurs during composition.

The composite starts the connector and runs the supported `diagnose --config` preflight before sidecar admission. The sidecar is a truthful `Type=simple` executable whose `--config` is the manual-N5, owner-custodied JSON contract: absolute state and one-use enrollment-credential paths, private headscale URL, `private-only` DERP policy, home role identity, a dynamic non-empty expected-peer set, listen address, and loopback connector address. Process startup does not return until enrollment is durably consumed, at least one expected peer is visible, connector reachability passes, and the listener is active. The sidecar binds to connector lifetime, so connector failure removes the listener first. Target stop reverses the ordering: sidecar admission stops before connector/downstream cleanup. Sidecar failure removes admission and restarts without replacing connector authority. Unit and package files are owner-only; the sidecar never receives the daemon bearer.

The installer validates duplicate-free exact archive/manifest membership, normalized allow-listed paths, schema, architecture, count, hashes, and modes before any write. Upgrades retain the prior payload and units until all publication boundaries succeed, restoring them on injected failure without staging residue. SBOM license hashes are named as license evidence, and inventory, CycloneDX components, and structured notice coordinates/SPDX/checksums must match one-to-one.

Install/upgrade/uninstall functions take an explicit fixture root and validate the manifest before mutation, enabling tests without touching the host. Production installation, service enablement, deployment, signing, and launch remain gated.
