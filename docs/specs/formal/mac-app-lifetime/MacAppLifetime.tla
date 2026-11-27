-------------------------- MODULE MacAppLifetime --------------------------
EXTENDS Naturals, TLC
CONSTANTS Owned, PreGateInstall
VARIABLES phase, owner, consent, gate, ack, document, evidence, uncertain,
          nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent
vars == <<phase, owner, consent, gate, ack, document, evidence, uncertain,
          nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Init == /\ phase = "idle" /\ owner = FALSE /\ consent = FALSE /\ gate = FALSE /\ ack = FALSE
        /\ document = TRUE /\ evidence = TRUE /\ uncertain = FALSE /\ nativeHold = FALSE
        /\ fresh = TRUE /\ sentSave = FALSE /\ stopped = ~Owned
        /\ stops = 0 /\ exits = 0 /\ releases = 0 /\ rounds = 0
        /\ intent \in {"close", "quit", "update"}
Request == /\ phase = "idle" /\ document /\ evidence /\ ~uncertain /\ ~nativeHold /\ rounds < 2
           /\ phase' = "consent" /\ owner' = TRUE /\ rounds' = rounds + 1
           /\ consent' = FALSE /\ gate' = FALSE /\ ack' = FALSE /\ fresh' = TRUE
           /\ UNCHANGED <<document, evidence, uncertain, nativeHold, sentSave, stopped, stops, exits, releases, intent>>
Consent == /\ phase = "consent" /\ fresh /\ document /\ ~nativeHold
           /\ consent' = TRUE /\ phase' = "web"
           /\ UNCHANGED <<owner, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Cancel == /\ phase \in {"consent", "web"} /\ phase' = "idle" /\ owner' = FALSE
          /\ UNCHANGED <<consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
FinalCurrent == document /\ evidence /\ ~uncertain /\ ~nativeHold /\ fresh /\ ~sentSave
GateReply == /\ phase = "web"
             /\ IF FinalCurrent THEN /\ phase' = "admitted" /\ gate' = TRUE /\ ack' = TRUE /\ owner' = TRUE
                ELSE /\ phase' = "idle" /\ gate' = FALSE /\ ack' = FALSE /\ owner' = FALSE
             /\ UNCHANGED <<consent, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Change == /\ phase \in {"consent", "web", "admitted", "stopping"} /\ fresh
          /\ fresh' = FALSE
          /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, nativeHold, sentSave, stopped, stops, exits, releases, rounds, intent>>
Save == /\ phase \in {"idle", "consent", "web"} /\ ~sentSave /\ sentSave' = TRUE
        /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, stopped, stops, exits, releases, rounds, intent>>
NativeWork == /\ ~nativeHold /\ phase \in {"idle", "consent", "web"} /\ nativeHold' = TRUE
              /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Retire == /\ document /\ document' = FALSE /\ evidence' = FALSE
          /\ uncertain' = (uncertain \/ sentSave)
          /\ IF phase \in {"admitted", "stopping", "ready", "unknown"} THEN /\ owner' = TRUE /\ phase' = "unknown"
             ELSE /\ owner' = FALSE /\ phase' = IF phase = "done" THEN "done" ELSE "idle"
          /\ UNCHANGED <<consent, gate, ack, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
NewDocument == /\ ~document /\ document' = TRUE /\ evidence' = TRUE
               /\ UNCHANGED <<phase, owner, consent, gate, ack, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Stop == /\ phase = "admitted" /\ FinalCurrent /\ Owned
        /\ phase' = "stopping" /\ stops' = stops + 1
        /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, exits, releases, rounds, intent>>
ObserveExit == /\ phase = "stopping" /\ fresh /\ stopped' = TRUE /\ phase' = "ready"
               /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stops, exits, releases, rounds, intent>>
Incomplete == /\ phase \in {"admitted", "stopping"} /\ phase' = "unknown"
              /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Unowned == /\ phase = "admitted" /\ ~Owned /\ FinalCurrent /\ phase' = "ready"
           /\ UNCHANGED <<owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, releases, rounds, intent>>
Complete == /\ phase = "ready" /\ fresh /\ stopped /\ phase' = "done" /\ owner' = FALSE
            /\ exits' = IF intent = "update" THEN exits ELSE exits + 1
            /\ releases' = IF intent = "update" THEN releases + 1 ELSE releases
            /\ UNCHANGED <<consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, rounds, intent>>
EarlyInstall == /\ PreGateInstall /\ intent = "update" /\ phase = "consent" /\ releases' = 1
                /\ UNCHANGED <<phase, owner, consent, gate, ack, document, evidence, uncertain, nativeHold, fresh, sentSave, stopped, stops, exits, rounds, intent>>
Next == Request \/ Consent \/ Cancel \/ GateReply \/ Change \/ Save \/ NativeWork \/ Retire \/ NewDocument
        \/ Stop \/ ObserveExit \/ Incomplete \/ Unowned \/ Complete \/ EarlyInstall
Spec == Init /\ [][Next]_vars
EffectsAfterGate == (stops > 0 \/ exits > 0 \/ releases > 0) => (consent /\ gate /\ ack)
SingleConsumption == stops <= 1 /\ exits <= 1 /\ releases <= 1
ExitBeforeRelease == (exits > 0 \/ releases > 0) => stopped
UnknownRetainsOwner == phase = "unknown" => owner
ExternalNeverSignalled == ~Owned => stops = 0
=============================================================================
