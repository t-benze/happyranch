-------------------------- MODULE HomeRetirement --------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANT SkipFreshOwnership, SkipFence

Devices == {"device-a", "device-b"}
Owners == Devices \cup {"removed-owner"}
Members == {"database", "private-keys", "unit", "activation"}
CloseStates == {"open", "inflight", "failed", "acknowledged"}
Observations == {"cutoff", "stop", "disable", "cleanup", "final"}
VARIABLE s
vars == <<s>>

Init == \E owned \in {"exact", "foreign"}, exclusive \in BOOLEAN :
  s = [fence |-> FALSE, burned |-> FALSE, denied |-> {},
       close |-> [d \in Owners |-> "open"], original |-> "live",
       owner |-> owned, exclusive |-> exclusive, stopped |-> FALSE,
       disabled |-> FALSE, removed |-> {}, account |-> "present",
       receipt |-> "absent", observed |-> {}, explicit |-> FALSE,
       available |-> TRUE, source |-> "old", stable |-> FALSE,
       reported |-> "pending", permit |-> FALSE, foreignEffects |-> 0,
       oldAccepted |-> FALSE, accountAuthority |-> FALSE, accountListener |-> FALSE]

Denied == s.burned /\ s.denied = Devices
Closed == (\A d \in Owners : s.close[d] = "acknowledged")
          \/ s.original = "dead"
CutoffFacts == s.fence /\ Denied /\ Closed
CleanupFacts == CutoffFacts /\ s.owner = "exact" /\ s.stopped /\ s.disabled
FinalFacts == CleanupFacts /\ s.removed = Members
              /\ s.account \in {"absent", "owned-disabled"}
Run == s.explicit /\ s.available
Own == s.owner = "exact" \/ SkipFreshOwnership

OldAdmission ==
  /\ ~s.fence \/ SkipFence
  /\ s' = [s EXCEPT !.oldAccepted = ~@]

\* Hypothetical later account authority is external to P24. It grants no old
\* mode fallback and never substitutes for retirement. No source lane exists.
LaterAccountAuthority ==
  /\ ~s.accountAuthority
  /\ s' = [s EXCEPT !.accountAuthority = TRUE]

AccountListen ==
  /\ s.accountAuthority /\ FinalFacts /\ s.receipt = "durable"
  /\ s.reported = "retired" /\ ~s.accountListener
  /\ s' = [s EXCEPT !.accountListener = TRUE]

SelectExplicit ==
  /\ ~s.explicit /\ s.available
  /\ s' = [s EXCEPT !.explicit = TRUE]

Fence ==
  /\ Run /\ ~s.fence
  /\ s' = [s EXCEPT !.fence = TRUE]

Burn ==
  /\ Run /\ s.fence /\ ~s.burned
  /\ s' = [s EXCEPT !.burned = TRUE]

Deny(d) ==
  /\ Run /\ s.fence /\ s.burned /\ d \notin s.denied
  /\ s' = [s EXCEPT !.denied = @ \cup {d}]

BeginClose(d) ==
  /\ Run /\ s.fence /\ s.burned
  /\ d \notin Devices \/ d \in s.denied
  /\ s.close[d] = "open"
  /\ s' = [s EXCEPT !.close[d] = "inflight"]

AckClose(d) ==
  /\ Run /\ s.close[d] = "inflight"
  /\ s' = [s EXCEPT !.close[d] = "acknowledged"]

FailClose(d) ==
  /\ ~s.stable /\ s.close[d] = "inflight"
  /\ s' = [s EXCEPT !.close[d] = "failed", !.reported = "pending"]

\* This is an environmental fact, not an empty-registry success assertion.
\* Original process death must be independently observed by the coordinator.
OriginalDies ==
  /\ ~s.stable /\ s.original = "live"
  /\ s' = [s EXCEPT !.original = "dead", !.observed = {}, !.reported = "pending"]

ObserveCutoff ==
  /\ Run /\ CutoffFacts /\ "cutoff" \notin s.observed
  /\ s' = [s EXCEPT !.observed = @ \cup {"cutoff"}]

Stop ==
  /\ Run /\ "cutoff" \in s.observed /\ CutoffFacts /\ Own /\ ~s.stopped
  /\ s' = [s EXCEPT !.stopped = TRUE,
             !.foreignEffects = @ + (IF s.owner = "exact" THEN 0 ELSE 1)]

ObserveStop ==
  /\ Run /\ CutoffFacts /\ s.owner = "exact" /\ s.stopped
  /\ "stop" \notin s.observed
  /\ s' = [s EXCEPT !.observed = @ \cup {"stop"}]

Disable ==
  /\ Run /\ "stop" \in s.observed /\ CutoffFacts /\ s.stopped
  /\ Own /\ ~s.disabled
  /\ s' = [s EXCEPT !.disabled = TRUE,
             !.foreignEffects = @ + (IF s.owner = "exact" THEN 0 ELSE 1)]

ObserveDisable ==
  /\ Run /\ CleanupFacts /\ "disable" \notin s.observed
  /\ s' = [s EXCEPT !.observed = @ \cup {"disable"}]

Remove(m) ==
  /\ Run /\ "disable" \in s.observed /\ CutoffFacts
  /\ s.stopped /\ s.disabled /\ Own /\ m \notin s.removed
  /\ s' = [s EXCEPT !.removed = @ \cup {m},
             !.foreignEffects = @ + (IF s.owner = "exact" THEN 0 ELSE 1)]

AccountDisposition ==
  /\ Run /\ CleanupFacts /\ s.removed = Members /\ s.account = "present"
  /\ s' = [s EXCEPT !.account = IF s.exclusive THEN "absent" ELSE "owned-disabled"]

ObserveCleanup ==
  /\ Run /\ FinalFacts /\ "cleanup" \notin s.observed
  /\ s' = [s EXCEPT !.observed = @ \cup {"cleanup"}]

WriteReceipt ==
  /\ Run /\ FinalFacts /\ "cleanup" \in s.observed
  /\ s.receipt \in {"absent", "torn"}
  /\ s' = [s EXCEPT !.receipt = "written"]

SyncReceipt ==
  /\ Run /\ FinalFacts /\ s.receipt = "written"
  /\ s' = [s EXCEPT !.receipt = "durable"]

ReadFinal ==
  /\ Run /\ FinalFacts /\ s.receipt = "durable"
  /\ "final" \notin s.observed
  /\ s' = [s EXCEPT !.observed = @ \cup {"final"}, !.reported = "retired"]

GateUnpack ==
  /\ Run /\ s.source = "old" /\ ~s.permit
  /\ FinalFacts /\ s.receipt = "durable" /\ "final" \in s.observed
  /\ s' = [s EXCEPT !.permit = TRUE]

\* Interrupted replacement retains denial/receipt, never restores authority.
ReplacePayload ==
  /\ ~s.stable /\ Run /\ s.source = "old" /\ s.permit
  /\ FinalFacts /\ s.receipt = "durable" /\ "final" \in s.observed
  /\ s' = [s EXCEPT !.source = "partial", !.available = FALSE,
             !.explicit = FALSE, !.observed = {}, !.reported = "pending"]

RestoreEntry ==
  /\ ~s.stable /\ s.source = "partial"
  /\ s' = [s EXCEPT !.source = "new", !.available = TRUE]

RollbackArtifact ==
  /\ ~s.stable /\ s.receipt = "durable" /\ s.owner = "exact"
  /\ s' = [s EXCEPT !.owner = "foreign", !.stopped = FALSE,
             !.disabled = FALSE, !.source = "rollback", !.observed = {},
             !.reported = "pending", !.permit = FALSE, !.accountListener = FALSE]

ReplaceForeign ==
  /\ ~s.stable /\ s.owner = "exact"
  /\ s' = [s EXCEPT !.owner = "foreign", !.stopped = FALSE,
             !.disabled = FALSE, !.observed = {}, !.reported = "pending",
             !.permit = FALSE, !.accountListener = FALSE]

Crash ==
  /\ ~s.stable /\ s.explicit
  /\ s' = [s EXCEPT !.explicit = FALSE, !.observed = {}, !.reported = "pending"]

InterruptWrite ==
  /\ ~s.stable /\ s.receipt = "written"
  /\ s' = [s EXCEPT !.receipt = "torn", !.explicit = FALSE,
             !.observed = {}, !.reported = "pending"]

\* Detection has no stop/disable/removal/deny/publication side effects.
Detect ==
  /\ ~s.explicit /\ s.available
  /\ s' = [s EXCEPT !.reported = IF FinalFacts /\ s.receipt = "durable"
                                 THEN "retired" ELSE "pending"]

\* Conditional completion begins only when exact ownership, trusted entry and
\* attainable original-owner cleanup proofs remain stable. Failed live cleanup
\* is excluded until independent original-process death is positively known.
Stabilize ==
  /\ ~s.stable /\ s.owner = "exact" /\ s.available
  /\ s.original = "dead" \/ (\A d \in Owners : s.close[d] # "failed")
  /\ s' = [s EXCEPT !.stable = TRUE]

Progress == SelectExplicit \/ Fence \/ Burn
  \/ (\E d \in Devices : Deny(d))
  \/ (\E d \in Owners : BeginClose(d) \/ AckClose(d))
  \/ ObserveCutoff \/ Stop \/ ObserveStop \/ Disable \/ ObserveDisable
  \/ (\E m \in Members : Remove(m)) \/ AccountDisposition
  \/ ObserveCleanup \/ WriteReceipt \/ SyncReceipt \/ ReadFinal \/ GateUnpack

Next == Progress \/ (\E d \in Owners : FailClose(d)) \/ OriginalDies
        \/ ReplaceForeign \/ Crash \/ InterruptWrite \/ Detect \/ Stabilize
        \/ ReplacePayload \/ RestoreEntry \/ RollbackArtifact
        \/ OldAdmission \/ LaterAccountAuthority \/ AccountListen

TypeOK ==
  /\ s.fence \in BOOLEAN /\ s.burned \in BOOLEAN /\ s.denied \subseteq Devices
  /\ s.close \in [Owners -> CloseStates] /\ s.original \in {"live", "dead"}
  /\ s.owner \in {"exact", "foreign"} /\ s.exclusive \in BOOLEAN
  /\ s.stopped \in BOOLEAN /\ s.disabled \in BOOLEAN /\ s.removed \subseteq Members
  /\ s.account \in {"present", "absent", "owned-disabled"}
  /\ s.receipt \in {"absent", "torn", "written", "durable"}
  /\ s.observed \subseteq Observations /\ s.explicit \in BOOLEAN
  /\ s.available \in BOOLEAN /\ s.source \in {"old", "partial", "new", "rollback"}
  /\ s.stable \in BOOLEAN /\ s.reported \in {"pending", "retired"}
  /\ s.permit \in BOOLEAN /\ s.foreignEffects \in Nat
  /\ s.oldAccepted \in BOOLEAN /\ s.accountAuthority \in BOOLEAN
  /\ s.accountListener \in BOOLEAN

NoForeignEffects == s.foreignEffects = 0
NoStopBeforeCutoff == s.stopped => CutoffFacts
NoDisableBeforeStop == s.disabled => s.stopped /\ CutoffFacts
NoCleanupBeforeQuiescence == s.removed # {} => CutoffFacts
RetiredTruthful == s.reported = "retired" => FinalFacts /\ s.receipt = "durable"
NoAccountGuess == s.account = "absent" => s.exclusive
GateAfterReadback == s.permit => FinalFacts /\ s.receipt = "durable"
NewListenerGated == s.accountListener => s.accountAuthority /\ FinalFacts /\ s.receipt = "durable"
DenialMonotone == [][s.denied \subseteq s'.denied /\ (s.burned => s'.burned)]_vars
FenceNeverReopened == [][s.fence => s'.fence]_vars
NoOldAdmissionAfterFence == [][s.fence => s'.oldAccepted = s.oldAccepted]_vars
RemovalAuthorized == [][s.removed # s'.removed => Run /\ CleanupFacts /\ "disable" \in s.observed]_vars
NoStartupDestruction == [][~s.explicit => s'.removed = s.removed
  /\ (~s.stopped => ~s'.stopped) /\ (~s.disabled => ~s'.disabled)]_vars

Spec == Init /\ [][Next]_vars
LiveSpec == Spec /\ WF_vars(Progress)
ConditionalCompletion == s.stable ~> (s.reported = "retired")
=============================================================================
