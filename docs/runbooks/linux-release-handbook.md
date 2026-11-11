# Linux release handbook

## Build the intermediate payload

Founder THR-346 seq43/44 authorizes one manual build workflow under THR-291.
After this workflow lands on main, open **Actions → Build Linux App Payload →
Run workflow**, select the reviewed main revision, and dispatch it. It builds
natively on `ubuntu-22.04` (x86_64) and `ubuntu-22.04-arm` (aarch64). It has no
automatic trigger and is not a required PR or merge check.

Each successful matrix leg uploads a `happyranch-linux-<arch>-<run>-<attempt>`
artifact containing `happyranch_<debversion>_<debarch>.deb`, the intermediate
`happyranch-<version>-linux-<arch>.tar.gz` and its `.tar.gz.sha256` file.
Version comes from the committed `pyproject.toml`.
Verify with `sha256sum -c happyranch-<version>-linux-<arch>.tar.gz.sha256`.
The tarball is an intermediate build input; the installable product is the .deb.

To build locally on Linux x86_64 or aarch64, use a clean committed checkout,
uv, Node 24/npm, Go 1.23.12+, git, GNU tar, gzip and sha256sum. No host
Python is required; the builder installs its private pinned CPython 3.12.10
before running any Python helper, including project metadata reads.
Network access to the pinned Python/dependency sources is required. Unset
`VIRTUAL_ENV`, inherited `PYTHON*` and uv selection/configuration controls
before invoking the build; unsupported controls and offline mode are refused.

```sh
bash packaging/linux/build-payload.sh
# Optional fresh absolute output directory:
bash packaging/linux/build-payload.sh --dist-dir /tmp/linux-payload-output
```

The script exports committed HEAD into a private temporary directory. It uses
an exact uv-managed CPython 3.12.10, `uv sync --frozen --no-dev
--no-default-groups` with binary-only runtime dependencies, then installs the
HappyRanch wheel as real `.py` files. It builds production `web/dist` with
`npm ci && npm run build` and the native Go sidecar with `CGO_ENABLED=0`.
Its private Go module cache uses `-modcacherw` so build cleanup can remove it.
Existing version/architecture outputs are refused; use a fresh destination.

The extracted payload contains `python/`, `web/dist/`, `licenses/`,
`build-info.json`, and `bin/{happyranch,happyranch-daemon,happyranch-tsnet-sidecar}`.
The two POSIX launchers resolve their prefix from their own `bin/` location,
execute the bundled interpreter with `-I -m`, and default
`HAPPYRANCH_WEB_DIST` to that prefix's `web/dist` only when unset. Invoke them
from the extracted directory; the .deb installs absolute forwarding wrappers.
The daemon launcher exports `HAPPYRANCH_BUNDLED_BIN=<prefix>/bin`, which startup
places first on agents' PATH, removing duplicate copies. This is source mode
(`sys.frozen` is false). Installed runtime data and bundled skills are retained.
The remote-access connector's `/opt/happyranch/venv/bin/python` layout stays
separate from desktop packaging (see the installation layout below).

Focused PATH tests drive the real executor module with stdlib unittest,
outside the retired pytest suite:

```sh
python3 -B packaging/linux/test_bundled_path.py -v
```

For a smoke run, unpack outside the repository, use a new scratch
`HAPPYRANCH_DAEMON_HOME` and `HAPPYRANCH_DAEMON_PORT=0`, then run the extracted
`bin/happyranch --help` and `bin/happyranch-daemon`. Read the actual port from
`<scratch-home>/daemon.port`, check `/api/v1/health` and `/`, and SIGTERM/wait that
owned daemon process. Keep the live daemon and its home out of the test.

## Desktop window (system Python)

The payload also contains `bin/happyranch-app`,
`share/happyranch/desktop/happyranch_window.py`,
`share/applications/happyranch.desktop.in`, and the existing clay brand artwork
under `share/icons/hicolor/` (16, 32, 64, 128, 256, 512, 1024 PNG and scalable
SVG). The builder copies the existing Mac PNGs and Web SVG without changing them.
The desktop entry is a template: the deb builder replaces `@APP_EXEC@`
with `/usr/bin/happyranch-app` and installs the menu entry/icons. Building the
payload alone does not install files into `/opt` or `/usr/share`.

The window launcher resolves its payload prefix relative to `bin/`, then runs
**`/usr/bin/python3 -I`** with system PyGObject, GTK 3.0 and WebKit2 **4.1**.
The host needs `python3-gi`, `gir1.2-gtk-3.0`, and
`gir1.2-webkit2-4.1`; they are apt-provided, never installed into the bundled
CPython. These are package Depends. The window does
not import HappyRanch. Its child uses the existing `bin/happyranch-daemon`,
which continues to run bundled CPython 3.12.10.

```sh
# Run from an extracted payload (a desktop session/display is required):
./bin/happyranch-app
# Explicit isolated home for a smoke run; never use the live daemon's home:
./bin/happyranch-app --daemon-home /tmp/happyranch-desktop-smoke
# Focused lifecycle tests: system stdlib only, no display/GI/retired pytest:
/usr/bin/python3 -B packaging/linux/desktop/test_happyranch_window.py -v
```

Default runtime home is `$XDG_DATA_HOME/happyranch`, or
`~/.local/share/happyranch` when XDG_DATA_HOME is empty, unset or relative.
`--daemon-home` is an explicit override; inherited `HAPPYRANCH_DAEMON_HOME`
is ignored. This mirrors the packaged Mac app's separate app-data home rather
than reusing the CLI's `~/.happyranch`. The child receives a minimal OS environment,
that home, loopback bind, port `0`, and the payload's `web/dist`; inherited
HappyRanch settings are not passed through.

**External daemon rule:** the Mac app can discover an external daemon but never
stops it. This window additionally refuses to attach: any existing `daemon.pid`
or `daemon.port` (even stale/partial state) produces an error view, without
launching or signaling a daemon or removing those files. Use a separate home;
do not concurrently launch a CLI daemon into the desktop's private home. CLI
launchers do not participate in the desktop lock.

The first window keeps an advisory `desktop.lock` for its full lifetime. A
second launch for the same canonical home activates/focuses that window through
Gtk.Application's session bus. Without a session bus it exits cleanly when the
lock is held; it never starts another daemon. The lock file remains after exit
so future launches lock the same inode; the OS releases the lock automatically.

The child publishes its actual ephemeral port in `daemon.port`. The window
requires `daemon.pid` to match its Popen child and waits up to 60 seconds for
`GET /api/v1/health` to return HTTP 200 and `status: ok` before loading `/`.
Health probes bypass HTTP proxies. Startup/health/child-exit failures produce an
in-window diagnostic with `<desktop-home>/logs/desktop-daemon.log`. Startup and
stop work run off the GTK main thread. Native loading/error text is available
in English and Simplified Chinese (desktop LANG); raw diagnostics stay verbatim.

Closing the window, Ctrl+Q, SIGTERM or SIGINT requests shutdown. The window
sends SIGTERM only to its child, waits five seconds, then SIGKILL and a two-second
wait if needed, and reaps it. After confirmed exit it removes remaining markers
only with its empty-at-launch ownership fact and unchanged single-link regular
marker inodes. That includes interrupted port/PID writes: an absent, empty or
ASCII decimal-prefix PID is owned partial publication; a complete matching PID
is also owned. Different/non-prefix PID contents, pre-existing markers and a
replacement of either observed inode preserve the remaining state. A partial
write may finish on its pinned inode during shutdown.
A stop that cannot observe exit retains ownership and the lock, displays the
failure, and allows a subsequent close to retry. There is no crash/SIGKILL
recovery promise for the window itself, nor process-tree shutdown guarantee for
arbitrary daemon-launched workloads. Independent QA must supply real Ubuntu
22.04/Debian 12 GUI screenshots; lint and these focused tests do not prove that.

## Build the Debian package

On Linux, install build-host `python3` (>= 3.10), `dpkg` and `coreutils`.
The builder needs neither root nor system GI, executes no payload code, and
downloads nothing. Supply the verified tarball from the preceding build:

```sh
bash packaging/linux/build-deb.sh /absolute/dist/happyranch-0.1.0-linux-x86_64.tar.gz
# Optional fresh literal absolute output directory (default: beside tarball):
bash packaging/linux/build-deb.sh /absolute/dist/happyranch-0.1.0-linux-x86_64.tar.gz --dist-dir /tmp/linux-deb-output
dpkg-deb -I /tmp/linux-deb-output/happyranch_0.1.0-1_amd64.deb
dpkg-deb -c /tmp/linux-deb-output/happyranch_0.1.0-1_amd64.deb
# Focused native packaging checks; synthetic payload, real dpkg-deb:
/usr/bin/python3 -B packaging/linux/test_build_deb.py -v
```

The payload's `build-info.json` records the installed HappyRanch distribution
version from `pyproject.toml` in its dependency inventory. Packaging uses that
version and architecture, checks the top-level directory agrees, refuses unsafe
archive members/escaping links and existing outputs, and calls
`dpkg-deb --root-owner-group`. It does not infer architecture from the build
host or execute a foreign interpreter: `x86_64` maps to Debian `amd64`, and
`aarch64` maps to `arm64`. The manual workflow retains its original two native
matrix rows and uploads the .deb alongside its intermediate inputs.
The package omits generated `__pycache__` directories, normalizes shared-library
and documentation files to mode 0644, and adds a Debian changelog. Original PR1
license/notice bytes remain in `/usr/share/doc/happyranch/licenses/`; the Debian
copyright file points there and to the system Apache-2.0 common license.

Debian version mapping is deliberately explicit: dotted numeric releases stay
unchanged, `aN`/`bN`/`rcN` become `~aN`/`~bN`/`~rcN`, `.postN` becomes
`+postN`, and `.devN` becomes `~devN` (after any preceding suffix). The packaging
revision is `-1`. Thus `0.1.0` → `0.1.0-1`, `1.2.3rc1` → `1.2.3~rc1-1`,
`1.2.3.dev2` → `1.2.3~dev2-1`, and `1.2.3.post1` → `1.2.3+post1-1`.
Other version forms require an explicit mapping review and are refused.

### Installed layout and connector separation

The single payload prefix is **`/opt/happyranch-desktop`**. The existing
connector scripts (`scripts/ci/m1_install_ready_uninstall.sh`,
`m2_tailnet_daemon.sh`, and the M3 recovery consumer) use
`/opt/happyranch/{venv,bin}`, `/etc/happyranch`, service state under `/var/lib`
and `/run`, and `happyranch-connector.service` /
`happyranch-tsnet-sidecar.service`. Their provisioning also requires
`/opt/happyranch` to be absent. A separate prefix avoids owning even that parent.
The desktop package installs no connector files, configuration, accounts,
systemd units or enablement, and never invokes the connector installer. See
[the connector contract](../agent-guides/contracts/remote-access-and-remote-jobs.md).

| Package path | Purpose |
| --- | --- |
| `/opt/happyranch-desktop/` | Relocatable payload, bundled Python, daemon, Web UI and desktop source; generated bytecode caches omitted |
| `/usr/bin/happyranch`, `/usr/bin/happyranch-app` | POSIX wrappers that exec the real absolute payload launcher, preserving arguments and exit status; direct symlinks would break the launchers' `$0` prefix lookup |
| `/usr/share/applications/happyranch.desktop` | Generated PR2 template; Exec/TryExec `/usr/bin/happyranch-app`, Icon `happyranch` |
| `/usr/share/icons/hicolor/{16x16,…,1024x1024,scalable}/apps/happyranch.*` | Existing brand icons |
| `/usr/share/doc/happyranch/` | Debian copyright/changelog, unchanged PR1 licenses/notices/inventories and build-info |

No maintainer scripts are needed. Ubuntu/Debian's `desktop-file-utils` and
`hicolor-icon-theme` dpkg triggers refresh their caches when installed. Package
removal deletes only package-owned files; it does not signal a running app or
daemon. Quit the desktop app before upgrading/removing it. The default user
home `~/.local/share/happyranch` (or `$XDG_DATA_HOME/happyranch`) is preserved,
including on purge. The package never owns user data or `~/.happyranch`.

### Runtime dependencies

All direct Depends names are available in both stock Jammy (22.04, with
updates/security and universe enabled) and Bookworm (12, with security updates):

| Depends | Reason | Archive verification |
| --- | --- | --- |
| `python3 (>= 3.10)` | System interpreter for the GI window; daemon/CLI use bundled CPython3.12 | [Jammy](https://packages.ubuntu.com/jammy/python3), [Bookworm](https://packages.debian.org/bookworm/python3) |
| `python3-gi` | System PyGObject binding | [Jammy](https://packages.ubuntu.com/jammy/python3-gi), [Bookworm](https://packages.debian.org/bookworm/python3-gi) |
| `gir1.2-gtk-3.0` | GTK3 namespace and transitive shared libraries | [Jammy](https://packages.ubuntu.com/jammy/gir1.2-gtk-3.0), [Bookworm](https://packages.debian.org/bookworm/gir1.2-gtk-3.0) |
| `gir1.2-webkit2-4.1` | WebKit2 4.1 namespace/engine and transitive libraries | [Jammy](https://packages.ubuntu.com/jammy/gir1.2-webkit2-4.1), [Bookworm](https://packages.debian.org/bookworm/gir1.2-webkit2-4.1) |
| `git` | Runtime repository/worktree operations | [Jammy](https://packages.ubuntu.com/jammy/git), [Bookworm](https://packages.debian.org/bookworm/git) |
| `ca-certificates` | HTTPS trust for runtime clients and git | [Jammy](https://packages.ubuntu.com/jammy/ca-certificates), [Bookworm](https://packages.debian.org/bookworm/ca-certificates) |
| `libc6 (>= 2.35)` | Explicit supported Linux ABI floor (Jammy 2.35, Bookworm 2.36) | [Jammy](https://packages.ubuntu.com/jammy/libc6), [Bookworm](https://packages.debian.org/bookworm/libc6) |
| `libgcc-s1` | GCC unwind runtime used by bundled native Python extensions | [Jammy](https://packages.ubuntu.com/jammy/libgcc-s1), [Bookworm](https://packages.debian.org/bookworm/libgcc-s1) |

GI's packages pull in GTK/WebKit's native dependency graph. The Go sidecar is
built with `CGO_ENABLED=0`; it needs no system Go. Node, npm, uv, compilers and
Python development headers are build tools, not desktop runtime requirements.
Provider/executor CLIs and their credentials are provisioned separately by the
user; the package does not install or authenticate them. No executor binary is
required to open the local UI or use CLI help. A graphical desktop session is
needed for the window; Xvfb/Openbox are acceptance tools, not product Depends.

### Install, remove, reinstall

```sh
sudo apt install ./happyranch_0.1.0-1_amd64.deb
happyranch --help
happyranch-app
# Or select HappyRanch from the applications menu.
# Quit HappyRanch first, then:
sudo apt remove happyranch
# User app data remains. Reinstall with the same apt install command.
sudo apt install ./happyranch_0.1.0-1_amd64.deb
```

For arm64 use `happyranch_<debversion>_arm64.deb`. These local .deb commands
precede the later founder-signed apt repository/signing work; no key handling
or repository publication is part of this builder.

### Lintian review

Save the actual `lintian --no-tag-display-limit <package.deb>` output alongside
`dpkg-deb -I` and `-c`. A lintian exit status alone does not establish that its
reported errors are absent. Review tags individually. This package deliberately
uses `/opt` as required by the desktop layout, rather than Debian's usual
unbundled `/usr` layout. Consequently `dir-or-file-in-opt` is expected.
The PR1 private CPython and CGO-disabled Go sidecar also retain their upstream
embedded libraries, static linkage and debug symbols: `embedded-library`,
`statically-linked-binary` and `unstripped-binary-or-object` reflect that existing
payload contract. `hardening-no-pie` on bundled CPython is inherited as well.
These findings are recorded, not hidden with lintian overrides.

For `shell-script-fails-syntax-check` on Python stdlib files such as `cgi.py`,
inspect the shipped headers and verify Python parsing with the bundled runtime.
Relocatable shell/Python headers can confuse the distro shell parser; these
files are imported or run by Python, not used as app shell entry points.
Do not classify invalid Python as a policy-only finding. Other inherited
payload warnings include timestamped upstream timezone archives, script modes
and interpreter conventions. Preserve the vendor payload and notices rather
than rewriting third-party files to silence these tags. CLI manual pages are
not shipped in v1 (`happyranch --help` supplies usage). Any newly introduced
metadata, mode or missing-dependency error needs correction or a specific
disposition in release evidence.
