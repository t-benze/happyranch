# M3 VM reboot acceptance

This is a manual, authorized **disposable Ubuntu 24.04 amd64 VM** test, separate
from GitHub M3 same-boot recovery. Never run on a product host. The VM must have
no existing HappyRanch installation, account, or M3 fixture units. Use only
synthetic data. No inbound control port is required: the host uses the existing
Aliyun CLI default profile and Cloud Assistant in `cn-hongkong`. Credentials
stay on the host. Do not open SSH or alter the security group.

From a clean, published checkout, submit this command as a persistent HappyRanch
job with the exact full commit SHA and an absolute task output directory:

```bash
python3 scripts/ci/m3_reboot_aliyun.py \
  --instance i-j6c1pwjqygzrccal4mzh \
  --sha FULL_PUBLISHED_SHA \
  --output /absolute/workspace/output/TASK-ID/vm
```

The runner installs build prerequisites (apt Go with automatic module toolchain
selection, system Python venv support, uv in a test venv), clones public source,
and checks out the exact SHA without credentials. It runs **two complete clean
passes**, rebooting twice per pass through `ecs RebootInstance`. A changed
`/proc/sys/kernel/random/boot_id` and a successful Cloud Assistant observation
prove return from each reboot; reachability polling never reissues the reboot.
The VM driver can also be invoked phase by phase from the checked-out repo:

```bash
M3_AUTHORIZED_VM=yes M3_PASS=1 bash scripts/ci/m3_reboot.sh prepare
```

Phases are `prepare`, `pre-reboot`, `post-reboot`, `stop-pre-reboot`,
`stop-post-reboot`, `negative-stop`, `purge`, `logs`, and `teardown`. Their order
is owned by the host runner. `negative-stop` is expected to exit 1 on the enabled
installed target; it performs no mutation. The restored stopped-state check
must pass after the second reboot. A phase failure fails the pass, collects
logs and attempts purge/teardown; the runner does not retry acceptance, alter
product units, tune recovery limits, or implement a product workaround. Report
any required product change as a finding. Bootstrap failures leave their log
at `/var/lib/happyranch-m3/bootstrap.log` for inspection.

The VM driver sources M2 for provisioning, installer, notify readiness, network
reachability, plain diagnostics and residue. M2 retains its GitHub-hosted guard;
its shared `provision_cell` and `start_fixture` allow this separately guarded VM
caller to create persistent **test-only** units. The units run local Headscale,
the real installed daemon bound to loopback, and a userspace peer tailscaled
with a loopback SOCKS listener. No dependency/order override is added to product
units. The saved credential is used for an actual paired HTTP 200/body check
before/after boot and after explicit re-enable. Credentials and enrollment keys
are never printed or collected. Before the first reboot the installer source and
credstore link must be absent; systemd may retain the initial runtime credential
copy until service stop. After reboot the runtime copy must also be absent.
Headscale projections contain only public node
key hashes, IDs/IPs/names and consumed preauthkey IDs/usage, never key values.

All state/build/cache/source/logs live under `/var/lib/happyranch-m3`; only logs
are copied to task output. Journald persistent auto-storage is enabled by its
standard `/var/log/journal` directory and flushed before reboot. Both product
unit journals from `-b -1` and `-b 0`, unit properties and per-phase logs are
retained. The runner transfers logs in bounded base64 chunks, checks complete
lengths, compresses plain files over 1 MB and retains head/tail excerpts, and
refuses output beyond 45 MB. `summary.json` records head SHA, verdicts and boot
IDs; `nodes-before.json` and `nodes-*.json` record both node identities.

`uninstall --purge` is verified with M2's residue assertions **before** fixture
cleanup. Documented product residue is payload/config/logs/account. Teardown
then removes that test-owned residue and disables/removes only the three M3
fixture units, deletes the private cell and its test CA, and retains reusable
build/download/source caches and plain evidence. The VM stays running. A failed
cleanup is reported, not treated as a clean VM. No workflow or required-check
change accompanies this test.
