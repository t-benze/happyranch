-------------------------- MODULE MacAppLifetime --------------------------
EXTENDS Naturals, TLC
CONSTANTS Owned, PreGateInstall, RetainAbortedAdmission
VARIABLES phase, owner, consent, gate, ack, document, evidence, uncertain,
          nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent,
          admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit
vars == <<phase, owner, consent, gate, ack, document, evidence, uncertain,
          nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Init == /\ phase = "idle" /\ owner = FALSE /\ consent = FALSE /\ gate = FALSE /\ ack = FALSE
        /\ document = TRUE /\ evidence = TRUE /\ uncertain = FALSE /\ nativeHold = FALSE
        /\ fresh = TRUE /\ sentSave = FALSE /\ stopped = ~Owned
        /\ stops = 0 /\ exits = 0 /\ releases = 0 /\ rounds = 0
        /\ intent \in {"close", "quit", "update"}
        /\ admittedTermination = FALSE /\ updateAborted = FALSE /\ postAbortOwner = "none"
        /\ laterQuit = FALSE /\ laterGate = FALSE /\ staleExit = FALSE
Request == /\ phase = "idle" /\ document /\ evidence /\ ~uncertain /\ ~nativeHold /\ rounds < 2
           /\ phase' = "consent" /\ owner' = TRUE /\ rounds' = rounds + 1
           /\ consent' = FALSE /\ gate' = FALSE /\ ack' = FALSE /\ fresh' = TRUE
           /\ UNCHANGED <<document, evidence, uncertain, nativeHold, sentSave, stopped, stops, exits, releases, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Consent == /\ phase = "consent" /\ fresh /\ document /\ ~nativeHold
           /\ consent' = TRUE /\ phase' = "web"
           /\ UNCHANGED <<owner, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Cancel == /\ phase \in {"consent", "web"} /\ phase' = "idle" /\ owner' = FALSE
          /\ UNCHANGED <<consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
FinalCurrent == document /\ evidence /\ ~uncertain /\ ~nativeHold /\ fresh /\ ~sentSave
GateReply == /\ phase = "web"
             /\ IF FinalCurrent THEN /\ phase' = "admitted" /\ gate' = TRUE /\ ack' = TRUE /\ owner' = TRUE
                ELSE /\ phase' = "idle" /\ gate' = FALSE /\ ack' = FALSE /\ owner' = FALSE
             /\ UNCHANGED <<consent, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Change == /\ phase \in {"consent", "web", "admitted", "stopping"} /\ fresh
          /\ fresh' = FALSE
          /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, nativeHold, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Save == /\ phase \in {"idle", "consent", "web"} /\ ~sentSave /\ sentSave' = TRUE
        /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
NativeWork == /\ ~nativeHold /\ phase \in {"idle", "consent", "web"} /\ nativeHold' = TRUE
              /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Retire == /\ document /\ document' = FALSE /\ evidence' = FALSE
          /\ uncertain' = (uncertain \/ sentSave)
          /\ IF phase \in {"admitted", "stopping", "ready", "unknown"} THEN /\ owner' = TRUE /\ phase' = "unknown"
             ELSE /\ owner' = FALSE /\ phase' = IF phase = "done" THEN "done" ELSE "idle"
          /\ UNCHANGED <<consent, gate, ack, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
NewDocument == /\ ~document /\ document' = TRUE /\ evidence' = TRUE
               /\ UNCHANGED <<phase, owner, consent, gate, ack, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Stop == /\ phase = "admitted" /\ FinalCurrent /\ Owned
        /\ phase' = "stopping" /\ stops' = stops + 1
        /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
ObserveExit == /\ phase = "stopping" /\ fresh /\ stopped' = TRUE /\ phase' = "ready"
               /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Incomplete == /\ phase \in {"admitted", "stopping"} /\ phase' = "unknown"
              /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Unowned == /\ phase = "admitted" /\ ~Owned /\ FinalCurrent /\ phase' = "ready"
           /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Complete == /\ phase = "ready" /\ fresh /\ stopped /\ phase' = "done" /\ owner' = FALSE
            /\ exits' = IF intent = "update" THEN exits ELSE exits + 1
            /\ releases' = IF intent = "update" THEN releases + 1 ELSE releases
            /\ admittedTermination' = (intent = "update")
            /\ UNCHANGED <<consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, rounds, intent, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
EarlyInstall == /\ PreGateInstall /\ intent = "update" /\ phase = "consent" /\ releases' = 1
                /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, rounds, intent, admittedTermination, updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
\* The first update's gate/effect facts remain historical after release.
\* Abort does not settle any save/native uncertainty; a later Quit needs its own gate.
AbortUpdate == /\ phase = "done" /\ intent = "update" /\ ~updateAborted
               /\ updateAborted' = TRUE /\ admittedTermination' = RetainAbortedAdmission
               /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain,
                    nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent,
                    postAbortOwner, laterQuit, laterGate, staleExit>>
NewProtectedOwner == /\ updateAborted /\ postAbortOwner = "none"
                     /\ postAbortOwner' \in {"dirty", "pending", "unknown", "native"}
                     /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain,
                          nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent,
                          admittedTermination, updateAborted, laterQuit, laterGate, staleExit>>
LaterQuit == /\ updateAborted /\ postAbortOwner # "none" /\ ~laterQuit
             /\ laterQuit' = TRUE /\ laterGate' = ~admittedTermination
             /\ staleExit' = admittedTermination /\ admittedTermination' = FALSE
             /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain,
                  nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent,
                  updateAborted, postAbortOwner>>
PlatformTermination == /\ phase = "done" /\ admittedTermination /\ ~updateAborted
                       /\ admittedTermination' = FALSE /\ exits' = exits + 1
                       /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain,
                            nativeHold, fresh, sentSave, stopped, stops, releases, rounds, intent,
                            updateAborted, postAbortOwner, laterQuit, laterGate, staleExit>>
Next == Request \/ Consent \/ Cancel \/ GateReply \/ Change \/ Save \/ NativeWork \/ Retire \/ NewDocument
        \/ Stop \/ ObserveExit \/ Incomplete \/ Unowned \/ Complete \/ EarlyInstall
        \/ AbortUpdate \/ NewProtectedOwner \/ LaterQuit \/ PlatformTermination
Spec == Init /\ [][Next]_vars
EffectsAfterGate == (stops > 0 \/ exits > 0 \/ releases > 0) => (consent /\ gate /\ ack)
SingleConsumption == stops <= 1 /\ exits <= 1 /\ releases <= 1
ExitBeforeRelease == (exits > 0 \/ releases > 0) => stopped
UnknownRetainsOwner == phase = "unknown" => owner
LaterQuitRequiresGate == laterQuit => (laterGate /\ ~staleExit)
ExternalNeverSignalled == ~Owned => stops = 0
=============================================================================
