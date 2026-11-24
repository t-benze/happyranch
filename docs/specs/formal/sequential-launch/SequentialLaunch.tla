-------------------------- MODULE SequentialLaunch --------------------------
EXTENDS Naturals, TLC
CONSTANT OldPredicate
VARIABLES visit, progress, sid, identity, cancelled, rendered, launched, authorized
vars == <<visit, progress, sid, identity, cancelled, rendered, launched, authorized>>
Init == /\ visit = "queued" /\ progress = "queued" /\ sid = "none"
        /\ identity = TRUE /\ cancelled = FALSE /\ rendered = FALSE
        /\ launched = FALSE /\ authorized = FALSE
Queued == visit = "queued" /\ progress = "queued" /\ sid = "none"
Bound == visit = "running" /\ progress = "executing" /\ sid = "mine"
Render == /\ identity /\ ~cancelled /\ ~rendered
          /\ IF OldPredicate THEN Bound ELSE (Queued \/ Bound)
          /\ rendered' = TRUE
          /\ UNCHANGED <<visit, progress, sid, identity, cancelled, launched, authorized>>
Commit == /\ rendered /\ Queued /\ identity /\ ~cancelled
          /\ visit' = "running" /\ progress' = "executing" /\ sid' = "mine"
          /\ launched' = TRUE /\ authorized' = identity /\ ~cancelled
          /\ UNCHANGED <<identity, cancelled, rendered>>
Cancel == /\ ~cancelled /\ cancelled' = TRUE
          /\ UNCHANGED <<visit, progress, sid, identity, rendered, launched, authorized>>
Drift == /\ identity /\ ~launched /\ identity' = FALSE
         /\ UNCHANGED <<visit, progress, sid, cancelled, rendered, launched, authorized>>
Restart == UNCHANGED vars
Next == Render \/ Commit \/ Cancel \/ Drift \/ Restart
TypeOK == /\ visit \in {"queued", "running"} /\ progress \in {"queued", "executing"}
          /\ sid \in {"none", "mine"}
          /\ identity \in BOOLEAN /\ cancelled \in BOOLEAN /\ rendered \in BOOLEAN
          /\ launched \in BOOLEAN /\ authorized \in BOOLEAN
Binding == Queued \/ Bound
LaunchFence == launched => (Bound /\ rendered /\ authorized)
Progress == <> (launched \/ cancelled \/ ~identity)
Spec == Init /\ [][Next]_vars /\ WF_vars(Render) /\ WF_vars(Commit)
=============================================================================
