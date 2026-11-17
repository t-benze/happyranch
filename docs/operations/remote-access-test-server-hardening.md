# Outside-home test Headscale server hardening (PR-A)

These are **FOUNDER-RUN** steps on `<TEST_CONTROL_HOST>`; agents must not access,
deploy to or modify the live host. Substitute private operator values locally;
never publish a real name, hostname, IP, instance or security-group identifier.
This guide targets the established official v0.25.1 `.deb`, system user
`headscale`, systemd, `/etc/headscale`, `/var/lib/headscale`, HTTPS and embedded
DERP on 8443/tcp and STUN on 3478/udp. Metrics and gRPC remain loopback-only.
The co-hosted website and its security group are outside this procedure.

PR-A supplies version-independent operating controls. No version is selected;
no Headscale config or ACL policy is changed here. Founder decision THR-327
seq563 is unanswered. Stop if a step needs a version choice or the pre-checks
disagree with this deployment. This does **not** certify the server dev-only.

## Known gaps before dev-only

- The v0.25.1 embedded relay is open to anyone: it has no `verify_clients` option.
  Relay membership enforcement and the relay-abuse acceptance test are BLOCKED
  pending PR-B and the founder's version decision (THR-327 seq563).
- ACL restrictions are not yet applied. An empty `policy.path` means default
  allow-all among enrolled peers; an approved device is **not** constrained to
  its invited home machine by this inventory. PR-B owns policy and acceptance.
- v0.25.1 remains unpatched. The patch procedure below is a future operator
  procedure, not authorization to select or install another version.

## 1. Read-only pre-checks

Run in a private root terminal, without session recording or shell tracing.
Keep raw CLI/config/firewall output on the host; share only redacted conclusions.

```sh
headscale version
# Expected: v0.25.1. Otherwise STOP; do not adapt this parser to another version.
awk '/^oidc:/ {section=1; next} section && /^[^[:space:]#]/ {section=0} section && /^[[:space:]]+issuer:/ {print}' /etc/headscale/config.yaml
# Expected: issuer absent or empty (issuer: ""). Inspect the oidc section locally
# to confirm no inline YAML mapping or other issuer representation was missed.
systemctl is-active headscale
# Expected: active.
ss -lntup
# Expected: 8443/tcp and 3478/udp; metrics/gRPC listen only on 127.0.0.1.
nft list ruleset
# Expected: complete baseline of existing tables, rules, handles and counters.
ufw status verbose
iptables-save
ip6tables-save
# Expected: existing firewall state; if a tool is absent, record NOT INSTALLED.
# Do not install it or replace the existing firewall manager.
```

Also inspect the effective Headscale unit and any override/environment files
locally for an OIDC issuer override; do not copy their contents into logs or
threads. Verify no externally enabled OIDC or registration front end exists.
If OIDC is enabled, STOP for a separately scoped change; do not edit config here.
Preserve the firewall baseline in root-only local storage for comparison after
step 5. Confirm `<HEADSCALE_SG>` is the separate Headscale group in the cloud
console. Its public ingress stays exactly **8443/tcp + 3478/udp**, with no IP
allowlist (the founder roams). Cloud security groups cannot rate-limit; host
nftables supplies the rate control. Do not alter the website group.

## 2. Tight joining and key retirement

With OIDC disabled, v0.25.1's browser registration page displays instructions
for the **administrator** to run `headscale nodes register --user ... --key ...`.
The page does not approve a node itself. An outsider knowing the URL cannot
self-approve. Administrators must not execute those commands for unapproved
requests. Use operator-issued, single-use, ten-minute keys only:

```sh
headscale preauthkeys create --user '<USER>' --expiration 10m
# Expected: one private key, delivered through the approved private channel.
# No --reusable; no --ephemeral unless separately and explicitly justified.
headscale preauthkeys list --user '<USER>' -o json
# Expected: new key has expiration about 10 minutes after created_at;
# reusable/ephemeral/used are false (the CLI omits false fields).
```

Creation/listing prints raw keys. Do it only in the private root terminal, never
in a recorded terminal, ticket, thread, job log or clipboard history. Inventory
**every user** (`headscale users list`), then retire reusable, long-lived and
unused keys for each user. A just-issued unused key is intentionally a finding
until consumed or expired; run the daily check after the invitation window.

**Explicit argv gap:** v0.25.1 `headscale preauthkeys expire KEY --user <USER>`
requires the **raw key as a positional argument**, not a key ID or prefix. This
is unsuitable for the protected programmatic transport described in the
[remote-access contract](../agent-guides/contracts/remote-access-and-remote-jobs.md).
For this manual server maintenance only, the founder must use a private root
shell with history, tracing and terminal recording disabled. For Bash:

```bash
set +x
set +o history
unset HISTFILE
IFS= read -r -s -p 'Key to expire (private): ' retire_key; printf '\n'
headscale preauthkeys expire "$retire_key" --user '<USER>'
unset retire_key
# Expected: Node-independent "Key expired" confirmation.
headscale preauthkeys list --user '<USER>' -o json
# Expected: that key's expiration is now in the past. Inspect locally only.
```

Hidden input avoids literal shell history, but **does not remove argv exposure**
to process inspection, privileged audit/exec logging or other root observers.
If this host records argv, do not use this procedure: wait for natural expiration
or request a separately authorized protected admin transport. Never weaken host
logging to hide key material. Never paste the key into tickets/threads. This is
an honest remaining manual-CLI custody gap, not a claimed secret-safe API.

Key expiration **does not revoke an enrolled device**. For device revocation:

```sh
headscale nodes list
headscale nodes delete --identifier '<ID>'
# Confirm the interactive deletion of the identified obsolete device.
headscale nodes list
# Expected: the exact ID is absent. Then perform the removed-device test below.
```

Retire its unused/reusable keys too, so it cannot enroll again. Do not replace
node deletion with key expiry or `nodes expire`.

## 3. Approved-device register

Install the placeholder example from `deploy/remote-access/test-server/` on the
host, then replace **every** placeholder and synthetic ID/date in a private
editor. Register only independently approved devices, never auto-approve the
current server inventory:

```sh
install -d -o root -g root -m 0700 /etc/happyranch-test-server
install -o root -g root -m 0600 approved-devices.example.json /etc/happyranch-test-server/approved-devices.json
# Edit /etc/happyranch-test-server/approved-devices.json locally before enabling.
stat -c '%U %G %a' /etc/happyranch-test-server /etc/happyranch-test-server/approved-devices.json
# Expected: root root 700; root root 600.
```

The JSON array requires `node_id` (server's numeric ID), `node_name` (the proto
`name`, not `given_name`), `user` (user name), `owner`, `purpose`,
`invited_home_machine`, `added_date` and `review_by` (`YYYY-MM-DD`, UTC dates).
Identity is ID + node name + user: renames/moves/re-enrollment need explicit
register review. Added date must not follow review-by. Assumption: a handful of
devices with a weekly owner review; set review-by within seven days. Review is
overdue starting the following UTC day. The register records intended invitation
scope; ACL enforcement is deferred.

For obsolete devices: identify the ID, delete it, verify absence and lost peer
access, retire relevant keys, then remove that approved entry locally. For an
unknown device, investigate and delete unless independently authorized; never
silence a finding by copying it into the register. Review approved-but-missing
entries with their owners and remove stale approvals.

## 4. Install and read the daily check

Copy reviewed files from this PR to a private staging directory on the host.
From that directory, run:

```sh
install -d -o root -g root -m 0755 /usr/local/libexec/happyranch-test-server
install -o root -g root -m 0755 headscale_device_check.py check-headscale-devices.sh /usr/local/libexec/happyranch-test-server/
install -o root -g root -m 0644 headscale-device-check.service headscale-device-check.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/headscale-device-check.service /etc/systemd/system/headscale-device-check.timer
# Expected: no unit errors.
systemctl daemon-reload
systemctl start headscale-device-check.service
# Expected: success only for a clean inventory; see exit codes below.
systemctl show headscale-device-check.service -p ExecMainStatus -p Result
journalctl -u headscale-device-check.service --since today --no-pager
# Expected: ExecMainStatus=0 and "0 findings", or an explicit finding/error.
systemctl enable --now headscale-device-check.timer
systemctl list-timers headscale-device-check.timer --all
# Expected: next daily run at 06:00 UTC plus up to ten minutes jitter.
```

The root wrapper collects **all users**, all nodes and one complete key list per
user. It writes raw JSON only to a new mode-0700 `/run` directory (files 0600),
removes it on exit, suppresses raw CLI diagnostics, and writes the redacted
result to the journal only. Run during a quiet admin window: separate CLI calls
are not an atomic server snapshot. If memberships change during collection,
repeat after activity settles. Enumeration failure is an error, never clean.
No automatic remediation or notification delivery is supplied; the operator
must read the journal and failed service state daily.

Standalone usage (sensitive snapshots must already be root-only):

```sh
python3 headscale_device_check.py --approved /etc/happyranch-test-server/approved-devices.json --nodes-json '<NODES_JSON>' --keys-json '<USER_ONE_KEYS_JSON>' '<USER_TWO_KEYS_JSON>' --max-key-lifetime-seconds 3600
# Expected: exit 0 clean, 1 findings, 2 input/parse error (no partial clean result).
```

Findings cover unknown/missing devices, overdue reviews, reusable keys,
unused-and-unexpired keys and original lifetime (`expiration - created_at`)
longer than the configurable maximum (default 1h). Output shows only numeric
node/key IDs or approved-entry indexes, never names, key prefixes or raw keys.
Expired historical reusable/long-lifetime rows remain findings if retained by
Headscale; expiration prevents new use but does not rewrite their history.
Record their retirement locally rather than expecting expiration to erase them.

### Tagged input contract

Field names come from v0.25.1
[`node.proto`](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/node.proto),
[`preauthkey.proto`](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/preauthkey.proto)
and [`user.proto`](https://github.com/juanfont/headscale/blob/v0.25.1/proto/headscale/v1/user.proto).
The CLI passes the node/key **slices** directly to Go `encoding/json`
([nodes](https://github.com/juanfont/headscale/blob/v0.25.1/cmd/headscale/cli/nodes.go#L168-L179),
[keys](https://github.com/juanfont/headscale/blob/v0.25.1/cmd/headscale/cli/preauthkeys.go#L64-L80),
[output](https://github.com/juanfont/headscale/blob/v0.25.1/cmd/headscale/cli/utils.go#L124-L153)).
Thus inputs are arrays (or `null` for a nil/empty CLI slice), not RPC envelopes.

Required node fields: integer `id`, string `name`, object `user` with string
`name`. Required key fields: decimal-string `id`, string `user`, string `key`,
`created_at`, `expiration`. Generated Go `omitempty` tags omit false
`reusable`, `ephemeral`, `used`; absent bools mean proto3 false, while explicit
non-booleans fail. Timestamps are `{"seconds": <integer>, "nanos": <integer>}`
objects, with omitted nanos equal to zero, **not** protojson RFC3339 strings.
See tagged generated
[key fields](https://github.com/juanfont/headscale/blob/v0.25.1/gen/go/headscale/v1/preauthkey.pb.go)
and [node fields](https://github.com/juanfont/headscale/blob/v0.25.1/gen/go/headscale/v1/node.pb.go).
Missing identity/key/time fields, wrong types, duplicate JSON fields or IDs,
invalid dates and unreadable/oversized input produce exit 2 with fixed redacted
text. No aliases or guessed schema are accepted. Limits: 16 MiB per file,
10,000 rows per inventory; snapshots must be complete, never filtered/truncated.
Revalidate this source contract after any future version change before enabling
this checker; this PR chooses no future version.

## 5. Connection rate limits, isolated from existing firewall rules

Assumptions: fewer than ten dev devices, possibly sharing one roaming/NAT source;
normal HTTPS/DERP reconnects stay far below 30 new packets/connections per second
with burst 120, and STUN stays below 100 packets/second with burst 400. TCP
`ct state new` includes retransmissions. Established HTTPS/DERP traffic is not
limited. IPv4 and IPv6 each use per-source dynamic sets (one-minute idle expiry,
65,535 entries per set). These generous limits control simple source floods,
not volumetric/distributed attacks; spoofed UDP and set exhaustion remain limits.

The template contains only `table inet happyranch_headscale`, an input hook at
priority -10, and four drop-over-limit rules with counters. It matches **only**
8443/tcp new connections and 3478/udp packets. Its accept policy continues into
other base chains; it does not override their drops or open a port. It has no
website-port rule. Compare the saved baseline before and after applying it.

```sh
nft list table inet happyranch_headscale
# Expected before first install: table does not exist. If it exists, STOP and
# inspect ownership; do not append duplicate rules or flush it automatically.
install -d -o root -g root -m 0755 /etc/nftables.d
install -o root -g root -m 0600 headscale-ratelimit.nft /etc/nftables.d/happyranch-headscale.nft
nft -c -f /etc/nftables.d/happyranch-headscale.nft
# Expected: exit 0, no syntax/kernel validation errors. Do not apply on failure.
```

For persistence use a **separate unit**, leaving `/etc/nftables.conf` untouched:

```sh
cat > /etc/systemd/system/happyranch-headscale-ratelimit.service <<'UNIT'
[Unit]
Description=Headscale ports only rate limits
After=nftables.service ufw.service firewalld.service
Before=headscale.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.d/happyranch-headscale.nft
ExecStop=/usr/sbin/nft delete table inet happyranch_headscale

[Install]
WantedBy=multi-user.target
UNIT
systemd-analyze verify /etc/systemd/system/happyranch-headscale-ratelimit.service
systemctl daemon-reload
systemctl enable --now happyranch-headscale-ratelimit.service
nft -a list table inet happyranch_headscale
nft list ruleset
# Expected: four limited-port drop rules with counters; all other tables intact.
```

Verify the actual nft binary is `/usr/sbin/nft` first (`command -v nft`). If the
existing firewall manager flushes foreign tables on reload, STOP and arrange
operator integration/reapplication; this unit cannot prevent another manager
from flushing it. After every firewall reload/reboot verify the dedicated table
and unchanged website rules. Verify `<HEADSCALE_SG>` in the console still allows
exactly 8443/tcp and 3478/udp, and the website group is unchanged. Do not change
security groups as part of host rate limiting.

Rollback only this control:

```sh
systemctl disable --now happyranch-headscale-ratelimit.service
# ExecStop deletes only this table. If the unit was never active, run:
# nft delete table inet happyranch_headscale
nft list table inet happyranch_headscale
# Expected: table absent. Verify all baseline tables and website still work.
```

## 6. Patching procedure (HOLD until founder selects a version)

Weekly, check [upstream releases](https://github.com/juanfont/headscale/releases)
and [security advisories](https://github.com/juanfont/headscale/security/advisories).
Record current version and the proposed release's migration/config requirements
privately. Ask for the THR-327 seq563 version decision; **do not execute this
upgrade template until that decision and PR-B are ready**. Never treat “latest”
as approval, and do not add unsupported v0.25.1 config keys.

For the explicitly approved release/architecture, download its official `.deb`
and SHA-256 manifest from that release's assets in a private root staging
directory. Verify against the upstream manifest entry for that exact filename:

```sh
sha256sum '<APPROVED_DEB_FILENAME>'
printf '%s  %s\n' '<UPSTREAM_SHA256>' '<APPROVED_DEB_FILENAME>' | sha256sum --check -
# Expected: exact approved filename: OK. STOP on missing manifest or mismatch.
umask 077
backup_dir="/root/headscale-backup-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup_dir"
systemctl stop headscale
systemctl is-active headscale
# Expected: inactive (nonzero is expected); stop before copying SQLite state.
cp -a /etc/headscale "$backup_dir/etc-headscale"
cp -a /var/lib/headscale "$backup_dir/var-lib-headscale"
diff -qr /etc/headscale "$backup_dir/etc-headscale"
diff -qr /var/lib/headscale "$backup_dir/var-lib-headscale"
# Expected: both diffs empty, exit 0; keep backup and current .deb root-only.
dpkg -i './<APPROVED_DEB_FILENAME>'
systemctl restart headscale
headscale version
systemctl is-active headscale
journalctl -u headscale --since '10 minutes ago' --no-pager
# Expected: approved version, active, no startup/migration errors.
```

Apply only the separately reviewed release-specific migration steps. Verify
local node/key inventory, HTTPS at `https://<TEST_CONTROL_HOST>:8443/health`
(expected healthy response), a previously approved peer connection, loopback
metrics/gRPC binding (`ss -lntup`) and the dedicated nft table. Review checker
schema compatibility before restarting its timer. Stop on failure; restoring an
old binary over a migrated database is unsafe. A separately reviewed rollback
must restore matching binary, config **and** stopped database backup together.

## 7. Daily/weekly log review

Read daily audit results as in step 4. Weekly, and after every enrollment,
removal, firewall reload or patch, review locally:

```sh
journalctl -u headscale --since '7 days ago' --no-pager --grep='(?i)register|registration|auth|unauthori|fail|error'
# Expected: only understood operator activity, no unexplained approvals/failures.
journalctl -u headscale-device-check.service --since '7 days ago' --no-pager
# Expected: daily execution; every finding/error has an owner and disposition.
nft -a list table inet happyranch_headscale
# Expected: four counters; compare their increase with the prior weekly reading.
```

Raw Headscale logs may contain sensitive registration details: never export them
unredacted. Counter increases signal rate-limit drops, not identity or membership
proof. Missing daily execution is an operational failure, never a clean check.

## 8. Founder-run outsider acceptance

Use a fresh disposable client **not enrolled in this tailnet**, knowing only
`https://<TEST_CONTROL_HOST>:8443`. No approved key, saved Tailscale state or
prior session may be present. On that client:

```sh
tailscale up --login-server='https://<TEST_CONTROL_HOST>:8443'
# Expected: remains unauthorized/waits for admin registration; do not approve it.
tailscale status
# Expected: not enrolled in the test tailnet.
tailscale ping '<TEST_PEER_TAILNET_IP>'
curl --connect-timeout 5 --max-time 10 'http://<TEST_PEER_TAILNET_IP>:<TEST_PORT>/health'
# Expected: ping and HTTP fail. Use a private known test peer; no public fallback.
```

On the server, `headscale nodes list` must show no newly approved outsider.
For removal acceptance first verify a disposable approved device successfully
pings/requests its intended test peer; then delete that exact ID using step 2.
On the removed client repeat the same ping and HTTP command: **both must fail**.
On the server re-run `headscale nodes list`: the exact ID must remain absent.
If old peer access persists while updates propagate, record the observed delay
and keep acceptance failed until it is denied; do not claim deletion alone
proves immediate revocation. Do not re-enroll it with a remaining key.

Relay-abuse acceptance is **BLOCKED pending PR-B / THR-327 seq563**. Do not call
this server dev-only until members-only relay, scoped ACLs, patching and actual
outsider/removed-device acceptance are all independently demonstrated.

## Repository verification

The checker uses Python stdlib only. Run synthetic CLI fixtures with
`uv run --frozen pytest scripts/test_headscale_device_check.py -v` and repository
boundary/private-endpoint lint with `scripts/local_ci.sh boundary-lint`.
If nft is installed, run `nft -c -f deploy/remote-access/test-server/headscale-ratelimit.nft`;
this needs kernel netfilter access even in check mode. Record permission errors
as unavailable kernel validation, never PASS; do not use sudo or install tools.
These checks prove no live server behavior. Founder installation, firewall
application, journal/timer operation and outsider acceptance remain NOT RUN by
agents. No CI trigger, retired suite or Product E2E run is added.
