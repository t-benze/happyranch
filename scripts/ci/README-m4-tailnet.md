# M4 public cell and outside embedded client

This is a **source-only, unexecuted** procedure until an exact-head full-pass
`summary.json` says PASS and the separate shipped clean-state exits 0. A successful
Headscale health request or native archive build proves neither daemon access nor
the refusal cases. TASK-15661's initial feasibility acquired a publicly trusted
TLS-ALPN-01 certificate on443 and built the unchanged app archive on Linux; that
is the current executed boundary.

Use only the founder-authorized disposable Ubuntu24.04 VM
`i-j6c1pwjqygzrccal4mzh` in `cn-hongkong`, public `47.238.219.200`. The driver pins
that identity and checks the existing security group's ingress:443/tcp and
3478/udp only. It never opens80/22, changes a cloud setting, creates a resource,
stops/reboots/resizes the VM, transfers the host Aliyun profile, or uses real org
data. Cloud Assistant uses the same single-issue mutations and bounded read-only
poll retries as [M3](README-m3-reboot.md). A lost RunCommand response is not
permission to replay it. Inspect effects through Cloud Assistant before another
authorized run.

The VM's source/build/download caches from M3 are prerequisites: the existing
`/var/lib/happyranch-m3/tools/bin/uv`, Go, system Python/venv/pip, and checksum-pinned
Headscale0.25.1 download. New public source is checked out at the exact published
SHA in `/var/lib/happyranch-m4/repo`. Caches/build products belong outside task
output; the disposable cell lives in `/var/lib/happyranch-m4/cell`.

## Run and evidence

Run from a clean published task worktree on this **outside Linux host**, through
the existing persistent jobs path (`review_required:false`, `persistent:true`):

```bash
python3 scripts/ci/m4_tailnet_aliyun.py --sha FULL_PUSHED_SHA --output /absolute/task-output/m4 --scratch /absolute/task-tmp/fresh-m4
python3 scripts/ci/m4_tailnet_aliyun.py --sha FULL_PUSHED_SHA --output /absolute/task-output/final-clean --clean-state-only
```

The first command is one complete maker pass; the second is a separate read-only
clean-state observation. Submit, block on the actual job ID, then inspect its
terminal receipt and command/runtime/exit evidence on resume. Do not wait in a
session or use shell backgrounding. Required local verification is
`bash scripts/local_ci.sh all` in a persistent job, shellcheck via
`uvx --from shellcheck-py shellcheck -x` on each affected shell script, a **separate**
`bash -n SCRIPT` per script, Python compile and runner `--help`. Normal hooks and
exact-head Source Boundary Lint / Web Lint (Node24) still apply; E2E is unchanged.

The driver builds `app/mac/tsnet-bridge/bridge.go` unchanged with Go1.23.12,
`-mod=readonly -buildmode=c-archive`, then links the archive with the host C
compiler into a shared object. Its private worker calls the actual eleven-export
C ABI through ctypes. No stub, copied transport, system Tailscale, Swift/GUI, or
production test seam is involved. The archive's readonly module graph is
unchanged. Build logs and private native state stay in task scratch and are
removed at the end; the archive digest and effective Go version are recorded.

| Check/phase | Observable result |
| --- | --- |
| prepare/install | Real Headscale HTTPS443 with trusted TLS-ALPN-01 certificate; STUN3478; real shipping installer, notify-READY connector/sidecar; real loopback daemon and synthetic org. Outside embedded node joins before installer expected-peer readiness. |
| allowed-200 | Paired GET teams ->200 and the exact M2 real-daemon synthetic teams body. |
| unpaired-refused | Same GET/transport, missing credential and never-paired credential ->403 `identity_denied`. Paired controls succeed before/after. |
| revoked-refused | Second device GET200; shipping CLI revokes it; same GET/credential ->403 `identity_denied` within30s after reconciliation. The unaffected device stays200. Persistent success is a product defect, never a harness workaround. |
| forbidden-route-refused | Six M2 forbidden method/path cases ->403 with exact `method_denied`/`route_denied`. Paired allowed GETs bracket each case and the synthetic org remains present. A forbidden route is never made allowed as a positive control. |
| DERP-path | `TS_DEBUG_ALWAYS_USE_DERP=true` disables client UDP. Private region999 is the **only** published region (`urls/paths=[]`, auto-update false). The unchanged client's backend logs must actually show UDP disabled and `derp-999 connected`; another/missing region fails. |
| exposure | Trusted direct TLS to the literal public IP with hostname SNI returns health200 and daemon route404. Outside sockets to public daemon/connector ports18765/18443/8765 must refuse/time out while443 is healthy. A missing positive control, network-unreachable error or proxy policy denial is not exposure proof. |
| logs | Per-run directory reset before prepare; guest microsecond boundary before provisioning; every journal has `--since` and short-unix timestamps; shipped attribution rejects old/unrecognized entries and requires actual product/control journals. Status snapshots suppress their second journal tail. |
| purge/teardown/clean-state | Product uninstall--purge residue assertions; remove synthetic cell including ACME account/cert/key, Headscale DB/noise/DERP state and recipient public key; remove payload/config/logs/account and synthetic CA source; `update-ca-certificates --fresh`; reject dangling synthetic CA chains and all test sockets443/3478/18765/18443/8765. No DNS/hosts edits are made; hashes must remain unchanged. PASS is gated on clean-state and outside final TCP probes. |

The cell ACL is explicit: `m4client` may reach `m4home:443/tcp` only, rather than
Headscale's allow-all default. No routes/exit nodes/tags are advertised. This
single-customer synthetic cell is destroyed at teardown; cross-tenant hostile
acceptance and fleet admission/economics remain outside this slice.

The VM already has sshd22 and DHCP68 sockets; SSH remains unreachable through
its unchanged security group. These pre-existing host services are not test
listeners and are not stopped. The two publicly admitted ports443/3478 must have
no listener after teardown. Reusable public source/build caches and bounded
plain logs may remain. The owned-sha marker is removed only after teardown; an
unknown/partial owner or failed purge must be reconciled rather than bypassed.

Memory evidence includes1s guest `/proc/meminfo` sampling starting before the
payload build, plus per-service `MemoryPeak`/`MemorySwapPeak` snapshots. Each
fixture has `MemoryMax=600M`, builds use `GOMAXPROCS=2 GOFLAGS=-p=2`; the existing
2GiB VM and2GiB swap are unchanged. Report measured sampled peak memory/swap and
sample count. Sampling is not a hard instantaneous whole-host RSS bound.

## Credential custody

The VM creates a non-reusable <=10minute enrollment key and normal one-use
<=5minute pairing codes. A fresh host RSA3072 public key goes to the VM over
Cloud Assistant. Each secret is encrypted with RSA-OAEP/SHA256 **before** command
output; only ciphertext crosses the Cloud Assistant result. The host decrypts
into process memory and sends private pipe messages to the actual C ABI worker.
No plaintext secret enters argv, environment, workflow inputs, logs, PR text or
artifacts. Pairing credentials retain shipping product lifetime semantics and
are destroyed with the cell/process; they are never redefined by this harness.

The client's existing upstream `TS_DEBUG_LOGTAIL_FLUSHDELAY=24h` knob keeps
backend diagnostics local during the bounded pass. Only a strict projection
(`udp_disabled`, selected region IDs) is exported from native filch logs.
The worker is killed/reaped before graceful log-upload shutdown, and its entire
private state/log directory is removed. Raw native logs, node state, private RSA
key and decrypted credentials never enter output. VM collection archives only
the designated plain-log directory, never cell/DB/state/key files.

Every collected stream records full byte count and SHA256, a <=256KiB head/tail
excerpt, gzip for streams over1MB, and a45MB task-output gate. Failures retain
their phase/exit/check evidence and cleanup errors; they never become PASS from
successful cleanup alone. Preserve actual product failures and return them to
the manager without changing product code, units or security checks.

## Outside-client alternatives and remaining acceptance

The brief's candidate order was evaluated before building:

1. **macOS GitHub runner:** headless use of the real archive is source-feasible.
   Existing M1–M3 workflows dispatch on main only. A new M4 workflow cannot
   supply this task's pre-merge exact-head maker pass under that precedent;
   secure automatic runner handoff is also unimplemented. NOT RUN. No workflow,
   secret, variable, trigger or setting is added.
2. **This outside Linux host:** selected C-ABI procedure above; it egresses via
   a proxy. This is actual packaged Go transport coverage, not native Swift
   ClientBridge or signed Mac.app acceptance. Until an actual job receipt exists,
   join, proxy compatibility,200/refusals/DERP/exposure remain NOT RUN.
3. **Founder Mac.app:** NOT RUN. The current UI is BYO-Tailscale; managed key
   input in Swift is a development environment variable, conflicting with the
   normative ban on enrollment-in-env. There is no current secure managed GUI
   enrollment procedure to prescribe. A separately authorized product change is
   needed before founder GUI acceptance; do not simulate it or use a key in env.

After that separately approved secure enrollment path exists, founder steps are:
use the signed app on an outside network without system Tailscale; securely
enroll at the temporary control URL; enter the pass's home tailnet address,
connector port443 and fresh one-use pairing code in Connect to Remote Runtime;
connect and report the synthetic teams result and redacted region999 diagnostic;
repeat operator-controlled unpaired/revoked/forbidden observations; disconnect
and remove disposable enrollment. These are conditional steps, not a claim that
the present GUI supports managed enrollment.

Neither maintained document previously named M4. The normative contract is
stricter than goal C: five rows covering same-Wi-Fi direct/private relay,
separate-network direct/private relay, and failure/recovery. This procedure is
only the **separate-network forced-private-DERP** slice. Same-Wi-Fi, direct path,
failure/recovery across both topologies and native Mac acceptance remain open.
Source delivery, lint and an eventual Linux pass do not close those obligations.
