-------------------- MODULE NeverLaunchedCancelClose --------------------
EXTENDS Naturals, FiniteSets
CONSTANT EnforceProof
VARIABLES run, task, admitted, evidence, journal, cas, receipts, audits, events,
          humanClosed, replayed
vars == <<run, task, admitted, evidence, journal, cas, receipts, audits, events,
          humanClosed, replayed>>
Keys == {"first", "second"}
Init == /\ run = "open" /\ task = "pending"
        /\ admitted = FALSE /\ evidence \in {FALSE, TRUE}
        /\ journal = 256 /\ cas = 0 /\ receipts = {} /\ audits = 0
        /\ events = 0 /\ humanClosed = FALSE /\ replayed = {}
Admit == /\ run = "open" /\ task = "pending"
         /\ admitted' = TRUE /\ evidence' = TRUE /\ task' = "running"
         /\ UNCHANGED <<run, journal, cas, receipts, audits, events, humanClosed, replayed>>
Cancel == /\ run = "open" /\ run' = "cancelling" /\ task' = "cancelled"
          /\ cas' = 1
          /\ UNCHANGED <<admitted, evidence, journal, receipts, audits, events, humanClosed, replayed>>
HumanClose(k) == /\ run = "cancelling" /\ task = "cancelled"
                 /\ (~EnforceProof \/ (~admitted /\ ~evidence))
                 /\ run' = "finished" /\ cas' = cas + 1
                 /\ receipts' = {<<k, cas, cas+1>>} /\ audits' = audits+1
                 /\ events' = events+1 /\ humanClosed' = TRUE
                 /\ UNCHANGED <<task, admitted, evidence, journal, replayed>>
EngineFinish == /\ run = "cancelling" /\ admitted /\ evidence
                /\ run' = "finished" /\ cas' = cas+1 /\ events' = events+1
                /\ UNCHANGED <<task, admitted, evidence, journal, receipts, audits, humanClosed, replayed>>
Replay(k) == /\ \E r \in receipts: r[1] = k
             /\ replayed' = replayed \cup {k}
             /\ UNCHANGED <<run, task, admitted, evidence, journal, cas, receipts, audits, events, humanClosed>>
Next == Admit \/ Cancel \/ EngineFinish \/ (\E k \in Keys: HumanClose(k) \/ Replay(k))
Spec == Init /\ [][Next]_vars
SafeHumanClose == humanClosed => (~admitted /\ ~evidence /\ task = "cancelled")
JournalUnchanged == journal = 256
OneFinish == events <= 1 /\ audits <= 1 /\ Cardinality(receipts) <= 1
ReceiptStable == \A r \in receipts: r[2] = 1 /\ r[3] = 2 /\ cas = r[3]
ReservationReleased == (run = "finished") <=> (events = 1)
=============================================================================
