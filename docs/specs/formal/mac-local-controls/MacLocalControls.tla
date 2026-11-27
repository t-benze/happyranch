------------------------ MODULE MacLocalControls ------------------------
EXTENDS Naturals, FiniteSets
CONSTANTS Owned, Selected, SkipRetention
ASSUME /\ Owned \in BOOLEAN /\ Selected \in {"local", "remote"}
       /\ SkipRetention \in BOOLEAN
Callers == {1, 2}
VARIABLES issued, slot, phase, document, effects, checked, epoch, readyEpoch,
          unknown, main, losses, target, remoteState
vars == <<issued, slot, phase, document, effects, checked, epoch, readyEpoch,
          unknown, main, losses, target, remoteState>>
Init == /\ issued = {} /\ slot = 0 /\ phase = "idle" /\ document = TRUE
        /\ effects = 0 /\ checked = TRUE /\ epoch = 0 /\ readyEpoch = 0
        /\ unknown = FALSE /\ main = 0 /\ losses = 0
        /\ target = Selected /\ remoteState = "ready"

\* MacLocalControls.call reserves synchronously, before native consent.
Request(c) == /\ c \notin issued /\ document
              /\ issued' = issued \cup {c}
              /\ IF Owned /\ slot = 0 THEN
                    /\ slot' = c /\ phase' = "consent"
                 ELSE UNCHANGED <<slot, phase>>
              /\ UNCHANGED <<document, effects, checked, epoch, readyEpoch,
                   unknown, main, losses, target, remoteState>>
Consent(accept) == /\ phase = "consent"
                  /\ (IF accept /\ document THEN
                        /\ phase' = "effect" /\ UNCHANGED slot
                     ELSE
                        /\ phase' = "idle" /\ slot' = 0)
                  /\ UNCHANGED <<issued, document, effects, checked, epoch,
                       readyEpoch, unknown, main, losses, target, remoteState>>
\* performLocalCommand invalidates admission before the existing child effect.
Effect == /\ phase = "effect" /\ document /\ Owned /\ slot # 0
          /\ phase' = "readmission" /\ effects' = effects + 1
          /\ epoch' = epoch + 1 /\ checked' = FALSE /\ unknown' = TRUE
          /\ UNCHANGED <<issued, slot, document, readyEpoch, main, losses,
               target, remoteState>>
Readmission == /\ phase = "readmission" /\ document
               /\ phase' = "publish" /\ checked' = TRUE /\ readyEpoch' = epoch
               /\ UNCHANGED <<issued, slot, document, effects, epoch, unknown,
                    main, losses, target, remoteState>>
Publish == /\ phase = "publish" /\ document /\ checked /\ readyEpoch = epoch
           /\ phase' = "idle" /\ slot' = 0 /\ unknown' = FALSE
           /\ UNCHANGED <<issued, document, effects, checked, epoch, readyEpoch,
                main, losses, target, remoteState>>
FailedEffect == /\ phase \in {"readmission", "publish"}
                /\ phase' = "recovery" /\ unknown' = TRUE
                /\ UNCHANGED <<issued, slot, document, effects, checked, epoch,
                     readyEpoch, main, losses, target, remoteState>>
\* Document loss is not effect cancellation, and cannot release its owner.
LoseDocument == /\ document /\ losses < 1
                /\ document' = FALSE /\ losses' = losses + 1 /\ main' = main + 1
                /\ IF phase \in {"readmission", "publish", "recovery"} THEN
                      /\ phase' = "recovery" /\ unknown' = TRUE
                      /\ slot' = IF SkipRetention THEN 0 ELSE slot
                   ELSE /\ phase' = "idle" /\ slot' = 0 /\ unknown' = FALSE
                /\ UNCHANGED <<issued, effects, checked, epoch, readyEpoch,
                     target, remoteState>>
Reopen == /\ ~document /\ document' = TRUE
          /\ UNCHANGED <<issued, slot, phase, effects, checked, epoch, readyEpoch,
               unknown, main, losses, target, remoteState>>
\* Only explicit local.status after fresh settled observation clears recovery.
Reconcile == /\ phase = "recovery" /\ document
             /\ phase' = "idle" /\ slot' = 0 /\ unknown' = FALSE
             /\ checked' = TRUE /\ readyEpoch' = epoch
             /\ UNCHANGED <<issued, document, effects, epoch, main, losses,
                  target, remoteState>>
Read == UNCHANGED vars
Next == (\E c \in Callers: Request(c)) \/ (\E accept \in BOOLEAN: Consent(accept))
        \/ Effect \/ Readmission \/ Publish \/ FailedEffect \/ LoseDocument
        \/ Reopen \/ Reconcile \/ Read
Spec == Init /\ [][Next]_vars
TypeOK == /\ slot \in {0, 1, 2} /\ issued \subseteq Callers
          /\ effects \in 0..2 /\ epoch = effects /\ readyEpoch \in 0..2
          /\ document \in BOOLEAN /\ checked \in BOOLEAN /\ unknown \in BOOLEAN
SlotCoversEffect == phase \in {"consent", "effect", "readmission", "publish", "recovery"} => slot # 0
UnknownRetainsOwner == unknown => slot # 0
ExternalNeverMutates == ~Owned => effects = 0
NoReplay == effects <= Cardinality(issued)
ReadyNeedsFreshAdmission == phase = "publish" => checked /\ readyEpoch = epoch
MainAndRemotePreserved == /\ main = losses /\ target = Selected /\ remoteState = "ready"
=============================================================================
