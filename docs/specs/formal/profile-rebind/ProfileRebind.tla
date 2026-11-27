---------------------------- MODULE ProfileRebind ----------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANTS Consumers, Orgs, BrokenIdentity, BrokenClosure, BrokenStartup
VARIABLES pending, canonical, publishedRoster, phase, owner, binding, held,
          startupEvidence, startupChecked, startupHeld, nativeState
vars == <<pending, canonical, publishedRoster, phase, owner, binding, held,
          startupEvidence, startupChecked, startupHeld, nativeState>>
StartupVars == <<startupEvidence, startupChecked, startupHeld, nativeState>>
Evidence == {"matching", "unrelated", "ordinary", "mixed", "partial", "active", "live", "scoped",
             "ambiguous-remove", "other-invocation", "not-published", "missing-installed"}
Home == [c \in Consumers |-> c]
Forward == \A o \in Orgs : phase[o] = "prepared"
Finalized == \A o \in Orgs : phase[o] \in {"published", "later-installed"}

Init == /\ pending = Consumers
        /\ canonical = [c \in Consumers |-> "removed-profile"]
        /\ publishedRoster = canonical
        /\ phase = [o \in Orgs |-> "prepared"]
        /\ owner = "original-remove"
        /\ binding = [o \in Orgs |-> "original-remove"]
        /\ held = FALSE
        /\ startupEvidence \in Evidence
        /\ startupChecked = FALSE
        /\ startupHeld = TRUE
        /\ nativeState = IF startupEvidence = "active" THEN "active" ELSE "unknown"

Classify == /\ ~startupChecked
            /\ startupChecked' = TRUE
            /\ startupHeld' = IF BrokenStartup THEN FALSE ELSE
                    (startupEvidence # "matching" \/ ~(Forward \/ Finalized))
            /\ UNCHANGED <<pending, canonical, publishedRoster, phase, owner, binding, held, startupEvidence, nativeState>>

(* Finalized classification has no prepared journal to adopt or defer. *)
Adopt == /\ startupChecked /\ ~startupHeld /\ ~held /\ Forward
         /\ held' = TRUE
         /\ owner' = IF BrokenIdentity THEN "new-rebind" ELSE owner
         /\ UNCHANGED <<pending, canonical, publishedRoster, phase, binding, StartupVars>>

Move(c) == /\ held /\ c \in pending
           /\ pending' = pending \ {c}
           /\ canonical' = [canonical EXCEPT ![c] = "builtin"]
           /\ UNCHANGED <<publishedRoster, phase, owner, binding, held, StartupVars>>

Reconnect == /\ held /\ pending # {}
             /\ pending' = {}
             /\ canonical' = [c \in Consumers |-> IF c \in pending THEN "reconnected-profile" ELSE canonical[c]]
             /\ UNCHANGED <<publishedRoster, phase, owner, binding, held, StartupVars>>

Release == /\ held /\ held' = FALSE
           /\ UNCHANGED <<pending, canonical, publishedRoster, phase, owner, binding, StartupVars>>

Publish(o) == /\ startupChecked /\ ~startupHeld
              /\ phase[o] = "prepared"
              /\ IF BrokenClosure THEN {c \in pending : Home[c] = o} = {} ELSE pending = {}
              /\ phase' = [phase EXCEPT ![o] = "published"]
              /\ publishedRoster' = [c \in Consumers |-> IF Home[c] = o THEN canonical[c] ELSE publishedRoster[c]]
              /\ UNCHANGED <<pending, canonical, owner, binding, held, StartupVars>>

(* A later ordinary journal preserves the original installed remove evidence. *)
OrdinaryPublish(o) == /\ startupChecked /\ ~startupHeld /\ Finalized
                      /\ phase[o] = "published"
                      /\ phase' = [phase EXCEPT ![o] = "later-installed"]
                      /\ UNCHANGED <<pending, canonical, publishedRoster, owner, binding, held, StartupVars>>

(* Crash drops only process-local ownership. Original identity, operation kind,
   member set and generation are constants, never a new operation or owner. *)
Crash == /\ startupChecked /\ held' = FALSE
         /\ startupChecked' = FALSE /\ startupHeld' = TRUE
         /\ UNCHANGED <<pending, canonical, publishedRoster, phase, owner, binding, startupEvidence, nativeState>>

Next == Classify \/ Adopt \/ Reconnect \/ Release \/ Crash
        \/ (\E c \in Consumers : Move(c)) \/ (\E o \in Orgs : Publish(o) \/ OrdinaryPublish(o))
Spec == Init /\ [][Next]_vars
TypeOK == /\ pending \subseteq Consumers
          /\ canonical \in [Consumers -> {"removed-profile", "builtin", "reconnected-profile"}]
          /\ publishedRoster \in [Consumers -> {"removed-profile", "builtin", "reconnected-profile"}]
          /\ phase \in [Orgs -> {"prepared", "published", "later-installed"}]
          /\ held \in BOOLEAN
          /\ startupEvidence \in Evidence
          /\ startupChecked \in BOOLEAN /\ startupHeld \in BOOLEAN
          /\ nativeState \in {"active", "unknown"}
OriginalOwner == /\ owner = "original-remove"
                 /\ \A o \in Orgs : binding[o] = owner
ClosureFence == pending # {} => \A o \in Orgs : phase[o] = "prepared"
CurrentRoster == \A c \in Consumers : phase[Home[c]] \in {"published", "later-installed"} => publishedRoster[c] = canonical[c]
StartupIsolation == /\ (startupChecked /\ ~startupHeld => startupEvidence = "matching")
                    /\ (held => startupChecked /\ ~startupHeld)
EligibleStartup == startupChecked /\ startupEvidence = "matching" /\ (Forward \/ Finalized) => ~startupHeld
UnknownPreserved == startupEvidence # "active" => nativeState = "unknown"
=============================================================================
