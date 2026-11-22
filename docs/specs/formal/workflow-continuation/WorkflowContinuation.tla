------------------------ MODULE WorkflowContinuation ------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANTS Runs, Capacity, ReturnCompletesSource
VARIABLES phase, batch, results, receipts, released, closed, recovered,
          carrierDone, inbox, consumed, allowed, ordinary, sourceDone, genuineDone
vars == <<phase, batch, results, receipts, released, closed, recovered,
          carrierDone, inbox, consumed, allowed, ordinary, sourceDone, genuineDone>>
Symmetry == Permutations(Runs)
Active == {r \in Runs : phase[r] \notin {"absent", "finished"}}
Members(b) == {r \in Runs : batch[r] = b}
Init == /\ phase = [r \in Runs |-> "absent"]
        /\ batch = [r \in Runs |-> 0]
        /\ results = {} /\ receipts = {} /\ released = {} /\ closed = {}
        /\ recovered = {} /\ carrierDone = {} /\ inbox = {} /\ consumed = {}
        /\ allowed = TRUE /\ ordinary = TRUE /\ sourceDone = FALSE /\ genuineDone = FALSE
Admit(S,b) == /\ ~sourceDone /\ allowed /\ b \in (1..2) \ results
              /\ S \subseteq {r \in Runs : phase[r] = "absent"}
              /\ Cardinality(S) \in 1..8 /\ Cardinality(Active \cup S) <= Capacity
              /\ phase' = [r \in Runs |-> IF r \in S THEN "queued" ELSE phase[r]]
              /\ batch' = [r \in Runs |-> IF r \in S THEN b ELSE batch[r]]
              /\ results' = results \cup {b} /\ receipts' = receipts \cup {b}
              /\ UNCHANGED <<released,closed,recovered,carrierDone,inbox,consumed,
                              allowed,ordinary,sourceDone,genuineDone>>
Settle(b) == /\ b \in results \ released /\ released' = released \cup {b}
             /\ UNCHANGED <<phase,batch,results,receipts,closed,recovered,carrierDone,
                             inbox,consumed,allowed,ordinary,sourceDone,genuineDone>>
Launch(r) == /\ phase[r] = "queued" /\ batch[r] \in released
             /\ phase' = [phase EXCEPT ![r] = "running"]
             /\ UNCHANGED <<batch,results,receipts,released,closed,recovered,carrierDone,
                             inbox,consumed,allowed,ordinary,sourceDone,genuineDone>>
Close(r) == /\ phase[r] \in {"running","cancelling"} /\ r \notin closed
            /\ closed' = closed \cup {r}
            /\ UNCHANGED <<phase,batch,results,receipts,released,recovered,carrierDone,
                            inbox,consumed,allowed,ordinary,sourceDone,genuineDone>>
Hold(r) == /\ phase[r] = "running" /\ r \in closed
           /\ phase' = [phase EXCEPT ![r] = "held"] /\ inbox' = inbox \cup {r}
           /\ UNCHANGED <<batch,results,receipts,released,closed,recovered,carrierDone,
                           consumed,allowed,ordinary,sourceDone,genuineDone>>
Recover(r) == /\ phase[r] = "held" /\ r \in closed /\ r \notin recovered
              /\ phase' = [phase EXCEPT ![r] = "queued"]
              /\ recovered' = recovered \cup {r} /\ closed' = closed \ {r}
              /\ UNCHANGED <<batch,results,receipts,released,carrierDone,inbox,consumed,
                              allowed,ordinary,sourceDone,genuineDone>>
Cancel(r) == /\ phase[r] \in {"queued","held","running"}
             /\ LET immediate == phase[r] \in {"queued","held"} IN
                /\ phase' = [phase EXCEPT ![r] = IF immediate THEN "finished" ELSE "cancelling"]
                /\ carrierDone' = IF immediate THEN carrierDone \cup {r} ELSE carrierDone
                /\ inbox' = IF immediate THEN inbox \cup {r} ELSE inbox
             /\ UNCHANGED <<batch,results,receipts,released,closed,recovered,consumed,
                             allowed,ordinary,sourceDone,genuineDone>>
Return(r) == /\ phase[r] \in {"running","cancelling"} /\ r \in closed
             /\ phase' = [phase EXCEPT ![r] = "finished"]
             /\ carrierDone' = carrierDone \cup {r} /\ inbox' = inbox \cup {r}
             /\ sourceDone' = IF ReturnCompletesSource THEN TRUE ELSE sourceDone
             /\ UNCHANGED <<batch,results,receipts,released,closed,recovered,consumed,
                             allowed,ordinary,genuineDone>>
Drain == /\ consumed # inbox /\ consumed' = inbox
         /\ UNCHANGED <<phase,batch,results,receipts,released,closed,recovered,carrierDone,
                         inbox,allowed,ordinary,sourceDone,genuineDone>>
OrdinaryDone == /\ ordinary /\ ordinary' = FALSE
                /\ UNCHANGED <<phase,batch,results,receipts,released,closed,recovered,carrierDone,
                                inbox,consumed,allowed,sourceDone,genuineDone>>
Done == /\ ~sourceDone /\ ~ordinary /\ Active = {} /\ results # {} /\ consumed = inbox
        /\ sourceDone' = TRUE /\ genuineDone' = TRUE
        /\ UNCHANGED <<phase,batch,results,receipts,released,closed,recovered,carrierDone,
                        inbox,consumed,allowed,ordinary>>
Disable == /\ allowed /\ allowed' = FALSE
           /\ UNCHANGED <<phase,batch,results,receipts,released,closed,recovered,carrierDone,
                           inbox,consumed,ordinary,sourceDone,genuineDone>>
Replay == UNCHANGED vars
Next == (\E S \in SUBSET Runs, b \in 1..2 : Admit(S,b)) \/ (\E b \in 1..2 : Settle(b))
        \/ (\E r \in Runs : Launch(r) \/ Close(r) \/ Hold(r) \/ Recover(r) \/ Cancel(r) \/ Return(r))
        \/ Drain \/ OrdinaryDone \/ Done \/ Disable \/ Replay
TypeOK == /\ phase \in [Runs -> {"absent","queued","running","held","cancelling","finished"}]
          /\ batch \in [Runs -> 0..2] /\ results \subseteq 1..2 /\ receipts \subseteq 1..2
          /\ released \subseteq results /\ closed \subseteq Runs /\ recovered \subseteq Runs
          /\ carrierDone \subseteq Runs /\ inbox \subseteq Runs /\ consumed \subseteq inbox
          /\ allowed \in BOOLEAN /\ ordinary \in BOOLEAN /\ sourceDone \in BOOLEAN /\ genuineDone \in BOOLEAN
AtomicBatch == results = receipts /\ \A r \in Runs : (phase[r] = "absent") = (batch[r] = 0)
               /\ \A b \in results : Members(b) # {}
CapacitySafe == Cardinality(Active) <= Capacity
CarrierDrained == carrierDone = {r \in Runs : phase[r] = "finished"}
OriginalSourceRelease == \A r \in Runs : phase[r] \in {"running","held","cancelling"} => batch[r] \in released
SourceFinalOnly == sourceDone => genuineDone /\ ~ordinary /\ Active = {} /\ consumed = inbox
Spec == Init /\ [][Next]_vars
=============================================================================
