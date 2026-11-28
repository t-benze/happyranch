----------------------------- MODULE OrgSecrets -----------------------------
EXTENDS Naturals, TLC
CONSTANTS UnsafeLaunch, LoseDiscovery, StaleSnapshot
VARIABLES configured, key, pending, terminal, otherHold, queued, launches,
          badDelivery, restarted, revision, queuedRevision, badSnapshot
vars == <<configured, key, pending, terminal, otherHold, queued, launches,
          badDelivery, restarted, revision, queuedRevision, badSnapshot>>
Init == /\ configured \in BOOLEAN
        /\ key = "locked" /\ pending = TRUE /\ terminal = FALSE
        /\ otherHold \in BOOLEAN /\ queued = FALSE /\ launches = 0
        /\ badDelivery = FALSE /\ restarted = FALSE
        /\ revision = 0 /\ queuedRevision = 0 /\ badSnapshot = FALSE
Ready == ~configured \/ key = "available"
Unlock == /\ key = "locked" /\ key' = "available"
          /\ UNCHANGED <<configured, pending, terminal, otherHold, queued,
                         launches, badDelivery, restarted, revision, queuedRevision, badSnapshot>>
DeleteLast == /\ configured /\ configured' = FALSE
              /\ UNCHANGED <<key, pending, terminal, otherHold, queued,
                             launches, badDelivery, restarted, revision, queuedRevision, badSnapshot>>
Release == /\ otherHold /\ otherHold' = FALSE
           /\ UNCHANGED <<configured, key, pending, terminal, queued,
                          launches, badDelivery, restarted, revision, queuedRevision, badSnapshot>>
Restart == /\ ~restarted /\ restarted' = TRUE /\ queued' = FALSE
           /\ key' = "locked"
           /\ UNCHANGED <<configured, pending, terminal, otherHold,
                          launches, badDelivery, revision, queuedRevision, badSnapshot>>
Change == /\ pending /\ revision < 2 /\ revision' = revision + 1
          /\ configured' \in BOOLEAN
          /\ UNCHANGED <<key, pending, terminal, otherHold, queued, launches,
                         badDelivery, restarted, queuedRevision, badSnapshot>>
Discover == /\ ~LoseDiscovery /\ pending /\ ~terminal /\ (Ready \/ UnsafeLaunch) /\ ~queued
            /\ queued' = TRUE /\ queuedRevision' = revision
            /\ UNCHANGED <<configured, key, pending, terminal, otherHold,
                           launches, badDelivery, restarted, revision, badSnapshot>>
Launch == /\ pending /\ ~terminal /\ queued /\ ~otherHold
          /\ (Ready \/ UnsafeLaunch)
          /\ pending' = FALSE /\ queued' = FALSE /\ launches' = launches + 1
          /\ badDelivery' = ~Ready
          /\ badSnapshot' = (StaleSnapshot /\ queuedRevision # revision)
          /\ UNCHANGED <<configured, key, terminal, otherHold, restarted, revision, queuedRevision>>
Cancel == /\ pending /\ ~terminal /\ terminal' = TRUE /\ pending' = FALSE
          /\ UNCHANGED <<configured, key, otherHold, queued, launches,
                         badDelivery, restarted, revision, queuedRevision, badSnapshot>>
Finish == /\ ~pending /\ launches = 1 /\ ~terminal /\ terminal' = TRUE
          /\ UNCHANGED <<configured, key, pending, otherHold, queued,
                         launches, badDelivery, restarted, revision, queuedRevision, badSnapshot>>
Next == Change \/ Unlock \/ DeleteLast \/ Release \/ Restart \/ Discover \/ Launch \/ Cancel \/ Finish
Spec == Init /\ [][Next]_vars /\ WF_vars(Unlock) /\ WF_vars(Release)
        /\ WF_vars(Discover) /\ WF_vars(Launch) /\ WF_vars(Finish)
TypeOK == /\ configured \in BOOLEAN /\ key \in {"locked", "available"}
          /\ pending \in BOOLEAN /\ terminal \in BOOLEAN /\ otherHold \in BOOLEAN
          /\ queued \in BOOLEAN /\ launches \in 0..1 /\ badDelivery \in BOOLEAN
          /\ restarted \in BOOLEAN /\ revision \in 0..2
          /\ queuedRevision \in 0..2 /\ badSnapshot \in BOOLEAN
NoMissingSecrets == ~badDelivery
NoStaleSnapshot == ~badSnapshot
OneLaunch == launches <= 1
TerminalAbsorbing == terminal => ~pending
Progress == <>(launches = 1 \/ terminal)
=============================================================================
