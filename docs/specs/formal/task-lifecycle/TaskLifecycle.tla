--------------------------- MODULE TaskLifecycle ---------------------------
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Scenario, LegacyAutoRevisit, LegacyUnknownDeferral,
          AllowRepeatedRevisit, AllowHold, MaxRestarts

Tasks == {"root", "child", "next"}
Terminal == {"completed", "failed", "cancelled", "superseded"}
Statuses == Terminal \cup {"absent", "pending", "in_progress", "escalated"}
Blocks == {"none", "delegated", "blocked_on_job"}
Phases == {"none", "prepared", "possible_launch", "action_started", "running",
           "result_processing", "retry_deferred", "recovery_deferred",
           "pending_job", "unknown"}

VARIABLES status, block, parent, alive, journal, result, queue, woke,
          daemon, restarts, held, revisited, revisitOf
vars == <<status, block, parent, alive, journal, result, queue, woke,
          daemon, restarts, held, revisited, revisitOf>>

Children(t) == {c \in Tasks : parent[c] = t /\ status[c] # "absent"}
ChildrenDone(t) == \A c \in Children(t) : status[c] \in Terminal
Fresh == {t \in Tasks : status[t] = "absent"}
Running(t) == status[t] = "in_progress" /\ block[t] = "none"
RootDrivers == {t \in Tasks : parent[t] = "none" /\
    (status[t] = "pending" \/ Running(t) \/ t \in queue)}

Init ==
    /\ status = [t \in Tasks |->
         IF Scenario = "restart-twin" THEN
             IF t \in {"root", "child"} THEN "in_progress" ELSE "absent"
         ELSE IF t = "root" THEN
             IF Scenario = "pause" THEN "in_progress" ELSE "pending"
         ELSE "absent"]
    /\ block = [t \in Tasks |->
         IF Scenario = "restart-twin" /\ t = "root" THEN "delegated" ELSE "none"]
    /\ parent = [t \in Tasks |->
         IF Scenario = "restart-twin" /\ t = "child" THEN "root" ELSE "none"]
    /\ alive = [t \in Tasks |->
         (Scenario = "pause" /\ t = "root") \/
         (Scenario = "restart-twin" /\ t = "child")]
    /\ journal = [t \in Tasks |->
         IF Scenario = "pause" /\ t = "root" THEN "running" ELSE "none"]
    /\ revisitOf = [t \in Tasks |-> "none"]
    /\ result = [t \in Tasks |-> FALSE]
    /\ queue = IF Scenario = "normal" THEN {"root"} ELSE {}
    /\ woke = {}
    /\ daemon = TRUE /\ restarts = 0 /\ held = FALSE /\ revisited = {}

Start(t) ==
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ daemon /\ ~held /\ t \in queue /\ ~alive[t]
    /\ status[t] = "pending" \/
         (status[t] = "in_progress" /\ block[t] # "none" /\ ChildrenDone(t))
    /\ status' = [status EXCEPT ![t] = "in_progress"]
    /\ block' = [block EXCEPT ![t] = "none"]
    /\ alive' = [alive EXCEPT ![t] = TRUE]
    /\ journal' = [journal EXCEPT ![t] = "running"]
    /\ queue' = queue \ {t}
    /\ UNCHANGED <<parent, woke, daemon, restarts, held, revisited, revisitOf>>

Delegate(t, n) ==
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ daemon /\ Running(t) /\ alive[t] /\ n \in Fresh /\ ~held
    /\ status' = [status EXCEPT ![n] = "pending"]
    /\ block' = [block EXCEPT ![t] = "delegated"]
    /\ parent' = [parent EXCEPT ![n] = t]
    /\ alive' = [alive EXCEPT ![t] = FALSE]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ queue' = queue \cup {n}
    /\ UNCHANGED <<woke, daemon, restarts, held, revisited, revisitOf>>

WaitJob(t) ==
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ daemon /\ Running(t) /\ alive[t] /\ ~held
    /\ block' = [block EXCEPT ![t] = "blocked_on_job"]
    /\ alive' = [alive EXCEPT ![t] = FALSE]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ UNCHANGED <<status, parent, queue, woke, daemon, restarts, held, revisited, revisitOf>>

Wake(t) ==
    /\ daemon /\ ~held /\ status[t] = "in_progress" /\ block[t] # "none"
    /\ ChildrenDone(t) /\ t \notin queue
    /\ (~LegacyAutoRevisit \/ t # "root" \/ status["child"] # "failed"
         \/ "root" \in revisited)
    /\ queue' = queue \cup {t}
    /\ woke' = woke \cup Children(t)
    /\ UNCHANGED <<status, block, parent, alive, journal, result,
                   daemon, restarts, held, revisited, revisitOf>>

PublishResult(t) ==
    /\ Scenario = "normal"
    /\ daemon /\ Running(t) /\ alive[t] /\ ~result[t]
    /\ result' = [result EXCEPT ![t] = TRUE]
    /\ journal' = [journal EXCEPT ![t] = "result_processing"]
    /\ UNCHANGED <<status, block, parent, alive, queue, woke,
                   daemon, restarts, held, revisited, revisitOf>>

Finish(t, outcome) ==
    /\ (Scenario = "normal" \/ restarts > 0)
    /\ daemon /\ Running(t) /\ (~held \/ result[t])
    /\ outcome \in {"completed", "failed", "escalated"}
    /\ (alive[t] \/ result[t])
    /\ status' = [status EXCEPT ![t] = outcome]
    /\ alive' = [alive EXCEPT ![t] = FALSE]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ queue' = queue \ {t}
    /\ UNCHANGED <<block, parent, woke, daemon, restarts, held, revisited, revisitOf>>

\* Bounded decision feedback reruns the same NON-terminal task id.
Feedback(t) ==
    /\ Scenario = "normal" /\ daemon /\ Running(t) /\ alive[t] /\ ~held
    /\ status' = [status EXCEPT ![t] = "pending"]
    /\ alive' = [alive EXCEPT ![t] = FALSE]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ queue' = queue \cup {t}
    /\ UNCHANGED <<block, parent, woke, daemon, restarts, held, revisited, revisitOf>>

RejectPreparation(t) ==
    /\ Scenario = "normal" /\ daemon /\ status[t] = "pending" /\ ~held
    /\ status' = [status EXCEPT ![t] = "failed"]
    /\ queue' = queue \ {t}
    /\ UNCHANGED <<block, parent, alive, journal, result, woke,
                   daemon, restarts, held, revisited, revisitOf>>

Crash ==
    /\ daemon /\ restarts < MaxRestarts
    /\ daemon' = FALSE /\ restarts' = restarts + 1
    /\ alive' \in [Tasks -> BOOLEAN]
    /\ \A t \in Tasks : alive'[t] => alive[t]
    /\ queue' = {}
    /\ UNCHANGED <<status, block, parent, journal, result, woke, held, revisited, revisitOf>>

Boot ==
    /\ ~daemon /\ daemon' = TRUE
    /\ journal' = [t \in Tasks |->
         IF journal[t] \in {"prepared", "possible_launch", "action_started",
                               "running", "result_processing"}
         THEN "unknown" ELSE journal[t]]
    /\ queue' = {t \in Tasks : status[t] = "pending"}
    /\ UNCHANGED <<status, block, parent, alive, result, woke, restarts, held, revisited, revisitOf>>

\* Narrow ordinary owner, exact current identity and positive death proof assumed.
Recover(t) ==
    /\ daemon /\ Running(t) /\ ~alive[t] /\ ~result[t]
    /\ (journal[t] = "none" \/ ~held)
    /\ journal[t] = "none" \/ (journal[t] = "unknown" /\ ~LegacyUnknownDeferral)
    /\ status' = [status EXCEPT ![t] = "failed"]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ UNCHANGED <<block, parent, alive, result, queue, woke,
                   daemon, restarts, held, revisited, revisitOf>>

LegacyRevisit(n) ==
    /\ restarts > 0
    /\ LegacyAutoRevisit /\ daemon /\ n \in Fresh
    /\ status["root"] = "in_progress" /\ block["root"] = "delegated"
    /\ status["child"] = "failed" /\ "root" \notin revisited
    /\ status' = [status EXCEPT ![n] = "pending"]
    /\ queue' = queue \cup {n}
    /\ revisited' = revisited \cup {"root"}
    /\ UNCHANGED <<block, parent, alive, journal, result, woke,
                   daemon, restarts, held, revisitOf>>

RECURSIVE RevisitReachable(_, _)
RevisitReachable(t, depth) ==
    IF depth = 0 THEN {t}
    ELSE {t} \cup UNION {RevisitReachable(n, depth - 1) :
                            n \in {s \in Tasks : revisitOf[s] = t}}
LiveRevisitDescendants(t) ==
    {n \in (RevisitReachable(t, Cardinality(Tasks)) \ {t}) :
        status[n] \notin Terminal}

\* AllowRepeatedRevisit bypasses admission only for the pre-fix counterexample.
\* Founder revisit: terminal predecessors remain terminal; blocked ones close.
Revisit(t, n) ==
    /\ daemon /\ ~held /\ n \in Fresh /\ parent[t] = "none"
    /\ status[t] \in {"completed", "failed", "cancelled", "escalated"} \/
         (status[t] = "in_progress" /\ block[t] = "delegated" /\ ChildrenDone(t))
    /\ AllowRepeatedRevisit \/ LiveRevisitDescendants(t) = {}
    /\ revisitOf' = [revisitOf EXCEPT ![n] = t]
    /\ status' = [status EXCEPT ![n] = "pending",
         ![t] = IF @ \in Terminal THEN @ ELSE "superseded"]
    /\ block' = [block EXCEPT ![t] = "none"]
    /\ queue' = (queue \ {t}) \cup {n}
    /\ revisited' = revisited \cup {t}
    /\ UNCHANGED <<parent, alive, journal, result, woke, daemon, restarts, held>>

\* A failed child is retried by NEW id; no terminal row is revived.
Retry(t, n) ==
    /\ daemon /\ ~held /\ n \in Fresh /\ status[t] = "failed"
    /\ parent[t] # "none" /\ status[parent[t]] = "in_progress"
    /\ block[parent[t]] = "delegated" /\ ChildrenDone(parent[t])
    /\ t \notin revisited /\ ~alive[parent[t]]
    /\ status' = [status EXCEPT ![n] = "pending"]
    /\ parent' = [parent EXCEPT ![n] = parent[t]]
    /\ queue' = (queue \ {parent[t]}) \cup {n}
    /\ revisited' = revisited \cup {t}
    /\ UNCHANGED <<block, alive, journal, result, woke, daemon, restarts, held, revisitOf>>

Rerun(t) ==
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ daemon /\ ~held /\ status[t] = "escalated"
    /\ status' = [status EXCEPT ![t] = "pending"]
    /\ queue' = queue \cup {t}
    /\ UNCHANGED <<block, parent, alive, journal, woke,
                   daemon, restarts, held, revisited, revisitOf>>

ManagerSupersede(t, n) ==
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ daemon /\ ~held /\ Running(t) /\ alive[t] /\ parent[t] = "none"
    /\ ChildrenDone(t) /\ n \in Fresh /\ revisited = {}
    /\ status' = [status EXCEPT ![t] = "superseded", ![n] = "pending"]
    /\ alive' = [alive EXCEPT ![t] = FALSE]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ queue' = (queue \ {t}) \cup {n}
    /\ revisited' = {t}
    /\ UNCHANGED <<block, parent, woke, daemon, restarts, held, revisitOf>>

Cancel(t) ==
    /\ result' = [result EXCEPT ![t] = FALSE]
    /\ daemon /\ status[t] \in {"pending", "in_progress", "escalated"}
    /\ ChildrenDone(t)
    /\ status' = [status EXCEPT ![t] = "cancelled"]
    /\ block' = [block EXCEPT ![t] = "none"]
    /\ alive' = [alive EXCEPT ![t] = FALSE]
    /\ journal' = [journal EXCEPT ![t] = "none"]
    /\ queue' = queue \ {t}
    /\ UNCHANGED <<parent, woke, daemon, restarts, held, revisited, revisitOf>>

Hold ==
    /\ AllowHold /\ daemon /\ ~held /\ held' = TRUE
    /\ UNCHANGED <<status, block, parent, alive, journal, result, queue,
                   woke, daemon, restarts, revisited, revisitOf>>

Next == Crash \/ Boot \/ Hold \/
    (\E t \in Tasks : Start(t) \/ WaitJob(t) \/ Wake(t) \/ PublishResult(t) \/
        Recover(t) \/ Rerun(t) \/ Cancel(t) \/ Feedback(t) \/ RejectPreparation(t) \/
        (\E o \in {"completed", "failed", "escalated"} : Finish(t, o)) \/
        (Scenario = "normal" /\ (\E n \in Tasks :
            Delegate(t, n) \/ Revisit(t, n) \/ Retry(t, n) \/ ManagerSupersede(t, n)))) \/
    (\E n \in Tasks : LegacyRevisit(n))

\* Fairness is on individual owners, never merely the disjunction Next.
Spec == Init /\ [][Next]_vars /\ WF_vars(Boot) /\
    (\A t \in Tasks : WF_vars(Recover(t)) /\ WF_vars(Wake(t)) /\
        WF_vars(Finish(t, "completed")))

TypeOK == /\ status \in [Tasks -> Statuses] /\ block \in [Tasks -> Blocks]
          /\ parent \in [Tasks -> (Tasks \cup {"none"})]
          /\ revisitOf \in [Tasks -> (Tasks \cup {"none"})]
          /\ alive \in [Tasks -> BOOLEAN] /\ journal \in [Tasks -> Phases]
          /\ result \in [Tasks -> BOOLEAN] /\ queue \subseteq Tasks
          /\ woke \subseteq Tasks /\ revisited \subseteq Tasks
          /\ daemon \in BOOLEAN /\ held \in BOOLEAN
          /\ restarts \in 0..MaxRestarts
OneDriver == Cardinality(RootDrivers) <= 1
OneLiveRootProcess == Cardinality({t \in Tasks :
    parent[t] = "none" /\ Running(t) /\ alive[t]}) <= 1
TerminalAbsorbing == [] [\A t \in Tasks :
    status[t] \in Terminal => status'[t] = status[t]]_vars
DeadProducerProgress == \A t \in Tasks :
    (Running(t) /\ ~alive[t]) ~> (status[t] \in Terminal \/ t \in woke)
RecoverableDeadProgress == \A t \in Tasks :
    (Running(t) /\ ~alive[t] /\ ~result[t]) ~> (status[t] \in Terminal \/ t \in woke)
=============================================================================
