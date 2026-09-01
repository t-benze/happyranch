# Managed remote access: Linux package operations (N3)

N3 composes the portable Python connector and embedded Linux tsnet sidecar into one reproducible owner-only artifact. It does not provision a control plane, deploy a service, enable managed access by default, or begin macOS work.

```sh
cd app/linux/tsnet-sidecar
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -buildvcs=false -o ../../../dist/happyranch-tsnet-sidecar ./cmd/happyranch-tsnet-sidecar
python tools/generate_inventory.py
cd ../../..
uv build --wheel --out-dir dist
uv run python app/linux/package/build_connector.py --wheel dist/happyranch-0.1.0-py3-none-any.whl --output dist/happyranch-connector
uv run python app/linux/package/build_package.py --sidecar dist/happyranch-tsnet-sidecar --connector dist/happyranch-connector --wheel dist/happyranch-0.1.0-py3-none-any.whl --version 0.1.0 --output dist/happyranch-linux-amd64.tar
```

The manifest hashes every payload. `share/sbom.cdx.json` and `share/THIRD_PARTY_NOTICES.md` couple to the exact checksum-pinned N1 inventory; composition refuses a module missing from the notices. No dependency resolution occurs during composition.

`build_connector.py` installs the real wheel into an isolated build target and freezes that installed code with the repository-pinned PyInstaller build dependency. The archive therefore carries an explicit self-contained Python executable; it never relies on a source checkout, ambient `python`, or an inert wheel as launch proof. The wheel remains bundled as an exact receipt.

The composite starts the connector and runs its supported `diagnose --config` preflight before sidecar admission. The sidecar is a truthful `Type=notify` executable whose `--config` is the manual-N5, owner-custodied JSON contract: absolute state and one-use enrollment-credential paths, private headscale URL, `private-only` DERP policy, home role identity, a dynamic non-empty expected-peer set, listen address, and loopback connector address. Process startup does not report READY until enrollment is durably consumed, at least one expected peer is visible, connector reachability passes, and the listener is active. While healthy it emits WATCHDOG every ten seconds for the unit's 30-second watchdog; notification failure terminates the service so systemd restarts it. The sidecar binds to connector lifetime, so connector failure removes the listener first. Target stop reverses the ordering: sidecar admission stops before connector/downstream cleanup. Sidecar failure removes admission and restarts without replacing connector authority. Unit and package files are owner-only; the sidecar never receives the daemon bearer.

The installer validates duplicate-free exact archive/manifest membership against a closed path set, normalized paths, strict non-boolean schemas, architecture, count, hashes, and modes before any write. Inventory and CycloneDX fields (coordinate, purl, SPDX, go.sum and license checksum) plus structured notice coordinates/SPDX/checksum/content match one-to-one. A durable owner-only transaction marker classifies publication state. On re-entry it either restores the last-known-good payload and units and removes staging residue before a fresh install, or rejects an incoherent marker/backup composition; it never silently deletes an unexplained backup.

Install/upgrade/uninstall functions take an explicit fixture root and validate the manifest before mutation, enabling tests without touching the host. Production installation, service enablement, deployment, signing, and launch remain gated.
