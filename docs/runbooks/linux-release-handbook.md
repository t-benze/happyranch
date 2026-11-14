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
The remote-access installer defaults to the separate source layout at
`/opt/happyranch/venv/bin/python`. Its `--layout deb` option uses the packaged
`/opt/happyranch-desktop/python/bin/python3 -I` and packaged sidecar. See
[Desktop package host](../../deploy/remote-access/RUNBOOK.md#desktop-package-host)
for provisioning before installation. The installer validates the dpkg-owned
prefix and executables without creating or changing their permissions. The
`happyranch` service account must have execute permission on both executables
(including the resolved bundled Python target) and search permission on every
payload parent through `/opt/happyranch-desktop` and `/opt`. Unix DAC owner,
primary/supplementary group, then other bits determine access. Missing access
refuses provisioning/install before configuration or unit publication, naming
the path and missing permission. Fresh-host provisioning rechecks after account
creation.

Managed remote access also enforces the cross-config port contract before
provisioning publishes files or install starts services: sidecar `ConnectorAddr`
must be exactly `127.0.0.1:<managed.bind_port>`, and ingress must differ from
`daemon_port`, permanent DEV 8765 and all excluded daemon ports. Deb staging
writes matching sorted unique connector `excluded_daemon_ports` and sidecar
`ExcludedDaemonPorts` containing 8765 and the configured daemon port (8766 for
RELEASE), preserving any extra operator exclusions. List entries must be
integers in 1..65535. Install and check-drift reject missing or inconsistent
staged lists or addresses. Older source configs may omit the lists; the shared
Go sidecar retains its previous absent-list behavior for Mac configs. See the
Desktop package host runbook above for inputs and operator recovery.

Inside a runtime session (nonempty `HAPPYRANCH_RUNTIME_SESSION_ID`), the packaged
`bin/happyranch` CLI refuses an unset or empty `HAPPYRANCH_DAEMON_HOME`, exits
nonzero and names that variable in one stderr line. Outside a session its
behavior is unchanged. The desktop child sets the home explicitly; the planned
release service must set it to `~/.happyranch-release`. Executors and jobs
preserve that inherited home. This catches partial environment scrubs only:
same-UID processes that clear both variables (for example `env -i`) are not
detected. The daemon launcher is unchanged.

Thread-delivery release requests stay under
`<daemon-home>/client-thread-delivery-releases/<org-slug>`. The daemon home must
already exist and belong to the current UID; it is opened without following a
final symlink. Only the two store levels are created, with mode 0700, retaining
descriptor-relative no-follow traversal, ownership/identity rechecks and
no-overwrite/no-unlink custody. With no home override the root is still
`~/.happyranch`. The canonical skill store's fallback also uses the selected
daemon home (`<daemon-home>/canonical-skills`); explicit store/settings roots
retain precedence.

The disposable sequential-venue fence protects both persistent account homes,
`<passwd-home>/.happyranch` and `<passwd-home>/.happyranch-release`, using their
realpaths independently of `HOME` and `HAPPYRANCH_DAEMON_HOME`. A candidate home
or org root inside either receives `sequential_venue_live_home` before any
file open, including live aliases. A separate disposable home remains eligible
under the existing marker and identity checks.

Focused native home-isolation checks use temporary homes and synthetic request
identities, with no daemon, installation or retired Python-unit selection:

```sh
uv run pytest packaging/linux/test_home_isolation.py -q
```

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
The same build job then runs the installed-package smoke on **both** rows and
uploads `happyranch-installed-deb-<arch>-<run>-<attempt>`, including on failure.
This adds steps only: the manual trigger, matrix, read-only contents permission
and required PR/merge checks remain unchanged.
The package omits generated `__pycache__` directories, normalizes shared-library
and documentation files to mode 0644, and adds a Debian changelog. Original PR1
license/notice bytes remain in `/usr/share/doc/happyranch/licenses/`; the Debian
copyright file points there and to the system Apache-2.0 common license.

### Installed-package smoke (disposable Ubuntu 22.04)

```sh
# Normal non-root user with passwordless sudo; fresh evidence directory:
bash packaging/linux/smoke-installed-deb.sh /absolute/happyranch_0.1.0-1_arm64.deb /tmp/installed-deb-evidence
```

Run only on a disposable machine with no registered `happyranch` package. The
script installs and removes that package. It first asserts native dpkg
architecture, installs the .deb and its Depends from stock Jammy archives with
recommendations disabled, checks apt consistency, runs `happyranch --help`, and
imports system GTK3/WebKit2 4.1 **before** installing display-test tools. Package
inventories before/after this phase record what was already present. Only then
does it install Xvfb, Openbox, xdotool, scrot, desktop-file-utils and dbus-x11
(plus their dependencies). No build/runtime dependency is added as a test tool.

It validates `/usr/share/applications/happyranch.desktop` and executes its
installed Exec command as the normal user, with Xvfb, a window manager, private
D-Bus and isolated HOME/XDG directories. Like PR2 QA it discovers the port from
the app home's `daemon.pid`/`daemon.port`, verifies the daemon is the app's child,
checks HTTP200/status `ok` at `/api/v1/health`, and compares `/` byte for byte
with the installed built `web/dist/index.html`. It captures the actual focused
window in `window.png`; a reviewer must view the PNG to assess rendered content.
Ctrl+Q must exit the app with status0, leave no daemon process and remove both
markers. Package removal must leave no package-specific files, links or
directories and must preserve the entire user-data home (bytes and metadata),
including a sentinel. Shared system directories are excluded from payload
leftover checks. The isolated user-data fixture is deleted only after that
preservation assertion. Failures return nonzero; teardown cannot change a
failed assertion to a pass.

Evidence contains `result.json` (stage, individual checks, command exit codes,
architecture, package hash, PID/port, removal/preservation result and cleanup
errors), `window.png`, installed desktop entry, served HTML and command/display
logs. Failed runs may have only partial evidence; absent screenshots/checks are
not passing results. Download both architecture artifacts from the exact-head
manual run, inspect the real job/step logs and `result.json`, and actually view
both screenshots. Record the run ID, head SHA and evidence paths in the PR.

**Hosted-runner smoke is not a clean-machine test.** GitHub runner images carry
preinstalled software; successful pre-display install/import checks cannot prove
that every dependency is declared on an otherwise empty machine. Native arm64
Ubuntu22.04 runner evidence does not qualify a clean arm64 install or Debian12.
Keep those checks explicitly NOT RUN until separately exercised. This workflow
remains manual-only and must never become a required PR/merge check.

Debian version mapping is deliberately explicit: dotted numeric releases stay
unchanged, `aN`/`bN`/`rcN` become `~aN`/`~bN`/`~rcN`, `.postN` becomes
`+postN`, and `.devN` becomes `~devN` (after any preceding suffix). The packaging
revision defaults to `-1`. Thus `0.1.0` → `0.1.0-1`, `1.2.3rc1` → `1.2.3~rc1-1`,
`1.2.3.dev2` → `1.2.3~dev2-1`, and `1.2.3.post1` → `1.2.3+post1-1`.
Other version forms require an explicit mapping review and are refused.
For a packaging-only update of the same payload, pass `--debian-revision 2`
(a positive integer without leading zeros). This produces `0.1.0-2` alongside
`0.1.0-1`, updates control/changelog/filename consistently, and leaves the
payload's upstream version/build-info unchanged. Never reuse an already published
version/architecture for different bytes. The manual workflow uses revision 1;
download its intermediate payload and use this option locally for a packaging
revision. No workflow trigger or signing automation is added.

### Installed layout and connector separation

The single payload prefix is **`/opt/happyranch-desktop`**. The existing
connector scripts (`scripts/ci/m1_install_ready_uninstall.sh`,
`m2_tailnet_daemon.sh`, and the M3 recovery consumer) use
`/opt/happyranch/{venv,bin}`, `/etc/happyranch`, service state under `/var/lib`
and `/run`, and `happyranch-connector.service` /
`happyranch-tsnet-sidecar.service`. Their provisioning also requires
`/opt/happyranch` to be absent. A separate prefix avoids owning even that parent.
The desktop package ships the connector Python modules and sidecar, but installs
no connector configuration, accounts, systemd units or enablement, and never
invokes the connector installer. Explicit `provision/install --layout deb`
uses those packaged executables and retains separate config/state ownership. See
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

For arm64 use `happyranch_<debversion>_arm64.deb`. Local .deb installation needs
no HappyRanch apt source or archive key. The package deliberately ships neither
a sources entry nor an archive keyring, and has no maintainer scripts to manage
them. Source enrollment is an explicit user action below; removal/purge leaves
that separately managed enrollment alone.

## Prepare, externally sign and publish the apt archive

The founder alone holds the real GPG signing key and publishes the Pages tree.
Agents/build hosts must never receive it. `build-apt-repo.py` uses system Python
3.10+ stdlib, `dpkg-deb`, `dpkg-scanpackages` (Debian/Ubuntu `dpkg-dev`) and `gpgv`.
It invokes no `gpg`, imports no key, consults no user GPG home and has no signing
or key-generation mode. `dpkg-scanpackages --multiversion` supplies real Debian
metadata/checksums while retaining earlier package versions; it avoids requiring
`apt-utils` merely for index generation. `gzip` output is deterministic.

Run these stages on an unpublished local staging tree, with no concurrent
prepare/finalize or writers. Never prepare directly in the served tree. A failed
prepare/finalize is not publishable; resolve the error and repeat the applicable
stage. Preserve the prior published snapshot until the founder replaces it with
the complete successfully finalized directory.

1. Dispatch the existing approved workflow **once on reviewed main**, then
   record its run ID, full source SHA and both matrix conclusions. Download each
   successful row's artifact; record the `.deb` SHA256 and verify the intermediate
   payload's supplied checksum. The commands below use placeholders for that
   actual run ID and artifact names; they do not add a workflow or trigger.

   ```sh
   gh workflow run build-linux-app.yml --ref main
   gh run view RUN_ID --json headSha,status,conclusion,jobs
   gh run download RUN_ID --name happyranch-linux-x86_64-RUN_ID-1 --dir /absolute/artifacts/amd64
   gh run download RUN_ID --name happyranch-linux-aarch64-RUN_ID-1 --dir /absolute/artifacts/arm64
   sha256sum /absolute/artifacts/amd64/*.deb /absolute/artifacts/arm64/*.deb
   ```

2. Prepare with one or more `.deb` files. Keep the existing tree for subsequent
   versions; the pool is retained and all indexes regenerated. A repeated input
   is idempotent, including Release Date when indexes are unchanged. Replacing
   an existing version/architecture with different bytes is refused.

   ```sh
   /usr/bin/python3 -B packaging/linux/build-apt-repo.py prepare --repo-dir /absolute/apt-repo /absolute/artifacts/amd64/happyranch_0.1.0-1_amd64.deb /absolute/artifacts/arm64/happyranch_0.1.0-1_arm64.deb
   ```

   The tree contains `pool/main/h/happyranch/*.deb`,
   `dists/stable/main/binary-{amd64,arm64}/Packages` and `Packages.gz`, and
   `dists/stable/Release` with Origin/Label HappyRanch, Suite/Codename stable,
   Architectures amd64/arm64, Components main, UTC Date and MD5Sum/SHA256 for
   all four indexes. An empty architecture index is valid until its package is
   added. **Valid-Until is omitted:** this manually published static archive
   remains installable between releases, at the cost of no expiry-based replay
   protection. The founder must distribute fresh versions and communicate key
   changes through a trusted channel. Every prepare removes stale InRelease and
   Release.gpg, even for unchanged inputs; sign again before publishing.

3. Transfer the unsigned Release to the founder's own signing environment.
   The tool prints the exact Release path, SHA256 and the following two commands.
   The founder verifies those bytes, sets `SIGNING_KEY` to their signing-key
   fingerprint and runs the commands themselves. If signing on another machine,
   use the corresponding transferred Release path, and return only signatures
   and the public export to the build host. Do not modify Release after signing.

   ```sh
   gpg --local-user "$SIGNING_KEY" --output /absolute/apt-repo/dists/stable/InRelease --clearsign /absolute/apt-repo/dists/stable/Release
   gpg --local-user "$SIGNING_KEY" --output /absolute/apt-repo/dists/stable/Release.gpg -abs /absolute/apt-repo/dists/stable/Release
   # Public certificate only; never export-secret-keys:
   gpg --export "$SIGNING_KEY" > /absolute/founder-public.gpg
   ```

4. Finalize with both external signatures and the founder-supplied public
   export (binary or ASCII PUBLIC KEY BLOCK). The tool dearmors public armor
   without importing it, rejects non-public certificate packet types, and uses
   `gpgv` with an isolated temporary public keyring. Both signatures must verify;
   InRelease's extracted bytes must equal Release exactly. The current indexes
   must equal a fresh scan of the pool and Release must cover their checksums.
   Missing/bad signatures, a changed Release, index or package fail closed.

   ```sh
   /usr/bin/python3 -B packaging/linux/build-apt-repo.py finalize --repo-dir /absolute/apt-repo --inrelease /absolute/apt-repo/dists/stable/InRelease --release-gpg /absolute/apt-repo/dists/stable/Release.gpg --public-key /absolute/founder-public.gpg
   ```

   Success copies the verified signatures and publishes the dearmored **public**
   `happyranch-archive-keyring.gpg` inside the local tree. It does not upload or
   push anything. The supplied public key's identity/fingerprint must be checked
   with the founder separately; mathematical verification alone does not identify
   the founder. The builder does not discover keys or choose a signing identity.

5. **Founder only:** fill the clearly marked Pages URL in the generated
   `README.txt`, inspect it and `index.html`, then publish the entire finalized
   tree to the founder's GitHub Pages static site. `.nojekyll` is included.
   This runbook/tool creates no Pages site, branch, release, publishing workflow,
   job or required check. Pages setup and the real public URL remain founder work.
   Keep the pool's old versions when adding N+1, and repeat prepare → external
   sign → finalize → founder publication. Publish a complete snapshot so clients
   do not fetch indexes from a different release.

Focused tests use real dpkg/GPG tools and a throwaway key generated **inside the
test only** under a temporary GNUPGHOME; no real key is used:

```sh
/usr/bin/python3 -B packaging/linux/test_build_apt_repo.py -v
/usr/bin/python3 -B packaging/linux/test_build_deb.py -v
```

### User installation and updates from Pages

**Placeholder:** the founder must replace
`https://FOUNDER-SET-OWNER.github.io/FOUNDER-SET-REPOSITORY` with the actual
published archive URL. Do not run these placeholder commands unchanged.
Obtain the public certificate and its fingerprint from the founder through a
trusted channel. Compare the fingerprint before enrolling the key:

```sh
curl -fsSLo happyranch-archive-keyring.gpg https://FOUNDER-SET-OWNER.github.io/FOUNDER-SET-REPOSITORY/happyranch-archive-keyring.gpg
gpg --show-keys --with-fingerprint happyranch-archive-keyring.gpg
sudo install -d -m 0755 /etc/apt/keyrings
sudo install -m 0644 happyranch-archive-keyring.gpg /etc/apt/keyrings/happyranch-archive-keyring.gpg
```

Create `/etc/apt/sources.list.d/happyranch.sources` with:

```text
Types: deb
URIs: https://FOUNDER-SET-OWNER.github.io/FOUNDER-SET-REPOSITORY
Suites: stable
Components: main
Architectures: amd64 arm64
Signed-By: /etc/apt/keyrings/happyranch-archive-keyring.gpg
```

`Signed-By` limits this key to the HappyRanch source. Do not use `apt-key`,
`trusted=yes` or insecure-repository overrides. `/usr/share/keyrings` is also
supported, but this runbook uses `/etc/apt/keyrings` for manual enrollment.

```sh
sudo apt update
sudo apt install happyranch
happyranch-app
# Or select HappyRanch from the applications menu.
# Quit the app first, then update it along with the system:
sudo apt update
sudo apt upgrade
# Quit before removal; user data remains even on purge:
sudo apt remove happyranch
# Optional: remove package configuration too:
sudo apt purge happyranch
# Unenroll the separately managed source/key when no longer wanted:
sudo rm /etc/apt/sources.list.d/happyranch.sources
sudo rm /etc/apt/keyrings/happyranch-archive-keyring.gpg
sudo apt update
```

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
