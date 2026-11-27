-------------------------- MODULE HomePortUpgrade --------------------------
EXTENDS Naturals
CONSTANT VersionFence
VARIABLE s
Protected(x) == <<x.receipt, x.units, x.sidecar, x.trust, x.writes, x.stops>>
Init == s = [build |-> "old", receipt |-> "ready-old", units |-> "active",
             sidecar |-> "legacy", trust |-> "old", writes |-> 0, stops |-> 0,
             reply |-> "none", before |-> <<"ready-old", "active", "legacy", "old", 0, 0>>,
             accepted |-> "none", effectBuild |-> "none", effect |-> "none"]
Install == \E build \in {"old", "new"}:
             s' = [s EXCEPT !.build = build, !.reply = "none"]
OldOff == /\ s.build = "old" /\ s.receipt = "ready-old"
          /\ s' = [s EXCEPT !.receipt = "off", !.units = "inactive", !.trust = "none",
                   !.writes = @ + 1, !.stops = @ + 1, !.reply = "off",
                   !.effect = "stop-old", !.effectBuild = "old", !.accepted = "none"]
RefuseLegacy == /\ VersionFence /\ s.build = "new" /\ s.receipt = "ready-old"
                /\ \E method \in {"on", "off"}:
                     s' = [s EXCEPT !.reply = "older-version", !.before = Protected(s)]
UnsafeOn == /\ ~VersionFence /\ s.build = "new" /\ s.receipt = "ready-old"
            /\ s' = [s EXCEPT !.accepted = "old", !.reply = "ready"]
UnsafeOff == /\ ~VersionFence /\ s.build = "new" /\ s.receipt = "ready-old"
             /\ s' = [s EXCEPT !.stops = @ + 1, !.receipt = "off",
                      !.units = "inactive", !.trust = "none", !.writes = @ + 1,
                      !.reply = "off", !.effect = "stop-old", !.effectBuild = "new"]
NewOn == /\ s.build = "new" /\ s.receipt = "off" /\ s.units = "inactive"
         /\ s.sidecar \in {"legacy", "current"} /\ s.trust = "none" /\ s.writes < 4
         /\ s' = [s EXCEPT !.receipt = "ready-new", !.units = "active",
                  !.sidecar = "current", !.trust = "fresh", !.accepted = "new",
                  !.writes = @ + 1, !.reply = "ready", !.effect = "start-new",
                  !.effectBuild = "new"]
NewOff == /\ s.build = "new" /\ s.receipt = "ready-new" /\ s.writes < 4
          /\ s' = [s EXCEPT !.receipt = "off", !.units = "inactive", !.trust = "none",
                   !.writes = @ + 1, !.stops = @ + 1, !.reply = "off",
                   !.accepted = "none", !.effect = "stop-new", !.effectBuild = "new"]
TamperRetained == /\ s.receipt = "off" /\ s.sidecar # "tampered"
                  /\ s' = [s EXCEPT !.sidecar = "tampered", !.reply = "none"]
RefuseTamper == /\ s.build = "new" /\ s.receipt = "off" /\ s.sidecar = "tampered"
                /\ s' = [s EXCEPT !.reply = "invalid", !.before = Protected(s)]
Next == Install \/ OldOff \/ RefuseLegacy \/ UnsafeOn \/ UnsafeOff
        \/ NewOn \/ NewOff \/ TamperRetained \/ RefuseTamper
Spec == Init /\ [][Next]_s
RefusalReadOnly == s.reply \in {"older-version", "invalid"} => Protected(s) = s.before
NewOnlyAdmission == s.accepted # "old"
OldStopUsesOldBuild == s.effect = "stop-old" => s.effectBuild = "old"
NoTrustMigration == s.receipt = "ready-new" => s.sidecar = "current" /\ s.trust = "fresh"
=============================================================================
