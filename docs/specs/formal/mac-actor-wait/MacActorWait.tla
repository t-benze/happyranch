-------------------------- MODULE MacActorWait --------------------------
EXTENDS Naturals
CONSTANTS NestedWait, Scenario
ASSUME /\ NestedWait \in BOOLEAN /\ Scenario \in {"quit", "cli"}
VARIABLES phase, queued, busy, settled, pendingRestart, quitting, launches
vars == <<phase, queued, busy, settled, pendingRestart, quitting, launches>>

Init ==
    /\ phase = IF Scenario = "quit" THEN "incompleteRestart" ELSE "waiting"
    /\ queued = (Scenario = "cli") /\ busy = TRUE /\ settled = FALSE
    /\ pendingRestart = (Scenario = "quit") /\ quitting = FALSE /\ launches = 1

Quit ==
    /\ phase = "incompleteRestart"
    /\ phase' = "stopping" /\ quitting' = TRUE /\ pendingRestart' = FALSE
    /\ UNCHANGED <<queued, busy, settled, launches>>

ChildExit ==
    /\ phase = "stopping"
    /\ queued' = TRUE /\ phase' = "waiting"
    /\ UNCHANGED <<busy, settled, pendingRestart, quitting, launches>>

LeaveWait ==
    /\ phase = "waiting"
    \* Nested actor waiting needs the queued job to settle first. Await
    \* releases the actor; Quit observes the exited handle synchronously.
    /\ (~NestedWait \/ settled)
    /\ phase' = "yielded" /\ busy' = FALSE
    /\ settled' = IF Scenario = "quit" THEN TRUE ELSE settled
    /\ UNCHANGED <<queued, pendingRestart, quitting, launches>>

RunQueued ==
    /\ queued /\ ~busy
    /\ queued' = FALSE /\ settled' = TRUE
    /\ launches' = IF pendingRestart /\ ~quitting THEN launches + 1 ELSE launches
    /\ pendingRestart' = FALSE
    /\ UNCHANGED <<phase, busy, quitting>>

Next == Quit \/ ChildExit \/ LeaveWait \/ RunQueued
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
QuitCancelsRestart == quitting => ~pendingRestart /\ launches = 1
QueuedWorkSettles == <> (settled /\ ~queued)
=======================================================================
