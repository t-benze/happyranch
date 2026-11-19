# Linux C19 address recovery design — TASK-17699

Base: origin/main a72ee642fe51d1027521dbcb2e3a7f94b17c6e1a (fetched before work).

Assumptions: an invite is still validated by the existing codec and expiry checks;
recovery needs exactly one saved record matching home ID, control-plane ID,
connector ID, CA DER and its SHA-256 fingerprint. Only control URL, dial IP and
home endpoint may change. Ambiguous/missing records or changed identity take the
ordinary join path with fresh native consent and Host approval. A recovery failure
also requires a separate fresh join confirmation; it never automatically spends
the new invite. No Client result establishes Host-wide invite invalidation.

The proposed existing-v3-resume route is supported by the source:
`home_session_state.go:readSavedSessionLaunch` accepts networkState instead of
joinKey, requires saveIntent and the saved stableNodeID, and validates the entire
bound state using restoreHomeState. `home_session.go:newHomeSession` restores the
memory store and starts non-ephemeral tsnet with no AuthKey. `homeSession.up`
compares the authenticated live Self.ID with the saved node before readiness;
mismatch is -22. `home_helper.go:validateHomeLaunch` retains certificate/home/
control-plane checks and the fixed internal control URL (only its already
accepted slash spelling can vary); dialIP supplies the changed network address.
There is no need to rewrite tsnet state or relax its control URL check.

Native code will compare the immutable identity tuple, display the updated target
(including control URL, dial IP, endpoint and fingerprint), and require explicit
GTK or controlling-terminal confirmation before launch. After confirmation it
rereads the exact protected record using SecretStore.load, then resumes with that
record's networkState, stable node, profile/hostname and bearer and the invite's
address metadata. The invite's join key and pair secret never enter the helper
frame or pairing requests on this path. ClientSession.health performs the existing
authenticated bearer request through the pinned transport to validate the live
grant. Only after both checks succeed may the existing 8b save/readback path save
the updated record. Identity/profile/bearer stay unchanged; the normal latest
network-state snapshot remains managed by 8a. Failure leaves the saved item alone;
cleanup must finish before offering a normal join with new native consent.

Helper protocol changes: **NONE**. No helper recovery field, bridge schema field,
flag, IPC, privileged mechanism, authentication/permission change, trust reset or
repin. `client.acceptInvite` already routes to ClientModel.accept, which selects
this native branch. GTK/runtime loaded-RELEASE gating remains unchanged. CLI uses
existing closed JSON states/effects and continues to claim runtimeReachable only.

Impact: client_session.py (matching/recovery using restore), client_model.py
(saved discovery, confirmation, recovery/fallback), client_ui.py (bilingual
sensitive confirmation), client_cli.py (terminal confirmation/recovery/fallback),
adjacent standalone tests, Linux documentation, spec C19/5b/8a and the maintained
bridge inventory. Targeted rg found connection_window.py's existing acceptInvite
call and all restore/target callers; neither that dispatch nor tsnet-bridge needs
editing. Risk: medium lifecycle/custody sequencing using unchanged trust checks.

Verification: supported standalone Linux suites and attributable fail/pass
regressions. Real-network C19 and packaged GTK acceptance are slice 7 **NOT RUN**.
No install, deployment, restart, Mac source edit or ad hoc Product E2E is authorized.
