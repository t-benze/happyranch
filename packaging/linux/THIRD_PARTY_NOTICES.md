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

These are dependency notice inputs for the later .deb release work. Signing
and public distribution remain founder-owned steps.
