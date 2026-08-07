# THR-107 Slice 1A — pre-edit impact inventory

> Status: implementation inventory
> Task: TASK-4687
> Baseline: `origin/main` `8dd5aa4075eadb0422a2d6e9a242930318f9338b`
> Scope: nonlaunchable, daemon-global direct-authority mint foundation only.

This is the required pre-production-edit inventory for Slice 1A. It is
committed before its production changes. The direct authority is an additive
runtime-root SQLite store, distinct from org provenance, YAML profiles, and
the registry. No row introduced by this slice is eligible to launch, project,
or represent a connection.

## GitNexus exception

The one permitted command was run once in this exact worktree:

```text
$ gitnexus analyze
Analysis failed: EPERM: operation not permitted, open '/Users/tangbz/.gitnexus/registry.json'
```

No workaround, alternate index, or retry was used. This manual `rg` /
import / caller inventory is the granted replacement for this slice.

## Modified production symbols

| Symbol / seam | Planned change | `rg` / import / caller result |
| --- | --- | --- |
| `runtime.daemon.routes.auth.RuntimeRegistrationTokenMintRequest` | Add optional `workspace_adapter_id` accepted only for `purpose="adapter"`. | Defined and consumed only by `mint_runtime_registration_token` in `routes/auth.py`; browser caller is `web/src/lib/api/settings.ts:RuntimeRegistrationTokenMintRequest`. |
| `runtime.daemon.routes.auth.mint_runtime_registration_token` | Validate the exact first-party allow-list before mint/store effects and atomically persist direct authority when supplied. | Routed at `POST /api/v1/auth/registration-token/runtime`; master/loopback gate remains its existing dependency and peer check. No scoped direct-connect route exists or is added. |
| `runtime.daemon.registration_token.RegistrationTokenStore.mint_runtime` and `RegistrationTokenRecord` | Preserve absent-field legacy adapter mint; add an internal all-or-nothing mint callback/seam so a returned selected direct mint has durable authority. | Runtime mint route is the sole production caller; adapter submit/conformance callers use validation/reservation only and remain unchanged. |
| `runtime.daemon.direct_connect_store.fingerprint_registration_token` | New domain-separated SHA-256 one-way fingerprint helper. | New Slice-1A-only module; invoked only while writing/finding the authority record. Raw `hrreg_` values never cross its returned string boundary. |
| `runtime.daemon.direct_connect_store.DirectConnectAuthorityStore` (`_init_schema`, `mint_authority`, readback helpers) | New additive runtime-root SQLite authority for fingerprint, intended profile/name, canonical server-derived wrapper destination, workspace adapter, issue/expiry, nonlaunchable state, and provenance. | New Slice-1A-only module. It does not import org DB, adapter/profile YAML stores, executor registry, or audit logger; SQLite transaction/readback is local to the new DB. |
| `runtime.daemon.state.DaemonState` | Construct the daemon-global store at `<runtime_root>/direct_connect_authority.db`, with in-memory idle support. | Existing daemon-global `MetricsStore` is the pattern; state is constructed by `idle` and `from_runtime`. No daemon lifecycle, runner, or auth mechanism changes. |
| `web/src/lib/api/settings.ts:RuntimeRegistrationTokenMintRequest` | Add optional TS request field only; no UI wiring. | `mintRuntimeRegistrationToken` is used by existing Connect/onboarding code. Existing callers omit the optional field, preserving legacy behavior. |
| `tests/contract/openapi.json` | Regenerate the already-exposed runtime mint request contract snapshot. | Snapshot covers `/api/v1/auth/registration-token/runtime`; no new route is classified or exposed. |

## Explicitly unchanged caller inventory (reserved for later fence slice)

| Surface | `rg` result | Slice 1A disposition |
| --- | --- | --- |
| Custom adapter Popen / retry | `runtime/orchestrator/executors.py:1483` `_launch`, `:1612` `subprocess.Popen` | Unchanged; no direct record can be launchable. |
| Executor eligibility / construction | `runtime/orchestrator/executor_registry.py:700` `build_executor` | Unchanged. |
| Task runner | `runtime/orchestrator/orchestrator.py:666` `_run_agent` | Unchanged. |
| Thread runner | `runtime/daemon/thread_runner.py:465` `run_invocation` | Unchanged. |
| Wake runner | `runtime/daemon/wake_runner.py:112` `run_wake` | Unchanged. |
| Dream runner | `runtime/daemon/dream_runner.py:105` `run_dream` | Unchanged. |
| Schedule runner | `runtime/daemon/schedule_runner.py:105` `run_schedule` | Unchanged. |

The later lifecycle slice owns direct ingress, COMMITTED/project/receipt and
compensation semantics, projections, central eligibility, launcher fencing,
Popen/retry proof, runner matrix, legacy disposition/cutover, and UI changes.

## Documentation sweep classification

`rg` over `protocol/`, `docs/agent-guides/`, and the THR-107 specs classified
the Slice-1A terms as follows:

| Surface | Classification |
| --- | --- |
| `protocol/05b-agent-runtime.md` | Updated: records the nonsecret, nonlaunchable authority foundation and deferred lifecycle. |
| `protocol/05c-orchestrator.md` | Updated: records no runner/eligibility effect and deferred fence. |
| `docs/superpowers/specs/2026-07-24-unified-adapter-runtime-architecture.md` | Updated: preserves the approved final D7B lifecycle while locating Slice 1A before it. |
| `docs/agent-guides/agent-executors-and-permissions.md` | Unchanged: describes shipping adapter submission/Popen behavior, which Slice 1A deliberately does not alter. |
| Connect/onboarding UI docs and components | Unchanged: this slice adds only a browser request type and does not wire UI behavior. |
