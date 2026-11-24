----------------------- MODULE RefusedPreparation -----------------------
EXTENDS Naturals, FiniteSets
CONSTANTS PreFix, UnsafeSettlement
VARIABLES stage, closed, remaining, journal, producerSettled, held,
          launchObserved, commandRunning, readable, cleanupLive, runningJob
vars == <<stage, closed, remaining, journal, producerSettled, held,
          launchObserved, commandRunning, readable, cleanupLive, runningJob>>
Repos == {"first", "last"}
Env == <<held, launchObserved, commandRunning, readable, cleanupLive, runningJob>>
Safe == ~launchObserved /\ ~commandRunning /\ readable /\ ~cleanupLive /\ ~runningJob
Init == /\ stage = "closing" /\ closed = {} /\ remaining = Repos
        /\ journal = "prepared" /\ producerSettled = FALSE /\ held = TRUE
        /\ launchObserved \in BOOLEAN /\ commandRunning \in BOOLEAN
        /\ readable \in BOOLEAN /\ cleanupLive \in BOOLEAN /\ runningJob \in BOOLEAN
Close == /\ stage = "closing" /\ closed # Repos
         /\ ~commandRunning /\ readable
         /\ \E repo \in Repos \ closed : closed' = closed \cup {repo}
         /\ stage' = IF PreFix THEN "releasing" ELSE "closing"
         /\ UNCHANGED <<remaining, journal, producerSettled, Env>>
Complete == /\ stage = "closing"
            /\ (closed = Repos \/ commandRunning \/ ~readable)
            /\ stage' = "releasing"
            /\ UNCHANGED <<closed, remaining, journal, producerSettled, Env>>
Release == /\ stage = "releasing" /\ remaining # {}
           /\ \E repo \in remaining : remaining' = remaining \ {repo}
           \* Model the first lease's release throwing after worker return.
           /\ stage' = IF PreFix THEN "terminal" ELSE "releasing"
           /\ UNCHANGED <<closed, journal, producerSettled, Env>>
Refuse == /\ ((stage = "releasing" /\ remaining = {}) \/ stage = "terminal")
          /\ stage' = "tail" /\ journal' = "unknown"
          /\ UNCHANGED <<closed, remaining, producerSettled, Env>>
Settle == /\ stage = "tail" /\ producerSettled' = TRUE /\ stage' = "settled"
          /\ journal' = IF UnsafeSettlement \/ (Safe /\ closed = Repos /\ remaining = {})
                        THEN "none" ELSE "unknown"
          /\ UNCHANGED <<closed, remaining, Env>>
Next == Close \/ Complete \/ Release \/ Refuse \/ Settle
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
NoFalsePaused == journal = "none" =>
    (Safe /\ closed = Repos /\ remaining = {} /\ producerSettled /\ stage = "settled")
HeldPreserved == held
SafeProgress == Safe ~> (stage = "settled" /\ journal = "none")
=============================================================================
