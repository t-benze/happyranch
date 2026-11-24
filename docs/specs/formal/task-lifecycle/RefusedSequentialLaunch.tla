---------------------- MODULE RefusedSequentialLaunch ----------------------
EXTENDS Naturals, FiniteSets, Sequences

\* One queued first visit of either released engine-v2 graph. Durable writes
\* (refusal receipt plus Held/event/inbox) are abstracted as one transaction.
VARIABLES graph, phase, refusals, queued, journal, historical,
          inbox, heldEvents, publications, publicationsAtHold
vars == <<graph, phase, refusals, queued, journal, historical,
          inbox, heldEvents, publications, publicationsAtHold>>

HistoryIds == {"old-unsettled", "old-settled"}
Ids == HistoryIds \cup {"own"}
InitialValue(id) == IF id = "old-settled" THEN "unknown-settled"
                   ELSE "unknown-unsettled"
Initiator == "recorded-source"
Limit == 3

Init ==
    /\ graph \in {"coding.v3", "draft-review.v1"}
    /\ historical \in SUBSET HistoryIds
    /\ phase = "queued"
    /\ refusals = 0
    /\ queued = TRUE
    /\ journal = [id \in Ids |-> IF id \in historical THEN InitialValue(id) ELSE "none"]
    /\ inbox = <<>>
    /\ heldEvents = 0
    /\ publications = 0
    /\ publicationsAtHold = 0

\* queue_started catches the instruction refusal BEFORE TaskInvocation/prepare.
\* No owner or journal entry exists for this attempt; history is never settled.
Refuse ==
    /\ phase = "queued" /\ queued /\ refusals < Limit
    /\ refusals' = refusals + 1
    /\ queued' = FALSE
    /\ IF refusals + 1 = Limit
          THEN /\ phase' = "held"
               /\ inbox' = Append(inbox, Initiator)
               /\ heldEvents' = heldEvents + 1
               /\ publicationsAtHold' = publications
          ELSE UNCHANGED <<phase, inbox, heldEvents, publicationsAtHold>>
    /\ UNCHANGED <<graph, journal, historical, publications>>

\* An admitted renderer can also succeed before the cap. COMMIT authority,
\* cancellation and actual launch/closure remain separate Python obligations.
Render ==
    /\ phase = "queued" /\ queued /\ refusals < Limit
    /\ phase' = "rendered" /\ queued' = FALSE
    /\ UNCHANGED <<graph, refusals, journal, historical, inbox, heldEvents,
                    publications, publicationsAtHold>>

Prepare ==
    /\ phase = "rendered"
    /\ phase' = "prepared"
    /\ journal' = [journal EXCEPT !["own"] = "prepared"]
    /\ UNCHANGED <<graph, refusals, queued, historical, inbox, heldEvents,
                    publications, publicationsAtHold>>

Launch ==
    /\ phase = "prepared" /\ phase' = "launched"
    /\ UNCHANGED <<graph, refusals, queued, journal, historical, inbox, heldEvents,
                    publications, publicationsAtHold>>

\* discover_pause_work/reaper may republish only an eligible queued visit.
Reaper ==
    /\ phase = "queued" /\ ~queued /\ refusals < Limit
    /\ queued' = TRUE /\ publications' = publications + 1
    /\ UNCHANGED <<graph, phase, refusals, journal, historical, inbox, heldEvents,
                    publicationsAtHold>>

\* Durable receipts, Held and inbox survive restart. No retry reset, unknown
\* removal, producer settlement or historical recovery action is introduced.
Restart == UNCHANGED vars
Next == Refuse \/ Render \/ Prepare \/ Launch \/ Reaper \/ Restart

TypeOK ==
    /\ graph \in {"coding.v3", "draft-review.v1"}
    /\ phase \in {"queued", "rendered", "prepared", "launched", "held"}
    /\ refusals \in 0..Limit /\ queued \in BOOLEAN
    /\ historical \in SUBSET HistoryIds
    /\ journal \in [Ids -> {"none", "prepared", "unknown-unsettled", "unknown-settled"}]
    /\ inbox \in {<<>>, <<Initiator>>} /\ heldEvents \in 0..1
    /\ publications \in 0..(Limit - 1) /\ publicationsAtHold \in 0..(Limit - 1)

\* Refused visits add zero entries. Only a successful render can reach prepare,
\* which adds at most the current attempt's single entry.
JournalGrowthBound ==
    /\ Cardinality({id \in Ids : journal[id] # "none"}) <= Cardinality(historical) + 1
    /\ (phase \in {"queued", "held"} =>
          Cardinality({id \in Ids : journal[id] # "none"}) = Cardinality(historical))
    /\ (journal["own"] # "none" => phase \in {"prepared", "launched"})

RefusalCap ==
    /\ refusals <= Limit
    /\ (refusals = Limit => phase = "held")
    /\ (phase = "held" => refusals = Limit)

HeldInboxAndNoPublication ==
    /\ (phase = "held" =>
          /\ inbox = <<Initiator>> /\ heldEvents = 1 /\ ~queued
          /\ publications = publicationsAtHold)
    /\ (phase # "held" => inbox = <<>> /\ heldEvents = 0)

HistoricalUnknownPreserved ==
    \A id \in historical : journal[id] = InitialValue(id)

\* Fair queue/host delivery is conditional, not an unconditional daemon promise.
Settles == <> (phase \in {"held", "launched"})
Spec == Init /\ [][Next]_vars
        /\ WF_vars(Reaper) /\ WF_vars(Refuse \/ Render)
        /\ WF_vars(Prepare) /\ WF_vars(Launch)
=============================================================================
