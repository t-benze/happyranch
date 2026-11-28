-------------------------- MODULE AdmissionRefusal --------------------------
EXTENDS Naturals, TLC
CONSTANTS PoisonRefusal, ErasePrior
VARIABLES phase, priorUnknown, unknown, hostCommitted, attemptOpen, launched,
          refused, ownerLive, laterQueued, recovered
vars == <<phase, priorUnknown, unknown, hostCommitted, attemptOpen, launched,
          refused, ownerLive, laterQueued, recovered>>
Init == /\ phase = "wait" /\ priorUnknown \in BOOLEAN /\ unknown = priorUnknown
        /\ hostCommitted = FALSE /\ attemptOpen = FALSE /\ launched = FALSE
        /\ refused = FALSE /\ ownerLive = TRUE
        /\ laterQueued = FALSE /\ recovered = FALSE
Prepare == /\ phase = "wait" /\ phase' = "prepared"
           /\ UNCHANGED <<priorUnknown, unknown, hostCommitted, attemptOpen, launched,
                          refused, ownerLive, laterQueued, recovered>>
HostPassthrough == /\ phase = "prepared" /\ ~priorUnknown
                   /\ phase' = "host" /\ hostCommitted' = TRUE /\ attemptOpen' = TRUE
                   /\ UNCHANGED <<priorUnknown, unknown, launched, refused,
                                  ownerLive, laterQueued, recovered>>
Refuse == /\ phase \in {"prepared", "host"} /\ ~launched
          /\ phase' = "refused" /\ refused' = TRUE
          /\ unknown' = IF ErasePrior THEN FALSE ELSE unknown \/ PoisonRefusal
          /\ UNCHANGED <<priorUnknown, hostCommitted, attemptOpen, launched,
                         ownerLive, laterQueued, recovered>>
Settle == /\ phase = "refused" /\ phase' = "settled"
          /\ attemptOpen' = FALSE /\ ownerLive' = unknown
          /\ UNCHANGED <<priorUnknown, unknown, hostCommitted, launched,
                         refused, laterQueued, recovered>>
Arrival == /\ phase = "settled" /\ ~laterQueued /\ laterQueued' = TRUE
           /\ UNCHANGED <<phase, priorUnknown, unknown, hostCommitted,
                          attemptOpen, launched, refused, ownerLive, recovered>>
Recover == /\ laterQueued /\ ~unknown /\ recovered' = TRUE
           /\ UNCHANGED <<phase, priorUnknown, unknown, hostCommitted,
                          attemptOpen, launched, refused, ownerLive, laterQueued>>
Next == Prepare \/ HostPassthrough \/ Refuse \/ Settle \/ Arrival \/ Recover
Spec == Init /\ [][Next]_vars /\ WF_vars(Prepare) /\ WF_vars(Refuse)
        /\ WF_vars(Settle) /\ WF_vars(Arrival) /\ WF_vars(Recover)
NoInventedUnknown == ~priorUnknown => ~unknown
PriorUnknownPreserved == priorUnknown => unknown
NoNativeLaunch == ~launched
CleanSettlement == (phase = "settled" /\ ~unknown) => (~ownerLive /\ ~attemptOpen)
Recovery == ~priorUnknown => <>recovered
=============================================================================
