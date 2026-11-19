# Linux connection client

The packaged connection view and `happyranch connection client accept-invite`
support C19 same-home address recovery. When exactly one saved record matches
the home, control plane, connector and CA, changed target metadata requires a
native confirmation (or typing `update` on the controlling terminal).

Recovery verifies the saved node and live grant without redeeming the new
invite's join/pair material, then uses the existing login-keyring save/readback.
Failed validation requires separate fresh consent and normal Host approval;
declining launches nothing. The client makes no Host-wide invite invalidation claim.

See the [Linux release handbook](../../docs/operations/linux-release-handbook.md)
for supported standalone checks, custody requirements and foreground CLI usage.
Real-network C19 acceptance is slice 7 **NOT RUN**.
