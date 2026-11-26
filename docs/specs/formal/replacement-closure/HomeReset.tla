---------------------------- MODULE HomeReset ----------------------------
EXTENDS Naturals, TLC
CONSTANT BadReset
VARIABLES caller, fixed, owned, quiet, authorized, state, resetOK, attempted,
          unsafeReset, result
vars == <<caller, fixed, owned, quiet, authorized, state, resetOK, attempted,
          unsafeReset, result>>
Init ==
    /\ caller \in {"turn-off", "ready-restart", "status"}
    /\ fixed \in BOOLEAN /\ owned \in BOOLEAN
    /\ quiet \in BOOLEAN /\ authorized \in BOOLEAN
    /\ state \in {"failed", "inactive"} /\ resetOK \in BOOLEAN
    /\ attempted = FALSE /\ unsafeReset = FALSE /\ result = "none"
Proof == fixed /\ owned /\ quiet /\ authorized /\ caller # "status"
Reset ==
    /\ state = "failed" /\ ~attempted /\ (Proof \/ BadReset)
    /\ attempted' = TRUE /\ unsafeReset' = ~Proof
    /\ state' = IF resetOK THEN "inactive" ELSE "failed"
    /\ result' = IF resetOK THEN "none" ELSE "partial"
    /\ UNCHANGED <<caller, fixed, owned, quiet, authorized, resetOK>>
Complete ==
    /\ Proof /\ state = "inactive" /\ result = "none"
    /\ result' = "complete"
    /\ UNCHANGED <<caller, fixed, owned, quiet, authorized, state, resetOK,
                    attempted, unsafeReset>>
Next == Reset \/ Complete
Spec == Init /\ [][Next]_vars
ResetBound == ~unsafeReset
StatusReadOnly == caller = "status" => ~attempted
CompletedInactive == result = "complete" => state = "inactive" /\ Proof
FailedResetPartial == attempted /\ ~resetOK => result = "partial"
=============================================================================
