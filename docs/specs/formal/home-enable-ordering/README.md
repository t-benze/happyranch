# Home Enable renewal ordering

TASK-19401: `HomeDaemonRetry.tla` complements HomeRetry's ordinary-start
abstraction. Timeout models repeated connector start-gate failures while the
desktop daemon is absent; attempts saturate at five to keep the graph finite.
`Limited=TRUE` abstracts the old finite start burst (including dependency/client
starts, not an assertion that five NRestarts occur). With the new interval zero,
timeouts retain a retryable starting state. Return assumes eventual stable
daemon availability; fair Ready/Follow model full readiness and successful
connector/sidecar startup with every other gate healthy. Invariants keep request
admission closed before the daemon gate passes and require connector readiness
before sidecar readiness. The Recovers property holds in daemon-retry.cfg;
daemon-limited.cfg must fail it (exit 13). Use the pinned TLC recipe below with
module HomeDaemonRetry and separate scratch state directories.

This finite model proves neither wall-clock budgets, systemd scheduling and
start counters, Python refinement, privileges/custody, installed upgrade nor
the D5 property mismatch. Exact previous-unit publication is exercised by the
retained production-composition keeper. Manager VM acceptance remains required.

TASK-19025 / THR-327 restoration. Bounded review model for the forward Enable
ordering and unchanged old-intent/Ready refusal paths. No dependency or CI trigger.

| Model action/state | Production boundary |
| --- | --- |
| mode forward / prestate | Fresh-auth HelperLifecycleBackend.dispatch with staged-inert or post-disable capture. |
| shipped / Capture | _home_staged_inventory and _home_unit_provisioning exact inventory, shipped artifact, provenance and custody checks. Only expiry may be deferred. |
| intent | Existing coordinator has durably published headscale_start_verify intent; EFFECTS and journal shape are unchanged. |
| Renew | HomeActivationOwner.perform -> HomeLiveConfigOwner.renew_policy -> _renew_policy_locked(fresh_auth=True), under the existing activation flock. Results abstract ordinary refusal, valid renewed policy and a no-op leaving expiry. |
| Start / RefuseCurrent | Live-config validation and strict _common require_current before the effect's daemon_reload/enable/start. headscale represents its first newly issued service effect, including reload. |
| EnableTimer / StartTarget | Existing managed_services re-stamp and timer-before-target ordering. |
| oldIntent / ready | Backend recovery and attempted interrupted Enable never replay effects; Ready Enable never replays activation; ordinary start is covered by HomeRetry. |

Invariants cover renewal before any start, strict currentness at start, zero starts
on renewal failure, no renewal in non-forward paths, no writes for non-shipped
policy, and timer before target. Terminal stuttering is valid, so deadlock checks
are disabled. This checks safety only, with no liveness/fairness claim.

The model has one activation and two accepted prestates. Service flags count new
effects only, so an already-Ready host can retain running services while issuing
no modeled effect. It collapses the held
flock, protected files and current-auth proof into atomic actions. It does not
prove Python locking, filesystem custody, old-journal byte parsing, native bus,
polkit, real systemd effects, timeout behavior or installed access. The later
idempotent managed-services re-stamp is abstracted into EnableTimer; it has no
additional start before that step. Policy expiry after a start and runtime
fail-closed behavior remain outside this model and unchanged in source.

Run with the existing pinned official TLA+ tools and command documented in
[task-lifecycle Run TLC](../task-lifecycle/README.md#run-tlc), replacing the model
with HomeEnable and each config. Keep jar/state directories outside this repo.
current must exit 0. negative-late-renewal must violate NoStartBeforeRenewal;
negative-noncurrent must violate StartRequiresCurrent (exit 12). Actual results
are recorded in RESULTS.md; finite model results do not establish live acceptance.


## Retry, ordinary start and serving-only Disable

`HomeRetry.tla` / `retry.cfg` complements the original renewal-order model.
Enable requires current renewed policy. Interrupted no-mint/live-key/expired-key
journals recover without replay; live keys must expire before rollover. Ready
ordinary start changes runtime only, preserving mint count and receipt. Disable
accepts active/inactive/activating/deactivating exact owned units (exact custody
and fresh authentication are abstract admission assumptions). Each invocation
resets ack; already-revoked generations require serving revoke again. Failed
stop retains ready and admits another Disable. Unavailable revoke still stops;
cleanup retains the ready receipt and trust, with a partial publication flag and
no modeled exit, matching the
accepted terminal B4 case (TASK-19256). Successful cleanup alone admits fresh Enable, with expired
policy requiring renewal. The inherited HomeEnable model owns detailed group 5
renewal-before-start and timer-before-target ordering.

Run HomeRetry with retry.cfg using the pinned jar/command above. Invariants check
no mint on ready, same-invocation revoke plus stop for Off, retained trust after
failed revoke and current policy before ready. Two mint operations and two
Disable invocations bound the state space. No liveness/fairness, native custody,
authentication, socket ownership or installed-host proof is implied. A failed
ordinary start/joined verifier stutters with a named refusal. Receipt alone is
not runtime availability. The never-ready-after-failed-stop requirement was
withdrawn in TASK-19228; the model intentionally retains ready.

The partial flag abstracts missing publication after B6, not a durable marker or
revocation proof. It prevents the model from inventing an ordinary restart of
the partially removed publication.


TASK-19256 restores the exact ready timer link before ordinary target startup,
then explicitly starts and verifies an inactive timer. The retained model's
ordinary-start transition remains an abstraction of successful runtime repair;
it does not prove timer ActiveState/SubState/enablement or first-renewal lock
ordering. The supported composition keeper owns that systemd-state assertion.
TASK-19255 supplies manager-owned VM evidence of the N5/N6 first-fire flock
collision and its bounded-wait resolution. TASK-19270 permits only unattended
renewal to wait at most 20 seconds for the same lock (below the unit's 30-second
start timeout); all other callers remain nonblocking. This adds no effect while
the lock is held: the model still abstracts lock ownership into atomic actions.
The existing finite TLC result does not prove the wait, native timer activity or
installed acceptance; real-flock standalone cases cover the wait/refusal boundary,
and the manager must repeat the VM flow on the rebuilt exact reviewed head.

## Fixed home port upgrade (THR-327 seq758)

`HomePortUpgrade.tla` adds the bounded version/refusal model for TASK-19588.
`Install` changes the build without rewriting protected home state. `OldOff`
abstracts successful old-build B6; its only port-dependent residue is the
retained sidecar. `NewOn` regenerates an exact prior own provision with fresh
trust. `RefuseLegacy` represents HelperLifecycleBackend.capture on both Enable
and Disable before dispatch can stop, disable or mutate. `RefuseTamper` models
unchanged staged provenance rejection. `NewOnlyAdmission`, `OldStopUsesOldBuild`,
`NoTrustMigration` and `RefusalReadOnly` express the upgrade boundary.

Run the existing pinned TLC command above with HomePortUpgrade and
`port-upgrade.cfg` (expected exit0), then `port-upgrade-unsafe.cfg` (expected
invariant violation/exit12) in separate task scratch directories. The unsafe
config models missing version gates only inside this model; it introduces no
production flag. Results are in RESULTS.md. The graph bounds protected writes
at four; it abstracts fresh authentication, exact URL matching, custody and
full B6 cleanup as prerequisites and proves none of their Python refinement,
native service effects, wall-clock behavior or installed recovery.
