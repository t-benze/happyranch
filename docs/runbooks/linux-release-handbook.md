# Linux release handbook

## Build the intermediate payload

Founder THR-346 seq43/44 authorizes one manual build workflow under THR-291.
After this workflow lands on main, open **Actions → Build Linux App Payload →
Run workflow**, select the reviewed main revision, and dispatch it. It builds
natively on `ubuntu-22.04` (x86_64) and `ubuntu-22.04-arm` (aarch64). It has no
automatic trigger and is not a required PR or merge check.

Each successful matrix leg uploads a `happyranch-linux-<arch>-<run>-<attempt>`
artifact containing `happyranch-<version>-linux-<arch>.tar.gz` and its
`.tar.gz.sha256` file. Version comes from the committed `pyproject.toml`.
Verify with `sha256sum -c happyranch-<version>-linux-<arch>.tar.gz.sha256`.
The tarball is an intermediate input to the later .deb packaging work.

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
from the extracted directory; symlink installation belongs to the .deb phase.
The daemon launcher exports `HAPPYRANCH_BUNDLED_BIN=<prefix>/bin`, which startup
places first on agents' PATH, removing duplicate copies. This is source mode
(`sys.frozen` is false). Installed runtime data and bundled skills are retained.
The remote-access connector's `/opt/happyranch/venv/bin/python` layout is owned
by the later .deb PR.

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
The desktop entry is a template: the .deb phase must replace `@APP_EXEC@`
with its installed launcher path and install the menu entry/icons. This payload
stage does not install files into `/opt` or `/usr/share`.

The window launcher resolves its payload prefix relative to `bin/`, then runs
**`/usr/bin/python3 -I`** with system PyGObject, GTK 3.0 and WebKit2 **4.1**.
The host needs `python3-gi`, `gir1.2-gtk-3.0`, and
`gir1.2-webkit2-4.1`; they are apt-provided, never installed into the bundled
CPython. These will become package Depends in the .deb phase. The window does
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
