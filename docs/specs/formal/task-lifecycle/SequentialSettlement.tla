------------------------- MODULE SequentialSettlement -------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANTS Harden, TrustUnknown
VARIABLES boundary, phase, native, intent, closed, unknown, heldEvents, inbox,
          result, originalResult, launches, originalLaunches, allowance, authority
vars == <<boundary, phase, native, intent, closed, unknown, heldEvents, inbox,
          result, originalResult, launches, originalLaunches, allowance, authority>>
Positive == {"P0", "P1"}
Uncertain == {"P2missing", "U0", "U1", "U2", "U3", "U4", "U5"}
Terminal == {"TBlocked", "TFailed", "TMissing", "TInvalid", "TFallback"}
Init == /\ boundary \in Positive \cup Uncertain \cup Terminal
        /\ phase = IF boundary \in Uncertain THEN "unknown" ELSE "original"
        /\ native = (boundary # "P0")
        /\ intent = FALSE
        /\ closed = (boundary \in Terminal)
        /\ unknown = (boundary \in Uncertain)
        /\ heldEvents = 0 /\ inbox = 0
        /\ result = IF boundary \in {"TBlocked", "TFailed", "TInvalid", "TFallback"} THEN 1 ELSE 0
        /\ originalResult = result
        /\ launches = IF boundary = "U5" THEN 1 ELSE 0
        /\ originalLaunches = launches
        /\ allowance = 2
        /\ authority \in BOOLEAN
Intent == /\ phase = "original" /\ boundary \in Positive
          /\ phase' = "intent" /\ intent' = TRUE
          /\ UNCHANGED <<boundary, native, closed, unknown, heldEvents, inbox,
                         result, originalResult, launches, originalLaunches, allowance, authority>>
RemoveNative == /\ phase = "intent" /\ intent
                /\ phase' = "removed" /\ native' = FALSE
                /\ UNCHANGED <<boundary, intent, closed, unknown, heldEvents, inbox,
                               result, originalResult, launches, originalLaunches, allowance, authority>>
Receipt == /\ phase = "removed" /\ intent /\ ~unknown /\ ~native
           /\ phase' = "closed" /\ closed' = TRUE
           /\ UNCHANGED <<boundary, native, intent, unknown, heldEvents, inbox,
                          result, originalResult, launches, originalLaunches, allowance, authority>>
Interrupted == /\ phase \in {"intent", "removed"}
               /\ phase' = "unknown" /\ unknown' = TRUE
               /\ UNCHANGED <<boundary, native, intent, closed, heldEvents, inbox,
                              result, originalResult, launches, originalLaunches, allowance, authority>>
Settle == /\ phase \in {"original", "closed"} /\ closed /\ ~unknown /\ authority
          /\ (Harden \/ boundary \notin {"TBlocked", "TFailed", "TMissing", "TFallback"})
          /\ phase' = "held" /\ heldEvents' = 1 /\ inbox' = 1
          /\ UNCHANGED <<boundary, native, intent, closed, unknown, result, originalResult,
                         launches, originalLaunches, allowance, authority>>
BadSettlement == /\ TrustUnknown /\ phase = "unknown" /\ authority
                 /\ phase' = "held" /\ heldEvents' = 1 /\ inbox' = 1
                 /\ UNCHANGED <<boundary, native, intent, closed, unknown, result, originalResult,
                                launches, originalLaunches, allowance, authority>>
AuthorityReady == /\ ~authority /\ authority' = TRUE
                  /\ UNCHANGED <<boundary, phase, native, intent, closed, unknown, heldEvents,
                                 inbox, result, originalResult, launches, originalLaunches, allowance>>
Replay == UNCHANGED vars
Next == Intent \/ RemoveNative \/ Receipt \/ Interrupted \/ Settle \/ BadSettlement \/ AuthorityReady \/ Replay
Spec == Init /\ [][Next]_vars /\ WF_vars(Intent) /\ WF_vars(RemoveNative)
        /\ WF_vars(Receipt) /\ WF_vars(Settle) /\ WF_vars(AuthorityReady)
IntentBeforeRemoval == phase \in {"removed", "closed"} => intent
NoFalseHold == heldEvents = 1 => closed /\ ~unknown
AtomicWake == heldEvents = inbox /\ heldEvents \in {0, 1}
OriginalTruth == result = originalResult /\ launches = originalLaunches /\ allowance = 2
StickyUnknown == unknown => ~closed
Completion == <> (phase \in {"held", "unknown"})
=============================================================================
