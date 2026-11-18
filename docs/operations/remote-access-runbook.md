# HappyRanch Remote Access — Self-Hosted headscale + DERP Runbook

For the fixed outside-home test server, follow the founder-run [test-server hardening guide](remote-access-test-server-hardening.md) (PR-A; version/relay/ACL decisions remain deferred).

**Phase-2 Part A** — deploy artifact runbook
**Author:** dev_agent (config + docs only — founder executes)
**Status:** Historical Phase-2 provisioning artifact (THR-097 / PR #425).

The steps below preserve that earlier self-hosted control-plane/Mac environment
setup, including its reusable-key example. Current THR-327 seq187/227 managed
Linux installation, single-use credential-file handoff and lifecycle are maintained
in [the remote-access guide](../agent-guides/contracts/remote-access-and-remote-jobs.md)
and [the normative contract](../specs/2026-08-26-managed-remote-access-contract.md#managed-linux-m1-acceptance-thr-327-seq187).
Bounded M1 **PASSED** on main `455c9c5b689fa25f625e2df8cae1b551c2a1de86`
([run 42978877282](https://github.com/t-benze/happyranch/actions/runs/42978877282)).
M2 tailnet transport, full-daemon acceptance, restart/reboot recovery and deployment
remain **UNEXECUTED** and outside M1. This historical runbook is not evidence of
those outcomes or authorization to provision/deploy.

---

## Daemon stop budget

The packaged user `happyranch-daemon.service` explicitly sets
`TimeoutStopSec=60`; its desktop service wrapper gives the daemon child
60 seconds after TERM before KILL (then a bounded reap). Source deployments
using `scripts/daemon.sh stop` also allow 60 seconds. This leaves room for the
jobs shutdown mitigation's 30-second TERM grace and finalization. Long native
drains can still exhaust the outer budget; this is not crash recovery.
The generated `systemd_unit.py` limits belong to separate processes:
connector 10 seconds, tsnet sidecar 35 seconds, verifier 5 seconds. They do
not stop the daemon and are unchanged. Existing installed units require the
normal separately authorized release/install; this PR does not restart them.

## Desktop package host

The Linux root installer supports `--layout source` (default, unchanged source
units at `/opt/happyranch`) and `--layout deb` (the installed desktop package at
`/opt/happyranch-desktop`). The latter uses:

* connector: `/opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.cli run --managed --config /etc/happyranch/connector.json`;
* sidecar: `/opt/happyranch-desktop/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json --credential-file ${CREDENTIALS_DIRECTORY}/enrollment-key`.

`packaging/linux/build-payload.sh` installs the runtime wheel into the private
Python and ships `python3 -> python3.12` plus this sidecar. The installer checks
literal root-owned, non-group/world-writable prefix directories and executable
files, including that exact Python link. Both executables and every payload
parent through `/opt/happyranch-desktop` and `/opt` must grant the configured
`happyranch` service account execute/search access, evaluated using Unix DAC
owner bits first, otherwise matching primary/supplementary group bits, otherwise
other bits. Missing access refuses before config/unit publication or start and
names the path and permission; fresh-host provisioning rechecks after account
creation. The installer never creates or chmods the dpkg prefix. Install/upgrade
the package separately with the required deployment approval; neither provisioning nor installation below builds a package.

The .deb additionally ships checksum-pinned Headscale v0.25.1 inert at
`/opt/happyranch-desktop/libexec/headscale/headscale` (0755, dpkg root ownership).
It is outside `bin/`, with no wrapper or symlink. Payload builds verify the
official native amd64/arm64 asset before copying or inspecting it and never
execute it. Exact BSD-3-Clause text, linked-module originals/inventory and Go
1.23.4 toolchain notices ship in the payload's `licenses/` and the .deb's
`/usr/share/doc/happyranch/licenses/`; see the [release handbook](linux-release-handbook.md#build-the-intermediate-payload).
This bundling creates no service, user, config, socket or state and performs no
start/enable. TASK-16780 owns lifecycle wiring to this fixed path.

Prepare operator-owned, single-link regular JSON input files with no symlinked
parents and no group/world write bits. Inputs are bounded to 64 KiB each. Supply
stable tenant/home/connector IDs; keep the same files for that installation.
For the planned release daemon, `connector.json` is:

```json
{
  "tenant_id": "YOUR-STABLE-TENANT",
  "home_id": "YOUR-STABLE-HOME",
  "connector_id": "YOUR-STABLE-CONNECTOR",
  "daemon_token_path": "/etc/happyranch/daemon.token",
  "policy_path": "/etc/happyranch/policy.json",
  "state_path": "/var/lib/happyranch-connector/trust-state.json",
  "system": true,
  "service_user": "happyranch",
  "service_group": "happyranch",
  "managed": {"bind_host": "127.0.0.1", "bind_port": 18443},
  "excluded_daemon_ports": [8765]
}
```

The service account has `/nonexistent` as its home. Do not use the supervisor's
home-based trust-state default. Provisioning creates the service-writable state
paths, including private `/var/lib/happyranch-tsnet-sidecar`. `sidecar.json` is:

```json
{
  "StateDir": "/var/lib/happyranch-tsnet-sidecar",
  "ControlURL": "https://<TEST_CONTROL_HOST>:8443",
  "RoleIdentity": "home-sidecar-YOUR-STABLE-ID",
  "ExpectedPeer": "YOUR-EXPECTED-PEER",
  "DERPPolicy": "private-only",
  "ListenAddr": ":443",
  "ConnectorAddr": "127.0.0.1:18443",
  "ExcludedDaemonPorts": [8765]
}
```

The real `ControlURL` comes from private operator configuration outside this repository.

Do not put an enrollment key or `CredentialFile` in this JSON. The existing
systemd credential-file override supplies it. The operator supplies a fresh
single-use Headscale key at installation, and later performs local pairing;
provisioning generates neither keys nor device credentials.

Supply `policy.json` as the current operational `PolicyEnvelope`: schema 1,
`artifact_version` equal to the shipped `runtime/remote_access/route_policy.json`
version, that complete artifact under `artifact`, current aware UTC `issued_at`,
`max_age_seconds` (for example 3600), revision 1 or the current revision, and
`state: "active"`. This is the same envelope used by
`scripts/ci/m1_install_ready_uninstall.sh`. Both structural policy validation and
freshness run before any service start; preserve the policy's existing renewal
process. Provisioning does not mint a new policy or relax its lifetime.

After host-install authorization, stage inputs with the packaged interpreter:

```sh
sudo /opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.installer provision --layout deb --connector-config /root/remote-access-inputs/connector.json --sidecar-config /root/remote-access-inputs/sidecar.json --policy-file /root/remote-access-inputs/policy.json --release-user benze --data-home /home/benze/.local/share/happyranch
```

The installer records the resolved data home at provisioning time. Supply the
actual `$XDG_DATA_HOME/happyranch` as `--data-home` when XDG is customized;
sudo cannot read the login environment. Omission defaults to the passwd home
plus `.local/share/happyranch`. The existing directory must belong to the release
uid, have no symlink components or group/other write bits, and be outside
`~/.happyranch`, `/etc` and `/var`. Provisioning and install also refuse paths
hidden/inaccessible in the verifier's unchanged hardened namespace: `/tmp`,
`/var/tmp`, `/dev`, `/usr/lib/modules`, `/proc/kallsyms`, `/proc/kcore`,
`/proc/kmsg`, `/dev/kmsg`, and descendants. The renderer and installer share one
directive/path table. The diagnostic names the path and hiding directive;
choose a visible directory such as `/home/<user>/.local/share/happyranch`.
Comparison follows no-follow traversal; symlinked parents refuse rather than
being followed. Do not relax hardening or add a data-home bind mount.
The token source defaults to
`<data_home>/daemon.token`; `--daemon-token-source /absolute/path` can override it.
Use the actual absolute token-source path as an input. It must be a literal,
single-link regular 0600 file containing a nonempty UTF8 token. The installer
copies it to `/etc/happyranch/daemon.token`, mode 0600, owned by
`happyranch:happyranch`. The managed connector reads this validated copy through
the unchanged `FileDaemonCredentialProvider`, as on main; its unit has no daemon
`LoadCredential`. It never reads the provisioning input in the release data
home. The root verifier has no `LoadCredential` and reads no bearer.
Configs are root-owned, group `happyranch`, mode 0640.
The root-owned 0600 `/etc/happyranch/provision.json` records the layout, exact
source path, token SHA-256 and config digests. No token value enters that record,
units, argv or diagnostics. Provision never starts services; it refuses existing
config/token/record destinations. Partial files remain for operator inspection;
there is no upgrade journal, rollback or automatic recovery.

Install only after the separate remote-access activation approval, supplying
the existing root-owned 0600 enrollment-key file:

```sh
sudo /opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.installer install --layout deb --enrollment-key-file /root/remote-access-inputs/enrollment.key
sudo /opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.installer check-drift
```

Deb installation requires complete, unchanged, valid provisioning before unit
writes or service-manager calls. Existing enrollment refusal, cleanup and
post-start target-stop behavior remain. Check-drift reads the installed layout,
compares exact units plus fragment/drop-ins/reload/active state, revalidates the
configs/current policy, and rereads BOTH the current token source and staged
copy against the recorded hash. A later daemon-token rotation therefore fails
drift even when the staged copy is unchanged. Reprovisioning/renewal requires
explicit operator recovery; it does not overwrite existing files automatically.
Use `uninstall --layout deb` (and optionally the existing `--purge`) to remove
units/state. Configs, provisioning record, staged token, package, logs and account are retained.
The release install record and verifier socket/service are removed.
A missing package is not required for uninstall; unit safety checks still apply.
The separately callable `disable-headscale --layout deb` root operator entry
and legacy uninstall remove the proven inert Headscale unit/policy/custody and
config/DERP/leaf/key/public-CA files and empty CA directory bound by the final
root-only publication receipt,
keeping package payload/notices. The staged-profile uninstall refusal remains;
helper Turn off and activation-state cleanup remain later slices. Unknown
or edited/unreceipted config/DERP/leaf/key/CA and all DB/Noise/socket entries
refuse and remain.
The `home-ca/` signing/staging tree is preserved. The config prefix selects from
complete kernel IPv4/IPv6 default-route inventory (including ECMP), refusing
unresolved preferred sources as `inventory`; no address input is accepted.
Port preflight precedes all publication. Unchanged address/config reruns are
idempotent; a changed address refuses `address-changed` pending future
RefreshHomeAddress. Publication is receipt-last; missing members or pending
residue refuse without automatic repair. Cleanup rechecks each digest/custody
before deletion; a crash with a partial pair and retained receipt also refuses.
Preparation invokes the fixed root-run materializer through the existing command
runner, validating its closed status and source custody. ensure validates the
initial handoff; complete unchanged reruns use read-only status and digests; renew-leaf runs only after status reports due. Only
server leaf/key enter service UID:GID 0600 custody. control-ca.pem is
root:happyranch 0640; empty-ca-directory is root:root 0755 and empty. The CA key
is never read/copied by the installer. Source/destination digests and public CA
fingerprint are bound before publication. Files and empty directory use
exclusive pending plus atomic no-replace publication; the extended existing
receipt is last. Config-only receipts remain admitted for extension. Due inert
renewal retains config/DERP/public-CA/directory inodes and the signing key, and
never reloads. Interrupted extension or renewal refuses without repair; cleanup
removes only fully receipted members and retires the publication receipt after
them. Helper Turn off and running-service coordination remain 3b-2h.
Only a valid creation receipt plus fresh identity/shape, unshared group, stopped unit and no process using its UID permits userdel then conditional
groupdel. Unowned accounts remain visibly `preserved: not created by HappyRanch`.
Before userdel succeeds, failures keep account/receipt. A later groupdel failure
keeps receipt and remaining objects, never recreates the user, and returns typed
secret-free `partial-cleanup-uncertain`, exit1. Explicit retry rechecks the valid
receipt and all remaining objects, skips an absent user only when its UID is
unused, refuses UID reuse/changed identity/shared or mismatched group, and deletes
only a matching empty remaining group. Remove the receipt last after every owned
object is confirmed absent; receipt-only retries converge. Other uncertainty is
`cleanup-uncertain`, never success. No automatic repair/retry. See the
[maintained A–E contract](../agent-guides/contracts/remote-access-and-remote-jobs.md#inert-linux-headscale-custody-prefix-thr-327-slice-3b-2f-1).

Preparation first refuses competing fragments/drop-ins in the default systemd
load paths with no-follow checks and zero effects (`foreign-unit`; unsafe
parent/I/O `custody-uncertain`). The maintained contract lists the exact paths
and literal merged-`/usr` alias exception. Preparation reuses cleanup's bounded
no-follow link inventory over all those paths before any account, receipt,
custody or unit effect. A symlink named for or whose literal target basename is
the unit refuses even when dangling: wants/requires and aliases are foreign.
Alias drop-ins across those paths cannot become applicable because the alias
itself refuses, even without a drop-in. Preserve every foreign byte/mode/link;
no adoption. Cleanup shares the all-load-path admission and retains its extra
check before stop/disable for matching alias/enablement symlinks under
`/etc/systemd/system` or
`/run/systemd/system`: wants/requires links, foreign/dangling targets and manual
links to the managed unit are all foreign because this installer creates none.
Typed secret-free `cleanup-uncertain`, exit1, preserves account, receipt, unit,
custody and links. Unrelated links remain; no-follow inventory uncertainty or
exhaustion refuses. A–E partial-cleanup/retry rules above remain unchanged.

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

Managed Linux no longer takes a fixed `daemon_port`. The connector and sidecar
exclusion lists remain sorted, equal and always include DEV 8765 plus operator
exclusions. The ingress address is exactly `127.0.0.1:<managed.bind_port>`;
ingress cannot use an excluded port. Every resolved daemon port is checked
against those exclusions, so leave the random release port out of static config.
The Go sidecar keeps its existing literal-loopback and excluded-port checks.
macOS and existing DIY/lab fixed-target configurations retain their contracts.

`/etc/happyranch/release-daemon.json` is root-owned 0644 and records exactly:
`user`, `uid`, `data_home`, `unit=happyranch-daemon.service`, and
`fragment_path=/usr/lib/systemd/user/happyranch-daemon.service`. Install/drift
revalidate the target uid and data directory. The root verifier reads the port
file no-follow (regular, correct uid, single link, at most 16 bytes, decimal
1..65535), refuses DEV/excluded ports, and verifies the active user unit, exact
packaged fragment and empty DropInPaths. **No drop-ins are tolerated.** It matches
the loopback LISTEN inode to a MainPID fd, then separately proves the server fd
of the connector's exact established connection before any HTTP/bearer bytes.
The connector rereads this evidence per connection; refusals log a short machine
reason and return HTTP 503, with no fixed-port fallback. DEV 8765 and
`~/.happyranch` are never modified. Random startup and desktop changes are PR-D.

The root-owned socket is `/run/happyranch-release-verifier/control.sock`, mode
0660 root:happyranch, under a root-owned 0755 directory. Only the closed
`resolve` / `confirm <local-port> <daemon-port>` protocol is accepted. It accepts
no paths or usernames and reads no daemon token. The connector remains the
unprivileged `happyranch` account with its existing empty capabilities.

If install times out, inspect the connector journal's initial `failing_gates`
categories and the verifier journal's closed refusal reason. An installer
rollback stops the units; a later `verifier_unavailable` probe alone does not
identify the original startup failure. Unchanged waiting gates are logged once;
raw evidence and exception text are never logged by the verifier.
For `credential_permissions:credential_unreadable`, the connector also journals
`daemon credential refused: <reason>` with a closed injection reason (not
configured, missing, symlink, unreadable, permissions or empty), or `unavailable`
for unknown failures. Inspect that reason before changing provisioning or
service setup. Credential readers, permission checks and selection logic are
unchanged; the managed unit has no daemon `LoadCredential` and the staged
`/etc/happyranch/daemon.token` configuration selects `FileDaemonCredentialProvider`.

Verifier hardening (the separate root service only):

| Setting | Reason |
| --- | --- |
| `User=root`, `Group=happyranch` | Read another uid's proc fd links and query that user's manager; socket access remains group-scoped. |
| `ProtectSystem=strict` | No persistent filesystem writes. |
| `ProtectHome=read-only` | Read the recorded port file while preventing home writes; hiding homes would break verification. |
| `NoNewPrivileges=yes`, empty `AmbientCapabilities` | No exec-time privilege gains or ambient propagation. |
| `CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_SETUID CAP_SETGID` | Read protected home/proc metadata and allow systemctl's local user-bus bridge to change uid/gid; no capabilities are added to the connector. |
| `PrivateTmp=yes`, `PrivateDevices=yes` | Isolate temporary files and hide physical devices. |
| `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectKernelLogs=yes`, `ProtectControlGroups=yes` | Prevent kernel/control-plane modification and kernel log reads. |
| `RestrictAddressFamilies=AF_UNIX` | Only local verifier/user-manager IPC; verifier does not dial TCP. |
| Omit `PrivateNetwork`, `ProtectProc`, `ProcSubset`, `PrivateUsers` | Must observe host `/proc/net/tcp`, the target process fds, and host user manager with real uid authority. |
| `RestrictSUIDSGID=yes`, `RestrictRealtime=yes`, `LockPersonality=yes` | Prevent set-id files, realtime scheduling and ABI switching. |
| `MemoryDenyWriteExecute=yes`, `SystemCallArchitectures=native` | No executable writable mappings or foreign syscall ABI. |
| `UMask=0077`, `TasksMax=16`, `MemoryMax=96M`, `LimitNOFILE=128` | Bound accidental file exposure, process count, memory and descriptors. |
| `Type=simple`, `Restart=on-failure`, `RestartSec=1`, `TimeoutStopSec=5` | Socket activation and bounded stop/recovery; no false notify readiness. |

Systemctl's local user-bus syntax is documented in the
[systemctl manual](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html).
The service deliberately retains the host network namespace even though its
only sockets are Unix sockets. Unit text and fixture checks do not prove that
all hardening settings work together; hosted M1/M2 must supply that evidence.
M1's synthetic server and M2's real daemon use the runner user manager and exact
packaged FragmentPath, with a CI source-wheel ExecStart instead of the `.deb`
binary. Neither allows a drop-in exception. M2 requires a squatter/non-MainPID
port-file negative. M3/M4's legacy shared-driver assumptions require separate
alignment before reuse. These source changes authorize no host installation,
activation or Mac testing.

## Overview

This runbook walks through provisioning a **self-hosted headscale control plane**
with an **embedded DERP relay**, all behind **Caddy + Let's Encrypt** for TLS.

When complete, you will have:
- A headscale coordination server at your chosen hostname (e.g. `headscale.example.com`)
- A DERP relay on the same host (STUN on UDP 3478, DERP HTTPS on 443)
- **No dependency on Tailscale's SaaS control plane or public DERP fleet**

The HappyRanch Mac client (Phase 1, PR #425) will connect to this control plane
via its `HAPPYRANCH_TSNET_TRANSPORT=1` / `HAPPYRANCH_TSNET_CONTROL_URL` /
`HAPPYRANCH_TSNET_AUTH_KEY` environment variables.

---

## Prerequisites

You need:
1. A **Linux host** (VM or bare-metal) with a **public IPv4 address**
   - Recommended: 2+ GB RAM, 10 GB disk
   - OS: Ubuntu 22.04+ or Debian 12+
2. **Docker** and **Docker Compose v2** installed on the host
3. A **domain name** you control (e.g. `example.com`) — you will create an
   A record for the headscale server
4. **Firewall rules** allowing inbound:
   - TCP 80   (Let's Encrypt HTTP-01 challenge)
   - TCP 443  (headscale API + DERP HTTPS)
   - UDP 3478 (DERP STUN)

---

## Step 1: Host provisioning

Provision a Linux VM or bare-metal host with a public IPv4 address.

```bash
# Example: Ubuntu 22.04
sudo apt update && sudo apt upgrade -y

# Install Docker (official)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER

# Install Docker Compose plugin (v2)
sudo apt install -y docker-compose-v2

# Verify
docker --version
docker compose version

# Open firewall ports (example: UFW)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 3478/udp
sudo ufw enable
```

> **Cloud-specific note:** If your cloud provider has a separate firewall
> (AWS security groups, GCP firewall rules, DigitalOcean cloud firewall),
> open TCP 80, TCP 443, and UDP 3478 there as well.

---

## Step 2: DNS — create an A record for the control-plane hostname

Choose a subdomain for your headscale server, e.g. `headscale.example.com`.

Create a **DNS A record** pointing that hostname to the host's **public IPv4**:

| Type | Name               | Value          | TTL   |
|------|--------------------|----------------|-------|
| A    | headscale          | `<public-ip>`  | 300   |

Wait for DNS propagation (usually < 5 minutes). Verify:

```bash
dig +short headscale.example.com
# Should return the host's public IP
```

---

## Step 3: Clone the deploy artifact and configure secrets

On the provisioned host:

```bash
# Clone the happyranch repo (or copy the deploy/remote-access/ directory)
git clone https://github.com/t-benze/happyranch.git
cd happyranch/deploy/remote-access
```

Copy the environment template and fill in real values:

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
# The full URL of your headscale server (must use https://)
HEADSCALE_SERVER_URL=https://headscale.example.com

# The DERP public hostname (same host, without https://)
DERP_PUBLIC_HOSTNAME=headscale.example.com

# Optional: custom STUN port if your cloud maps it differently
# DERP_STUN_PORT=3478

# Optional: Let's Encrypt email for TLS certificate issuance
# Uncomment the tls line in Caddyfile if setting this
# TLS_EMAIL=you@example.com
```

---

## Step 4: Verify TLS configuration

The Caddyfile includes `tls {$TLS_EMAIL}` which reads the email from the
`TLS_EMAIL` variable passed by docker-compose from your `.env` file.  Set
`TLS_EMAIL=you@example.com` in `.env` to receive certificate expiry
notifications from Let's Encrypt.

If `TLS_EMAIL` is empty (the default), Caddy still obtains valid
Let's Encrypt certificates — the email is only used for notifications.

---

## Step 5: Set the DERP hostname in derp.yaml

**This step is REQUIRED before starting the stack.**

Docker Compose does NOT expand environment variables inside bind-mounted
files.  The file `derp/derp.yaml` contains the literal placeholder
`changeme.example.com` — you MUST edit it to your real hostname.

```bash
# Replace the placeholder with your real DERP hostname
sed -i 's/changeme.example.com/headscale.example.com/g' derp/derp.yaml

# Verify the replacement
grep hostname derp/derp.yaml
# Expected: hostname: "headscale.example.com"
```

> If you skip this step, clients will receive a DERP map pointing at
> `changeme.example.com` — a non-routable hostname — and DERP relay
> fallback will fail.

---

## Step 6: Start the stack

```bash
docker compose up -d
```

Check that both services are running:

```bash
docker compose ps
```

Expected output:

```
NAME                  STATUS
happyranch-headscale  Up (healthy)
happyranch-caddy      Up
```

Check logs for any startup errors:

```bash
docker compose logs headscale
docker compose logs caddy
```

---

## Step 7: Verify the control plane is reachable

From the host itself:

```bash
# headscale health endpoint (internal, via Docker network)
curl -s http://localhost:8080/health
# Expected: OK

# Control plane public URL (via Caddy + TLS)
curl -s https://headscale.example.com/health
# Expected: OK
```

> **Troubleshooting:** If the public URL returns a TLS error, check:
> - DNS A record is correct (`dig +short headscale.example.com`)
> - Port 443 is open to the internet
> - Caddy logs: `docker compose logs caddy`
> - Let's Encrypt may take 30-60 seconds on first request

---

## Step 8: Health check — headscale nodes list

Use the headscale CLI inside the container:

```bash
# Should return an empty list (no nodes registered yet)
docker compose exec headscale headscale nodes list
```

Expected output:

```
ID | Hostname | Name | ... | LastSeen | Expiry
(empty — no nodes registered)
```

---

## Step 9: DERP reachability probe

Verify the DERP relay is accessible:

```bash
# Check that the STUN port is open (from an external machine, or the host)
nc -vzu headscale.example.com 3478
# Expected: Connection to headscale.example.com 3478 port [udp/stun] succeeded!

# Check DERP HTTPS endpoint (should return WebSocket upgrade or 404 — either is fine)
curl -s -o /dev/null -w "%{http_code}" https://headscale.example.com/
# Expected: any HTTP response (200, 404, etc.) — confirms TLS + headscale are running
```

The DERP relay is working if:
- UDP 3478 is reachable
- HTTPS 443 on the control-plane hostname returns a headscale response

---

## Step 10: Create the admin user

Before minting auth keys you need a user in headscale.  Create the
`admin` user now:

```bash
# Create the admin user
docker compose exec headscale headscale users create admin

# Verify the user was created
docker compose exec headscale headscale users list
# Expected output:
# ID | Name
# 1  | admin
```

---

## Step 11: Mint a device auth key

This is the key the HappyRanch Mac client consumes via
`HAPPYRANCH_TSNET_AUTH_KEY`.

```bash
# Create a REUSABLE auth key (recommended for a small tailnet)
# The key is printed ONCE — save it immediately.
docker compose exec headscale headscale preauthkeys create \
  --reusable \
  --expiration 365d \
  --user admin \
  --output json
```

The output contains the auth key:

```json
{"key": "abc123def456..."}
```

**Save this key securely.** You will use it in the next step to configure
the HappyRanch Mac client.

Options:
- `--reusable` — the same key can register multiple nodes (useful for dev)
- `--expiration 365d` — key expires after one year
- `--user admin` — nodes register under the `admin` user in headscale
- Omit `--reusable` for one-time keys (more secure for production)

To verify the key was created:

```bash
docker compose exec headscale headscale preauthkeys list --user admin
```

---

## Step 12: Wire the HappyRanch Mac client back to this control plane

On the Mac running the HappyRanch app, set these environment variables:

```bash
# Enable the in-process tsnet WireGuard tunnel
export HAPPYRANCH_TSNET_TRANSPORT=1

# Point at YOUR self-hosted headscale (NOT Tailscale SaaS)
export HAPPYRANCH_TSNET_CONTROL_URL=https://headscale.example.com

# The auth key minted in Step 11
export HAPPYRANCH_TSNET_AUTH_KEY=abc123def456...

# Optional: custom hostname for the node
export HAPPYRANCH_TSNET_HOSTNAME=my-macbook
```

Then launch the app. The client's `ensureTsnetEngine()` will:
1. Read `HAPPYRANCH_TSNET_AUTH_KEY` and `HAPPYRANCH_TSNET_CONTROL_URL`
2. Call `tsnet_init(authKey, controlURL, hostname)` — this points at YOUR headscale
3. Call `tsnet_start()` — registers the node with your headscale

Verify the node appeared:

```bash
# On the server
docker compose exec headscale headscale nodes list
```

Expected: a new node with the hostname you set (or `happyranch-tsnet` if not set).

---

## Step 13: Verify end-to-end connectivity (optional)

Once a Mac client is registered, you can verify the tailnet is working:

```bash
# On the server, ping the Mac node's Tailscale IP
docker compose exec headscale headscale nodes list
# Copy the Tailscale IP of the Mac node (100.64.x.y)

# From the Mac node, verify it can reach the headscale server
# (tsnet local_addr returns the node's own Tailscale IP)
```

---

## Appendix A: Directory layout

```
deploy/remote-access/
├── docker-compose.yml       # Docker Compose stack (headscale + Caddy)
├── Caddyfile                # Caddy reverse proxy + TLS config
├── .env.example             # Environment variable placeholders
├── .env                     # REAL secrets (gitignored — founder creates from .example)
├── .gitignore               # Ignores .env
├── headscale/
│   └── config.yaml          # headscale server config (self-hosted, no Tailscale SaaS)
├── derp/
│   └── derp.yaml            # DERP region map (self-hosted relay ONLY)
docs/operations/remote-access-runbook.md # This file
```

---

## Appendix B: Troubleshooting

### Caddy can't obtain a TLS certificate
- Verify DNS A record is correct and propagated
- Verify port 80 is open (Let's Encrypt HTTP-01 challenge)
- Check Caddy logs: `docker compose logs caddy`
- Ensure `tls {$TLS_EMAIL}` is uncommented in Caddyfile

### headscale starts but isn't reachable
- Check `docker compose logs headscale` for errors
- Verify `.env` has `HEADSCALE_SERVER_URL` set
- The `server_url` in config.yaml references `${HEADSCALE_SERVER_URL}` — ensure the env var is set

### Node registration fails ("auth key invalid")
- Verify the auth key was copied correctly (no trailing whitespace)
- Check if the key expired: `headscale preauthkeys list --user admin`
- Create a new key if needed (Step 11)

### DERP STUN is unreachable
- Verify UDP 3478 is open on both the host firewall and cloud firewall
- Check headscale logs for DERP startup messages
- Try `nc -vzu <hostname> 3478` from an external machine

### DERP relay traffic doesn't flow
- DERP uses WebSocket over HTTPS on port 443. Caddy must forward WebSocket upgrades.
- The default Caddy `reverse_proxy` directive handles WebSocket automatically.
- If behind an additional CDN/proxy, ensure WebSocket support is enabled.

---

## Appendix C: Day-to-day operations

### View registered nodes
```bash
docker compose exec headscale headscale nodes list
```

### Expire (remove) a node
```bash
docker compose exec headscale headscale nodes expire --identifier <node-id>
```

### List preauth keys
```bash
docker compose exec headscale headscale preauthkeys list --user admin
```

### Revoke a preauth key
```bash
docker compose exec headscale headscale preauthkeys expire --user admin --key <key-prefix>
```

### View logs
```bash
docker compose logs -f headscale
docker compose logs -f caddy
```

### Update headscale
```bash
docker compose pull headscale
docker compose up -d headscale
```

### Backup
```bash
# The persistent state lives in the headscale-data Docker volume.
# Back it up:
docker run --rm -v happyranch_headscale-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/headscale-backup-$(date +%Y%m%d).tar.gz -C /data .

# Back up the .env file as well (contains secrets).
```

## History

Citations removed from the body, with the section they supported:

- `THR-097` — HappyRanch Remote Access — Self-Hosted headscale + DERP Runbook (original line 3).
