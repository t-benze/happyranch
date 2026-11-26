------------------------- MODULE HomeLANFirewall -------------------------
EXTENDS Naturals, FiniteSets
Ports == {1, 2}
States == {"pending", "add_intent", "added", "preexisting", "remove_intent", "removed"}
VARIABLES plan, journal, tagged, foreign, collisions, active, privateSubnet, off
vars == <<plan, journal, tagged, foreign, collisions, active, privateSubnet, off>>
Init == /\ plan \in BOOLEAN
        /\ journal = [p \in Ports |-> "pending"]
        /\ tagged = {}
        /\ collisions \in SUBSET Ports
        /\ foreign = collisions
        /\ active \in BOOLEAN
        /\ privateSubnet \in BOOLEAN
        /\ off = FALSE
Upgrade == /\ ~plan /\ ~off
           /\ plan' = TRUE
           /\ UNCHANGED <<journal, tagged, foreign, collisions, active, privateSubnet, off>>
AddIntent(p) == /\ plan /\ ~off /\ active /\ privateSubnet
                /\ journal[p] = "pending"
                /\ journal' = [journal EXCEPT ![p] = "add_intent"]
                /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, off>>
Add(p) == /\ ~off /\ journal[p] = "add_intent" /\ p \notin collisions
          /\ tagged' = tagged \cup {p}
          /\ UNCHANGED <<plan, journal, foreign, collisions, active, privateSubnet, off>>
Adopt(p) == /\ ~off /\ journal[p] = "add_intent" /\ p \in tagged
            /\ journal' = [journal EXCEPT ![p] = "added"]
            /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, off>>
Collision(p) == /\ ~off /\ journal[p] = "add_intent" /\ p \in collisions
                /\ journal' = [journal EXCEPT ![p] = "preexisting"]
                /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, off>>
TurnOff == /\ ~off /\ off' = TRUE
           /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet>>
RemoveIntent(p) == /\ plan /\ off /\ journal[p] \notin {"remove_intent", "removed"}
                   /\ journal' = [journal EXCEPT ![p] = "remove_intent"]
                   /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, off>>
Remove(p) == /\ off /\ journal[p] = "remove_intent"
             /\ tagged' = tagged \ {p}
             /\ UNCHANGED <<plan, journal, foreign, collisions, active, privateSubnet, off>>
CommitRemove(p) == /\ off /\ journal[p] = "remove_intent" /\ p \notin tagged
                   /\ journal' = [journal EXCEPT ![p] = "removed"]
                   /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, off>>
Crash == UNCHANGED vars
Next == Upgrade \/ TurnOff \/ Crash \/ \E p \in Ports:
        AddIntent(p) \/ Add(p) \/ Adopt(p) \/ Collision(p) \/ RemoveIntent(p) \/ Remove(p) \/ CommitRemove(p)
Spec == Init /\ [][Next]_vars
TypeOK == /\ journal \in [Ports -> States] /\ tagged \subseteq Ports /\ foreign \subseteq Ports
ForeignPreserved == foreign = collisions
JournalBeforeEffect == tagged \subseteq {p \in Ports: journal[p] \in {"add_intent", "added", "remove_intent"}}
SubnetAndActivity == tagged # {} => privateSubnet /\ active
NoCollisionRewrite == tagged \cap collisions = {}
RemovedIsAbsent == \A p \in Ports: journal[p] = "removed" => p \notin tagged
=============================================================================
