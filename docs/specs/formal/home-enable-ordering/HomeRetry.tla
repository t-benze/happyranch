----------------------------- MODULE HomeRetry -----------------------------
EXTENDS Naturals
VARIABLE s
Init == s = [receipt |-> "prepared", txn |-> "none", policy |-> "expired",
             units |-> "inactive", serving |-> FALSE, enabled |-> FALSE,
             trust |-> FALSE, revoked |-> FALSE, action |-> "idle",
             ack |-> FALSE, stopped |-> FALSE, mints |-> 0, readyMint |-> 0,
             attempts |-> 0]
Renew == /\ s.action = "idle" /\ s.receipt = "prepared" /\ s.txn = "none"
         /\ s' = [s EXCEPT !.policy = "current"]
Enable == /\ s.action = "idle" /\ s.receipt = "prepared" /\ s.txn = "none"
          /\ s.policy = "current" /\ s.mints < 2
          /\ s' = [s EXCEPT !.receipt = "ready", !.txn = "joined",
                   !.units = "active", !.serving = TRUE, !.enabled = TRUE,
                   !.trust = TRUE, !.revoked = FALSE,
                   !.mints = @ + 1, !.readyMint = s.mints + 1]
Interrupt == /\ s.action = "idle" /\ s.receipt = "prepared" /\ s.txn = "none"
             /\ \E prefix \in {"no-mint", "live-key", "expired-key"}:
                  s' = [s EXCEPT !.receipt = "failed", !.txn = prefix]
WaitWindow == /\ s.action = "idle" /\ s.receipt = "failed" /\ s.txn = "live-key"
              /\ s' = [s EXCEPT !.txn = "expired-key"]
Recover == /\ s.action = "idle" /\ s.receipt = "failed"
           /\ s.txn \in {"no-mint", "expired-key"}
           /\ s' = [s EXCEPT !.receipt = "prepared", !.txn = "none"]
RuntimeDown == /\ s.action = "idle" /\ s.receipt = "ready"
               /\ \E state \in {"inactive", "activating", "deactivating"}, serves \in BOOLEAN:
                    s' = [s EXCEPT !.units = state, !.serving = serves]
OrdinaryStart == /\ s.action = "idle" /\ s.receipt = "ready" /\ s.txn = "joined"
                 /\ s.enabled /\ s.trust /\ s.policy = "current"
                 /\ s' = [s EXCEPT !.units = "active", !.serving = TRUE]
BeginOff == /\ s.action = "idle" /\ s.receipt = "ready" /\ s.attempts < 2
            /\ s' = [s EXCEPT !.action = "revoke", !.ack = FALSE,
                     !.stopped = FALSE, !.attempts = @ + 1]
Revoke == /\ s.action = "revoke" /\ s.serving
          /\ s' = [s EXCEPT !.action = "stop", !.ack = TRUE, !.revoked = TRUE]
RevokeUnavailable == /\ s.action = "revoke"
                     /\ s' = [s EXCEPT !.action = "stop", !.ack = FALSE]
Stop == /\ s.action = "stop"
        /\ s' = [s EXCEPT !.action = "cleanup", !.stopped = TRUE,
                 !.units = "inactive", !.serving = FALSE, !.enabled = FALSE]
StopFailed == /\ s.action = "stop"
              /\ s' = [s EXCEPT !.action = "idle", !.units = "activating"]
Cleanup == /\ s.action = "cleanup" /\ s.stopped
           /\ s' = [s EXCEPT !.receipt = IF s.ack THEN "off" ELSE "partial",
                    !.txn = IF s.ack THEN "none" ELSE s.txn,
                    !.trust = ~s.ack, !.action = "idle"]
Reenable == /\ s.action = "idle" /\ s.receipt = "off" /\ ~s.trust
            /\ s' = [s EXCEPT !.receipt = "prepared", !.policy = "expired"]
Next == Renew \/ Enable \/ Interrupt \/ WaitWindow \/ Recover \/ RuntimeDown
        \/ OrdinaryStart \/ BeginOff \/ Revoke \/ RevokeUnavailable \/ Stop
        \/ StopFailed \/ Cleanup \/ Reenable
Spec == Init /\ [][Next]_s
NoReadyMint == s.receipt = "ready" => s.mints = s.readyMint
OffRequiresSameInvocationRevoke == s.receipt = "off" => s.ack /\ s.stopped /\ ~s.trust
PartialRetainsTrust == s.receipt = "partial" => s.trust /\ ~s.ack
NoReadyAfterBadPolicy == s.receipt = "ready" => s.policy = "current"
=============================================================================
