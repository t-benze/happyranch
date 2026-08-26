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
enroll into, learn about, or reach tenant B on any path (direct or
deterministic forced-DERP), that a shared DERP never bypasses cell policy, that
forged tags/routes/subnet/exit-node/SSH advertisements fail, that wrong
cell/home/device/account/node/key reuse fails, that missing/empty/malformed/
stale policy fails closed, and that cross-cell backup/state/key contamination
is detected — all with **tenant-neutral category-level results and zero
secret/raw-exception leakage**.

## Honest runtime status (read first)

- **Real hostile proof requires the isolated CI/lab runtime** — GitHub Actions
  `ubuntu-latest` (the repo's existing authorized CI runtime) via the
  path-scoped workflow `.github/workflows/lab-tenant-isolation.yml`. It has
  Docker preinstalled; the harness pins its artifacts by digest/sha256.
- **The manager/authoring host has no Docker/Podman/Go/Headscale/Tailscale.**
  `--runtime mock` and the unit tests exercise orchestration/parsing/assertion
  logic only. **A mocked/unit-only pass is NOT proof of tenant isolation** and
  every summary labels its `runtime_kind` honestly (`real` / `mock` / `none`);
  `hostile_proof` is `true` only for a real run with all probes passed.
- `--check-runtime` and `--runtime none` emit `no-run-evidence.json` with exact
  prerequisite evidence — never fabricated runtime proof.

## What the harness proves (semantically consumed from the contract)

The harness reads `tests/contract/managed_remote_access/*.json` at runtime
(read-only; pinned digests in `manifest.json`, drift fails closed):

- expected deny/audit categories for every threat case come **from the
  fixtures**, never duplicated in harness code;
- every one of the 56 threat cases is accounted for in `coverage.json`
  (`probe` executed on the lab runner, or `deferred-unit-c` for connector-level
  request-decision cases owned by merge unit C — never silently dropped);
- hostile ⇒ denied with a `deny_category`; positive controls ⇒ allowed;
  existence-guard pairs (absent vs consumed/replayed) must produce **byte
  identical** visible detail (no cross-tenant existence oracle);
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
tests/tenant_isolation/   focused unit tests (87) incl. the mandated mutation probes
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

Exit codes: `0` all probes passed · `1` hostile proof failed · `2` preflight
declined · `3` residue found · `5` runtime unavailable (no-run evidence).

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

- `summary.json` — run id, honest `runtime_kind`, host/versions, per-probe
  results, residue, limitations, `hostile_proof` flag, `deferred_case_ids`.
- `results.jsonl` — one redacted record per executed probe.
- `coverage.json` — every threat case → `probe` | `deferred-unit-c`.
- `manifest-consumed.json` — the exact pinned manifest used.
- `no-run-evidence.json` — exact prerequisite evidence when the runtime is
  absent (never fabricated proof).

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
