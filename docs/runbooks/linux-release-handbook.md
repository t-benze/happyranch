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
Python 3.12+, uv, Node 24/npm, Go 1.23.12+, git, GNU tar, gzip and sha256sum.
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
