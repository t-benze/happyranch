# Home Enable renewal ordering

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
N5/N6 first-renewal timing still needs manager-owned VM evidence; the earlier
finite TLC result is not proof of that race or its resolution.
