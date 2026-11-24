----------------------- MODULE RefusedPreparation -----------------------
EXTENDS Naturals, FiniteSets
CONSTANTS PreFix, UnsafeSettlement
VARIABLES stage, closed, remaining, journal, producerSettled, held,
          launchObserved, commandRunning, readable, cleanupLive, runningJob,
          child, refused, attempts, holdWinner, resumed
vars == <<stage, closed, remaining, journal, producerSettled, held,
          launchObserved, commandRunning, readable, cleanupLive, runningJob,
          child, refused, attempts, holdWinner, resumed>>
Repos == {"first", "last"}
Env == <<launchObserved, commandRunning, readable, cleanupLive, runningJob>>
Disposition == <<child, refused, attempts, holdWinner, resumed>>
Safe == ~launchObserved /\ ~commandRunning /\ readable /\ ~cleanupLive /\ ~runningJob
Init == /\ stage = "closing" /\ closed = {} /\ remaining = Repos
        /\ journal = "prepared" /\ producerSettled = FALSE /\ held \in BOOLEAN
        /\ child = "pending" /\ refused = FALSE /\ attempts = 0
        /\ holdWinner = FALSE /\ resumed = FALSE
        /\ launchObserved \in BOOLEAN /\ commandRunning \in BOOLEAN
        /\ readable \in BOOLEAN /\ cleanupLive \in BOOLEAN /\ runningJob \in BOOLEAN
Pause == /\ ~held /\ ~resumed /\ stage = "closing" /\ held' = TRUE
         /\ UNCHANGED <<stage, closed, remaining, journal, producerSettled, Env, Disposition>>
Close == /\ stage = "closing" /\ closed # Repos
         /\ ~commandRunning /\ readable
         \* Before repair, hold incorrectly vetoed the original finishing suffix.
         /\ ~(PreFix /\ held)
         /\ \E repo \in Repos \ closed : closed' = closed \cup {repo}
         /\ stage' = IF PreFix THEN "releasing" ELSE "closing"
         /\ UNCHANGED <<remaining, journal, producerSettled, held, Env, Disposition>>
Complete == /\ stage = "closing"
            /\ (closed = Repos \/ commandRunning \/ ~readable \/ (PreFix /\ held))
            /\ stage' = "releasing"
            /\ UNCHANGED <<closed, remaining, journal, producerSettled, held, Env, Disposition>>
Release == /\ stage = "releasing" /\ remaining # {}
           /\ \E repo \in remaining : remaining' = remaining \ {repo}
           \* Model the first lease's release throwing after worker return.
           /\ stage' = IF PreFix THEN "terminal" ELSE "releasing"
           /\ UNCHANGED <<closed, journal, producerSettled, held, Env, Disposition>>
Refuse == /\ ((stage = "releasing" /\ remaining = {}) \/ stage = "terminal")
          /\ stage' = "tail" /\ journal' = "unknown"
          \* A hold winner vetoes refusal accounting, including the lease OSError.
          /\ holdWinner' = held
          /\ child' = IF held THEN "pending" ELSE "failed"
          /\ refused' = ~held /\ attempts' = IF held THEN 0 ELSE 1
          /\ UNCHANGED <<closed, remaining, producerSettled, held, Env, resumed>>
Settle == /\ stage = "tail" /\ producerSettled' = TRUE /\ stage' = "settled"
          /\ journal' = IF UnsafeSettlement \/ (Safe /\ closed = Repos /\ remaining = {})
                        THEN "none" ELSE "unknown"
          /\ UNCHANGED <<closed, remaining, held, Env, Disposition>>
Resume == /\ stage = "settled" /\ journal = "none" /\ held /\ ~resumed
          /\ held' = FALSE /\ resumed' = TRUE /\ holdWinner' = FALSE
          /\ stage' = "closing" /\ closed' = {} /\ remaining' = Repos
          /\ journal' = "prepared" /\ producerSettled' = FALSE
          /\ UNCHANGED <<child, refused, attempts, Env>>
Next == Pause \/ Close \/ Complete \/ Release \/ Refuse \/ Settle \/ Resume
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
NoFalsePaused == journal = "none" =>
    (Safe /\ closed = Repos /\ remaining = {} /\ producerSettled /\ stage = "settled")
HeldPreserved == holdWinner => held
HoldAccounting == holdWinner => (child = "pending" /\ ~refused /\ attempts = 0)
ResumeAccounting == (resumed /\ stage = "settled") =>
                    (~held /\ child = "failed" /\ refused /\ attempts = 1)
SafeProgress == Safe ~> (stage = "settled" /\ journal = "none")
=============================================================================
