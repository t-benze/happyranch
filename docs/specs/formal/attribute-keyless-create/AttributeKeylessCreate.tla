---------------------- MODULE AttributeKeylessCreate ----------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANT N, MaxKey
Clients == 1..N
Keys == 1..MaxKey
VARIABLES phase, sends, ownSuccess, owners, checked, committed, history,
          writer, chosen, processed, otherKey, edited, audit
vars == <<phase, sends, ownSuccess, owners, checked, committed, history,
          writer, chosen, processed, otherKey, edited, audit>>
Note(kind, i) == [kind |-> kind, actor |-> i, beforeOwners |-> owners,
                  beforePhase |-> phase, beforeHistory |-> history]
Free == Keys \ history
FirstFree == CHOOSE k \in Free : \A j \in Free : k <= j
Init == /\ phase = [i \in Clients |-> "fresh"]
        /\ sends = [i \in Clients |-> 0]
        /\ ownSuccess = [i \in Clients |-> FALSE]
        /\ owners = {}
        /\ checked = {}
        /\ committed = [i \in Clients |-> 0]
        /\ history = {1} \* a historical, archived/renamed-away reservation
        /\ writer = 0 /\ chosen = 0 /\ processed = {}
        /\ otherKey = 0 /\ edited = {}
        /\ audit = Note("init", 1)
Send(i) == /\ phase[i] = "fresh"
           /\ phase' = [phase EXCEPT ![i] = "pending"]
           /\ sends' = [sends EXCEPT ![i] = @ + 1]
           /\ owners' = owners \cup {i}
           /\ audit' = Note("send", i)
           /\ UNCHANGED <<ownSuccess, checked, committed, history, writer,
                          chosen, processed, otherKey, edited>>
Begin(i) == /\ sends[i] = 1 /\ i \notin processed /\ writer = 0 /\ Free # {}
            /\ writer' = i /\ chosen' = FirstFree
            /\ audit' = Note("begin", i)
            /\ UNCHANGED <<phase, sends, ownSuccess, owners, checked, committed,
                           history, processed, otherKey, edited>>
Commit(i) == /\ writer = i
             /\ committed' = [committed EXCEPT ![i] = chosen]
             /\ history' = history \cup {chosen}
             /\ processed' = processed \cup {i}
             /\ writer' = 0 /\ chosen' = 0
             /\ audit' = Note("commit", i)
             /\ UNCHANGED <<phase, sends, ownSuccess, owners, checked, otherKey, edited>>
Rollback(i) == /\ writer = i
               /\ processed' = processed \cup {i}
               /\ writer' = 0 /\ chosen' = 0
               /\ audit' = Note("rollback", i)
               /\ UNCHANGED <<phase, sends, ownSuccess, owners, checked, committed,
                              history, otherKey, edited>>
Exhaust(i) == /\ sends[i] = 1 /\ i \notin processed /\ writer = 0 /\ Free = {}
             /\ processed' = processed \cup {i}
             /\ phase' = (IF phase[i] = "ack" THEN phase ELSE [phase EXCEPT ![i] = "failed"])
             /\ checked' = checked \ {i}
             /\ audit' = Note("exhaust", i)
             /\ UNCHANGED <<sends, ownSuccess, owners, committed, history, writer, chosen, otherKey, edited>>
Lose(i) == /\ phase[i] = "pending"
           /\ phase' = [phase EXCEPT ![i] = "unknown"]
           /\ audit' = Note("lose", i)
           /\ UNCHANGED <<sends, ownSuccess, owners, checked, committed, history,
                          writer, chosen, processed, otherKey, edited>>
OwnResponse(i) == /\ committed[i] # 0 /\ ~ownSuccess[i]
                  /\ ownSuccess' = [ownSuccess EXCEPT ![i] = TRUE]
                  /\ phase' = (IF phase[i] = "ack" THEN phase ELSE [phase EXCEPT ![i] = "saved"])
                  /\ checked' = checked \ {i}
                  /\ audit' = Note("response", i)
                  /\ UNCHANGED <<sends, owners, committed, history, writer, chosen, processed, otherKey, edited>>
CheckList(i) == /\ phase[i] = "unknown" /\ i \in owners /\ i \notin checked
                /\ checked' = checked \cup {i}
                /\ audit' = Note("checkOK", i)
                /\ UNCHANGED <<phase, sends, ownSuccess, owners, committed, history,
                               writer, chosen, processed, otherKey, edited>>
CheckFailed(i) == /\ phase[i] = "unknown" /\ i \in owners
                  /\ checked' = checked \ {i}
                  /\ audit' = Note("checkFailed", i)
                  /\ UNCHANGED <<phase, sends, ownSuccess, owners, committed, history,
                                 writer, chosen, processed, otherKey, edited>>
Acknowledge(i) == /\ phase[i] = "unknown" /\ i \in owners /\ i \in checked
                  /\ phase' = [phase EXCEPT ![i] = "ack"]
                  /\ owners' = owners \ {i} /\ checked' = checked \ {i}
                  /\ audit' = Note("ack", i)
                  /\ UNCHANGED <<sends, ownSuccess, committed, history, writer,
                                 chosen, processed, otherKey, edited>>
OtherTab == /\ writer = 0 /\ otherKey = 0 /\ Free # {}
            /\ otherKey' = FirstFree /\ history' = history \cup {FirstFree}
            /\ audit' = Note("other", 1)
            /\ UNCHANGED <<phase, sends, ownSuccess, owners, checked, committed,
                           writer, chosen, processed, edited>>
EditMetadata(i) == /\ committed[i] # 0 /\ i \notin edited
                   /\ edited' = edited \cup {i}
                   /\ audit' = Note("metadata", i)
                   /\ UNCHANGED <<phase, sends, ownSuccess, owners, checked,
                                  committed, history, writer, chosen, processed, otherKey>>
Next == (\E i \in Clients : Send(i) \/ Begin(i) \/ Commit(i) \/ Rollback(i) \/ Exhaust(i)
                           \/ Lose(i) \/ OwnResponse(i) \/ CheckList(i) \/ CheckFailed(i)
                           \/ Acknowledge(i) \/ EditMetadata(i)) \/ OtherTab
Spec == Init /\ [][Next]_vars
NoFalseSaved == \A i \in Clients : phase[i] = "saved" => ownSuccess[i]
NoResendFromUnknown == /\ \A i \in Clients : sends[i] <= 1
                       /\ audit.kind = "send" => audit.beforePhase[audit.actor] = "fresh"
AckReleasesOnlySelf == audit.kind = "ack" =>
    /\ owners = audit.beforeOwners \ {audit.actor}
    /\ \A i \in Clients \ {audit.actor} : phase[i] = audit.beforePhase[i]
FailedReadbackKeepsHold == audit.kind = "checkFailed" =>
    /\ phase[audit.actor] = "unknown" /\ owners = audit.beforeOwners
DistinctKeysNoHistoricalReuse ==
    /\ \A i, j \in Clients : (i # j /\ committed[i] # 0) => committed[i] # committed[j]
    /\ \A i \in Clients : committed[i] # 0 => committed[i] \in history /\ committed[i] # otherKey
    /\ audit.kind = "commit" => committed[audit.actor] \notin audit.beforeHistory
    /\ audit.kind = "other" => otherKey \notin audit.beforeHistory
=============================================================================
