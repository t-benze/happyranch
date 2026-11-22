# Linux release handbook

## Main app scheme and Connection dialog (S7-L1)

Linux Main always loads the installed `web/dist` at `happyranch-app://app/`
for local and remote targets. No environment opt-in is required. Header
Connection and the labelled Access wasn't saved warning request the SPA
Connection dialog through `app.dialogRequested`; unavailable delivery shows
the named retry action without automatic replay. No native connection-screen
fallback or second Remote runtime window is available. Native sensitive
consent, invite display and credential dialogs remain in `client_ui.py` and
`host_bridge.py`; the same Main owner retains candidate/active state.

The GUI uses the in-process RemoteDaemon adapter and opens no RuntimeProxy
listener. If Main is absent or closed, a remote open reports runtime unavailable
and cleans up its candidate. Foreground CLI use still owns its RuntimeProxy and
prints `Runtime reachable: <url>` on its controlling terminal.

If WebKit or the packaged bridge fails, retain the honest load error. Use the
installed CLI in a terminal: `happyranch connection host --help` or
`happyranch connection client --help`, then the existing `status`,
`accept-invite` or `connect-saved` actions described below. These commands do not
require WebKit; invitations/consent stay terminal-only. No successful connection
or saved access is promised. Reinstall a qualified release if its assets fail.

Payloads include `host_bridge.py`, `client_ui.py`, `connection_contract.py` and
the unchanged `native-connection-bridge.v1.json`. Linux no longer builds or
ships `desktop/connection/`, `connection_window.py` or `dist-connection`.
Mac still packages the shared web Connection shell.

`build-deb.sh` validates the main `web/dist/resource-manifest.json` using the
packager's trusted `connection_contract.Assets`, never supplied Python code.
The reader bounds the manifest to 1 MiB, 4096 listed resources, 64 MiB per file,
256 MiB total and 8192 inventory entries. It rejects duplicate/nonliteral paths,
symlinks/nonregular files, missing/unlisted files, incorrect sizes and hashes.
The runtime's existing Bundle inventory/no-follow delivery checks remain.
Both builders honor `TMPDIR` for scratch; choose a short writable non-`/tmp`
directory when the host's `/tmp` quota is exhausted.

Inspect the built package with `dpkg-deb -c /absolute/package.deb`: confirm
`host_bridge.py` and the v1 JSON exist and the retired paths above do not.
Extract with `dpkg-deb -x /absolute/package.deb /absolute/fresh-directory` and
validate its main manifest using the trusted source reader. The focused
`test_build_deb.py DebPackageTests.test_tampered_main_manifest_is_refused -v`
case exercises refusal of missing/altered manifests, file tampering, extras
and a contained symlink. This is maker verification; installed launch, minimum
engine, native focus and genuine remote-pair acceptance remain QA work.

## Remote configuration read-only routing (S8-2b, Linux native)

The committed `protocol/remote-client-config-policy.v1.json` is shipped unchanged
by `build-payload.sh` into `share/happyranch/desktop/`, beside
`app_scheme_webkit.py`. The native Main scheme loads that installation-owned
file once. Direct source runs use the repository's `protocol/` copy; a missing
or invalid installed policy refuses remote API routing with `bundle_unavailable`.

For a committed remote target, each matching method and decoded path template
returns HTTP 403 with `{"detail":{"code":"remote_read_only"}}` before generation
admission, body reads, compatibility/bootstrap probes or any peer I/O. Query
strings, encoded parameters and trailing slashes do not bypass the match. The
forward worker also checks its captured remote target before bootstrap. A
candidate remote does not change a committed local target's write behavior.

All 21 configuration entries are covered, including schedule **PATCH** (edit).
Local configuration writes and unmatched remote operations/GETs keep their
existing forwarding and generation checks. No retry, credential change or daemon
remote-allowlist change is involved. Visible read-only controls, localized Host
scope copy and draft-preserving handling of this 403 are the web leg on the same
PR; this native slice alone does not establish that UI behavior.

Focused standalone boundary checks use two ephemeral loopback stand-in peers,
synthetic authority and GIO without a display, outside the retired unit suite:

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_scheme_remote_policy.py -v
/usr/bin/python3 -B scripts/check_remote_client_config_policy.py
```

Use a task-owned non-`/tmp` TMPDIR when the host's `/tmp` quota is exhausted.
These checks do not establish real-remote, installed-package or visual acceptance.
Independent QA still uses the reviewed head and an isolated WebKitGTK scheme
smoke on Xvfb (Broadway fallback when Xvfb is absent); never use the live daemon.

## Native language preference (S8-1-L)

`packaging/linux/desktop/app_locale.py` supplies the native preference
store used by the Main bridge and GTK launcher. Its closed API is `load()`,
`current()`, and `set(value)`, each returning a fresh
`{"current": "en" | "zh-CN", "persisted": bool}` dictionary compatible with
the shell locale snapshot. First access reads once; repeated loads preserve a
session-only choice. Unsupported values (including non-strings) raise
`ValueError` without changing state or disk.

The installation-local file is `$XDG_CONFIG_HOME/happyranch/locale`, or
`~/.config/happyranch/locale` when XDG is empty, unset or relative. Its content
is ASCII `en` or `zh-CN`, optionally followed by one newline. Missing, corrupt,
unknown, oversized (over 64 bytes), unreadable, symlinked or non-regular files
fall back to the existing rule: `LANG.lower().startswith("zh")` selects
`zh-CN`; everything else selects `en`, with `persisted: false`.

The module copies the desktop lowercase `happyranch` directory and atomic
write conventions locally, with descriptor-relative no-follow parent traversal
and bounded nonblocking regular-file reads. A mode-0600 exclusive temporary
file in the same directory is flushed, fsynced and atomically replaced; the
`happyranch` directory is then fsynced. Before writing the file, every write
fsyncs the containing directory of every path component after opening that child
without following links, whether mkdir created it or found it already present.
This runs from `/` through the config parent, so retries confirm any ancestor
entry left by a failed sync, with no durability state cached between calls.
For default `~/.config/happyranch`, it syncs `/`, every HOME ancestor, HOME and
`~/.config`, then `happyranch` after replacement. For absolute XDG config paths,
it syncs `/` and every directory through `XDG_CONFIG_HOME`, including containing
directories of missing intermediate ancestors, then `happyranch` after
replacement. The bound is `/`: the traversal may create any component below
root, and there is no containing entry above root to confirm.
`persisted: true` is returned only after these steps succeed. Write failure
keeps the choice in memory with `persisted: false`; a failure after replacement
may leave the new file visible without confirmed durability. One lock covers
memory and disk throughout each call, ordering concurrent threads by lock
acquisition. The assumption is one owning native process with stable
environment; concurrent processes have no ordering guarantee.

The admitted Main document accepts `app.locale.set` through the existing string
`call` facade, with exactly `{"schema":2,"locale":"en"|"zh-CN"}`. Native rejects
malformed/duplicate-key JSON, wrong types, missing/extra fields and other values
as `invalidRequest`, `effect:none`, `recovery:none`, without a store call, file
change or event. The JSON-string result is exactly
`{"schema":2,"locale":{"current":"en"|"zh-CN","persisted":boolean}}`, using the
snapshot returned by the completed store write. Write failure resolves with
`persisted:false` and the live choice retained. Both `app.status` and the full
`app.stateChanged` read the store, rather than recomputing `LANG`.

A bounded process-owned FIFO admits choices on the GTK context in observed
order, including across view reconstruction. One worker writes at a time. Native
returns the result, then awaits the private named-world relay's reply/publication
barrier before starting the next write. The relay posts the setter reply before
the full state event, including `persisted:false`, only for the current admitted
document and its existing subscription. Other state observations defer during
that interval, so they cannot publish the new locale ahead of its reply. A
retired document drops queued, unstarted writes; an already-started write may
finish, with an unknown-effect retirement error and no retired-document event.
The setter performs no daemon request, target/generation change, navigation,
service restart or app relaunch.

The GTK launch translation closure reads `load()` once instead of `LANG` alone.
Native header, menus and dialogs retain that language until next app launch;
service-only Restart is not an app launch. Built-in target/candidate names also
retain the launch language in native state. No synchronous locale property or
HTML `lang` injection precedes page scripts: web obtains native locale through
the asynchronous status/event bridge. Web live translation and truthful Saved /
session-only UI remain the separate S8-1-W slice.

For isolated verification, set `TMPDIR` and `XDG_CONFIG_HOME` to disposable
directories outside real user config, then run from `packaging/linux/desktop`:
`GSETTINGS_BACKEND=memory /usr/bin/python3 -B -m unittest test_app_locale -v`.
Check a saved choice in a new process, a read-only config directory, corrupt
files, and rapid threaded sets; no daemon or Product E2E is needed.
Bridge ordering/refusal and fresh-process native-header checks use the existing
direct native test venue:
`GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_events.py -v`
and `GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_main_header.py -v`
from the repository root. Real WebKitGTK/header visuals need a disposable
desktop/Xvfb session; headless checks do not establish that acceptance.

## Build the intermediate payload

Founder authorizes one manual build workflow under THR-291.
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
uv, Node 24/npm, Go 1.23.12+, a C compiler and libc development headers (CGO),
git, GNU tar, gzip, sha256sum and binutils
(`readelf`). No host Python is required; the builder installs its private pinned CPython 3.12.10
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
The user-run client helper
`bin/happyranch-home-bridge` uses `CGO_ENABLED=1`; it needs the C toolchain.
It runs unprivileged with `--home-session-v1`, without service registration,
saved credentials or a network-state profile.
Its private Go module cache uses `-modcacherw` so build cleanup can remove it.
Existing version/architecture outputs are refused; use a fresh destination.

The payload also carries the official **Headscale v0.25.1** native Linux asset
at `libexec/headscale/headscale` (installed as
`/opt/happyranch-desktop/libexec/headscale/headscale`, mode 0755, root-owned by
dpkg). This fixed path sits outside `bin/` and the daemon's bundled-bin PATH
surface. It has no wrapper or symlink and stays inert; TASK-16780 owns service
and installer wiring. Building the payload never executes it or starts/enables
a service. No download happens at Enable.

The same native Go build also ships the inert root-run home CA materializer at
`libexec/remote-access/happyranch-home-ca` (installed as
`/opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca`, root:root
0755). It is outside `bin/`, `/usr/bin` and `HAPPYRANCH_BUNDLED_BIN`; no service
or helper calls it yet. Its fixed `ensure`, `renew-leaf` and `status` verbs and
root-only staging custody are described in the [remote-access contract](../agent-guides/contracts/remote-access-and-remote-jobs.md#inert-linux-home-ca-materializer-thr-327-slice-3b-2e).
The command uses stdlib and the existing first-party `homeca` package only.
Python ELF repair/checking remains restricted to `python/`.

`packaging/linux/headscale-pin.json` pins tag, official amd64/arm64 asset names,
SHA-256 values and embedded Go version. The already-verified managed Python
downloads over HTTPS into private build scratch, allowing only github.com and
its explicit release CDN `release-assets.githubusercontent.com` on redirects.
Each request has a 20-second socket timeout; streaming is capped at 128 MiB
and a 180-second deadline (checked after bounded socket reads, including EOF).
No new build tool is needed. `LINUX_HEADSCALE_DOWNLOAD_FAILED` or
`LINUX_HEADSCALE_SHA256_MISMATCH` aborts before the binary is installed or
inspected. The pin was checked against upstream release `checksums.txt` and
fresh downloads of both assets.

Headscale's exact pinned BSD-3-Clause license ships in
`licenses/headscale-LICENSE`. `headscale-modules.json` inventories the verified
binary's linked modules from `go version -m`; their original notices are in
`licenses/headscale/modules/`. Downloads use private Go module and GOPATH
checksum-state caches, the public proxy and checksum DB, and must match the embedded module sums. Missing
originals fail the build. `headscale-build-info.json` records asset/tag/hash,
Go 1.23.4 and the matching toolchain module sum; `headscale/go-runtime/` retains
that toolchain's license and vendor notices. All are also copied to the .deb's
`/usr/share/doc/happyranch/licenses/`.

After copying the interpreter and runtime wheels,
`python-elf-stack.py --fix --mdwe` checks every regular ELF in `python/`, regardless of name or mode.
It reports each missing/executable `PT_GNU_STACK` and repairs it to a
non-executable stack. Missing headers are added using an appended read-only
program-header mapping; existing LOAD segments, code and data stay intact.
This avoids patchelf's assertion failure on the pinned BOLT interpreter.
`readelf -lW` independently verifies every resulting stack header.
The bundled interpreter then starts and joins a thread under
`systemd-run --user -p MemoryDenyWriteExecute=yes` when a user manager is
available. Otherwise a separate Python helper sets and confirms
`prctl(PR_SET_MDWE, PR_MDWE_REFUSE_EXEC_GAIN)` before exec. Only an unavailable
user manager together with an unsupported kernel MDWE operation permits an
explicit `SKIP MDWE` reason; actual thread/loader failures fail the build.
These checks change no systemd hardening and install no host packages.

The Go notice collector and `licenses/go-modules.json` exclude only the exact
first-party `happyranch/tsnethealth => ../../shared/tsnethealth` local replacement
when both Go module directories resolve to `app/shared/tsnethealth` inside the
exported repository. This matches Mac notice handling and the sidecar's retained
third-party inventories; the module is covered by `HappyRanch-LICENSE`.
All other non-main modules still require their original notices. The focused
native packaging checks below also cover this exclusion, inventory output and
refusal of unlicensed near-miss or escaping replacements.

The extracted payload contains `python/`, `web/dist/`, `licenses/`,
`build-info.json`, `bin/{happyranch,happyranch-daemon,happyranch-tsnet-sidecar}`,
`libexec/headscale/headscale` and `libexec/remote-access/happyranch-home-ca`.
The payload additionally contains the zero-option
`bin/happyranch-remote-access-policy-renew`, which launches only the fixed bundled
Python `-I -m runtime.remote_access.policy_renew` with an empty environment.
The .deb also installs the exact B14 renderer bytes as
`/usr/lib/systemd/system/happyranch-remote-access-policy-renew.service` and
`happyranch-remote-access-policy-renew.timer`. Package construction enables or
starts neither unit; Enable/Disable timer wiring remains I4b-2.

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
[Desktop package host](remote-access-runbook.md#desktop-package-host)
for explicit operator provisioning before installation. Public clean-install
Enable now composes omitted-input provisioning, Headscale preparation, exact
managed fragments, protected @2 invite config and forward activation. It derives
release user/home only through the authenticated UID's B8 binding; the desktop
supplies no JSON, ExpectedPeer or enrollment key. Complete staged generations
(including ReplaceFailed closure) and proven post-Disable retained bytes reuse
the same owners under one activation flock and original 480-second budget.
Systemd supplies service state/log/runtime directories at start; the helper leaves
absent service-owned paths absent. Partial/unknown/pending residue refuses and
retains existing recovery routes. Installed clean Enable/READY acceptance remains
I6; temporary-root/native-simulation tests do not establish it.
The installer validates the dpkg-owned
prefix and executables without creating or changing their permissions. The
`happyranch` service account must have execute permission on both executables
(including the resolved bundled Python target) and search permission on every
payload parent through `/opt/happyranch-desktop` and `/opt`. Unix DAC owner,
primary/supplementary group, then other bits determine access. Missing access
refuses provisioning/install before configuration or unit publication, naming
the path and missing permission. Fresh-host provisioning rechecks after account
creation.

The embedded-tailnet sidecar disables new Tailscale log uploads, clears historical `tailscaled.log1.txt`/`tailscaled.log2.txt` spool files before each start so queued records are not uploaded, and keeps raw backend logs out of the journal; symlinked or nonregular spool paths fail startup without being followed, while durable state and log configuration are preserved.

The Linux sidecar writes one compact `tsnet-health ` JSON line to stderr (the
service journal) after successful join and every five minutes. One-second local
status polling emits backend/peer-online/path changes at most once per 30 seconds.
Only join and five-minute samples send one disco ping (three-second timeout);
STUN refresh shares the ten-minute cap and cached age. A changed peer identity,
online state or address/relay hint invalidates the cached verified path to `none`
until the next scheduled ping; hints never establish a direct path.

The record fields are `v`, `at`, `trigger`, `backend`, `control`, `health`, `udp`,
`nat_varies`, `portmap`, `derp_home`, `derp_ms`, and `peer` (`name`, `online`,
`path`, `relay`, `rtt_ms`, `handshake_age_s`, `direct_kind`). `at` reflects the
cached probe's measurement age; `portmap` is `unknown` in v1. Records are bounded
to 2 KiB and eight DERP regions. **The expected Mac peer's hostname is disclosed**;
other peers, keys, addresses, AuthURL and raw health/error text are excluded.
Missing/ambiguous peers use an empty-name/offline/none placeholder.

When systemd supplies `RUNTIME_DIRECTORY`, the same JSON is atomically written
to `/run/happyranch-tsnet-sidecar/health.json` using a same-directory temporary
file, explicit fchmod 0644 despite UMask 0077, fsync and rename. Existing symlink
or nonregular targets are refused. Without that variable, only the journal is
used. A file-publication failure leaves the journal as fallback. Health work is
cancelled at shutdown without delaying transport teardown or sd_notify.
`RuntimeDirectoryPreserve=yes` retains the file after stop (not reboot).
[systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectoryMode=)
documents the directory mode default of 0755, so the desktop user can traverse
the directory and read this 0644 file without privilege. Existing units are not
automatically upgraded: `ManagedInstaller._preflight` compares their bytes with
the newly rendered units and refuses any difference before install or uninstall.
An operator must inspect and explicitly reconcile the installed units with the
new rendering, including `RuntimeDirectoryPreserve=yes`, and reload systemd to
apply changed unit files. Rerunning install alone does not replace an old unit;
uninstall with the new renderer also refuses the old bytes. This documents the
existing drift refusal, not an automatic upgrade procedure. Source publication
performs no live unit action. Sidecar on-demand refresh remains the five-minute
periodic cadence in v1.

Managed remote access resolves the running release daemon through the root
verifier and its provisioned data-home record. There is no fixed release port.
The verifier reads `daemon.port`, excludes DEV 8765 and configured exclusions,
and proves the packaged unit's MainPID owns the listener and the connected
socket before the connector sends a bearer/request. Sidecar `ConnectorAddr`
remains exactly `127.0.0.1:<managed.bind_port>`; staged connector and sidecar
exclusion lists retain 8765 and operator exclusions. For a custom XDG home,
provision the connector with `--data-home` pointing to that same absolute
`$XDG_DATA_HOME/happyranch`: its default is the passwd account's
`.local/share/happyranch`, and it does not discover the user manager's XDG value.
See the [managed remote-access runbook](remote-access-runbook.md).

Inside a runtime session (nonempty `HAPPYRANCH_RUNTIME_SESSION_ID`), the packaged
`bin/happyranch` CLI refuses an unset or empty `HAPPYRANCH_DAEMON_HOME`, exits
nonzero and names that variable in one stderr line. Outside a session its
behavior is unchanged. The desktop child sets the home explicitly; the packaged
release user unit resolves it to `$XDG_DATA_HOME/happyranch` when the manager
has an absolute XDG value, otherwise `~/.local/share/happyranch`. Executors and jobs
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

The disposable sequential-venue fence retains the realpaths of
`<passwd-home>/.happyranch` and `<passwd-home>/.happyranch-release`, and adds
`<passwd-home>/.local/share/happyranch`. An absolute `XDG_DATA_HOME` in the venue
process adds `$XDG_DATA_HOME/happyranch`; an absent, empty or relative value
removes none of the account exclusions. Candidate home or org-root containment,
including aliases, receives `sequential_venue_live_home` before any file open.
The fence does not query the user manager or read installation records. A custom
XDG value present only in the user manager is not detected. A separate disposable
home remains eligible under the existing marker and identity checks.

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

Health includes additive `api_contract: {current: 1, min_client: 1}` and
`release: "0.1.0+<source_sha>"`, using the payload's existing `build-info.json`
beside `python/`. Source or missing/invalid build identity reports `"unreleased"`;
see the [health contract](../agent-guides/web-and-cli.md#host-compatibility-health-metadata).

## Inert Headscale custody prefix (3b-2f-1)

The explicit root operator command `python -m runtime.remote_access.installer
prepare-headscale --layout deb` prepares the locked dedicated
`happyranch-headscale` OS identity, fixed 0700 config/state/runtime directories,
0600 TCP443 invite-to-node ACL and separate root-owned hardened system unit.
It also derives the LAN address and publishes receipted config/DERP YAML (the
inert 3b-2f-2 prefix below). It never enables, starts or reloads the unit, runs
Headscale or activates the prepared leaf/CA material. The staged home connector
activation guards and
`home_transport=False` stay unchanged. Database names are `happyranch-node`
and dedicated Mac-invite user `happyranch-invite`; actual user creation/minter
injection remains future activation work.

The 3b-2f-2 config prefix uses bounded kernel `NETLINK_ROUTE` dumps, including
IPv4/IPv6 defaults and every equal-cost next hop, without metrics ranking or
supplied addresses. A missing kernel preferred source refuses as `inventory`;
it never guesses from interface address order. The merged selector and exclusive
four-port preflight run before any publication. The root-only
`/var/lib/happyranch-remote-access/headscale-publication.json` receipt is written
last and binds config/DERP, leaf/key and public-CA paths/digests, the empty CA
directory, service IDs and selected IP. The merged config-only receipt remains
admitted for extension without rewriting config inodes.
Unchanged reruns are idempotent; changed addresses refuse `address-changed`.
Unreceipted files, missing members, pending residue and unsafe metadata refuse
without repair/deletion. Cleanup rechecks custody and digests before each unlink,
then retires the publication receipt. Interrupted removal with a retained receipt
refuses on retry. Preparation now invokes the fixed root-run materializer
(ensure/status; renew-leaf only when status reports due), validates its closed
status and binds the public CA fingerprint and staging digests. Only server
leaf/key are copied to service UID:GID 0600 custody. control-ca.pem is
root:happyranch 0640; empty-ca-directory is root:root 0755 and empty. The CA
private key is never read/copied by the installer and home-ca/ survives cleanup.
Every new member uses exclusive pending and atomic no-replace publication,
exact custody checks and owned-only cleanup before receipt retirement. Unchanged
reruns write nothing; interrupted extension/renewal refuses without repair.
Inert renewal retains the CA and never reloads a service. Authenticated
RefreshHomeAddress and running-service coordination remain later work. See the
[full publication/crash contract](../agent-guides/contracts/remote-access-and-remote-jobs.md#inert-linux-headscale-config-publication-3b-2f-2-prefix).

Account ownership is distinct from its shape. An exact-shape pre-existing
account without the protected creation receipt is reusable but **unowned**;
preparation visibly reports `preserved: not created by HappyRanch` and never
modifies/adopts it. Only actual successful useradd plus looked-up UID/GID may
publish root:root 0600 `headscale-account.json` under root:root 0700
`/var/lib/happyranch-remote-access`. A crash before publication leaves the
account unowned; there is no pre-creation intent. Shape mismatch, malformed or
foreign/mismatched receipt, unsafe/foreign custody or differing unit refuses
without repair or deletion.

Before any preparation effect, no-follow checks refuse foreign fragments and
applicable drop-ins across the default systemd load paths, including runtime,
local-vendor, vendor and separate `/lib` paths. The maintained 3b-2f-1 contract
lists every checked directory and the literal merged-`/usr` alias exception.
The same bounded no-follow link inventory used by cleanup runs across every
listed load path before preparation effects. Any symlink named for or whose
literal target basename is `happyranch-headscale.service` refuses, including
dangling wants/requires and aliases. Alias drop-ins in any of those paths cannot
become applicable: the alias itself refuses, even without a drop-in. Foreign
objects remain byte/mode/link-exact; refusal is typed secret-free `foreign-unit`
(unsafe parent/I/O: `custody-uncertain`).

The same admission also refuses **any object** whose name starts with
`happyranch-headscale.` anywhere under those unit roots, except the installer's
exact owned `/etc/systemd/system/happyranch-headscale.service` fragment. This
includes same-stem timer/socket/path/mount/automount triggers, masks and drop-in
directories. Every regular unit file and drop-in `.conf` is read as bounded
bytes: a literal `happyranch-headscale` reference refuses, regardless of directive
(Wants/Requires/BindsTo/Upholds/PartOf/OnFailure/OnSuccess/Triggers/Unit/Also or
otherwise). The installer's exact owned fragment is excluded from that content
check. One package-owned helper unit is also excluded, only at
`/usr/lib/systemd/system/happyranch-remote-access-helper.service`, only as a
regular single-link root:root 0644 file with bytes exactly equal to the trusted
in-code `installer.PACKAGED_HELPER_UNIT_BYTES`. Both inventory consumers use the
same predicate after their bounded no-follow read and signature checks; expected
bytes never come from the scanned filesystem. Native packaging tests bind the
shipped unit and build-deb's installed copy to that constant. Changed bytes,
metadata or type, same-name `/etc` or `/run` copies and helper drop-ins gain no
exclusion. Every regular file under `/usr/share/dbus-1/system-services`,
`/usr/local/share/dbus-1/system-services` and `/etc/dbus-1/system-services`, when
present, receives the same literal-byte check with no link following. The
bounded no-follow reader uses the existing 64 KiB per-file limit; read failure,
unsafe or changing files, oversize, or shared 65,536-entry/depth-32 exhaustion
refuses before any preparation effect. Foreign bytes/modes/links/identities stay
unchanged. Cleanup uses this same admission before stop/disable; A–E identity,
receipt authority, partial-cleanup uncertainty and explicit retry are unchanged.

This is sampled admission in the fixed default roots under a **cooperative
root-writer** assumption. Executables in `system-generators` directories and
root writers acting after preparation are outside this inventory; they can
create later activation policy. No generator execution/inspection or concurrent
root-writer fence is implemented. Custom compiled/environment unit load paths
and native whole-host acceptance remain existing limitations. Preparation never
starts/enables/reloads a unit and grants no later activation authority.

**Owned cleanup:** `python -m runtime.remote_access.installer disable-headscale
--layout deb` and legacy `uninstall --layout deb` stop/disable the exact owned
Headscale unit and remove its canonical policy, receipted config/DERP and fixed
custody. The
staged-profile uninstall refusal stays unchanged; helper Turn off remains later
work. Unreceipted/edited config or DERP and all DB/Noise/key/socket entries are
preserved and refused. The separate `home-ca/` staging tree is always preserved.
Cleanup shares that all-load-path admission. Before stop/disable, the additional
config-tree check for matching alias/enablement links anywhere below
`/etc/systemd/system` or `/run/systemd/system` also refuses with typed secret-free
`cleanup-uncertain`, preserving account, receipt, unit, custody and links without
stop/disable. This includes wants/requires links, foreign/dangling targets and
manual links to the managed unit: the inert installer creates no such links.
No-follow inventory is bounded; uncertainty/exhaustion refuses. Unrelated links
are preserved. A–E partial-cleanup and explicit retry semantics are unchanged.

A valid creation receipt plus fresh matching identity/shape, unshared group,
stopped unit and no process using the UID permits userdel, then conditional
groupdel. An unowned account/group stays with visible typed
`preserved: not created by HappyRanch`. Before userdel succeeds, failures keep
account/receipt. After userdel succeeds, groupdel failure keeps receipt and all
remaining objects, never recreates the user, and reports typed secret-free
`partial-cleanup-uncertain`, exit1. Retry freshly refuses UID reuse/changed
identity or shared/mismatched group; absent user skips userdel and a matching
empty group gets groupdel only. Already absent objects converge by removing the
receipt last, only after every owned object is confirmed absent. Other failures
are typed `cleanup-uncertain`, never success. No automatic repair/retry.

The inert payload/notices and all unrelated data stay. See the maintained
remote-access contract's 3b-2f-1 subsection for rulings A–E, exact
paths/bytes, ports, bounded startup and tests. Real root/systemd/NSS/bind
acceptance remains separate from temp-root verification.

## Desktop window (system Python)

The payload also contains `bin/happyranch-app`,
`share/happyranch/desktop/happyranch_window.py`,
`share/happyranch/desktop/app_locale.py`,
`share/happyranch/desktop/client_store.py`,
`share/happyranch/desktop/client_session.py`,
`share/happyranch/desktop/client_runtime.py`,
`share/happyranch/desktop/client_model.py`,
`share/happyranch/desktop/client_ui.py`,
`share/happyranch/desktop/host_bridge.py`,
`share/happyranch/desktop/connection_contract.py`,
`share/happyranch/desktop/native-connection-bridge.v1.json`, the verbatim shared pure codec
`share/happyranch/desktop/invite_v2.py`,
`share/applications/happyranch.desktop.in`, and the existing clay brand artwork
under `share/icons/hicolor/` (16, 32, 64, 128, 256, 512, 1024 PNG and scalable
SVG). The builder copies the existing Mac PNGs and Web SVG without changing them.
The desktop entry is a template: the deb builder replaces `@APP_EXEC@`
with `/usr/bin/happyranch-app` and installs the menu entry/icons. Building the
payload alone does not install files into `/opt` or `/usr/share`.

The packaged connection bridge also wires Linux Host Status, NextDevice,
ApprovePending, RejectPending and RevokeSelected on one private system-bus
connection. All calls have zero inputs; the three mutations use genuine polkit
authorization with a 122-second transport timeout. A mutation acknowledgement
is followed by Status; device lists are never predicted.
Host mutations return `busy` while Client paste/target confirmation is pending,
including asynchronous invite decoding. Client confirmations also return `busy`
during Host authorization; conflicting prompts are never queued. Host event subscribers
receive full nonsecret observations with a three-second visible-window poll;
unsubscribe and document teardown stop delivery. Fingerprints stay native;
bounded comparison metadata follows the closed v1 projection. A lost mutation
reply has unknown effect, permission denial has no effect, and missing helper
service reports not set up/unavailable with an unreadable device list. Revoke
confirms host denial separately from unknown network cleanup. I5c adds only
Enable, Turn off and Create invite, with zero inputs and 482/302/122-second
transport deadlines. Enable/Disable acceptance leaves effect/host denial/host
and network cleanup unknown, while the persistent bus and later Status drive
the card. IssueInvite shares the CLI frame validator and shows a native GTK
sheet: visible complete text (non-selectable and non-focusable; explicit Copy
only), Linux approval explanation, local absolute expiry/m:ss countdown, and
Close. The web gets issuance/expiry
metadata only. Expiry/close drops native text and best-effort clears only our
unchanged clipboard content; Close does not cancel, and clipboard history/other
apps may retain a copy. Missing/closed desktop refuses before dispatch with
unavailable/nativePermission. Failed-install replacement, invite cancellation/
recovery and policy renewal remain CLI-only; address refresh is B13-deferred.
These are source capabilities; I6 native/live acceptance remains NOT RUN. The existing
native RemoteAccessPanel remains until slice 6; source wiring is not native
helper/polkit or end-to-end acceptance.

### Linux host CLI fallback (slice 5a)

The payload ships `share/happyranch/desktop/connection_cli.py` beside
`connection_contract.py` and the existing schema. The wheel supplies the new
`cli.commands.connection` registration; its fixed entry uses `/usr/bin/python3 -I`
for system Gio/GTK, independently of WebKit and daemon availability. No new
runtime dependency is required. This is source delivery; real-helper/polkit,
minimum-target and full lifecycle acceptance remain pending.

```sh
happyranch connection status
happyranch connection host --help
happyranch connection host status
happyranch connection host devices
happyranch connection host next-device
happyranch connection host approve-device
happyranch connection host reject-device
happyranch connection host revoke-device
happyranch connection host enable
happyranch connection host turn-off
happyranch connection host create-invite
happyranch connection host cancel-invite
happyranch connection host replace-failed
happyranch connection host recover-invite
happyranch connection host retry-policy-renewal
```

Status reports closed nonsecret observations; the top-level status reports only
platform/role availability. Every invocation owns one dedicated system-bus
connection and the shared fixed zero-input transport and closed validation.
Reads allow 3 seconds and device mutations 122 seconds. I5b's lifecycle
calls allow 482 seconds for Enable/ReplaceFailed, 302 for Disable and 122 for
invite/policy methods, covering the original helper budgets plus 2 seconds.
Mutations require a reachable
desktop and fresh interactive polkit authorization. No desktop returns
`unavailable`, effect `none`, recovery `nativePermission`, before bus access.
Revoke also requires a terminal: inspect the helper-selected snapshot, type
`next`, `revoke` or `cancel` (default Cancel). Selection is bounded to 32 choices
and 120 seconds; Status, NextDevice and RevokeSelected stay in that process on
that same connection. A separate next-device command does not save selection.
No device ID or configuration arguments are accepted. Fingerprints and backend
diagnostics are never printed. A lost mutation reply is unknown,
with no retry; confirmed host denial never promises network cleanup.

Exit 0: completed read/action or cancelled selection with effect `none` (read
observations may explicitly be unavailable). Exit 1: closed error. Exit 2:
invalid command/arguments. `create-invite` first requires a controlling terminal
(`/dev/tty`), validates the exact terminal invite frame,
then displays the invite only there. Stdout JSON carries only the existing
nonsecret action fields, including invite state and UTC expiry. Malformed,
expired or lost replies report `outcomeUnknown` / effect `unknown`, with no
invite display or redispatch. No terminal refuses before dispatch, effect `none`.
`replace-failed` also requires terminal confirmation: exactly
“Replace failed remote access; existing pairings will be lost”, followed by
`Continue? [y/N] `. Only `y` or `yes` dispatches; no tty, EOF or any other answer
dispatches nothing, effect `none`. `turn-off` adds no CLI prompt to fresh polkit
authorization. Enable/Disable/ReplaceFailed/CancelInvite/RecoverInvite/
RetryPolicyRenewal acknowledge `accepted` before effects. The CLI keeps its
dedicated bus alive through the operation budget plus margin for the helper's
live-caller revalidation, then reports
`outcomeUnknown` / effect `unknown`, exit 1, rather than inventing completion,
host denial or cleanup from admission. Private outcomes/residue are not a public
terminal receipt. No polling or automatic retry follows. Only
`refresh-home-address` remains closed unavailable without dispatch (B13).
Linux foreground Client CLI is documented below. Mac host remains deferred/unavailable;
the [Mac foreground client CLI](../../app/mac/README.md#thr-379-slice-5c-foreground-mac-client-cli) is separate. There is no connector, DIY, installer or admin fallback.

### Linux foreground client CLI fallback (slice 5b)

The payload ships `client_cli.py` beside the existing ClientSession, RuntimeProxy,
ClientModel projection and login-only SecretStore owners. Fixed system Python
`/usr/bin/python3 -I` needs neither WebKit nor a healthy local daemon. No new IPC,
control socket, daemon route, service, dependency or credential path is added;
the existing per-session runtime proxy is unchanged.

```sh
happyranch connection client --help
happyranch connection client status
happyranch connection client accept-invite
happyranch connection client saved-computers
happyranch connection client connect-saved
happyranch connection client remove-saved
```

Invites are hidden and read only from the controlling terminal, never argv,
environment, files or piped stdin. Target/comparison phrase stay only on that
terminal; type `connect` for target consent. Saving requires typing `save`;
default is session-only. Existing login-only Secret Service custody/readback is
unchanged. Locked/denied/unavailable/readback failure shows an explicit not-saved,
session-only warning, with no plaintext fallback. Saved listing uses the app's
closed generic labels. Connect/remove select at most 32 records, reread the exact
current binding after consent and require explicit `connect`/`remove` confirmation.
Remove confirms only local saved-access deletion.
If interrupted after dispatch, removal settles at the existing storage safe point:
confirmed absence reports `removed`/confirmed; an unconfirmed outcome reports
`removalIncomplete`/unknown, including when the bounded wait expires.
Each input is limited to 120 seconds; empty/invalid selection or timeout cancels
without mutation. Missing terminal fails closed before launch. No invite/device/
path/credential flags exist.

C19 address recovery uses the same `client accept-invite` action and web
`client.acceptInvite`. One exact saved home/control-plane/connector/CA binding
with changed address metadata opens a native updated-target confirmation; the
terminal requires `update` and GTK defaults to Cancel. Confirmation precedes
exact keyring reread and unchanged v3 resume with saved network state/node and
no join key. Authenticated health validates the live grant before 8b save/readback.
Identity/profile/bearer remain the same. Failure requires separate fresh consent
for normal join and Host approval, without overwriting the saved record. Decline
launches nothing. No Client update proves Host-wide invalidation. Real-network
C19 acceptance belongs to slice 7 and is **NOT RUN**.

Connections live only in this foreground invocation. Authenticated native health
yields `runtimeReachable`, never `Connected`: CLI cannot observe the GUI's loaded
RELEASE UI. Open the loopback URL printed only on the terminal and keep CLI
running. Ctrl-C/SIGTERM cancels/disconnects this owner with bounded cleanup.
Unfinished save/readback reaches its existing safe point before teardown;
unconfirmed cleanup reports `cleanupIncomplete`, effect unknown.

`client status` observes only this fresh invocation (`notOwned`, observation
unavailable), never another session. Registered `disconnect`, `cancel-attempt`,
`retry`, `retry-save`, `health` from a separate invocation return closed
`unavailable/notOwned`, effect none, exit 1. They cannot control GUI/other CLI
processes. Retry with a fresh accept/connect invocation and consent; no session
control keys are added. JSON excludes target/phrase/URL, invites, credentials,
fingerprints, network state and diagnostics. Exit 0 completed/cancelled, 1 closed
error, 2 grammar. Mac Client CLI is unavailable/deferred pending Mac builds.
Source tests do not establish packaged/cross-device acceptance.

The Connection dialog in Main exposes session-only client status, invite,
cancel, disconnect, retry (fresh invite) and health controls. Native GTK dialogs
own invite paste, exact Host consent and comparison phrases; worker threads own
blocking session calls. A ready candidate requires native health/compatibility
admission and explicit commit to Main. No separate runtime view is opened.
The labelled native Access wasn't saved warning remains on Main after the
dialog closes. The existing owner handling governs dialog close and target
changes; Main close/Quit cutover remains a later slice. Cancel never promises
Host revocation or membership removal.
Native save consent enables same-node reconnect through GI Secret. Only the
default login collection is accepted, with exact home/control/node/profile binding,
verified readback and local absence after deletion. Locked/denied/unavailable or
failed saving keeps the session usable with a persistent accessible native warning;
Retry save remains available in the Connection dialog.
No plaintext, file or substitute credential store is used. One unreadable saved
item does not hide readable peers. Remove is local-only and never claims Host
revocation. Cancel during saving waits for write/readback before teardown, while
fencing later connection/load callbacks. The foreground CLI runtime proxy answers bootstrap with an unrelated session
token, strips browser authority headers, and injects the device bearer only
on the private helper hop. Session stop seals admission and closes owned streams
before bounded helper terminate/kill/reap; forced kill reports cleanup incomplete
because helper scratch removal cannot be confirmed.

```sh
# Focused session/runtime behavior; stdlib only, no GTK or installs:
/usr/bin/python3 -B packaging/linux/desktop/test_client.py -v
/usr/bin/python3 -B packaging/linux/desktop/test_client_model.py -v
/usr/bin/python3 -B packaging/linux/desktop/test_client_store.py CustodyTests -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_connection.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_connection_cli.py -v
# GTK/WebKit smoke when the existing desktop session is available:
PYTHONWARNINGS=ignore GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_client_gtk.py -v
# Shipping connector parser/forwarder compatibility (existing project Python dependencies):
python -B packaging/linux/desktop/test_client_connector.py -v
# Focused Go helper tests with the already-cached toolchain/modules:
(cd app/mac/tsnet-bridge && GOPROXY=off GOTOOLCHAIN=local go test -run '^TestHomeSession' -count=1 .)
```

The window launcher resolves its payload prefix relative to `bin/`, then runs
**`/usr/bin/python3 -I`** with system PyGObject, GTK 3.0 and WebKit2 **4.1**.
The host needs `python3-gi`, `gir1.2-gtk-3.0`, and
`gir1.2-webkit2-4.1`, plus `gir1.2-secret-1` (libsecret GI 1 and its native
`libsecret-1-0` dependency); they are apt-provided, never installed into the bundled
CPython. These are package Depends. Saved access also requires a Secret Service
provider on the user session D-Bus, with an unlocked default login collection;
absence degrades to session-only. The app never installs/manages a provider or
creates/unlocks a collection; the desktop manages Secret Service activation. Qualify real custody using `RealKeyringTests` in
`test_client_store.py` only under an explicitly disposable `dbus-run-session`
and gnome-keyring-daemon: set HOME and all XDG homes under task scratch and
`HAPPYRANCH_DISPOSABLE_KEYRING` to that scratch root (HOME must be its `home`
child). Never point that test at a user's login keyring. The window does
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

The packaged service launcher and GUI create a missing release data folder at
0700 before daemon state or desktop.lock publication. The final leaf is checked
without following symlinks, including current-UID ownership and exact 0700 for
a new folder. Existing unsafe folders are never chmod/chown repaired; the log
names the path and problem and says it must be private to the user. The daemon
keeps its existing startup behavior and remote Enable refuses
`release_home_invalid`. This changes no shared development-home creation, other
file modes, systemd UMask or helper-owner safety checks (THR-327 seq613).

### Native Main header (Settings S6-L)

The GTK Main header has labelled **Connection / 连接** and **Settings / 设置**
entry actions. With the packaged Main scheme, its status pill reads the
native `app.status` v2 projection: no selection, starting, connected,
unavailable, busy, or incompatible with Host/app/unknown-side guidance. It
preserves **This computer / 此电脑** versus the selected remote name and shows
the candidate's separate sign-in/approval/ready-to-switch/failure/cancelled phase.
Long names are ellipsized with full native tooltips. Native copy follows the
launch locale; web locale changes do not relabel the native header until next
launch.

Content replacement preserves the native titlebar during startup, web loading,
restart/error recovery and Retry. A newly launched Main installs its own header;
GTK's child enumeration includes that header, so only content is removed.

The **Access wasn't saved / 访问权限未保存** badge follows `access_unsaved`,
including no-target recovery. Losing the web document does not clear the warning.
Its keyboard-accessible action requests the Connection dialog in Main;
its tooltip explains Retry saving and that a new invite may be needed.
Only the native owner's confirmed save projection clears the badge; header
actions do not write that state.

The Connection button and “Access wasn't saved” badge deliver the
`app.dialogRequested` JSON string `{"schema":2,"dialog":"connection"}`.
The Settings header action delivers `{"schema":2,"dialog":"settings"}`.
The page facade, named-world relay and native subscriber keep this closed event
separate from `app.stateChanged`. Delivery requires the current admitted Main
document's dialog subscription, independently of target readiness. Navigation,
revocation and WebKit loss retire both subscriptions. Unavailable requests are
dropped without queue or replay.
The header shows the requested destination plus an explicit Retry
action; becoming available never retries automatically. The subscribed web Main owns
the single-dialog/unsaved-owner gate, repeated entry and focus within the dialog.
Native entry starts no daemon, changes no target/generation, and neither commits
nor cancels an attempt. S7-L2 removes the Main control/remote-access strip,
its margins and separator. Local controls move into Settings and remote-access
controls use Connection. Close/Quit policy remains a later cutover slice.

Focused native checks use the existing direct system-Python venue, with Node 24
for executing the literal page/relay scripts in an isolated message-bus fixture:

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_main_header.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_events.py -v
# In a disposable GTK3/WebKit2 4.1 desktop/Xvfb session (DISPLAY required):
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_main_titlebar.py -v
```

The first two headless checks cover rendered labels/actions and the event boundary.
The titlebar regression drives shipping `run_gui` with real GTK, including its
actual titlebar child enumeration, web content, restart failure, Retry and fresh
Main processes in EN/ZH. Host lifecycle/helper operations are isolated. It uses
a minimal static web fixture by default; set `HAPPYRANCH_TITLEBAR_WEB_DIST` to an
absolute same-source built `web/dist` for the real Main bundle, and optionally
`HAPPYRANCH_TITLEBAR_OUTPUT` to an existing directory for 760px screenshots and
titlebar observation JSON.
They do not establish GTK fit, native/web focus or WebKit process behavior;
qualify those in a disposable desktop/Xvfb session. The inherited launcher suite
above has known failures and must be compared against the same base, never
reported as passing from these focused results.

### Main Connection bridge admission (S6-W3-L)

With `HAPPYRANCH_DESKTOP_APP_SCHEME=1`, the admitted top-level Main document
also accepts the eight existing v1 `host.*` commands, `host.stateChanged`,
`client.health`, `client.removeSaved` and `client.retrySave`. Results and events
are raw v1 bodies, validated by the existing Protocol; mutation errors retain
their native effect/recovery, including unknown outcomes. CSP/frame revocation
and `pagehide` reject pending mutations as `documentChanged` / effect `unknown` /
recovery `nativeRecovery`, never replaying them. This is conservative even before native
admission, since posting supplies no no-effect receipt. Read-only calls retain
effect `none` / recovery `refresh`, as do new calls refused after retirement.
`host_bridge.py` is installed and required in the desktop payload. Main and the
Connection dialog share its dedicated bus, one busy slot and bounded
Status waiters through the Main owner.
Subscriptions receive a full snapshot and poll Status every three seconds
while a subscribing window is visible; document retirement drops late events.
The header now requests the Connection dialog in Main; the legacy window is retired.

`app.ownersChanged` admits the closed v2 aggregate summary for the current Main
document. Commit/disconnect consult it: missing/unknown, pending, then dirty or
failed owners refuse with `owner_save_unknown`, `owner_save_pending`, then
`owner_edits_pending`; current non-GET transfers still gate effects. Invalid
current summaries retire clearance, and document loss with unresolved work
retains a process-lifetime unknown latch. No lifecycle keep/proceed decisions
are consumed here (S7-L3); unknown/unissued gate IDs are refused.

Focused checks, with Node 24 on PATH:

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_host_bridge.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_events.py -v
PYTHONWARNINGS=ignore GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_client_gtk.py -v
```

Real helper/polkit mutations require a disposable venue; synthetic transport
checks do not establish installed helper acceptance.

### Packaged runtime UI (Settings S2a / S7-L1)

Main loads `happyranch-app://app/`, served directly from the payload's
`web/dist` with no listening port, for both local and remote targets.
The app still performs its existing daemon ownership/health checks first.
The Connection dialog uses that same trusted packaged document.

The loader requires WebKitGTK **2.40+** in the WebKit2 **4.1** namespace
(`URISchemeRequest.get_http_body` was introduced in 2.40). Qualification on
the minimum engine remains **NOT RUN**; the available Linux venue is 2.52.6.
The scheme is secure and CORS-enabled, without local-scheme privileges.
Static responses use a startup file/hash allowlist, no-follow reads, CSP,
`nosniff` and `no-store`; root assets/fonts and SPA deep links work, while
traversal, symlinks, directory listing and `/api` fallback are refused. The main build manifest is validated during Debian packaging (S7-L1).

Native bootstraps the verified loopback daemon and keeps its bearer only in
memory. The page's bootstrap receives a dummy token. Local API forwarding
allows only safe content/concurrency headers, reconstructs framing, injects
the native bearer and refuses redirects.

S2b PR-A also supplies a listener-free remote adapter (`client_runtime.RemoteCall`)
and native target selection (`AppScheme.select_target`). The existing
launcher still selects its verified local daemon. A native owner may select
`LocalDaemon(port)`, `RemoteDaemon(active_session, ready=False/True)`, or `None`;
passing `None` as the loader's verified port starts with no target. Selection
is a native/UI-thread operation. S5-L1 gates voluntary commits through
`select_target(target, owner_saves=...)`: missing/unknown owner evidence,
pending saves, or current-generation non-GET transfers return a named refusal
without changing target/generation. An empty tuple means a captured empty
owner registry; it must not substitute for unavailable owner evidence. The
client.commitSwitch command/active-candidate owner remains S5-L2 and web owner
registry/summary push landed in S6-W2b (#1804). `ready=False` denotes
a selected remote session still connecting; only its owner publishes readiness.
The adapter calls that session's existing pinned-peer `dial`, `release` and
native `_credential` callbacks, without constructing a proxy/listener. It shares
the loopback proxy's authority-header stripping, injects the device credential,
and reconstructs Content-Length from bounded payload bytes/streams. Response
Content-Length/chunk framing is decoded incrementally, including SSE. Existing
remote windows/listeners stay unchanged until S7; candidate/commit ownership
remains S5. No enrollment or daemon authentication changes.

Every scheme transfer captures its target before worker dispatch. Native
selection atomically swaps target plus process-unique generation, cancels old
generation transfers, then emits full app.stateChanged. A first upstream HTTP
send fence distinguishes forwarding from page delivery: only a request with no
upstream send can return `409 {"detail":{"code":"target_changed"}}`. An
already-forwarded transfer interrupted before page headers returns 499 cancelled;
an established stream errors through GIO, never successful EOF. Native never
replays a mutation. No selected
target, a connecting remote target, and unavailable transport return `503`
with exactly `{"detail":{"code":"no_target"}}`, `target_starting`, or
`target_unreachable` in the same envelope. Native concurrency/body caps and admission lock timeout use `429 too_many_requests`.
`503 busy` is reserved for health update_busy.busy and is rechecked until clear.
S4b-Linux PR-B gates every API transfer, including the page's dummy bootstrap,
on native health admission. Browser authorization, cookies and native/
forwarded identity headers are stripped before native injection. Only safe
response headers reach WebKit; exception text and real credentials never do.

S4b-Linux compatibility admission runs `GET /api/v1/health` on the selected
instance before it can forward API traffic. The local request uses only the
verified loopback port, **before bearer bootstrap**: the daemon health route is
unauthenticated (`runtime/daemon/routes/health.py`, mounted without authentication
dependencies in `runtime/daemon/app.py`). Remote health uses `RemoteCall` and the
same pinned `ClientSession.dial/release/_credential` callbacks as ordinary
traffic; the existing home connector still requires its device credential.
There is no remote bearer-bootstrap fetch or alternate credential path.

The app contracts are `CLIENT=1`, `MIN_DAEMON=1`. `api_contract` must be an
object with actual integer `current` and `min_client` (Booleans fail). Admission
requires `min_client <= 1 <= current` and `current >= 1`. Missing/invalid fields,
unparseable/non-200/truncated health, or a reachable peer exceeding the 64 KiB
body cap or the **five-second total health deadline** yield
`503 {"detail":{"code":"incompatible","side":"host"}}`. Valid integer
`min_client > 1` uses `side:"app"`. No current case uses `unknown`. Failure to
connect/dial the target remains `503 target_unreachable`, is not cached as
incompatible, and can be retried. Redirects are not followed. Health response
bytes, upstream diagnostics, bearer and device credential are never returned
as native errors or logged. Only the normal compatible health API response may
be forwarded in answer to a page request for health.

The health result (including incompatibility) is cached on that target instance.
`AppScheme.select_target` gates commit, swaps target/generation, cancels captured
old transfers and resets both old/new admission, even on reselection of the
same object. Native status admission runs without requiring page API traffic,
so a recovery page can receive ready/incompatible/busy state changes. `load-changed STARTED`,
`web-process-terminated` and `close` invalidate admission; close also clears the
local bearer. `RemoteDaemon.ready` transitions reset admission before the native
owner publishes the new transport state. In the launcher, `message` closes the
scheme before Restart; `show_web` closes any previous scheme and constructs a
fresh `LocalDaemon` after service restart, initial attach, retry or child attach.
A new instance always checks again at its next API bootstrap. There is currently
no same-instance remote reconnect callback in this launcher: its future
native owner must reselect the target or transition `ready` on reconnect. This
is an integration assumption, not an automatic reconnect or S5 candidate switch.
Cancellation during admission interrupts dial/headers/body and returns 499
`cancelled`; native selection/reset before forwarding returns 409 target_changed.
Concurrent admission wait is bounded and returns 429 too_many_requests. Reset
clears the local bearer. A subsequent upstream 401 invalidates bearer and health
admission; the next request performs native bootstrap, without replaying the
401 request. Post-forward redirects and transport loss stay ordinary 502
upstream_redirect / incomplete_body errors, never recovery codes. An incompatible cached
instance performs no subsequent API forwarding or bearer-bootstrap fetch.

The document-start fetch adapter serializes request bodies into bytes before
calling WebKit, including Blob/FormData and S1a's byte multipart bodies.
Unadapted body requests are rejected before `get_http_body`: S0 observed a
native crash when calling that API on raw Blob/FormData. This adapter remains
local to the opted-in view after S1a: S1a serializes multipart, while native
JSON byte conversion/admission, request IDs and explicit abort messages still
use this adapter; no web source changes are needed here. Body cap:
64 MiB/10 seconds, at most two body transfers and 32 total API transfers.
Native setup has a 15-second deadline; local upstream socket operations time out
after 5 seconds before headers (remote request upload/headers share 10 seconds),
and after 30 seconds during non-event response reads.
Quiet `text/event-stream` reads have no idle timeout and deliver later events.
Streams are incremental, capped at one hour, with a 10-second slow-reader limit.
Abort, document navigation, process termination and view destruction cancel
owned upstreams; downstream closure is checked every 100 ms even when SSE is idle.
Failures before headers return closed JSON HTTP errors. After headers, timeout,
lifetime expiry, abort, short Content-Length bodies and transport/chunk-framing
failures produce a GIO input-stream error, never successful EOF. Known response
lengths are passed to WebKit and delivered byte counts are validated; complete
bodies close normally. The bounded AF_UNIX socket pair has no network listener;
its unread completion marker produces a kernel read error on failed closure.
No exception text or bearer is sent.

Downloads use fetch → blob → `a.download`, followed by the native Save chooser
with overwrite confirmation. Direct API attachment navigation is blocked;
it does not reliably promote to a WebKit download. Off-origin HTTP(S) links
open in the system browser. The opted-in view has persistent storage under
`<desktop-home>/desktop-web`. S5-L1 exposes only app.status/app.stateChanged
through the existing string call/on facade in the trusted Main document. The
private controller registers its handler only in a named world; native probes
the top document, empty frame tree and absence of a page-world native channel
before admitting its isolated document token. TOP_FRAME relay, CSP and frame/CSP
observation revoke on frame insertion. Navigation, process loss and close fence
old messages/events. Reads queued during initial admission are bounded. Failed
admission leaves the bridge unavailable. S6-W3-L admits the reviewed Connection commands on Main; S7-L1 retires the separate window.

The full v2 appState supplies native generation, target, state, compatibility,
local status, nullable candidate, access_unsaved and the store's current/persisted
locale. A selected remote never exposes a local prompt URL or remote address. Native owners supply
sanitized remote id/name; native locale persistence and ordered setter publication
are wired by S8-1-L (above); live web locale UI remains S8-1-W. After an admitted
target becomes unreachable, status
refresh requires fresh successful health and compatibility checks before ready,
and native bootstrap if its bearer is absent. Cached compatibility/bearer alone
cannot establish recovery; no forwarded request is replayed. Every scheme API
request requires the captured X-HappyRanch-Target header except
GET /api/v1/auth/bootstrap. Native refuses a missing/mismatched generation before forwarding and strips it upstream. Browser
mode stays unchanged; web stamping/stale-drop is S5-W1.

Focused standalone native checks (no GI/display/retired Python suite):

```sh
/usr/bin/python3 -B packaging/linux/desktop/test_app_scheme.py -v
```

Remote adapter and response-body checks use real loopback HTTP and system
GIO/GLib (no display). The reset fixture closes directly without SHUT_WR and
omits Content-Length, distinguishing TCP reset from a short orderly EOF.
The quiet-stream case waits 31 seconds, exceeding the former 30-second cap:

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_compatibility.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_remote_runtime.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_scheme_response.py -v
```

The main window has a compact **Remote access** card, including while the
release daemon is starting or unavailable. Open **Settings and devices** for
home-network status, pending approval and the device list; opening it reads
fresh helper status. **Ready to invite** means remote access is ready for
invitation; it does not assert that a Mac has
opened an authorized session. **Remote access ready; HappyRanch
offline/restarting** means use the normal runtime controls, then **Refresh**.
An absent helper or an off installation shows **Not set up**. An unavailable or
failed helper shows a bounded retry message; **Refresh** reads status again.

For a pending device, compare **Device**, **Stable-ID fingerprint** and
**Comparison phrase** with the requesting computer. The target text is
**HappyRanch on <this computer's hostname>**. That local hostname is display
only. **Device** displays the helper's bounded label; real OS-label delivery
depends on runtime slice **4a-0**, so this UI slice alone does not establish
founder acceptance. The UI does not invent a name from the fingerprint.
Choose **Approve device** or **Reject device**. Each asks the fixed root helper
for a fresh system polkit prompt. No app admin-key prompt or input is involved.

Devices with access remain approved until revoked; this list does not claim an
active session. **Next device** advances the helper's selection. Read the
**Selected device** details, then choose **Revoke device** for that device's
fresh polkit prompt. Approval, rejection and revocation update the display only
from a successful helper reply and a fresh status read. A cancelled, denied or
failed action shows **Action cancelled — no change**, preserves the prior view
and queues nothing. Click the action again only when you want a new attempt
and a new permission prompt. If transport was lost, use **Refresh** to observe
the current helper state. Closing the window cancels its in-flight requests.

Enable, Create invite and Turn off are omitted until their helper methods
arrive in later slices. Custom-server URL/manual-key input does not exist and is not
planned; outside-home access uses a fixed build-configured server (see
[the spec](../specs/2026-11-16-one-click-remote-access.md#outside-home-access-fixed-operator-server)). The window uses only Status, NextDevice, ApprovePending,
RejectPending and RevokeSelected, with no input arguments. Native GTK rendering
and real polkit dialogs require a desktop acceptance venue; the standalone
command above uses GI/transport doubles and does not prove those dialogs.

The release home is `$XDG_DATA_HOME/happyranch` for an absolute XDG value,
otherwise `~/.local/share/happyranch`, with `desktop.lock` in that home.
Service mode selects XDG from the user manager, including when it differs from
the window's environment. Without a manager, the child fallback uses the
window's XDG value. Inherited `HAPPYRANCH_DAEMON_HOME` does not select the home.
An existing `~/.happyranch-release` is left untouched and unused; no data is
moved, copied or deleted.
The development daemon (`~/.happyranch`, port 8765) is never contacted or
signaled; selecting its home explicitly is refused before taking a lock.
`--daemon-home` remains useful for an isolated smoke run only when no user
manager is available; service mode refuses a different home because the
packaged unit resolves its own runtime home.

Opening the window first checks for a user manager. A degraded manager is
available even though `is-system-running` exits 1. When available, the window
validates the checks below, runs `systemctl --user start happyranch-daemon`,
and waits up to 60 seconds for the release service. It never spawns a child in
service mode. The web view attaches only when all observations agree:

- The loaded `FragmentPath` is exactly
  `/usr/lib/systemd/user/happyranch-daemon.service`, and `DropInPaths` is empty.
- The release home's `daemon.pid` equals the unit's positive `MainPID`, and
  `daemon.port` is an integer in 1..65535 other than 8765.
- The listener for `127.0.0.1:<port>` read from `daemon.port` belongs to that MainPID: matching LISTEN
  inodes from `/proc/net/tcp` and relevant wildcard/mapped `/proc/net/tcp6`
  entries must appear in `/proc/<MainPID>/fd` socket links.
- `GET http://127.0.0.1:<port>/api/v1/health` returns HTTP 200 and `status: ok`.
  Probes bypass inherited HTTP proxies. Unit, markers and listener ownership
  are checked again after the response before releasing the web URL.

`happyranch-app --print-port` uses this same ownership/health proof without
starting, stopping or enabling a service, acquiring a lock, or importing GTK.
It prints only the verified port on success. Missing manager, marker, unit,
listener or health evidence produces a clear stderr message and nonzero exit;
it never falls back to a fixed port. For SSH browser access, obtain the port
on the remote release host, then forward it from your local machine:

```bash
# On the release host:
happyranch-app --print-port
# Locally, replace <port> with that verified value:
ssh -L <local>:127.0.0.1:<port> <user>@<release-host>
```

Open `http://127.0.0.1:<local>/` locally. Re-read the port after a daemon restart.

Missing or unreadable evidence refuses attachment. These are sampled
same-user observations, not an atomic fence against later process/unit changes.
The checks detect overrides; they do not protect user-owned configuration from
other processes of the same user.
Existing PID and port markers must be current-user-owned regular files of at
most 64 bytes, with one link, opened without following a final symlink and
rechecked for changes. They must decode as UTF-8 positive decimal integers
(port at most 65535, excluding 8765). Invalid markers refuse with a plain message before any
start, health probe or stop; their bytes are preserved for the user to review.
After a successful admitted start, missing, empty, malformed or mismatched
markers are pending publication within the existing 60-second startup deadline:
the daemon writes the port marker, then the PID marker, with a truncate/write
interval for each. No health probe or attachment proceeds until the complete
PID/port pair and listener ownership match. If publication stays invalid until
the deadline, the window shows a localized connection-timeout error with
**Retry** and enters the existing packaged-service shutdown path. It never
repairs the markers.

On launch and every start/retry, the window checks `NeedDaemonReload` and runs
`systemctl --user daemon-reload` for the current user when it reports `yes`,
before starting the service. This loads unit changes installed by a `.deb`
upgrade. Reload failure or command timeout shows a localized error with
**Retry**, without starting the service. No root maintainer script reloads
other users' managers.

Before start, the window inspects `systemctl --user show-environment`. Any
`HAPPYRANCH_*` variable refuses startup and the message lists names only.
It also validates `~/.config/happyranch/daemon.env` when present. Only `PATH`
assignments, blank lines and full-line `#`/`;` comments are accepted. A PATH
assignment must preserve the complete packaged base
`/opt/happyranch-desktop/bin:/usr/local/bin:/usr/bin:/bin` in order, adding
literal absolute directories before or after it. Shell expansions, `%h`,
relative/empty entries and replacements are refused. Whole-value quotes are
allowed. `LD_*`, `PYTHON*`, `HAPPYRANCH_DAEMON_*` and every other key refuse
startup; the message names offending keys without values. The window never
edits this file. Non-regular files and invalid UTF-8 refuse startup.

The old hand-written `~/.config/systemd/user/happyranch-release.service` is
checked before starting the packaged service. SHA-256
`482dee228bce0f41354ea7a803341b1b6cdb4076a41c713467b10d348f4a2817`
produces an explanation and a **Remove old unit** button. Only an explicit
click, after another hash/identity check, removes that exact file and runs
`systemctl --user daemon-reload`. A separate **Start HappyRanch** click then
begins startup. Other content or
non-regular files refuse startup and are never changed. There is no automatic
migration or cleanup of any other user unit.

Service failures remain in the window with a plain diagnostic:

- Foreign listener: **Port <port> is used by another program**. No attachment,
  start command or process signal follows a pre-start refusal.
- Before start, a live release-home `daemon.pid` different from MainPID: stop
  the CLI-started daemon yourself before opening the window. The app never signals it.
- Unexpected unit path/drop-ins: review user service overrides before starting.
- `ActiveState=failed` and `Result=start-limit-hit`: show the last 20 journal
  lines from `journalctl --user -u happyranch-daemon -n 20`. **Retry** repeats
  validation, then runs `reset-failed` followed by `start`.
- Failed ownership or health: refuse to attach. The app does not try to kill a
  process to recover the port or repair marker files.

After the app successfully starts the validated packaged service, it retains
the shutdown obligation independently of web attachment. With the keep-running
switch OFF (or partially on), closing the window,
Ctrl+Q, SIGTERM or SIGINT, including during startup, stops that service with
`systemctl --user stop happyranch-daemon`. A startup failure after a successful
start also enters shutdown. Unit provenance is checked again before stop;
pre-start refusals create no shutdown obligation. A nonzero or timed-out stop
shows a plain failure message and keeps the worker, window and desktop lock.
Closing again requests one more bounded stop attempt; only successful shutdown
permits exit and lock release. No process is signaled directly in service mode.

### Linux active and candidate switching (Settings S5-L2)

Main and its Connection dialog share one ClientUI/model owner. Main's admitted document can start an invitation
or saved-computer attempt while its current target continues serving. Native
dialogs retain invitation, approval and credential custody. The candidate uses
the existing listener-free RemoteDaemon health/compatibility admission; it does
not open a second runtime window or a loopback proxy. Its public identity is a
generic name and opaque id, with phase and optional deadline only.

`client.commitSwitch` requires a `ready_to_switch` candidate and uses the existing
`select_target` gate. Refusals retain both targets and generation, with
`owner_save_unknown`, `owner_save_pending` or `non_get_pending`. Candidate
compatibility failures retain the active target and report `incompatible` with
the existing host/app side. Cancel fences late completion and stops only the
candidate. Success swaps target/generation, errors old-generation streams, then
publishes status; old native-session cleanup is separately unknown until observed.
Cleanup outcomes belong to each native session. A successful cleanup cannot clear
another session's pending or failed cleanup; new Main attempts refuse while any
owned cleanup is unknown or incomplete. Cancelling or failing a candidate restores
the still-connected active projection, including its saved-computer reference and
save intent. Session-only access keeps `access_unsaved` and the native Retry save
action; a saved active connection keeps its saved-access metadata.

Owner evidence: section 12 requires pending/unknown owner saves to refuse. The v2
schema defines the closed `app.ownersChanged` summary (web → native) and the
`app.lifecycleRequested` event (native → web); see the bridge spec's owner summary
and lifecycle gate section. Linux Main admits the closed summary
(`schema`, `blocking`, `counts`, optional `gate`) and replies `"{}"` after
admission. A gate must match the outstanding native request and current Main
document; malformed, stale or consumed decisions invalidate current owner
evidence and are refused with `invalidRequest` / `effect:none`. Foreign-document
input cannot invalidate the current document. The web sends a
registered save only after that acknowledgement; a refused or lost push holds the
save (nothing is sent). Without an admitted summary, or with pending/unknown
counts, voluntary commit/disconnect refuses (`owner_save_unknown`,
`owner_save_pending`); dirty/failed refuses with `owner_edits_pending`.
Main close, Quit and Relaunch emit `app.lifecycleRequested` with schema 2,
one process-unique requestId and the captured action. Even clean owners need
the matching web decision. Repeated actions focus that same request; they do
not replace its action. Keep cancels it; proceed consumes it once, requires
zero blocking owners, and rechecks native non-GET transfers before effect
admission. Native confirmation rechecks document identity after its nested GTK
loop. Unsubscription, document replacement and WebKit death drop the request;
pending/unknown saves and unfinished non-GETs retain the existing coarse latch.
Main view
recreation reattaches the same committed session before navigation, with a fresh
transport admission; it cancels an unfinished candidate. This is initial attachment
of the same target, not a voluntary switch. Earlier S5-L2-pending descriptions in
this handbook describe the preceding source epoch; native/live acceptance remains
separate from this source implementation.

### Startup selection and native app exits (S7-L3)

`app_startup_selection.py` stores only `local`, `remote:unsaved`, or
`remote:<opaque saved-access reference>` in
`$XDG_CONFIG_HOME/happyranch/startup-selection` (absolute XDG path only), else
`~/.config/happyranch/startup-selection`. It uses the locale preference's locked,
bounded no-follow read and private atomic-write/directory-fsync pattern. It
stores no credential, host or display name. Only an absent record defaults to
local; unreadable/corrupt records suppress local startup. Like locale, a failed
post-replace sync may leave new bytes visible without confirmed durability.

Ordinary local/Host launch auto-starts through the existing supervisor. Any
remote record loads packaged Main without local Start, session restoration or
silent reconnect. A discovered exact saved reference is selected but disconnected;
no first-saved-computer fallback is used. Missing/removed/locked saved access,
unsaved access and invalid records keep local startup suppressed with native
recovery text. Connection and explicit Start HappyRanch remain reachable.
Reconnection still requires the existing native saved-target consent and an
explicit commit. Opening/reopening Connection or Settings starts nothing.

The preference changes after confirmed remote target commit, confirmed explicit
local Start, or confirmed removal/revocation of the recorded access reference.
Attempts, candidates and Disconnect do not change it. A remote commit with
session-only access records `remote:unsaved`. Preference write failure does not
undo a successful commit; a native notice says the selection was not saved and
the next launch uses the last confirmed record.

Dirty/failed owners use the web's one contextual keep/discard gate. Pending,
unknown or missing evidence holds normal exits. Native Exit anyway is a separate
Stay-default loss warning: unsent edits may be lost, a sent save may still take
effect and is not cancelled, and Check result belongs to the original context.
It bypasses waiting for web only, never target-commit or native service ownership
checks. Unknown native local effects remain held. If a remote-only launch never
started or attached to a local service, or startup ended without an owned
supervisor/service, and inspection is unavailable, a separate
native confirmation states that exiting sends no local Start or Stop; it does
not claim the service is running or stopped. Quit/Relaunch otherwise inspect the
actual local policy even under remote selection, disclose local interruption
when Off/Partly on, and disclose remote Host continuity and retained grants.
Freshly inspected Fully On follows the existing close_window keep-running
policy. Foreground owned children still stop and reap.
The separate native Exit anyway action is an explicit Quit recovery. It remains
available when an outstanding Relaunch cannot verify installed assets and web
cannot answer Keep; it never silently relaunches.

Relaunch is ordinary Quit followed by launch after supervisor completion and
desktop-lock release. It first reobserves the relevant target's service contract,
checks installed desktop CLIENT/MIN_DAEMON against that range, requires readable
release metadata, packaged Main, native modules and resources, and compiles the
installed Python without importing it. Reads reject nonregular files and have
byte/work bounds; the fingerprint covers the complete bounded packaged web
bundle as well as native code/resources. A content fingerprint must remain
unchanged after exit. Unknown/incompatible/missing assets refuse relaunch.
This verifies the inspected installed assets' compatibility, not an updater,
signed provenance, successful launch/reconnect or resumed work.

Focused standalone native venues (set TMPDIR to a short, existing non-/tmp
directory and isolate XDG_CONFIG_HOME before running):

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_scheme_lifecycle.py -v
/usr/bin/python3 -B packaging/linux/desktop/test_app_startup_selection.py -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_client_model.py StartupSelectionHooksTests -v
/usr/bin/python3 -B packaging/linux/desktop/test_happyranch_window.py StartupLifecycleTests -v
```

The lifecycle matrix's documented harness drives the real native bridge and
existing ServiceMode against synthetic systemd/proc/health observations in a
temporary home. It observes command admission and policy, not real logind.
The new application lifecycle model and its TLC evidence are under
`docs/specs/formal/local-service-supervisor/`; service-only Restart behavior is
unchanged. Installed-package, full Main, real remote pair and session-end
qualification remain separate evidence.

Focused switch and admitted-document checks use the existing standalone venues:

```sh
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_remote_runtime.py SwitchTests ClientDispatchTests -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_client_gtk.py GTKTests.test_main_bridge_status_event_and_frame_navigation_refusal -v
```

### Keep running after logout

S7-L2 places local controls in Settings → **HappyRanch on this computer**.
Main has no control strip or reserved height. The admitted Main document calls
`local.status/start/stop/restart/keepRunning.set/repair/turnOff/update.status`
through the existing supervisor queue. These commands affect this computer,
including while a remote target is selected; they never operate on that Host.
`local.status` reports independent running and keep-running observations:
Checking, Off, Fully on, Partly on or Unknown. Failed inspection is Unknown.
The supervisor atomically reserves one mutation slot before native consent;
another mutation returns `busy` with effect `none` and recovery `refresh`.
Reads remain available while busy. A failed effect retains its slot until an
explicit fresh status inspection reconciles it; no mutation is replayed.

**Keep HappyRanch running when I'm logged out** defaults OFF. Turning it ON
calls logind's system-bus `SetUserLinger(own uid, true, interactive=true)` via
system Python's existing Gio/GLib bindings, then
`systemctl --user enable happyranch-daemon`. There is no new dependency. A local
polkit `AccessDenied` refuses the change; a fresh status read determines the
displayed state. If enable fails after the app enabled linger, the app rolls
that linger change back.

The app atomically replaces `~/.config/happyranch/desktop.json` with
`{"linger_set_by_app": true}` only when it originally enabled linger; otherwise
the value is false. Repeated ON/repair preserves an existing true undo obligation.
The replacement uses a same-directory temporary file, file fsync and rename.
Missing, malformed, invalid-UTF-8 or non-Boolean ownership data means the app did
not enable linger. If recording the ON change fails, newly enabled linger and
unit enablement are rolled back; a failed linger rollback retains undo provenance
where the file can be written.

Turning OFF runs `systemctl --user disable happyranch-daemon` and disables linger
only when that provenance is true. Existing external linger is preserved. The
daemon keeps running while the window stays open. State is derived from fresh
`UnitFileState=enabled` and `loginctl show-user <own uid> -p Linger --value`
observations, never from the JSON flag. Both must be on for ON. A mismatch shows
**Partly on** with **Repair** and **Turn off** actions. Turn off uses a
Cancel-default native confirmation explaining that background running turns off
but HappyRanch does not stop now. Controls read fresh state after actions;
closing observes the state again.
Only a fully attached, freshly ON service is left running when the window closes;
the window exits and releases its lock. Startup/restart proof failures retain the
existing shutdown obligation even when keep-running had been ON. Failed state
reads or stops keep window/worker/lock custody for a subsequent close retry.

### Update installed while running

After ownership proof, `local.update.status` reports `restart_required`
when `/opt/happyranch-desktop/build-info.json` has `st_ctime` newer than the
unit's process start, or the unit reports `NeedDaemonReload=yes`. The Debian
builder moves the payload under `/opt/happyranch-desktop`; dpkg replacement by
rename makes the installed file's ctime the install-time signal. The app reads
the unit's recorded realtime `ExecMainStartTimestamp` in microseconds through
`busctl --user get-property` and compares it directly with the file's realtime
ctime. Suspend does not shift that recorded start; no current realtime-minus-
monotonic offset is reconstructed. Wall-clock adjustments between service start
and installation can still affect this best-effort signal. Missing/unreadable
metadata or invalid/unrepresentable start timestamps return the helper's negative
hint (`not_detected`) and log a warning, without refusing attachment. An unavailable
service observation returns `unknown`; foreground returns `unsupported`.

This is the authorized substitute for design 2.4's comparison against the
running daemon's reported version: the daemon currently reports no build
identity. It preserves the installed-update hint without
reading daemon version fields or changing runtime source. An immutable running
build identity is a possible later improvement.

**Restart HappyRanch** is always presented when local controls are supported,
independently of the update hint. Native uses Cancel-default interruption consent
naming this computer and checks current Main owners/effects before admission.
It first revalidates ownership, reloads the user manager if
`NeedDaemonReload=yes`, and restarts `happyranch-daemon`. It then repeats the full
B2a ownership proof, including the bounded asynchronous PID/port publication
wait, listener inode, health and post-health rechecks. Native invalidates cached
local compatibility/bearer and uses the existing loopback bootstrap after Start
or Restart, then publishes `app.stateChanged`. Service success and API compatibility
are separate: a remaining mismatch keeps its named Host/app/unknown guidance.
Local interruption cancels already-admitted background status workers before
resetting local transport, so a late worker cannot cache the pre-effect bearer
or publish readiness during Start/Stop/Restart. Reads remain available. Successful
Start/Restart replies include this computer's closed `readmission` result
(`ready|unreachable|incompatible|unknown` plus the existing incompatibility detail
or null). A remote-selected `app.status` remains remote; this local result carries
no endpoint or credential and never authorizes retrying the effect. Other local
mutation replies omit `readmission`.
Main, its current dialog and drafts stay mounted; this never relaunches the app
or changes keep-running. A selected remote target/candidate is preserved.
Restart failure revokes leave-running eligibility and retains the operation
owner for explicit inspection. Existing shutdown custody remains until an owned
stop succeeds on close; page Retry does not repeat Restart.

If `systemctl --user is-system-running` is unavailable or reports no running
manager, the window retains its child-process mode. Settings observes
`supported:false, mode:"foreground"`; lifecycle/keep-running commands refuse with
`unavailable`, effect `none`, recovery `nativeRecovery`. It requires an empty home
(any existing `daemon.pid` or
`daemon.port`,
even stale/partial, refuses startup without changing it). The child receives a
minimal OS environment, the selected home, loopback bind, random port `0`, and
the payload's `web/dist`. The window waits for its own Popen PID and healthy
published port, then attaches. Closing sends TERM only to that child, waits five
seconds, then KILL and a two-second reap if necessary. Marker cleanup retains
the existing empty-at-spawn and unchanged-inode checks. Child logs are at
`<desktop-home>/logs/desktop-daemon.log`.

Focused local-control checks use the same direct native test venue:

```sh
/usr/bin/python3 -B packaging/linux/desktop/test_happyranch_window.py LocalControlsTests -v
GSETTINGS_BACKEND=memory /usr/bin/python3 -B packaging/linux/desktop/test_app_events.py LocalBridgeTests EventTests.test_local_page_relay_admission_and_retired_effect_semantics -v
```

These isolate system-service effects and exercise real loopback compatibility/
bootstrap with synthetic credentials. They do not qualify a real systemd/logind
transition or GTK/WebKit layout. Use a disposable temp-home user-service venue
for those checks, never the development daemon or the account's live user unit.

A second launch for the same canonical home activates/focuses the first window
through Gtk.Application's session bus. Without a bus, `desktop.lock` still
excludes another window; the lock inode is never unlinked. Startup and stop run
off the GTK main thread. Native headings/actions are available in English and
Simplified Chinese; raw diagnostics remain verbatim.

The focused command above runs both child lifecycle and service-mode doubles
from the existing stdlib test file; no new CI selection is needed. Required PR
checks remain lint/static only. Real user-manager operation and GTK screenshots
on Ubuntu 22.04/Debian 12 remain acceptance work; these doubles and lint do not
prove them. No real service, user file or linger state is changed by the tests.
There is no crash/SIGKILL recovery or process-tree shutdown guarantee for the
window itself or arbitrary daemon-launched workloads.

## Build the Debian package

On Linux, provide build-host `python3` (>= 3.10), `dpkg`, `coreutils` and
binutils (`readelf`).
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
# Check an extracted native payload without installing it:
/usr/bin/python3 -B -I packaging/linux/python-elf-stack.py /absolute/payload/python --mdwe
```

The payload's `build-info.json` records the installed HappyRanch distribution
version from `pyproject.toml` in its dependency inventory. Packaging uses that
version and architecture, checks the top-level directory agrees, refuses unsafe
archive members/escaping links and existing outputs, and calls
`dpkg-deb --root-owner-group`. It does not infer architecture from the build
host or execute a foreign interpreter: `x86_64` maps to Debian `amd64`, and
`aarch64` maps to `arm64`. The manual workflow retains its original two native
matrix rows and uploads the .deb alongside its intermediate inputs.
The .deb builder rejects any ELF in `python/` without exactly one non-executable stack
header before packaging, including older unsafe tarballs; rebuild those through
`build-payload.sh`. This check never executes a foreign interpreter. The native
payload build and installed-package smoke perform the actual MDWE thread test.
The Headscale static Go binary outside `python/` is never scanned or repaired.
The installed smoke requires build-host `readelf` and retains its output in
`python-stack-mdwe.log`, including any explicit unsupported-venue skip reason.
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
D-Bus and isolated HOME/XDG directories. It first requires that the private bus
has no systemd user manager, then exercises the existing child-daemon fallback
with the release home at the isolated `$XDG_DATA_HOME/happyranch`. This is not
service-mode acceptance: it neither starts nor enables a user service. It
discovers the port from the app home's `daemon.pid`/`daemon.port`, verifies the
daemon is the app's child, checks HTTP200/status `ok` at `/api/v1/health`, and
compares `/` byte for byte
with the installed built `web/dist/index.html`. It captures the actual focused
window in `window.png`; a reviewer must view the PNG to assess rendered content.
The smoke sends Ctrl+Q, waits for the app-owned native confirmation to become
active, then uses xdotool Tab and Space to select and activate Quit/OK from the
Stay default. After confirmation, the existing 20-second wait must observe app
exit with status0, no daemon process and removal of both markers. Package removal
must leave no package-specific files, links or directories and must preserve the
entire user-data home (bytes and metadata),
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
no connector configuration, accounts, connector systemd units or enablement, and never
invokes the connector installer. Explicit `provision/install --layout deb`
uses those packaged executables and retains separate config/state ownership. See
[the connector contract](../agent-guides/contracts/remote-access-and-remote-jobs.md).

| Package path | Purpose |
| --- | --- |
| `/opt/happyranch-desktop/` | Relocatable payload, bundled Python, daemon, Web UI and desktop source; generated bytecode caches omitted |
| `/usr/bin/happyranch`, `/usr/bin/happyranch-app` | POSIX wrappers that exec the real absolute payload launcher, preserving arguments and exit status; direct symlinks would break the launchers' `$0` prefix lookup |
| `/usr/lib/systemd/user/happyranch-daemon.service` | Root-owned mode 0644 release daemon user unit; shipped without enablement or startup |
| `/usr/share/applications/happyranch.desktop` | Generated PR2 template; Exec/TryExec `/usr/bin/happyranch-app`, Icon `happyranch` |
| `/usr/share/icons/hicolor/{16x16,…,1024x1024,scalable}/apps/happyranch.*` | Existing brand icons |
| `/usr/share/doc/happyranch/` | Debian copyright/changelog, unchanged PR1 licenses/notices/inventories and build-info |

No maintainer scripts are needed. Ubuntu/Debian's `desktop-file-utils` and
`hicolor-icon-theme` dpkg triggers refresh their caches when installed. Package
removal deletes only package-owned files; it does not signal a running app or
daemon. Quit the desktop app before upgrading/removing it. The release data in
`~/.local/share/happyranch` / `$XDG_DATA_HOME/happyranch` is preserved, including
on purge. Any legacy `~/.happyranch-release` remains untouched and unused. The
package never owns user data or `~/.happyranch`.

### Packaged release user unit (slice B1)

`build-deb.sh` copies `packaging/linux/systemd/happyranch-daemon.service`
byte for byte into `/usr/lib/systemd/user/happyranch-daemon.service`, with
root:root ownership and mode 0644 in the package. Its ExecStart runs the
system Python desktop module with `--run-service --user-home %h`. This uses the
same `desktop_home` resolver as the window and port query, then `exec`s
`/opt/happyranch-desktop/bin/happyranch-daemon`, preserving MainPID. This small
launcher is needed because the home rule requires an absolute-XDG test and a
fallback; no shell expansion or drop-in is required. The home is
`$XDG_DATA_HOME/happyranch` when the user manager's XDG value is absolute,
otherwise `%h/.local/share/happyranch`. Bind host remains `127.0.0.1`, and the
requested port is `0`. The existing daemon `_bind_port` binds that request and
returns `getsockname()[1]`; startup publishes the actual bound port to
`<daemon-home>/daemon.port`, then its PID to `daemon.pid`, and serves on the same
socket. A new free port is selected at each start.

The bundled bin directory and fixed PATH remain explicit. The optional
`EnvironmentFile=-%h/.config/happyranch/daemon.env` is restricted by desktop
validation to PATH additions. The service launcher sets the home, port and
loopback host before exec. The loaded unit must still have exactly
`FragmentPath=/usr/lib/systemd/user/happyranch-daemon.service` and no drop-ins.

Installation does not enable or start the unit. The desktop window starts it,
validates environment-file overrides and handles the old hand-written unit as
described above. With **Keep HappyRanch running when I'm logged out** OFF (the
default), closing the window stops the unit. Turning the switch ON enables the
unit and sets linger; see the [B2b keep-running section](#keep-running-after-logout).
The packaged user unit provides no sandboxing.

### Runtime dependencies

All direct Depends names are available in both stock Jammy (22.04, with
updates/security and universe enabled) and Bookworm (12, with security updates):

| Depends | Reason | Archive verification |
| --- | --- | --- |
| `python3 (>= 3.10)` | System interpreter for the GI window; daemon/CLI use bundled CPython3.12 | [Jammy](https://packages.ubuntu.com/jammy/python3), [Bookworm](https://packages.debian.org/bookworm/python3) |
| `python3-gi` | System PyGObject binding | [Jammy](https://packages.ubuntu.com/jammy/python3-gi), [Bookworm](https://packages.debian.org/bookworm/python3-gi) |
| `gir1.2-gtk-3.0` | GTK3 namespace and transitive shared libraries | [Jammy](https://packages.ubuntu.com/jammy/gir1.2-gtk-3.0), [Bookworm](https://packages.debian.org/bookworm/gir1.2-gtk-3.0) |
| `gir1.2-webkit2-4.1 (>= 2.40)` | WebKit2 4.1 namespace/engine and transitive libraries | [Jammy](https://packages.ubuntu.com/jammy/gir1.2-webkit2-4.1), [Bookworm](https://packages.debian.org/bookworm/gir1.2-webkit2-4.1) |
| `gir1.2-secret-1` | Secret Service GI 1 and transitive libsecret runtime; a user-session provider is separately required | Package prerequisite declared for slice 8b; stock distro/package acceptance pending |
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


### Inert home admin prefix (3b-2g-1)

The package now includes root:root 0755
`/opt/happyranch-desktop/libexec/remote-access/happyranch-home-admin`, beside the
home CA materializer and outside PATH. It uses the existing pinned x/net HTTP/2
module and existing collected notices. Packaging never invokes it. Only the
fixed protected local socket's Create/List/ExpirePreAuthKey RPCs are admitted;
secret wire bytes use anonymous pipes and failures use fixed categories.
Before reading secrets, runner startup discards standard logger output, including
x/net frame logs enabled at package init by inherited `GODEBUG=http2debug=1/2`.
The private binary response pipe remains separate. Python carries one original
monotonic deadline through transport, complete projection, user/duplicate-ID
validation and final success; expiry raises sanitized uncertainty. Checks are
cooperative and do not establish hard OS-I/O or scheduling deadlines.
The uncalled `home_admin.RootHomeInviteMinter` holds a per-invite-user lock over
mint/recovery; root-only operation reservations and the lost-create deadline
fence survive helper restart. Preserve every reservation and uncertain/pending
fence; do not remove them to retry issuance. Complete list/expiry settlement is
required. No cleanup or live maintenance command is added.

Items 4 and 5 now deliver inert issuer-directory provisioning/owned cleanup
and the uncalled local-node mint composition. Activation remains absent.
HomeInviteConfig reader moves to 3b-2g-2 because the runtime tailnet endpoint exists only after node
activation and will require a root activation receipt. Both activation refusals,
unit bytes and home_transport=False remain unchanged. No host install, root,
real Headscale or systemd acceptance is claimed. See the remote-access contract's
“Inert Linux home admin transport” subsection for custody, wire and budget rules.


## History

Citations removed from the body, with the section they supported:

- `THR-346 seq43/44` — Build the intermediate payload (original line 5).
