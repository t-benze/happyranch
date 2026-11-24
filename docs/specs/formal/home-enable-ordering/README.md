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
| oldIntent / ready | Backend recovery and attempted interrupted Enable never replay effects; Ready Enable inspects only. |

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
