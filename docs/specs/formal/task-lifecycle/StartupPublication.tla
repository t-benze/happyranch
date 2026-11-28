------------------------- MODULE StartupPublication -------------------------
EXTENDS Naturals, FiniteSets
CONSTANT Interval
VARIABLES settled, wired, serving, age, completed, inbox, hints, firstSweep
vars == <<settled, wired, serving, age, completed, inbox, hints, firstSweep>>

Init == /\ settled = FALSE /\ wired = FALSE /\ serving = FALSE
        /\ age = 0 /\ completed = {1} /\ inbox = {} /\ hints = {}
        /\ firstSweep = FALSE
Settle == /\ ~settled /\ settled' = TRUE /\ inbox' = completed
          /\ UNCHANGED <<wired, serving, age, completed, hints, firstSweep>>
Wire == /\ settled /\ ~wired /\ wired' = TRUE /\ hints' = inbox
        /\ UNCHANGED <<settled, serving, age, completed, inbox, firstSweep>>
Serve == /\ wired /\ ~serving /\ serving' = TRUE
         /\ UNCHANGED <<settled, wired, age, completed, inbox, hints, firstSweep>>
Complete == /\ wired /\ 2 \notin completed /\ completed' = completed \cup {2}
            /\ UNCHANGED <<settled, wired, serving, age, inbox, hints, firstSweep>>
Tick == /\ wired /\ age < Interval /\ age' = age + 1
        /\ UNCHANGED <<settled, wired, serving, completed, inbox, hints, firstSweep>>
Sweep == /\ wired /\ age = Interval /\ firstSweep' = TRUE
         /\ inbox' = completed /\ hints' = completed
         /\ UNCHANGED <<settled, wired, serving, age, completed>>
Next == Settle \/ Wire \/ Serve \/ Complete \/ Tick \/ Sweep
Spec == Init /\ [][Next]_vars /\ WF_vars(Settle) /\ WF_vars(Wire)
        /\ WF_vars(Serve) /\ WF_vars(Tick) /\ WF_vars(Sweep)
StartupSafety == serving => (settled /\ wired)
DelayedSweep == firstSweep => age = Interval
AuthenticHints == hints \subseteq inbox /\ inbox \subseteq completed
Discoverable == 2 \in completed ~> 2 \in hints
=============================================================================
