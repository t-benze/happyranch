---------------------------- MODULE ProfileRebind ----------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANTS Consumers, Orgs, BrokenIdentity, BrokenClosure
VARIABLES pending, canonical, publishedRoster, phase, owner, binding, held
vars == <<pending, canonical, publishedRoster, phase, owner, binding, held>>
Home == [c \in Consumers |-> c]

Init == /\ pending = Consumers
        /\ canonical = [c \in Consumers |-> "removed-profile"]
        /\ publishedRoster = canonical
        /\ phase = [o \in Orgs |-> "prepared"]
        /\ owner = "original-remove"
        /\ binding = [o \in Orgs |-> "original-remove"]
        /\ held = FALSE

Adopt == /\ ~held /\ \A o \in Orgs : phase[o] = "prepared"
         /\ held' = TRUE
         /\ owner' = IF BrokenIdentity THEN "new-rebind" ELSE owner
         /\ UNCHANGED <<pending, canonical, publishedRoster, phase, binding>>

Move(c) == /\ held /\ c \in pending
           /\ pending' = pending \ {c}
           /\ canonical' = [canonical EXCEPT ![c] = "builtin"]
           /\ UNCHANGED <<publishedRoster, phase, owner, binding, held>>

Reconnect == /\ held /\ pending # {}
             /\ pending' = {}
             /\ canonical' = [c \in Consumers |-> IF c \in pending THEN "reconnected-profile" ELSE canonical[c]]
             /\ UNCHANGED <<publishedRoster, phase, owner, binding, held>>

Release == /\ held /\ held' = FALSE
           /\ UNCHANGED <<pending, canonical, publishedRoster, phase, owner, binding>>

Publish(o) == /\ phase[o] = "prepared"
              /\ IF BrokenClosure THEN {c \in pending : Home[c] = o} = {} ELSE pending = {}
              /\ phase' = [phase EXCEPT ![o] = "published"]
              /\ publishedRoster' = [c \in Consumers |-> IF Home[c] = o THEN canonical[c] ELSE publishedRoster[c]]
              /\ UNCHANGED <<pending, canonical, owner, binding, held>>

(* Crash drops only process-local ownership. Original identity, operation kind,
   member set and generation are constants, never a new operation or owner. *)
Crash == /\ held /\ held' = FALSE
         /\ UNCHANGED <<pending, canonical, publishedRoster, phase, owner, binding>>

Next == Adopt \/ Reconnect \/ Release \/ Crash
        \/ (\E c \in Consumers : Move(c)) \/ (\E o \in Orgs : Publish(o))
Spec == Init /\ [][Next]_vars
TypeOK == /\ pending \subseteq Consumers
          /\ canonical \in [Consumers -> {"removed-profile", "builtin", "reconnected-profile"}]
          /\ publishedRoster \in [Consumers -> {"removed-profile", "builtin", "reconnected-profile"}]
          /\ phase \in [Orgs -> {"prepared", "published"}]
          /\ held \in BOOLEAN
OriginalOwner == /\ owner = "original-remove"
                 /\ \A o \in Orgs : binding[o] = owner
ClosureFence == pending # {} => \A o \in Orgs : phase[o] = "prepared"
CurrentRoster == \A c \in Consumers : phase[Home[c]] = "published" => publishedRoster[c] = canonical[c]
=============================================================================
