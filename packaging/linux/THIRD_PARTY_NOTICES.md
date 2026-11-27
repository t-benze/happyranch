# Linux payload third-party notices

This intermediate payload contains CPython 3.12.10 supplied by uv's managed
python-build-standalone distribution. `CPython-LICENSE.txt` is copied from that
interpreter's stdlib. The python-build-standalone project's MPL 2.0 text is
retained separately from its `20250409` release:
https://github.com/astral-sh/python-build-standalone/tree/20250409
Upstream source and distribution licensing information:
https://gregoryszorc.com/docs/python-build-standalone/main/running.html#licensing

Installed Python distributions retain their original license files in
`python/lib/python3.12/site-packages`; `python-distributions.json` names those
locations relative to site-packages. `uv.lock` records the resolved graph.
The bundled interpreter's original stdlib and native libraries remain intact.

`npm/` retains installed package original notices, including build tools;
`npm-package-lock.json` retains package identities and license declarations.
`Radix-LICENSE` is the repository's retained upstream Radix original.
Font OFL originals remain beside the fonts in `web/dist/fonts`.

`sidecar/` retains the existing Linux sidecar notices and pinned inventory.
`go/` and `go-modules.json` supplement it with originals from this native
target's actual module selection. `go-runtime/` retains the selected Go
toolchain's originals and vendor notices.

`client-go/` and `client-go-modules.json` separately retain the actual client
bridge graph and its original notices. Its single approved local replacement,
Tailscale v1.78.0, records the pristine module h1, exact replacement path and
committed PeerAPI patch SHA-256. Both collectors verify the full generated tree
and original notice bytes against the pinned upstream zip. Other third-party
replacements refuse; the Host graph stays separate. See
`app/mac/tsnet-bridge/third_party/README.md` in the source distribution.

Headscale v0.25.1 is shipped inert at `libexec/headscale/headscale`, outside
`bin/`. Its BSD-3-Clause copyright, conditions and disclaimer are preserved
byte-for-byte in `headscale-LICENSE` from the pinned tag:
https://raw.githubusercontent.com/juanfont/headscale/v0.25.1/LICENSE
`packaging/linux/headscale-pin.json` pins both official Linux release assets
(amd64 and arm64) and their SHA-256 values, checked against upstream
`checksums.txt` and independent downloads when the pin was introduced:
https://github.com/juanfont/headscale/releases/tag/v0.25.1
Builds verify the native asset before copying or inspecting it; they never
execute Headscale. No service is installed, enabled or started by this payload.

`headscale-modules.json` records Path, Version and Sum for the binary's exact
linked dependency set from `go version -m`, rather than the tag's larger
development/test graph. `headscale/modules/` retains those modules' original
notices, downloaded through Go's public module proxy and checksum database,
with sums also matched against the verified binary's embedded inventory.
A missing original fails the build. `headscale-build-info.json` records the
tag, release asset, SHA-256, embedded Go version (go1.23.4) and toolchain module
identity/sum. `headscale/go-runtime/` contains that exact toolchain's original
license and vendor notices, separately from the sidecar build toolchain.
The .deb retains all these files both under the payload's `licenses/` and
`/usr/share/doc/happyranch/licenses/`.

These are dependency notice inputs for the later .deb release work. Signing
and public distribution remain founder-owned steps.
