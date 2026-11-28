------------------------- MODULE HomeLANFirewall -------------------------
EXTENDS Naturals, FiniteSets
Ports == {1, 2}
States == {"pending", "add_intent", "added", "preexisting", "skipped", "remove_intent", "removed"}
VARIABLES plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired
vars == <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
Init == /\ plan \in BOOLEAN
        /\ journal = [p \in Ports |-> "pending"]
        /\ tagged = {}
        /\ collisions \in SUBSET Ports
        /\ foreign = collisions
        /\ active \in BOOLEAN
        /\ privateSubnet \in BOOLEAN
        /\ lockAvailable \in BOOLEAN
        /\ off = FALSE
        /\ workers = {} /\ custody = TRUE /\ ownerAlive = TRUE /\ expired = FALSE
Upgrade == /\ ~plan /\ ~off
           /\ plan' = TRUE
           /\ UNCHANGED <<journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
AddIntent(p) == /\ plan /\ ~off /\ custody /\ ownerAlive /\ ~expired /\ active /\ privateSubnet /\ lockAvailable
                /\ journal[p] = "pending"
                /\ journal' = [journal EXCEPT ![p] = "add_intent"]
                /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
Add(p) == /\ ~off /\ custody /\ ~expired /\ p \in workers /\ journal[p] = "add_intent" /\ p \notin collisions
          /\ tagged' = tagged \cup {p}
          /\ UNCHANGED <<plan, journal, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
Adopt(p) == /\ ~off /\ p \notin workers /\ journal[p] = "add_intent" /\ p \in tagged
            /\ journal' = [journal EXCEPT ![p] = "added"]
            /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
Collision(p) == /\ ~off /\ journal[p] = "add_intent" /\ p \in collisions
                /\ journal' = [journal EXCEPT ![p] = "preexisting"]
                /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
SkipLock(p) == /\ plan /\ ~off /\ ~lockAvailable /\ journal[p] = "pending"
               /\ journal' = [journal EXCEPT ![p] = "skipped"]
               /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
StartWorker(p) == /\ custody /\ ownerAlive /\ ~expired /\ ~off
                  /\ journal[p] = "add_intent" /\ p \notin workers
                  /\ workers' = workers \cup {p}
                  /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, custody, ownerAlive, expired>>
ReapWorker(p) == /\ p \in workers /\ workers' = workers \ {p}
                 /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, custody, ownerAlive, expired>>
OwnerDies == /\ ownerAlive /\ ownerAlive' = FALSE
             /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, expired>>
Deadline == /\ ~expired /\ expired' = TRUE
            /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive>>
ReleaseCustody == /\ custody /\ workers = {} /\ custody' = FALSE
                  /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, ownerAlive, expired>>
TurnOff == /\ ~off /\ ~custody /\ off' = TRUE
           /\ custody' = TRUE /\ ownerAlive' = TRUE /\ expired' = FALSE
           /\ UNCHANGED <<plan, journal, tagged, foreign, collisions, active, privateSubnet, lockAvailable, workers>>
RemoveIntent(p) == /\ plan /\ off /\ custody
                   /\ journal[p] \notin {"pending", "skipped", "remove_intent", "removed"}
                   /\ journal' = [journal EXCEPT ![p] = "remove_intent"]
                   /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
Remove(p) == /\ off /\ custody /\ journal[p] = "remove_intent"
             /\ tagged' = tagged \ {p}
             /\ UNCHANGED <<plan, journal, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
CommitRemove(p) == /\ off /\ custody /\ journal[p] = "remove_intent" /\ p \notin tagged
                   /\ journal' = [journal EXCEPT ![p] = "removed"]
                   /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
NoEffectCleanup(p) == /\ off /\ custody /\ journal[p] \in {"pending", "skipped"}
                      /\ journal' = [journal EXCEPT ![p] = "removed"]
                      /\ UNCHANGED <<plan, tagged, foreign, collisions, active, privateSubnet, lockAvailable, off, workers, custody, ownerAlive, expired>>
Crash == UNCHANGED vars
Next == Upgrade \/ TurnOff \/ Crash \/ OwnerDies \/ Deadline \/ ReleaseCustody \/ \E p \in Ports:
        StartWorker(p) \/ ReapWorker(p) \/ NoEffectCleanup(p) \/ SkipLock(p) \/ AddIntent(p) \/ Add(p) \/ Adopt(p) \/ Collision(p) \/ RemoveIntent(p) \/ Remove(p) \/ CommitRemove(p)
Spec == Init /\ [][Next]_vars
TypeOK == /\ workers \subseteq Ports /\ custody \in BOOLEAN /\ ownerAlive \in BOOLEAN /\ expired \in BOOLEAN
          /\ journal \in [Ports -> States] /\ tagged \subseteq Ports /\ foreign \subseteq Ports
ForeignPreserved == foreign = collisions
JournalBeforeEffect == tagged \subseteq {p \in Ports: journal[p] \in {"add_intent", "added", "remove_intent"}}
SubnetAndActivity == tagged # {} => privateSubnet /\ active /\ lockAvailable
NoCollisionRewrite == tagged \cap collisions = {}
WorkerCustody == workers # {} => custody /\ ~off
NoEffectProvenance == \A p \in Ports: journal[p] \in {"pending", "skipped", "removed"} => p \notin tagged /\ p \notin workers
RemovedIsAbsent == \A p \in Ports: journal[p] = "removed" => p \notin tagged
=============================================================================
