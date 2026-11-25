----------------------------- MODULE GuestSeats -----------------------------
EXTENDS Naturals, FiniteSets
CONSTANTS Seats, CheckEnd
VARIABLES state, wrong, token, joins, waiting, ended, clock, deadline,
          hasMessage, badDisclosure
vars == <<state, wrong, token, joins, waiting, ended, clock, deadline,
          hasMessage, badDisclosure>>
Live(s) == state[s] = "joined" /\ token[s] /\ clock < deadline[s]
Init == /\ state = [s \in Seats |-> "invited"]
        /\ wrong = [s \in Seats |-> 0]
        /\ token = [s \in Seats |-> FALSE]
        /\ joins = [s \in Seats |-> 0]
        /\ waiting = [s \in Seats |-> 0]
        /\ ended = {} /\ clock = 0
        /\ deadline = [s \in Seats |-> 2]
        /\ hasMessage = FALSE /\ badDisclosure = FALSE
Wrong(s) == /\ state[s] = "invited" /\ clock < deadline[s] /\ wrong[s] < 5
            /\ wrong' = [wrong EXCEPT ![s] = @ + 1]
            /\ UNCHANGED <<state, token, joins, waiting, ended, clock, deadline,
                            hasMessage, badDisclosure>>
NewCode(s) == /\ state[s] = "invited" /\ clock < deadline[s]
              /\ wrong' = [wrong EXCEPT ![s] = 0]
              /\ UNCHANGED <<state, token, joins, waiting, ended, clock, deadline,
                              hasMessage, badDisclosure>>
Join(s) == /\ state[s] = "invited" /\ clock < deadline[s] /\ wrong[s] < 5
           /\ state' = [state EXCEPT ![s] = "joined"]
           /\ token' = [token EXCEPT ![s] = TRUE]
           /\ joins' = [joins EXCEPT ![s] = @ + 1]
           /\ UNCHANGED <<wrong, waiting, ended, clock, deadline, hasMessage, badDisclosure>>
End(s) == /\ state[s] # "ended"
          /\ state' = [state EXCEPT ![s] = "ended"]
          /\ token' = [token EXCEPT ![s] = FALSE]
          /\ ended' = ended \cup {s}
          /\ UNCHANGED <<wrong, joins, waiting, clock, deadline, hasMessage, badDisclosure>>
Authenticate(s) == /\ state[s] = "joined" /\ clock >= deadline[s]
                   /\ End(s)
StartWait(s) == /\ Live(s) /\ waiting[s] = 0 /\ ~hasMessage
                /\ waiting' = [waiting EXCEPT ![s] = @ + 1]
                /\ UNCHANGED <<state, wrong, token, joins, ended, clock, deadline,
                                hasMessage, badDisclosure>>
Cancel(s) == /\ waiting[s] = 1
             /\ waiting' = [waiting EXCEPT ![s] = 0]
             /\ UNCHANGED <<state, wrong, token, joins, ended, clock, deadline,
                             hasMessage, badDisclosure>>
Poll(s) == /\ waiting[s] = 1
           /\ waiting' = [waiting EXCEPT ![s] = 0]
           /\ badDisclosure' = (badDisclosure \/ (hasMessage /\ ~Live(s) /\ ~CheckEnd))
           /\ UNCHANGED <<state, wrong, token, joins, ended, clock, deadline, hasMessage>>
Message == /\ ~hasMessage /\ hasMessage' = TRUE
           /\ UNCHANGED <<state, wrong, token, joins, waiting, ended, clock, deadline, badDisclosure>>
Tick == /\ clock < 2 /\ clock' = clock + 1
        /\ UNCHANGED <<state, wrong, token, joins, waiting, ended, deadline, hasMessage, badDisclosure>>
Restart == /\ waiting' = [s \in Seats |-> 0]
           /\ UNCHANGED <<state, wrong, token, joins, ended, clock, deadline, hasMessage, badDisclosure>>
Next == (\E s \in Seats : Wrong(s) \/ NewCode(s) \/ Join(s) \/ End(s)
                         \/ Authenticate(s) \/ StartWait(s) \/ Cancel(s) \/ Poll(s))
        \/ Message \/ Tick \/ Restart
Spec == Init /\ [][Next]_vars
TypeOK == /\ state \in [Seats -> {"invited", "joined", "ended"}]
          /\ wrong \in [Seats -> 0..5] /\ token \in [Seats -> BOOLEAN]
          /\ joins \in [Seats -> 0..1] /\ waiting \in [Seats -> 0..1]
          /\ ended \subseteq Seats /\ clock \in 0..2
          /\ deadline \in [Seats -> 0..2]
          /\ hasMessage \in BOOLEAN /\ badDisclosure \in BOOLEAN
SingleUse == \A s \in Seats : joins[s] <= 1
EndedPermanent == \A s \in ended : state[s] = "ended" /\ ~token[s]
TokenScope == \A s \in Seats : token[s] => state[s] = "joined" /\ joins[s] = 1
FixedDeadline == \A s \in Seats : deadline[s] = 2
OneWait == \A s \in Seats : waiting[s] <= 1
NoDisclosureAfterEnd == ~badDisclosure
=============================================================================
