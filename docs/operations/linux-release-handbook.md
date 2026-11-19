# Linux release handbook

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
for provisioning before installation. The installer validates the dpkg-owned
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
check. Every regular file under `/usr/share/dbus-1/system-services`,
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
`share/happyranch/desktop/client_store.py`,
`share/happyranch/desktop/client_session.py`,
`share/happyranch/desktop/client_runtime.py`,
`share/happyranch/desktop/client_model.py`,
`share/happyranch/desktop/client_ui.py`, the verbatim shared pure codec
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
confirms host denial separately from unknown network cleanup. Enable, Turn off
and Create invite remain unavailable pending TASK-16779/slice 2b, together with
failed-install replacement, address refresh and policy renewal. The existing
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
```

Status reports closed nonsecret observations; the top-level status reports only
platform/role availability. Every invocation owns one dedicated system-bus
connection and shares the window's fixed zero-input methods, closed validation,
3-second reads and 122-second mutation deadline. Mutations require a reachable
desktop and fresh interactive polkit authorization. No desktop returns
`unavailable`, effect `none`, recovery `nativePermission`, before bus access.
Revoke also requires a terminal: inspect the helper-selected snapshot, type
`next`, `revoke` or `cancel` (default Cancel). Selection is bounded to 32 choices
and 120 seconds; Status, NextDevice and RevokeSelected stay in that process on
that same connection. A separate next-device command does not save selection.
No device ID or configuration arguments are accepted. Fingerprints, invite bytes
and backend diagnostics are never printed. A lost mutation reply is unknown,
with no retry; confirmed host denial never promises network cleanup.

Exit 0: completed read/action or cancelled selection with effect `none` (read
observations may explicitly be unavailable). Exit 1: closed error. Exit 2:
invalid command/arguments. Enable/turn-off/create-invite return unavailable
without dispatch. Recovery actions and Client CLI are not registered; Mac host
is deferred/unavailable. There is no connector, DIY, installer or admin fallback.

The packaged connection window now exposes session-only client status, invite,
cancel, disconnect, retry (fresh invite) and health controls. Native GTK dialogs
own invite paste, exact Host consent and comparison phrases; worker threads own
blocking session calls. Only the expected runtime root's successful WebKit load
reports Connected. A separate ephemeral runtime view has no control bridge and
keeps a visible native session-only notice after the connection window closes.
Closing controls cancels an unfinished attempt; closing the runtime asks for
local disconnect. Cancel never promises Host revocation or membership removal.
Native save consent enables same-node reconnect through GI Secret. Only the
default login collection is accepted, with exact home/control/node/profile binding,
verified readback and local absence after deletion. Locked/denied/unavailable or
failed saving keeps the session usable with a persistent accessible native warning;
its Retry save action opens the static controls even after that view closes.
No plaintext, file or substitute credential store is used. One unreadable saved
item does not hide readable peers. Remove is local-only and never claims Host
revocation. Cancel during saving waits for write/readback before teardown, while
fencing later connection/load callbacks. The runtime proxy answers bootstrap with an unrelated session
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

### Keep running after logout

The native controls sit above the web view with 12px margins and a separator.
The setting label and compact switch share a horizontal row; partial-state and
update actions each share a row with their label. Diagnostics wrap below the setting.

**Keep HappyRanch running when I'm logged out** defaults OFF. Turning it ON
calls logind's system-bus `SetUserLinger(own uid, true, interactive=true)` via
system Python's existing Gio/GLib bindings, then
`systemctl --user enable happyranch-daemon`. There is no new dependency. A local
polkit `AccessDenied` leaves the switch OFF and names the local polkit rule in
a plain diagnostic. If enable fails after the app enabled linger, the app rolls
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
**Partly on - click to repair/turn off** with **Repair** and **Turn off** actions.
Controls refresh while open and after actions; closing observes the state again.
Only a fully attached, freshly ON service is left running when the window closes;
the window exits and releases its lock. Startup/restart proof failures retain the
existing shutdown obligation even when keep-running had been ON. Failed state
reads or stops keep window/worker/lock custody for a subsequent close retry.

### Update installed while running

After ownership proof, the app shows **Update installed - restart HappyRanch**
when `/opt/happyranch-desktop/build-info.json` has `st_ctime` newer than the
unit's process start, or the unit reports `NeedDaemonReload=yes`. The Debian
builder moves the payload under `/opt/happyranch-desktop`; dpkg replacement by
rename makes the installed file's ctime the install-time signal. The app reads
the unit's recorded realtime `ExecMainStartTimestamp` in microseconds through
`busctl --user get-property` and compares it directly with the file's realtime
ctime. Suspend does not shift that recorded start; no current realtime-minus-
monotonic offset is reconstructed. Wall-clock adjustments between service start
and installation can still affect this best-effort signal. Missing/unreadable
metadata or invalid/unrepresentable start timestamps suppress the banner and log
a warning, without refusing attachment.

This is the authorized substitute for design 2.4's comparison against the
running daemon's reported version: the daemon currently reports no build
identity. It preserves the same user-visible banner/restart behavior without
reading daemon version fields or changing runtime source. An immutable running
build identity is a possible later improvement.

**Restart HappyRanch** first revalidates ownership, reloads the user manager if
`NeedDaemonReload=yes`, and restarts `happyranch-daemon`. It then repeats the full
B2a ownership proof, including the bounded asynchronous PID/port publication
wait, listener inode, health and post-health rechecks, before reattaching the
web view. Every restart error, including reload failure or timeout, revokes
leave-running eligibility and enters the existing shutdown path. A failed stop
retains worker/window/lock custody and subsequent-close retry until a packaged-unit
stop succeeds, even if the switch is ON and the old daemon was attached.

If `systemctl --user is-system-running` is unavailable or reports no running
manager, the window shows a fallback explanation and retains its child-process
mode. Its keep-running switch is disabled with the reason **Unavailable: no
systemd user manager.** It requires an empty home (any existing `daemon.pid` or
`daemon.port`,
even stale/partial, refuses startup without changing it). The child receives a
minimal OS environment, the selected home, loopback bind, random port `0`, and
the payload's `web/dist`. The window waits for its own Popen PID and healthy
published port, then attaches. Closing sends TERM only to that child, waits five
seconds, then KILL and a two-second reap if necessary. Marker cleanup retains
the existing empty-at-spawn and unchanged-inode checks. Child logs are at
`<desktop-home>/logs/desktop-daemon.log`.

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
| `gir1.2-webkit2-4.1` | WebKit2 4.1 namespace/engine and transitive libraries | [Jammy](https://packages.ubuntu.com/jammy/gir1.2-webkit2-4.1), [Bookworm](https://packages.debian.org/bookworm/gir1.2-webkit2-4.1) |
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
