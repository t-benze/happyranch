----------------------------- MODULE OrgSecrets -----------------------------
EXTENDS Naturals, TLC
CONSTANTS UnsafeLaunch, LoseDiscovery
VARIABLES configured, key, pending, terminal, otherHold, queued, launches,
          badDelivery, restarted
vars == <<configured, key, pending, terminal, otherHold, queued, launches,
          badDelivery, restarted>>
Init == /\ configured \in BOOLEAN
        /\ key = "locked" /\ pending = TRUE /\ terminal = FALSE
        /\ otherHold \in BOOLEAN /\ queued = FALSE /\ launches = 0
        /\ badDelivery = FALSE /\ restarted = FALSE
Ready == ~configured \/ key = "available"
Unlock == /\ key = "locked" /\ key' = "available"
          /\ UNCHANGED <<configured, pending, terminal, otherHold, queued,
                         launches, badDelivery, restarted>>
DeleteLast == /\ configured /\ configured' = FALSE
              /\ UNCHANGED <<key, pending, terminal, otherHold, queued,
                             launches, badDelivery, restarted>>
Release == /\ otherHold /\ otherHold' = FALSE
           /\ UNCHANGED <<configured, key, pending, terminal, queued,
                          launches, badDelivery, restarted>>
Restart == /\ ~restarted /\ restarted' = TRUE /\ queued' = FALSE
           /\ key' = "locked"
           /\ UNCHANGED <<configured, pending, terminal, otherHold,
                          launches, badDelivery>>
Discover == /\ ~LoseDiscovery /\ pending /\ ~terminal /\ (Ready \/ UnsafeLaunch) /\ ~queued
            /\ queued' = TRUE
            /\ UNCHANGED <<configured, key, pending, terminal, otherHold,
                           launches, badDelivery, restarted>>
Launch == /\ pending /\ ~terminal /\ queued /\ ~otherHold
          /\ (Ready \/ UnsafeLaunch)
          /\ pending' = FALSE /\ queued' = FALSE /\ launches' = launches + 1
          /\ badDelivery' = ~Ready
          /\ UNCHANGED <<configured, key, terminal, otherHold, restarted>>
Cancel == /\ pending /\ ~terminal /\ terminal' = TRUE /\ pending' = FALSE
          /\ UNCHANGED <<configured, key, otherHold, queued, launches,
                         badDelivery, restarted>>
Finish == /\ ~pending /\ launches = 1 /\ ~terminal /\ terminal' = TRUE
          /\ UNCHANGED <<configured, key, pending, otherHold, queued,
                         launches, badDelivery, restarted>>
Next == Unlock \/ DeleteLast \/ Release \/ Restart \/ Discover \/ Launch \/ Cancel \/ Finish
Spec == Init /\ [][Next]_vars /\ WF_vars(Unlock) /\ WF_vars(Release)
        /\ WF_vars(Discover) /\ WF_vars(Launch) /\ WF_vars(Finish)
TypeOK == /\ configured \in BOOLEAN /\ key \in {"locked", "available"}
          /\ pending \in BOOLEAN /\ terminal \in BOOLEAN /\ otherHold \in BOOLEAN
          /\ queued \in BOOLEAN /\ launches \in 0..1 /\ badDelivery \in BOOLEAN
          /\ restarted \in BOOLEAN
NoMissingSecrets == ~badDelivery
OneLaunch == launches <= 1
TerminalAbsorbing == terminal => ~pending
Progress == <>(launches = 1 \/ terminal)
=============================================================================
