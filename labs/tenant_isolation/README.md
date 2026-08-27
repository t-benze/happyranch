# Hostile Tenant-Isolation Lab Harness (THR-097, merge unit B)

**Status:** non-production lab/harness — strictly additive. No production code,
deployment, auth/schema, dependency, permission, or default is touched.
**Merge unit A (normative contract):** `docs/superpowers/specs/2026-08-26-managed-remote-access-contract.md`
+ `tests/contract/managed_remote_access/*.json` (read-only inputs).
**Sibling unit:** `labs/managed-remote-access/capacity/` (merge unit D) is a
separate, unrelated lab; this harness does not reuse or depend on it.

This harness builds **two independent Headscale cells (A and B)** — distinct
state/key/config/network identity, deny-by-default policies, synthetic nodes —
and proves, against the merged normative threat matrix, that tenant A cannot
enroll into, learn about, or reach tenant B on any path (genuine
source-node-to-destination-node probes), that forged tags/routes/subnet/
exit-node/SSH advertisements fail, that wrong cell/home/device/account/node/
key reuse fails, that missing/empty/malformed policy fails closed, and that
the two-cell topology runs deterministically with outcome-bearing cleanup —
all with **tenant-neutral category-level results and zero secret/raw-exception
leakage**.

**DERP relay isolation is genuinely PROVEN via a real relay path.** Each cell
runs the headscale v0.25.1 **embedded DERP server** (plain http, served on the
headscale control listener; host/port advertised from `server_url`), and the
pinned tailscale client (1.102.3) dials it over plain HTTP using its built-in
`TS_DEBUG_USE_DERP_HTTP=true` knob — no new dependency, no TLS
infrastructure, no secrets. The forced-relay probes suppress direct
WireGuard/disco UDP paths (passwordless `sudo iptables` on the isolated
runner) so the client **genuinely relays**; the node's actual DERP region is
read from `tailscale status` (`Self.Relay`) and recorded as
`route_evidence=relay` in every result. DERP isolation is never inferred from
disabled DERP or control-plane TCP.

## Honest runtime status (read first)

- **Real hostile proof requires the isolated CI/lab runtime** — GitHub Actions
  `ubuntu-latest` (the repo's existing authorized CI runtime) via the
  path-scoped workflow `.github/workflows/lab-tenant-isolation.yml`. It has
  Docker preinstalled **and passwordless sudo + iptables** (a standard
  GitHub-hosted runner capability) used solely to suppress direct
  WireGuard/disco UDP inside the disposable lab runner; the harness pins its
  artifacts by digest/sha256. If the relay tooling is unavailable, the REAL
  preflight declines with the exact prerequisite — proof is never weakened.
- **The manager/authoring host has no Docker/Podman/Go/Headscale/Tailscale.**
  `--runtime mock` and the unit tests exercise orchestration/parsing/assertion
  logic only. **A mocked/unit-only pass is NOT proof of tenant isolation** and
  every summary labels its `runtime_kind` honestly (`real` / `mock` / `none`);
  `hostile_proof` is `true` only for a real run whose genuinely executed
  probes all passed with no residue and no cleanup failure.
- `--check-runtime` and `--runtime none` emit `no-run-evidence.json` with exact
  prerequisite evidence — never fabricated runtime proof.

## What the harness proves (semantically consumed from the contract)

The harness reads `tests/contract/managed_remote_access/*.json` at runtime
(read-only; pinned digests in `manifest.json`, drift fails closed):

- expected deny/audit categories for every threat case come **from the
  fixtures**, never duplicated in harness code;
- every one of the 56 threat cases is accounted for in `coverage.json`:
  `probe` (genuinely executed on the lab runner), or `deferred-unit-c`
  (connector-level request-decision / policy-epoch logic owned by merge unit
  C) — never silently dropped; the forced-relay (DERP) hostile cases are
  EXECUTED probes with a real relay path (see DERP section above);
- hostile ⇒ denied with a `deny_category`; positive controls ⇒ allowed;
  existence-guard pairs (absent vs consumed/replayed) must produce **byte
  identical** visible detail (no cross-tenant existence oracle);
- transport probes are GENUINE source-node-to-destination-node data-plane
  probes: they originate in a source node's own tailscaled context (SOCKS5
  proxy on the runner host) and target a destination node's tailnet
  IP:connector-port synthetic connector listener — never a runner-host TCP
  connection to a Headscale control-plane port. `route_evidence`
  (`direct`/`relay`/`none`) is recorded per result so a relay claim can never
  be confused with a direct path;
- one **single-use pre-auth key per node** is minted and consumed once; every
  expected node is proven online/readiness-checked (cell record + own tailnet
  identity) BEFORE any probe; every container/process launch result is checked
  immediately (bounded/redacted stderr, no downstream work after failure);
  cleanup is outcome-bearing (removal/termination awaited and escalated,
  residue-checked on every terminal path) and cleanup/residue failure fails
  the evidence closed;
- results are category-level prose only: no sentinel credential shapes, no raw
  exception text, no synthetic hostnames/IPs/keys, no concrete tenant ids.

## Required mutation probes (checked-in red/green TDD evidence)

`tests/tenant_isolation/test_orchestrator.py`, `test_policy.py`,
`test_probes.py`, and `test_redact.py` prove each of the brief's mandated
mutations **fails for its intended reason**:

| Mutation | Guard | Test |
|---|---|---|
| collapse A/B onto one cell/state/key/network | preflight disjoint cell identity | `test_preflight_rejects_collapsed_cells` |
| make policy allow-all | policy-state validation (cell-scoped, deny-by-default) | `test_run_rejects_allow_all_policy` |
| accept wrong-cell enrollment (hostile allowed) | post-run hostile⇒denied guard | `test_run_fails_when_hostile_case_is_allowed` |
| leak B peer metadata (hostname/IP/key) | post-run leak guard (identity patterns) | `test_run_rejects_leaked_b_peer_metadata` |
| permit direct / forced-DERP reachability | evaluate layer + fixture outcome | `test_run_rejects_direct_reachability_permitted` |
| accept forged routes/tags | evaluate layer + fixture outcome | `test_run_rejects_forged_route_applied` / `test_policy` cross-cell |
| leak a credential (sentinel) | post-run leak guard (sentinel scan) | `test_run_rejects_credential_leak_in_results` |
| skip cleanup | residue check (containers/state) | `test_residue_check_detects_leftover_containers` |
| target a non-lab endpoint | preflight endpoint allow-range | `test_preflight_rejects_non_lab_port` / `_public_hostname` |
| runner-host control-plane TCP probe in transport recipes | node-context SOCKS5 probe (never 127.0.0.1:control-port) | `test_transport_probes_use_node_context_socks5_not_control_port` |
| DERP isolation claimed while DERP disabled | relay cases are EXECUTED probes that fail closed unless the node's own status shows a real DERP region (`Self.Relay`) + route_evidence=relay | `test_relay_categories_are_executed_probes`, `test_relay_probe_fails_closed_without_real_relay_session`, `test_relay_probe_records_real_relay_evidence_and_denies_cross_cell` |
| relay block / iptables rule left behind | cleanup removes rules; residue detects leftover | `test_cleanup_removes_relay_block_rules`, `test_residue_detects_leftover_relay_block` |
| policy-variant restart hangs on full health wait / aborts run | variant restarts skip the launch health wait; bounded `cell_healthy` classifies; restore always runs | `test_policy_variant_restart_does_not_wait_full_health_timeout`, `test_policy_variant_probe_restores_when_apply_fails` |
| empty/zero RAW policy at initial launch | read-back + pre-launch guard fails closed BEFORE docker run (headscale v0.25.1 ErrEmptyPolicy) | `test_initial_launch_rejects_empty_policy_before_docker_run`, `test_materialize_reads_back_and_rejects_zero_policy` |
| empty DERPMap config (embedded DERP disabled) | config schema validation fails closed before launch (headscale v0.25.1 refuses to boot with an empty DERPMap) | `test_config_without_derp_map_is_invalid` / `test_cell_config_without_derp_map_fails_validation` |
| one pre-auth key per cell reused for two nodes | one single-use key minted per node; reuse rejected | `test_mint_preauth_keys_issues_one_key_per_node`, `test_run_aborts_before_probes_when_key_reuse_rejected` |
| missing/offline node | pre-probe readiness gate (every node online) | `test_node_ready_false_when_record_missing_from_cell`, `test_run_aborts_before_probes_when_node_offline` |
| docker run result ignored | immediate launch check + bounded/redacted stderr | `test_launch_failure_aborts_before_any_enrollment`, `test_missing_container_immediately_after_launch_aborts` |
| cleanup removal/termination failure ignored | outcome-bearing cleanup, residue fails closed | `test_cleanup_reports_failed_docker_rm_as_cleanup_failure`, `test_cleanup_records_failed_daemon_termination`, `test_cleanup_and_residue_used_on_signal_path` |

## Layout

```
labs/tenant_isolation/
  README.md            this runbook
  manifest.json        pinned artifacts + normative fixture digests (read-only inputs)
  harness/
    models.py          pure data model (cells, nodes, probes, summaries, status parsing)
    contract.py        semantic reader of the merged unit-A fixtures (read-only)
    redact.py          category-level emission + sentinel/raw-exception/identity leak guards
    policy.py          deny-by-default Grants policy, policy-state variants, fail-closed validation
    cellspec.py        headscale v0.25 cell config generator (map-form prefixes, MagicDNS off, …)
    backend.py         DockerBackend (real lab) / FakeBackend (deterministic tests + mock)
    probes.py          threat-category → recipe mapping, outcome classifier, evaluate (assertion layer)
    orchestrator.py    preflight, lifecycle, cleanup, residue check, post-run guards, evidence
    main.py            CLI: --check-runtime / --runtime {auto,real,mock,none} / bounds
tests/tenant_isolation/   focused unit tests (122) incl. the mandated mutation probes
.github/workflows/lab-tenant-isolation.yml   the one path-scoped lab workflow
```

## Run locally (no runtime proof)

```bash
# 1. unit + contract tests (TDD surface; fast, hermetic)
uv run pytest tests/tenant_isolation/ -q
uv run pytest tests/contract/ -q

# 2. labeled dry-run of the orchestrator (fake backend; hostile_proof=false)
uv run python -m labs.tenant_isolation.harness.main \
    --runtime mock --results-dir /tmp/hs-mock

# 3. honest runtime check (no docker here → exit 5 + no-run evidence)
uv run python -m labs.tenant_isolation.harness.main --check-runtime \
    --results-dir /tmp/hs-check
```

Exit codes: `0` all executed probes passed, no residue, cleanup ok · `1` an
executed probe failed / run aborted · `2` preflight declined · `3` residue or
cleanup failure · `5` runtime unavailable (no-run evidence).

## Run for real (isolated CI/lab runtime)

The path-scoped workflow runs on every PR/push touching
`labs/tenant_isolation/**` and uploads the machine-readable evidence as an
artifact. It performs the full lifecycle on `ubuntu-latest` (Docker
preinstalled):

```
uv run python -m labs.tenant_isolation.harness.main --runtime real \
    --results-dir labs/tenant_isolation/results/<run_id> \
    --per-probe-timeout 60 --total-timeout 1500
```

The harness itself: preflights (fixture digests, endpoints, cell identity,
policy states, placeholder hygiene, runtime), pulls the pinned headscale image
by digest, verifies the pinned tailscale tarball sha256, brings up cells A/B,
enrolls synthetic nodes, runs the probe matrix, cleans up (success/failure/
signal), residue-checks (processes/containers/networks/volumes/state), and
writes `summary.json` + `results.jsonl` + `coverage.json`.

## Evidence format

- `summary.json` — run id, honest `runtime_kind`, host/versions incl. runtime
  path and pinned digests, per-probe results (with `disposition`,
  `route_evidence`, `target_kind`), `proof_scope` (executed / deferred-unit-c
  / not-executed-prerequisite), `cleanup_ok`/`cleanup_failures`, residue,
  limitations (incl. the real-relay mechanism used), `hostile_proof`.
- `results.jsonl` — one redacted record per threat case (executed, deferred,
  or not-executed with its reason).
- `coverage.json` — every threat case → `probe` | `deferred-unit-c` |
  `not-executed-prerequisite` (the forced-DERP cases are `probe`).
- `manifest-consumed.json` — the exact pinned manifest used.
- `no-run-evidence.json` — exact prerequisite evidence when the runtime is
  absent (never fabricated proof).
- `cell-<id>-launch-failure.txt` / `cell-diagnostics.txt` — bounded,
  secret-redacted launch/diagnostics evidence (fail-fast, never raw secrets).

## Scope fence and STOP conditions

Forbidden here (unchanged, untouched): production runtime/client/Swift/Go/web,
`deploy/remote-access`, Services/account/auth, schema/migrations/overloaded
columns, dependencies/lockfiles, permissions, packaging, defaults,
signing/releases, provisioning/deployment, telemetry, pricing/pilot/launch,
and all merge-unit-D files (`labs/managed-remote-access/capacity/`,
`tests/labs/capacity/`, `.github/workflows/lab-capacity.yml`). The normative
spec/fixtures are read-only inputs; this lab never writes them.

STOP and escalate on: any fixed invariant failure (daemon loopback/bearer
locality, cell-per-customer, DERP ciphertext-only), any auth/schema/permission/
dependency/production-infrastructure need, or any attempt to claim production
SLA/capacity/DERP share from this lab.
