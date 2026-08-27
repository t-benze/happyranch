# Managed Remote Access — Normative Contract

> **Status:** current
> **Date:** 2026-08-26
> **Merge unit:** A — normative contracts and threat fixtures only (TASK-5771)
> **Governing design:** `output/TASK-5724/managed-remote-access-architecture.md` (TASK-5724)
> **Founder authority:** THR-097 seq59 (operate Headscale + DERP ourselves), seq82 approval of seq78 items 1–3 as clarified at seq80
> **Machine-readable contract:** `tests/contract/managed_remote_access/*.json`, validated by `tests/contract/test_managed_remote_access_contract.py`
> **Scope fence:** this document and the fixtures specify *required behavior*. No production Python, Swift, Go, or web behavior is implemented or changed by this PR. Merge units B–D (connector skeleton, hostile runtime harness, lab capacity spike) and all provisioning/deployment/defaults remain explicitly outside this PR.

## 1. Purpose

This is the normative contract for the HappyRanch-managed remote-access lane: the supervised portable home connector, the one-Headscale-cell-per-customer tenant boundary, the shared ciphertext-only DERP fleet, the loopback-only daemon boundary, the credential taxonomy, and the hostile threat matrix that later implementation merge units must satisfy. It corrects known weaknesses in the legacy Swift `HomeConnector`/`SurfaceAllowList` implementation rather than canonizing them (see §11).

The contract is executable: the fixtures under `tests/contract/managed_remote_access/` encode the normative decision order, allow-list, forbidden classes, credential classes, failure/audit categories, and threat cases, and the validator tests reject fixtures that omit, duplicate, malformed, or secretly violate them.

## 2. Fixed invariants (load-bearing — do not bend)

1. **One Headscale cell per customer** is the primary hostile-tenant boundary. Hostile customers are never placed in one shared Headscale tailnet with ACL-only separation.
2. **The shared DERP fleet observes/relays network metadata and WireGuard ciphertext only.** It never grants reachability beyond what cell policy authorizes, and it never sees daemon plaintext or credentials.
3. **The home connector is a supervised portable Python companion process** (Linux/macOS/Windows), never an in-process daemon listener and never a blind TCP port forward.
4. **The daemon remains bound to loopback.** No tailnet, LAN, wildcard, public, or Services bind is permitted.
5. **The daemon bearer is obtained and injected solely by the connector on the final connector→127.0.0.1 hop.** It must never appear in remote input, the client, Services, Headscale, DERP, network, fixture secrets, logs, errors, or audit.
6. **The connector enforces an explicit remote-surface allow-list, current paired/current-device identity, and current policy/revocation epoch, failing closed** on missing/invalid identity, stale policy, unavailable registry, unreadable credential, ambiguous tenant, state corruption, or bind mismatch.
7. **Revocation denies new sessions and closes live ones before or atomically with network-node removal; never success early.**
8. **Managed and DIY are explicit provider lanes.** Local and DIY functionality never depends on Services entitlement or availability.
9. **No Headscale admin/API credential is stored on a client or home endpoint.**

## 3. Trust boundaries

```text
User/OIDC provider
      | authenticated account session (PKCE; device-bound where supported)
      v
HappyRanch Services API ---- durable account/home/device/enrollment registry
      | one-time job capability                 | signed connector trust update
      v                                         v
Provisioning worker --> tenant Headscale     Home connector companion
      |                       ^                 | final loopback hop + bearer
      | one-time auth key     | WG control      v
macOS tsnet node ===== direct or DERP WG ===== 127.0.0.1 daemon
                           ciphertext only
```

- Services and Headscale are trusted for membership and metadata, **not** daemon content.
- DERP is trusted for availability and bounded routing metadata only.
- The home connector is the **final remote authorization point**; the daemon retains its existing local bearer boundary.
- The connector's local store is authoritative for whether a request may reach this home when Services is unavailable; reconciliation compares Services registry, Headscale state, and connector state and **fail-closes on conflict**.

## 4. Tenant model: one Headscale cell per customer

- Immutable `tenant_id` maps to exactly one active `cell_id`; neither is derived from user-supplied names.
- Each cell owns its Headscale instance/database/keyset and a **deny-by-default policy**. Cells may share host-level infrastructure only when compute identity, network policy, storage credentials, backups, encryption keys, and operator authorization remain tenant-scoped.
- Generated policy begins with `grants: []`. Only `client:<device_id> -> home:<home_id>:connector_port` is granted. Home-to-client, client-to-client, home-to-home, SSH, exit-node, subnet-route, funnel, and arbitrary ports are denied unless separately designed and founder-approved.
- Tags are compiler output, never client-asserted authority. Route advertisement and exit-node use are disabled for this product; any advertised route is quarantined and alerts — never auto-approved.
- Cell creation is transactional: status remains `provisioning` until Headscale health, deny-default policy checksum, cell identity, and hostile probes pass. Failure leaves no usable credential.
- Tenant deletion is staged: revoke devices, expire nodes/keys, stop enrollment, retain only policy-approved tombstone/audit data, then cryptographically erase cell secrets and backups per retention policy.

### Hostile proof required (fixture-backed)

With tenant A and B in separate cells, prove: A's account token cannot request enrollment for B's `home_id`/`device_id`; A's one-use enrollment credential cannot be redeemed at B's cell; an A node receives no B peer/map/IP data and cannot open B's connector port via direct path or forced DERP; forged tags, B hostnames/IPs/cell URLs, and route advertisements do not cross the boundary; a provisioning capability for A cannot list/mutate B; empty/malformed/missing/stale policy denies traffic (never Headscale's allow-all fallback); restoring A's backup into a clean cell cannot expose B data or keys. These are encoded as `CROSS-*`, `TOPO-*`, `POLICY-*` cases in `threat-cases.json`.

## 5. DERP fleet semantics

- The shared regional DERP fleet relays WireGuard ciphertext for authorized sessions and meters by tenant/device identity. It is not an authorization authority: **a DERP relay must never create reachability that Headscale/cell policy denies** (`derp_cannot_bypass_headscale_policy`).
- DERP sees source/destination node identity or routing metadata, timestamps, sizes, and ciphertext only. No packet payload capture in routine logging; emergency capture is incident-ticketed, time-bounded, least-privileged, and still ciphertext-only.
- A DERP outage degrades only sessions requiring that region; clients retry boundedly and surface relay unavailable. Never bypass through a public DERP or a less restrictive network.
- DERP admission accepts only currently enrolled managed nodes where supported; otherwise network/identity controls apply and the exact upstream behavior must be validated before launch.
- Rate limits, connection/bandwidth quotas, and per-IP/account/enrollment limits protect the fleet; tenant throttling, circuit breakers, egress-budget alerts, region evacuation, and abuse suspension/appeal procedures precede production.

## 6. Home connector contract (supervised Python companion)

The connector is a companion process supervised by the platform service manager (systemd/launchd/Windows Service later). It owns tailnet-only binding, pairing/revocation enforcement, the remote allow-list, and final loopback token injection. Connector readiness requires: daemon loopback reachable, valid local credential permissions, valid current policy, valid bind identity, non-corrupt trust state — otherwise **no listener**.

### 6.1 Locked request decision order

Every new request/stream runs these steps **in this exact order** (encoded as `decision_order` in `route-policy.json`; the validator rejects any reordering, and the connector-core consumer rejects reversed/missing/duplicated order, altered defaults, and every security-relevant nested value that deviates from the canonical Unit-A semantics — including contradictory non-empty prose and altered allowed-template lists — fail closed at load):

1. `authenticate` — establish authenticated connector/device context (device proof valid, connector identity established).
2. `bind` — verify tenant/home/device/cell binding and current pairing.
3. `proof` — validate non-expired/non-replayed client proof (fresh nonce/timestamp window).
4. `policy` — require present, well-formed, **current** policy/revocation state (see §10).
5. `normalize` — parse and normalize method/path **exactly once**; deny ambiguity (§6.2).
6. `allowlist` — match the normalized method+path against the **explicit** remote allow-list; deny unclassified (§6.4).
7. `strip` — strip all remote auth/forwarding/hop-by-hop/host/cookie/proxy credentials (§6.3).
8. `bearer` — **only now** read the local daemon bearer and forward to `127.0.0.1`.
9. `redact` — record the outcome as a stable category; never raw exception text or credentials (§9).

### 6.2 Normalization rules (exactly once, deny ambiguity)

- Percent-encoding: decode once with strict validation; reject invalid/overlong octets and double-encoding that changes the decoded segment set.
- Dot segments: resolve dot/dot-dot after decoding; deny any result that escapes the daemon path root or changes the matched template.
- Duplicate slashes: collapse deterministically and re-evaluate; deny when collapsing changes the template match outcome.
- Query separation: split at the first `?`; query strings never participate in route identity.
- Unicode/control bytes: reject control bytes, NUL, CR/LF injection, and overlong UTF-8 forms in method, path, or headers.
- Absolute-form/authority ambiguity: reject absolute-form request targets (`scheme://authority/path`) and any host/authority ambiguity.
- **Ambiguity is denied.** The connector parses and reconstructs requests; it is never a blind TCP port forward.

### 6.3 Header stripping and bearer injection

- Strip all incoming `Authorization`, forwarding (`X-Forwarded-*`, `Forwarded`), hop-by-hop (`Connection`, `Keep-Alive`, `Transfer-Encoding`, `TE`, `Upgrade` per hop rules, `Proxy-*`), `Host`, `Cookie`, and proxy headers.
- Reject duplicate critical headers and conflicting `Content-Length`/`Transfer-Encoding` framing (smuggling).
- The connector injects the locally read daemon bearer **only** into the reconstructed loopback request to `127.0.0.1` — never into any network-facing frame.

### 6.4 Allow-list semantics

- Remote policy is **explicit allow-by-method+normalized-template** and deny-unclassified. The allow-list is the browser-consumed daemon surface expressed as explicit `{method, path_template}` entries, **minus** routes this contract forbids remotely even though the local SPA consumes them (auth bootstrap/registration).
- `route-classification.json` `included` (web coverage) is **not** equivalent to remotely allowed; it is an input from which the explicit allow-list is derived and audited. The Swift deny-list approach is evidence of a weakness, not authority (§11).
- Forbidden classes (each with example templates in `route-policy.json`, validated to never overlap the allow-list):

  | Class | Meaning |
  |---|---|
  | `auth_bootstrap_registration` | auth bootstrap + registration-token minting — local-only |
  | `agent_callbacks` | completion/progress, jobs submit, thread reply/decline/dispatch/escalation/compose/post-as-agent, dreams complete, work-hours/schedules spawn, agents manage |
  | `management` | set-executor, agent repos, enrollment administration (SPA settings surface is separately explicitly allowed) |
  | `founder_as_founder` | founder-only + `/as-founder` surfaces, portability reconciliation |
  | `memory_learning_writes` | memory/learning POST/PUT/PATCH/compact/promote/reindex/lifecycle |
  | `artifact_upload_agent_only` | agent-facing artifact upload/list/download, thread attachments |
  | `adapter_administration` | adapter register/submit/approve/reject/bind-profile, custom-cli connect |
  | `executor_registration` | executor/conformance/binary registration |
  | `portability_reconciliation` | org-portability preflight + reconcile |
  | `unclassified_default_deny` | any route not in `allow`, including all future routes — deny by default |

- Method-awareness: an allowed template permits only its listed method(s); unsupported methods on an allowed path are denied (`method_denied`).

### 6.5 Upgrade semantics (HTTP + SSE/WebSocket)

- HTTP is the baseline surface. SSE and WebSocket are allowed only via explicitly listed GET templates (`upgrade_semantics.sse.allowed_templates` / `.websocket.allowed_templates` in `route-policy.json`); every upgrade template must also be on the allow-list (validator-enforced).
- Unsupported upgrades, unsupported bodies on upgrade surfaces, and non-GET upgrade methods are denied.
- WebSocket: currently **no** daemon WebSocket surface is remotely allowed (`websocket.allowed_templates: []`); any future WS surface must be deliberately added and reviewed.
- Revocation cancels open HTTP/SSE/WebSocket streams immediately (§10).

## 7. Credential taxonomy

Seven distinct credential classes (encoded in `credential-taxonomy.json`; the validator requires all seven with complete fields and obvious non-secret `PLACEHOLDER_*` examples):

| Class | Issuer | Storage/custodian | Single-use/replay | Forbidden exposure |
|---|---|---|---|---|
| `account_session` | Services auth (PKCE) | macOS Keychain on client | short-lived; refresh rotation with reuse detection | logs, connector, Headscale/DERP, home host, fixtures, audit, plaintext network transport, exposure beyond the authenticated Services audience |
| `one_use_enrollment` | Services provisioning worker, cell-scoped | memory/transient; returned once | single-use, ≤10 min; redeemed/expired/revoked → same category as absent | disk config/env, reuse, admin scope, logs, fixtures, plaintext network transport, exposure beyond the authenticated TLS delivery to the intended device |
| `node_device` | client-generated | client Keychain/tsnet state | persistent; re-enroll to rotate; proofs single-use per challenge | Services plaintext, logs, network transmission of the private key material, fixtures, exposure of derived proofs beyond the authenticated audience |
| `connector_device_proof` | connector-generated | connector encrypted local store | persistent; epoch-based; nonce-windowed proofs | Headscale/DERP, Services plaintext, fixtures, daemon-bearer derivation, plaintext network transport, exposure beyond the authenticated Services audience |
| `pairing_authorization` | home-owner approval via Services | connector encrypted store + Services desired state | epoch-bound; lower-epoch replays rejected as rollback | fixtures, logs, audit, daemon bearer, plaintext network transport, exposure beyond the authenticated paired-connector/Services audience |
| `policy_revocation_material` | Services policy compiler/revocation | connector store + cell-pinned artifact | epoch/revision/checksum-bound; stale/future/rollback denied | fixtures, client, logs, audit, daemon bearer, plaintext policy details, exposure beyond the authenticated connector audience |
| `local_daemon_bearer` | daemon runtime on home host (existing token file) | home-only protected file | persistent; connector reads it only for the final loopback hop | network, Headscale, DERP, Services, client, logs, errors, audit, fixtures, remote input, support exports, diagnostics |

**Network transport rule:** remotely presented credentials (account/session, one-use enrollment, node/device proofs, connector device proof, pairing authorization, policy/revocation material) travel **only over encrypted transport (HTTPS/TLS/WireGuard) to their exact authenticated audience** — never in plaintext and never beyond that audience (each class's `network_presentation` in `credential-taxonomy.json` states this). The **local daemon bearer is the only class with an absolute network prohibition**: it never traverses any network in any form and is read/injected by the connector only on the final loopback hop to `127.0.0.1`.

Distinguish: account/session credentials, one-use enrollment credentials, node/device keys, connector device proofs, pairing authorization, policy/revocation material, and the local daemon bearer. **No class is interchangeable with another**; the daemon bearer is never an enrollment, pairing, network, support, or client credential. Fixtures, docs, examples, and tests use only obvious non-secret placeholders (`PLACEHOLDER_*`); sentinel credential shapes (`hrpair_`, `hrreg_`, `Bearer <token>`, PEM private keys, etc.) must never occur in serialized fixtures, validation errors, expected audit fields, or failure details (validator-enforced).

## 8. Failure/audit category taxonomy

Deny/failure categories (encoded in `failure-categories.json`): `identity`, `enrollment`, `cell`, `policy`, `map`, `peer`, `pairing`, `current_device`, `revocation`, `replay`, `expiry`, `route`, `method`, `normalization`, `relay`, `direct`, `transport`, `local_daemon`, `internal`, `unknown`.

Audit categories are a fixed enumerated set of stable lowercase snake_case identifiers (`enrollment_denied`, `revocation_stream_closed`, `normalization_denied`, …). Rules:

- **No cross-tenant existence oracle.** Paired cases (target exists vs target absent) must produce identical deny and audit categories **and identical externally visible failure detail**; a denied request never reveals whether a tenant/cell/home exists. This extends to credentials: an absent credential and a consumed/replayed one are externally indistinguishable (see the `CRED-003`/`CRED-003b` pair).
- **Redaction.** Audit fields, failure details, error responses, and logs contain category-level prose only: no raw exception text, no tracebacks, no file/line references, no request paths/bodies, no cookies, no authorization headers, no credential material, no full IPs unless an approved retention policy requires them.
- **Internal/unknown failures** are redacted to category and treated as deny.

## 9. Revocation ordering

- Revocation is a saga with a monotonic `revocation_epoch`. Services denies new sessions/enrollment; Headscale expires/deletes the node; the **connector durably denies and closes live streams**; the client clears local provider/session state.
- Ordering: connector deny/close happens **first or atomically with** durable desired-state application, and **before** network-node removal. **Never report success before required acknowledgements.** Timeout means `revocation_pending` and remote access remains connector-denied.
- The connector core applies revocation through **one authoritative transaction** (`RevocationCoordinator.revoke` in `runtime/remote_access/revocation.py`): the epoch must advance (rollback rejected before any side effect), then every open HTTP/SSE/WebSocket handle is closed through the stream registry **fail closed** — the registry seals every externally retained wrapper FIRST, so even a raising transport `close()` leaves the retained handle irrevocably rejecting receive/send (never readable, writable, or untracked; the failed stream ids are surfaced as `RevocationIncomplete` outcome evidence without claiming physical closure) — then trust-state revocation is applied atomically. `TrustState._apply_revocation` is private and reachable only through this transaction; the old public `apply_revocation` bypass is removed. If a handle close fails, the revocation is still applied (the deny side is the safe side) and surfaced as `RevocationIncomplete` carrying the applied epoch — state is never left ambiguously advanced.
- **Concurrent revocations are serialized and the complete cleanup terminal result is shared/persisted** (TASK-5867): the stream registry runs the transport cleanup exactly once and persists the failed stream ids; every concurrent waiter observes the same terminal result (no lock is held while waiting, and transport-close callbacks never run under a lock, so a callback that re-enters the transaction on the same thread fails closed rather than deadlocking). **No caller may return success while an in-flight or completed cleanup failure relevant to the sealed generation is unreported**: a racing higher-epoch revoke can never treat an in-flight closure as a successful idempotent no-op, and every later revoke that would otherwise succeed re-surfaces the persisted failed ids as `RevocationIncomplete`. Seal-first fail-closed byte safety, monotonic epochs, rollback-before-side-effects, idempotency, and deterministic outcomes are preserved.
- **Admission and revocation share ONE atomic ownership boundary** (TASK-5874): the stream registry's single lifecycle lock guards BOTH the sealed flag and registry membership. `StreamRegistry.open` performs its sealed-flag check and its registration in ONE critical section; `StreamRegistry.close_all` performs the seal (the **linearization point**) and the live-stream snapshot in the SAME critical section, the seal occurring before the snapshot. An admission is **successful only if it is fully registered before the seal**; any admission not fully registered before the seal **fails closed and never returns a usable wrapper** — even when transport allocation or callbacks raced. A pre-seal registration is included in the revocation snapshot: its wrapper is irrevocably sealed and its required physical-cleanup outcome is acknowledged before `close_all` success. Once a handle is passed to `open` the registry owns its cleanup; the fail-closed admission closes the unregistered allocated transport itself (never leaked, never returned). Transport open/close and duplicate-replacement callbacks **never execute under the lifecycle lock** (no lock-across-callback deadlock or re-entrancy hazard; same-thread re-entry fails closed), and failure mapping stays stable and redacted. Permitted observations for both orderings — an admission that linearizes before the seal is revoked with the snapshot (`revocation_stream_closed`); an admission that linearizes after the seal is denied (`revocation_stream_closed`) and never yields a usable stream — are encoded machine-readably in threat case **REV-004**.
- Policy/revocation state must be present, well-formed, and current before any request is authorized. Empty, malformed, stale, future, rollback, compiler-failed, and apply-failed states fail closed; the last valid non-expired policy is not a substitute for a missing current one beyond a separately founder-approved bounded grace, and otherwise the connector stops listening.

## 10. Direct path and forced-DERP semantics

- Nodes receive only the peer/map data their cell policy grants. NAT traversal attempts direct UDP first; if direct fails, the transport uses a HappyRanch DERP region.
- Acceptance must prove both paths. **Forced relay must be deterministic** (lab firewall/netcheck controls plus client evidence showing the selected DERP region), never inferred from success.
- Direct-path denial and deterministic forced-DERP denial are distinct fixtures (`direct_path_denied`, `forced_derp_denied`); DERP cannot bypass Headscale/cell policy (`derp_no_bypass`).

## 11. Observed-vs-required behavior (Swift legacy)

- **Observed today:** `app/mac/Sources/HappyRanchSupervisor/SurfaceAllowList.swift` is a **deny-list** gate (deny known-bad routes, allow the rest), mirroring `tests/contract/route-classification.json` via its drift-guard test. `HomeConnector` (Swift) injects the daemon bearer on loopback and enforces pairing via `RealPairingStore`. The Swift implementation is legacy evidence of the *intent* (remote SPA surface, bearer on final hop, pairing/allow enforcement), not authority for the *form*.
- **Required:** explicit allow-by-method+normalized-template with deny-unclassified; ambiguity-denying normalization; header stripping + bearer injection on the final hop only; current-device/policy/revocation-epoch enforcement; fail-closed on every policy/identity/daemon anomaly. A new daemon route is **never** remotely reachable merely by being browser-consumed locally.
- **Known Swift weaknesses this contract corrects (not canonizes):** deny-list orientation (new routes allowed by default); `contains`-based segment patterns that can over-match; auth bootstrap not structurally excluded from the remote surface; legacy unprefixed forms retained for defense-in-depth rather than normalization-exactly-once; no normative credential taxonomy separating the daemon bearer from enrollment/pairing material; no normative failure/audit category taxonomy.

## 12. DIY lane

Supported DIY remains an additional provider lane terminating at the **same connector contract** and allow-list, without Services entitlement or infrastructure. Provider state is namespaced; switching requires explicit user confirmation, fresh enrollment/pairing, cleanup proof; managed credentials never become DIY configuration or vice versa. Managed is not a limit imposed on DIY; DIY is never silently migrated into Services.

## 13. State/persistence requirements (schema-agnostic)

This contract deliberately does **not** select a persistence schema (that is a founder-gated schema decision). Required properties only:

- Connector identity/private key in OS credential storage where practical; non-secret registry in a versioned store under the runtime root with owner-only permissions.
- Atomic replace/fsync, corruption detection, rollback-safe migrations, and crash-safe application of trust updates.
- Never co-mingle connector state with daemon token contents or mutable Services caches.
- Connector state is recoverable by re-enrollment; it is not silently reconstructed from Headscale alone.

## 14. Versioning

- The wire contract and the route-policy artifact are **versioned independently** from the daemon API. First frame carries protocol version, tenant/home/device IDs, authorization epoch, client nonce, timestamp window, requested stream type, and proof — never the daemon bearer.
- The route-policy artifact is generated from the daemon route inventory and pinned with a version + signature/hash; startup validates compatibility. Unknown routes/methods are denied; policy update failure keeps the last valid non-expired policy only within the separately approved bounded grace, otherwise the connector stops listening.
- Fixtures carry `version` + `status: normative-contract`; changes to the contract update fixtures and this document together in one PR.

## 15. Swift retirement/conformance expectations

- Freeze the Swift home connector as legacy; accept only security fixes.
- Conformance of future implementations is judged against **this contract and its fixtures**, not against Swift behavior. Differential tests may compare intended parity, but Swift is not the authority.
- Do not run both listeners simultaneously; validate macOS-home with the Python companion while Swift remains rollback-only.
- Removal of Swift `HomeConnector`/`RealPairingStore` and obsolete tests happens in a dedicated retirement PR after conformance, migration/re-pairing evidence, and rollback evidence.
- Never copy raw legacy `hrpair_` values into the managed verifier store by default; explicit re-pairing is safer unless a reviewed migration proves binding and revocation semantics.

## 16. Implementation status and out-of-scope merge units

This contract PR covered **merge unit A** (normative contracts, threat fixtures, validator tests, doc parity). Subsequent implementation status (tranche lettering per TASK-5766; phase-unit numbering per TASK-5724 §18):

- **Portable connector core (TASK-5724 phase unit 2 = tranche merge unit C) — IMPLEMENTED** by merge unit C (TASK-5842): `runtime/remote_access/` (strict parser/normalization, versioned route-policy consumer with schema/digest/version/staleness fail-closed drift **and the locked nine-step decision order, default behavior, every security-relevant nested normalization/header-stripping/upgrade value by exact canonical equality (contradictory non-empty prose and altered allowed-template lists rejected at load), forbidden classes, and operational state validated at load — unknown states never treated as active**, connector identity + device-proof verifier seam, current authorization/revocation with live-stream closure **through one authoritative `RevocationCoordinator` transaction that seals the stream registry fail closed before applying trust-state revocation, serializes concurrent revocations, and shares/persists the complete cleanup terminal result — no caller returns success while an in-flight or completed cleanup failure relevant to the sealed generation is unreported (TASK-5867); admission and revocation share one atomic ownership boundary — the sealed-flag check and registration are one critical section, the seal (the linearization point) precedes the live-stream snapshot, any admission not fully registered before the seal fails closed and never returns a usable wrapper, and transport open/close and duplicate-replacement callbacks never run under the lifecycle lock (TASK-5874)**, allow-list enforcement, remote-auth/hop-by-hop stripping, daemon-credential-provider seam, and a loopback-only forwarding abstraction **whose boundary normalizes every forward/open/stream failure — including connection refusal, timeouts, and hostile exception text — into stable Unit-A deny categories and deterministically closes partial resources**) plus `tests/remote_access/` (focused, adversarial, and checked-in mutation tests consuming the Unit-A fixtures; loopback-only test harness; in-memory persistence abstraction only). It adds **no** tailnet or externally reachable bind, **no** Headscale/DERP/Services integration, **no** durable persistence schema/migration, and **no** packaging/service installation.
- **Hostile tenant-isolation runtime harness (tranche merge unit B)** — IMPLEMENTED at `labs/tenant_isolation/` (see §18); it consumes the same fixtures and shares the fail-closed contract.
- **Linux supervised connector packaging (TASK-5724 phase unit 3)** — outside and unimplemented: systemd lifecycle, permissions, diagnostics, lab-only provider adapter.
- **Lab capacity spike (phase unit 4 / tranche unit D)** — deferred by founder THR-097 seq108; its retained PR #733 is closed-unmerged and its partial CI-runner artifacts support no capacity, SLA, DERP-share, economics, or pricing claim.
- Services domain/API design + additive schema, tenant-cell orchestrator, macOS managed enrollment, managed trust sync, DERP operations slice, signed end-to-end beta, macOS-home/Windows-home conformance, Swift retirement, production readiness.

No production provisioning, deployment, dependency, schema, auth, or permission change is authorized by this contract or by the connector-core skeleton.

## 17. Fixture inventory and validation

| Fixture | Contents |
|---|---|
| `tests/contract/managed_remote_access/route-policy.json` | locked decision order; normalization/header/upgrade semantics; forbidden classes with examples; explicit allow-list (134 method+template entries derived from the browser-consumed daemon surface minus remote-forbidden auth bootstrap) |
| `tests/contract/managed_remote_access/credential-taxonomy.json` | the seven credential classes with issuer/subject/storage/lifetime/single-use/replay/rotation/revocation/forbidden-exposure fields and `PLACEHOLDER_*` examples |
| `tests/contract/managed_remote_access/failure-categories.json` | deny + audit category taxonomies and the existence-guard rule |
| `tests/contract/managed_remote_access/threat-cases.json` | 57 cases: 3 positive controls + 54 hostile negatives covering every mandated scenario class, with existence-guard pairs (including the absent-vs-consumed credential pair `CRED-003`/`CRED-003b`) and the admission-vs-seal atomic ownership boundary race (`REV-004`) |
| `tests/contract/test_managed_remote_access_contract.py` | semantic validator (stdlib + pytest): schema exactness, decision-order lock, allow-list well-formedness/uniqueness/daemon-snapshot consistency/forbidden-overlap rejection, credential-class completeness, placeholder hygiene, encrypted-transport-to-audience transport rules, category-taxonomy membership, hostile⇒denied + positive-control⇒allowed enforcement with a checked-in hostile→allowed mutation proof, threat-matrix coverage completeness, redaction checks, sentinel-credential scan (fixtures + validator error messages), existence-pair identity including identical visible failure detail |

Validators are semantic, not tautological snapshots: the normative invariants are hard-coded in the test module and the fixtures must encode them. They reject missing/unknown required fields, duplicate/ambiguous cases, malformed methods/paths/ordering, secret-bearing examples, and incomplete hostile matrices.

## 18. Parity pointers

- `CLAUDE.md` — "Managed remote access (normative contract)" Essentials bullet (load-bearing invariants + implementation status).
- `docs/superpowers/specs/README.md` — this spec is indexed as `current`.
- Connector core implementation: `runtime/remote_access/` (portable supervised Python connector skeleton, loopback-only harness) with `tests/remote_access/`.
- Hostile tenant-isolation runtime harness: `labs/tenant_isolation/`.
- The web OpenAPI coverage test (`web/src/test/openapi-coverage.test.ts`), the Swift drift-guard, and `tests/contract/route-classification.json` are **unchanged** consumers/inputs; this contract reads them but does not modify them.
