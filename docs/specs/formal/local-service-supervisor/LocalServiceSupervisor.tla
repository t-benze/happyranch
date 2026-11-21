----------------------- MODULE LocalServiceSupervisor -----------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Mode, SelectedTarget, SkipRecheck, SkipStatusFence
ASSUME /\ Mode \in {"service", "foreground"}
       /\ SelectedTarget \in {"local", "remote"}
       /\ SkipRecheck \in BOOLEAN
       /\ SkipStatusFence \in BOOLEAN

Callers == {"first", "second"}
Commands == {"Start", "Stop", "Restart", "KeepOn", "KeepOff", "Repair", "TurnOff"}
Lifecycle == {"Start", "Stop", "Restart"}
Admission == {"Start", "Restart"}
Phases == {"idle", "consent", "queued", "effect", "readiness", "recheck",
           "refresh", "publish", "finish", "unknown"}
Failures == {"none", "incompatible", "unreachable", "busy"}

VARIABLES issued, active, phase, operation, service, transport, surface,
          readResult, reply, emission, background, interrupted
vars == <<issued, active, phase, operation, service, transport, surface,
          readResult, reply, emission, background, interrupted>>

Main == [document |-> "Main", generation |-> 0, owners |-> "retained",
         candidate |-> "retained", target |-> SelectedTarget, child |-> "owned"]
EmptyEmission == [state |-> "none", checked |-> FALSE, bearer |-> "empty",
                  ready |-> FALSE, interrupted |-> FALSE]

Init ==
    /\ issued = {} /\ active = {} /\ phase = "idle" /\ operation = "none"
    /\ \E linger \in BOOLEAN:
         service = [running |-> "running", enabled |-> FALSE, linger |-> linger,
                    owned |-> FALSE, initialLinger |-> linger]
    /\ transport = [checked |-> TRUE, bearer |-> "fresh", ready |-> TRUE,
                     failure |-> "none"]
    /\ surface = Main /\ readResult = "none" /\ reply = "none"
    /\ emission = EmptyEmission
    /\ background = [phase |-> "unused", cancelled |-> FALSE]
    /\ interrupted = FALSE

\* Reservation and competing refusal are atomic under LocalControls.lock.
Request(c, cmd) ==
    /\ c \notin issued
    /\ issued' = issued \cup {c}
    /\ IF Mode = "foreground" THEN
          /\ reply' = "unavailable/none/nativeRecovery"
          /\ UNCHANGED <<active, phase, operation, background, interrupted>>
       ELSE IF active # {} THEN
          /\ reply' = "busy/none/refresh"
          /\ UNCHANGED <<active, phase, operation, background, interrupted>>
       ELSE
          /\ active' = {c} /\ operation' = cmd /\ phase' = "consent"
          /\ reply' = "reserved"
    /\ UNCHANGED <<service, transport, surface, readResult, emission, background, interrupted>>

\* Nested native consent may interleave with Request and Read.
Consent(accept) ==
    /\ phase = "consent"
    /\ IF accept THEN
          /\ phase' = "queued" /\ reply' = "accepted"
          /\ UNCHANGED <<active, operation, background, interrupted>>
       ELSE
          /\ phase' = "idle" /\ active' = {} /\ operation' = "none"
          /\ reply' = "permissionDenied/none/nativePermission"
    /\ UNCHANGED <<issued, service, transport, surface, readResult, emission, background, interrupted>>

WorkerGate(allow) ==
    /\ phase = "queued"
    /\ IF allow THEN
          /\ phase' = "effect"
          /\ interrupted' = IF operation \in Lifecycle THEN TRUE ELSE interrupted
          /\ background' = IF operation \in Lifecycle
                THEN [background EXCEPT !.cancelled = TRUE] ELSE background
          /\ service' = IF operation \in Lifecycle
                         THEN [service EXCEPT !.running = "starting"] ELSE service
          /\ transport' = IF operation \in Lifecycle
                THEN [checked |-> FALSE, bearer |-> "empty", ready |-> FALSE,
                      failure |-> "none"] ELSE transport
          /\ UNCHANGED <<active, operation, reply>>
       ELSE
          /\ phase' = "idle" /\ active' = {} /\ operation' = "none"
          /\ reply' = "ownerOrDocumentRefusal/none/refresh"
          /\ UNCHANGED <<service, transport, background, interrupted>>
    /\ UNCHANGED <<issued, surface, readResult, emission>>

Effect ==
    /\ phase = "effect" /\ Mode = "service"
    /\ CASE operation \in Admission ->
               /\ phase' = "readiness" /\ UNCHANGED service
         [] operation = "Stop" ->
               /\ service' = [service EXCEPT !.running = "stopped"]
               /\ phase' = "finish"
         [] operation \in {"KeepOn", "Repair"} ->
               /\ service' = [service EXCEPT !.enabled = TRUE, !.linger = TRUE,
                                             !.owned = @ \/ ~service.linger]
               /\ phase' = "finish"
         [] operation \in {"KeepOff", "TurnOff"} ->
               /\ service' = [service EXCEPT !.enabled = FALSE, !.owned = FALSE,
                            !.linger = IF service.owned THEN FALSE ELSE @]
               /\ phase' = "finish"
    /\ UNCHANGED <<issued, active, operation, transport, surface, readResult,
                    reply, emission, background, interrupted>>

UnknownEffect ==
    /\ phase \in {"effect", "finish"}
    /\ phase' = "unknown"
    /\ service' = [service EXCEPT !.running = "unknown"]
    /\ reply' = "outcomeUnknown/unknown/nativeRecovery"
    /\ emission' = [EmptyEmission EXCEPT !.state = "unknown"]
    /\ UNCHANGED <<issued, active, operation, transport, surface, readResult, background, interrupted>>

Readiness ==
    /\ phase = "readiness"
    /\ service' = [service EXCEPT !.running = "running"]
    /\ transport' = [transport EXCEPT !.ready = TRUE]
    /\ phase' = IF SkipRecheck THEN "refresh" ELSE "recheck"
    /\ UNCHANGED <<issued, active, operation, surface, readResult, reply, emission, background, interrupted>>

Recheck ==
    /\ phase = "recheck"
    /\ transport' = [transport EXCEPT !.checked = TRUE]
    /\ phase' = "refresh"
    /\ UNCHANGED <<issued, active, operation, service, surface, readResult,
                    reply, emission, background, interrupted>>

RefreshBearer ==
    /\ phase = "refresh"
    /\ transport' = [transport EXCEPT !.bearer = "fresh"]
    /\ phase' = "publish"
    /\ UNCHANGED <<issued, active, operation, service, surface, readResult,
                    reply, emission, background, interrupted>>

NamedFailure(reason) ==
    /\ phase \in {"readiness", "recheck", "refresh"}
    /\ reason \in {"unreachable", "incompatible", "busy"}
    /\ transport' = [transport EXCEPT !.failure = reason]
    /\ phase' = "publish"
    /\ UNCHANGED <<issued, active, operation, service, surface, readResult,
                    reply, emission, background, interrupted>>

\* A ready emission is downstream of actual bootstrap, not service running alone.
Publish ==
    /\ phase = "publish"
    /\ emission' = [state |-> IF transport.failure = "none" THEN "ready"
                             ELSE transport.failure,
                     checked |-> transport.checked, bearer |-> transport.bearer,
                     ready |-> transport.ready, interrupted |-> FALSE]
    /\ phase' = "finish"
    /\ interrupted' = FALSE
    /\ UNCHANGED <<issued, active, operation, service, transport, surface,
                    readResult, reply, background>>

Finish ==
    /\ phase = "finish"
    /\ phase' = "idle" /\ active' = {} /\ operation' = "none" /\ reply' = "observed"
    /\ UNCHANGED <<issued, service, transport, surface, readResult, emission, background, interrupted>>

\* Busy reads observe pending state, never acquire/release the mutation slot.
Read(kind) ==
    /\ readResult' = IF Mode = "foreground" THEN "unsupported"
                     ELSE IF active # {} THEN
                       IF kind = "status" THEN "busy" ELSE "update_unknown"
                     ELSE IF kind = "status" THEN "fresh" ELSE "update_hint"
    /\ UNCHANGED <<issued, active, phase, operation, service, transport,
                    surface, reply, emission, background, interrupted>>

\* Only explicit local.status with successful fresh inspection clears unknown.
Reconcile(running) ==
    /\ phase = "unknown" /\ running \in {"running", "stopped"}
    /\ service' = [service EXCEPT !.running = running]
    /\ readResult' = "reconciled" /\ reply' = "observed"
    /\ active' = {} /\ operation' = "none" /\ phase' = "idle"
    /\ UNCHANGED <<issued, transport, surface, emission, background, interrupted>>


\* One bounded background status worker can pause before bootstrap or delivery.
StatusAdmission ==
    /\ Mode = "service" /\ background.phase = "unused" /\ ~interrupted
    /\ background' = [phase |-> "admitted", cancelled |-> FALSE]
    /\ UNCHANGED <<issued, active, phase, operation, service, transport, surface,
                    readResult, reply, emission, interrupted>>

StatusBootstrap ==
    /\ background.phase = "admitted"
    /\ IF background.cancelled /\ ~SkipStatusFence THEN
          /\ background' = [background EXCEPT !.phase = "done"]
          /\ UNCHANGED transport
       ELSE
          /\ background' = [background EXCEPT !.phase = "completion"]
          /\ transport' = [checked |-> TRUE, ready |-> TRUE, failure |-> "none",
                           bearer |-> IF interrupted THEN "stale" ELSE "fresh"]
    /\ UNCHANGED <<issued, active, phase, operation, service, surface,
                    readResult, reply, emission, interrupted>>

StatusCompletion ==
    /\ background.phase = "completion"
    /\ background' = [background EXCEPT !.phase = "done"]
    /\ emission' = IF background.cancelled /\ ~SkipStatusFence THEN emission
       ELSE [state |-> IF transport.ready THEN "ready" ELSE "none",
             checked |-> transport.checked, bearer |-> transport.bearer,
             ready |-> transport.ready, interrupted |-> interrupted]
    /\ UNCHANGED <<issued, active, phase, operation, service, transport, surface,
                    readResult, reply, interrupted>>

Progress == \/ \E accept \in BOOLEAN: Consent(accept)
            \/ \E allow \in BOOLEAN: WorkerGate(allow)
            \/ Effect \/ UnknownEffect \/ Readiness \/ Recheck \/ RefreshBearer
            \/ \E reason \in Failures \ {"none"}: NamedFailure(reason)
            \/ Publish \/ Finish
Reconciliation == \E running \in {"running", "stopped"}: Reconcile(running)
Next == \/ \E c \in Callers, cmd \in Commands: Request(c, cmd)
        \/ Progress \/ Reconciliation
        \/ StatusAdmission \/ StatusBootstrap \/ StatusCompletion
        \/ \E kind \in {"status", "update"}: Read(kind)

Spec == Init /\ [][Next]_vars /\ WF_vars(Progress) /\ WF_vars(Reconciliation)

TypeOK ==
    /\ issued \subseteq Callers /\ active \subseteq issued
    /\ phase \in Phases /\ operation \in Commands \cup {"none"}
    /\ service \in [running: {"running", "stopped", "starting", "unknown"},
                     enabled: BOOLEAN, linger: BOOLEAN, owned: BOOLEAN,
                     initialLinger: BOOLEAN]
    /\ transport \in [checked: BOOLEAN, bearer: {"fresh", "empty", "stale"},
                       ready: BOOLEAN, failure: Failures]
    /\ surface = Main
    /\ readResult \in {"none", "unsupported", "busy", "update_unknown",
                        "fresh", "update_hint", "reconciled"}
    /\ emission \in [state: {"none", "ready", "unknown"} \cup (Failures \ {"none"}),
                      checked: BOOLEAN, bearer: {"fresh", "empty", "stale"}, ready: BOOLEAN, interrupted: BOOLEAN]

    /\ background \in [phase: {"unused", "admitted", "completion", "done"}, cancelled: BOOLEAN]
    /\ interrupted \in BOOLEAN

NoStaleLocalCache == transport.bearer # "stale"
NoReadyDuringInterruption == emission.state = "ready" => ~emission.interrupted

AtMostOneMutationInFlight == Cardinality(active) <= 1
SlotTracksMutation == (active = {}) <=> (phase = "idle" /\ operation = "none")
NoStateChangedWithStaleBearer == emission.state = "ready" =>
    (emission.checked /\ emission.bearer = "fresh" /\ emission.ready)
ForegroundNeverMutates == Mode = "foreground" =>
    /\ active = {} /\ service.running = "running" /\ service.enabled = FALSE
    /\ service.owned = FALSE /\ service.linger = service.initialLinger
    /\ surface.child = "owned"
MainAndRemoteUnchanged == surface = Main
MutationEventuallyReleases == \A c \in Callers: (c \in active) ~> (c \notin active)
RestartScenario == operation \in {"none", "Restart"}
=============================================================================
