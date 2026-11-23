------------------------ MODULE ReplacementClosure ------------------------
EXTENDS Naturals, TLC
CONSTANTS BadReset, BadSettlement
VARIABLES version, phase, reloaded, unitState, managed, shape, authorized,
          unsafeReset, worker, slot, sealed, locked, completionRaises, result
vars == <<version, phase, reloaded, unitState, managed, shape, authorized,
          unsafeReset, worker, slot, sealed, locked, completionRaises, result>>

Init ==
    /\ version \in {1, 2}
    /\ phase \in {"staged", "reload"}
    /\ reloaded \in BOOLEAN
    /\ unitState \in {"failed", "inactive"}
    /\ managed \in BOOLEAN
    /\ shape \in {"absent-safe", "pid", "job", "fragment", "dropin"}
    /\ authorized \in BOOLEAN
    /\ unsafeReset = FALSE
    /\ worker = "running" /\ slot = TRUE /\ sealed = FALSE
    /\ locked \in BOOLEAN /\ completionRaises \in BOOLEAN /\ result = "none"

Valid == managed /\ shape = "absent-safe" /\ authorized
Intent ==
    /\ phase = "staged" /\ Valid
    /\ phase' = "reload" /\ reloaded' = FALSE
    /\ UNCHANGED <<version, unitState, managed, shape, authorized, unsafeReset,
                    worker, slot, sealed, locked, completionRaises, result>>
Reload ==
    /\ phase = "reload" /\ Valid /\ ~reloaded
    /\ reloaded' = TRUE
    /\ UNCHANGED <<version, phase, unitState, managed, shape, authorized,
                    unsafeReset, worker, slot, sealed, locked, completionRaises, result>>
Reset ==
    /\ phase = "reload" /\ reloaded /\ unitState = "failed"
    /\ (Valid \/ BadReset)
    /\ unitState' = "inactive" /\ unsafeReset' = ~Valid
    /\ UNCHANGED <<version, phase, reloaded, managed, shape, authorized,
                    worker, slot, sealed, locked, completionRaises, result>>
Retire ==
    /\ phase = "reload" /\ reloaded /\ Valid /\ unitState = "inactive"
    /\ phase' = "retire"
    /\ UNCHANGED <<version, reloaded, unitState, managed, shape, authorized,
                    unsafeReset, worker, slot, sealed, locked, completionRaises, result>>
Unlink ==
    /\ phase = "retire" /\ Valid /\ unitState = "inactive"
    /\ phase' = "absent"
    /\ UNCHANGED <<version, reloaded, unitState, managed, shape, authorized,
                    unsafeReset, worker, slot, sealed, locked, completionRaises, result>>
Crash ==
    /\ phase = "reload" /\ reloaded /\ reloaded' = FALSE
    /\ UNCHANGED <<version, phase, unitState, managed, shape, authorized,
                    unsafeReset, worker, slot, sealed, locked, completionRaises, result>>
Return ==
    /\ worker = "running" /\ worker' = "returned"
    /\ UNCHANGED <<version, phase, reloaded, unitState, managed, shape, authorized,
                    unsafeReset, slot, sealed, locked, completionRaises, result>>
Lock ==
    /\ worker # "settled" /\ ~locked /\ locked' = TRUE
    /\ UNCHANGED <<version, phase, reloaded, unitState, managed, shape, authorized,
                    unsafeReset, worker, slot, sealed, completionRaises, result>>
Unlock ==
    /\ locked /\ locked' = FALSE
    /\ UNCHANGED <<version, phase, reloaded, unitState, managed, shape, authorized,
                    unsafeReset, worker, slot, sealed, completionRaises, result>>
Settle ==
    /\ worker = "returned" /\ ~locked
    /\ worker' = "settled" /\ slot' = BadSettlement /\ sealed' = TRUE
    /\ result' = IF completionRaises THEN "uncertain" ELSE "observed"
    /\ UNCHANGED <<version, phase, reloaded, unitState, managed, shape, authorized,
                    unsafeReset, locked, completionRaises>>
Next == Intent \/ Reload \/ Reset \/ Retire \/ Unlink \/ Crash
        \/ Return \/ Lock \/ Unlock \/ Settle
TypeOK ==
    /\ version \in {1, 2}
    /\ phase \in {"staged", "reload", "retire", "absent"}
    /\ unitState \in {"failed", "inactive"}
    /\ worker \in {"running", "returned", "settled"}
    /\ result \in {"none", "uncertain", "observed"}
ResetBound == ~unsafeReset
RetiredInactive == phase \in {"retire", "absent"} => unitState = "inactive" /\ Valid
SettledReleased == worker = "settled" => ~slot /\ sealed
NoSyntheticSuccess == completionRaises => result # "observed"
WorkerSettles == worker = "returned" ~> worker = "settled"
Spec == Init /\ [][Next]_vars /\ WF_vars(Unlock) /\ SF_vars(Settle)
=============================================================================
