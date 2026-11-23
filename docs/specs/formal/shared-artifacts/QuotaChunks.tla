------------------------ MODULE QuotaChunks ------------------------
EXTENDS Naturals, Sequences, TLC
CONSTANT BadPartialCommit
Slots == 1..2
VARIABLES sequence, latched, transaction, staged, visible, receipt, audits,
          replayOf, replaySeqs
vars == <<sequence,latched,transaction,staged,visible,receipt,audits,replayOf,replaySeqs>>
Seqs(s) == <<3*s-2,3*s-1,3*s>>
Init == /\ sequence = 0 /\ latched = FALSE /\ transaction = FALSE /\ staged = 0
        /\ visible = [s \in Slots |-> 0] /\ receipt = [s \in Slots |-> FALSE]
        /\ audits = [s \in Slots |-> 0] /\ replayOf = 0 /\ replaySeqs = <<>>
Begin == /\ ~latched /\ ~transaction /\ sequence < 2
         /\ transaction' = TRUE /\ staged' = 0
         /\ UNCHANGED <<sequence,latched,visible,receipt,audits,replayOf,replaySeqs>>
Stage == /\ transaction /\ staged < 3 /\ staged' = staged+1
         /\ visible' = IF BadPartialCommit THEN [visible EXCEPT ![sequence+1] = staged+1] ELSE visible
         /\ UNCHANGED <<sequence,latched,transaction,receipt,audits,replayOf,replaySeqs>>
Commit == /\ transaction /\ staged = 3
          /\ visible' = [visible EXCEPT ![sequence+1] = 3]
          /\ receipt' = [receipt EXCEPT ![sequence+1] = TRUE]
          /\ audits' = [audits EXCEPT ![sequence+1] = @+1]
          /\ sequence' = sequence+1 /\ latched' = TRUE
          /\ transaction' = FALSE /\ staged' = 0
          /\ UNCHANGED <<replayOf,replaySeqs>>
Crash == /\ transaction /\ transaction' = FALSE /\ staged' = 0
         /\ UNCHANGED <<sequence,latched,visible,receipt,audits,replayOf,replaySeqs>>
Below == /\ ~transaction /\ latched' = FALSE
         /\ UNCHANGED <<sequence,transaction,staged,visible,receipt,audits,replayOf,replaySeqs>>
Replay(s) == /\ receipt[s] /\ replayOf' = s /\ replaySeqs' = Seqs(s)
             /\ UNCHANGED <<sequence,latched,transaction,staged,visible,receipt,audits>>
Next == Begin \/ Stage \/ Commit \/ Crash \/ Below \/ (\E s \in Slots: Replay(s))
TypeOK == /\ sequence \in 0..2 /\ staged \in 0..3
          /\ visible \in [Slots -> 0..3] /\ audits \in [Slots -> 0..1]
AllOrNothing == \A s \in Slots: visible[s] = IF receipt[s] THEN 3 ELSE 0
OneAudit == \A s \in Slots: audits[s] = IF receipt[s] THEN 1 ELSE 0
StableReplay == replayOf # 0 => replaySeqs = Seqs(replayOf)
Spec == Init /\ [][Next]_vars
=============================================================================
