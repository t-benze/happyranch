------------------------- MODULE AppLifecycle -------------------------
EXTENDS Naturals
CONSTANTS KeepRunning, SelectedTarget, SkipAssets
ASSUME /\ KeepRunning \in BOOLEAN
       /\ SelectedTarget \in {"local", "remote"}
       /\ SkipAssets \in BOOLEAN

VARIABLES alive, document, summary, uncertain, subscribed, pending, issued,
          action, admitted, recovery, compatible, serviceRunning, lockReleased,
          launched, stopPhase, attempts
vars == <<alive, document, summary, uncertain, subscribed, pending, issued,
          action, admitted, recovery, compatible, serviceRunning, lockReleased,
          launched, stopPhase, attempts>>

Init ==
    /\ alive = TRUE /\ document = 0 /\ summary = "missing"
    /\ uncertain = FALSE /\ subscribed = FALSE /\ pending = 0 /\ issued = 0
    /\ action = "none" /\ admitted = "none" /\ recovery = FALSE
    /\ compatible \in BOOLEAN
    /\ serviceRunning = TRUE /\ lockReleased = FALSE /\ launched = FALSE
    /\ stopPhase = "idle" /\ attempts = 0

Subscribe ==
    /\ alive /\ ~subscribed /\ subscribed' = TRUE
    /\ UNCHANGED <<alive, document, summary, uncertain, pending, issued,
         action, admitted, recovery, compatible, serviceRunning, lockReleased,
         launched, stopPhase, attempts>>

Publish(state) ==
    /\ alive /\ subscribed /\ state \in {"clean", "dirty", "pending", "unknown"}
    \* A sent save can resolve here only in the original document. A
    \* replacement document cannot discharge the retained coarse latch.
    /\ summary' = state
    /\ UNCHANGED <<alive, document, uncertain, subscribed, pending, issued,
         action, admitted, recovery, compatible, serviceRunning, lockReleased,
         launched, stopPhase, attempts>>

Replace ==
    /\ alive /\ document = 0
    /\ document' = 1 /\ subscribed' = FALSE /\ pending' = 0
    /\ uncertain' = (uncertain \/ summary \in {"pending", "unknown"})
    /\ summary' = "missing"
    /\ UNCHANGED <<alive, issued, action, admitted, recovery, compatible,
         serviceRunning, lockReleased, launched, stopPhase, attempts>>

Request(kind) ==
    /\ alive /\ subscribed /\ pending = 0 /\ issued < 2
    /\ kind \in {"close", "quit", "relaunch"}
    /\ issued' = issued + 1 /\ pending' = issued + 1 /\ action' = kind
    /\ UNCHANGED <<alive, document, summary, uncertain, subscribed, admitted,
         recovery, compatible, serviceRunning, lockReleased, launched, stopPhase, attempts>>

Keep ==
    /\ alive /\ pending # 0 /\ pending' = 0
    /\ UNCHANGED <<alive, document, summary, uncertain, subscribed, issued,
         action, admitted, recovery, compatible, serviceRunning, lockReleased,
         launched, stopPhase, attempts>>

Proceed ==
    /\ alive /\ pending # 0 /\ summary = "clean" /\ ~uncertain
    /\ (action # "relaunch" \/ compatible \/ SkipAssets)
    /\ admitted' = action /\ pending' = 0 /\ alive' = FALSE
    /\ stopPhase' = "stopping"
    /\ UNCHANGED <<document, summary, uncertain, subscribed, issued, action,
         recovery, compatible, serviceRunning, lockReleased, launched, attempts>>

RecoveryExit(kind) ==
    \* Separate explicit loss warning: Stay is default; no automatic timeout.
    /\ alive /\ kind \in {"close", "quit", "relaunch"}
    /\ (kind # "relaunch" \/ compatible \/ SkipAssets)
    /\ alive' = FALSE /\ pending' = 0 /\ admitted' = kind /\ recovery' = TRUE
    /\ stopPhase' = "stopping"
    /\ UNCHANGED <<document, summary, uncertain, subscribed, issued, action,
         compatible, serviceRunning, lockReleased, launched, attempts>>

StopFailed ==
    /\ stopPhase = "stopping" /\ attempts = 0
    /\ stopPhase' = "held" /\ attempts' = 1
    /\ UNCHANGED <<alive, document, summary, uncertain, subscribed, pending,
         issued, action, admitted, recovery, compatible, serviceRunning,
         lockReleased, launched>>

RetryStop ==
    /\ stopPhase = "held" /\ stopPhase' = "stopping"
    /\ UNCHANGED <<alive, document, summary, uncertain, subscribed, pending,
         issued, action, admitted, recovery, compatible, serviceRunning,
         lockReleased, launched, attempts>>

Stopped ==
    /\ stopPhase = "stopping"
    /\ serviceRunning' = KeepRunning /\ lockReleased' = TRUE
    /\ stopPhase' = "stopped"
    /\ UNCHANGED <<alive, document, summary, uncertain, subscribed, pending,
         issued, action, admitted, recovery, compatible, launched, attempts>>

Launch ==
    /\ ~alive /\ admitted = "relaunch" /\ lockReleased /\ ~launched
    /\ (compatible \/ SkipAssets)
    /\ launched' = TRUE
    /\ UNCHANGED <<alive, document, summary, uncertain, subscribed, pending,
         issued, action, admitted, recovery, compatible, serviceRunning,
         lockReleased, stopPhase, attempts>>

Next == Subscribe \/ (\E state \in {"clean", "dirty", "pending", "unknown"}: Publish(state))
        \/ Replace \/ (\E kind \in {"close", "quit", "relaunch"}: Request(kind))
        \/ Keep \/ Proceed
        \/ (\E kind \in {"close", "quit", "relaunch"}: RecoveryExit(kind))
        \/ StopFailed \/ RetryStop \/ Stopped \/ Launch
Spec == Init /\ [][Next]_vars

NormalExitNeedsOwners == ~alive /\ ~recovery => summary = "clean" /\ ~uncertain
RelaunchNeedsAssets == admitted = "relaunch" => compatible
LaunchNeedsReleasedLock == launched => lockReleased /\ ~alive /\ stopPhase = "stopped"
KeepRunningApplied == lockReleased => serviceRunning = KeepRunning
StopFailureKeepsCustody == stopPhase = "held" => ~lockReleased /\ ~launched
OneCorrelatedRequest == pending \in 0..issued /\ issued <= 2
=============================================================================
