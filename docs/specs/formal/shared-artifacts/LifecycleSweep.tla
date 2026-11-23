------------------------ MODULE LifecycleSweep ------------------------
EXTENDS Naturals, Integers, TLC
CONSTANT BadFence
VARIABLES state, bytes, live, identity, mode, approved, now, deadline,
          intent, writer, proof, unsafe, early, audited
vars == <<state,bytes,live,identity,mode,approved,now,deadline,
          intent,writer,proof,unsafe,early,audited>>
Init == /\ state = "active" /\ bytes = TRUE /\ live = FALSE /\ identity = TRUE
        /\ mode = "preview" /\ approved = FALSE /\ now = 0 /\ deadline = -1
        /\ intent = "none" /\ writer = "none" /\ proof = FALSE
        /\ unsafe = FALSE /\ early = FALSE /\ audited = FALSE
Tick == /\ now < 16 /\ now' = now+1
        /\ UNCHANGED <<state,bytes,live,identity,mode,approved,deadline,intent,writer,proof,unsafe,early,audited>>
Approve == /\ writer = "none" /\ approved' = TRUE
           /\ UNCHANGED <<state,bytes,live,identity,mode,now,deadline,intent,writer,proof,unsafe,early,audited>>
SetMode(m) == /\ writer = "none" /\ mode' = m
              /\ UNCHANGED <<state,bytes,live,identity,approved,now,deadline,intent,writer,proof,unsafe,early,audited>>
Arrive == /\ bytes /\ (writer = "none" \/ BadFence) /\ live' = TRUE
          /\ UNCHANGED <<state,bytes,identity,mode,approved,now,deadline,intent,writer,proof,unsafe,early,audited>>
EndReference == /\ writer = "none" /\ live' = FALSE
                /\ UNCHANGED <<state,bytes,identity,mode,approved,now,deadline,intent,writer,proof,unsafe,early,audited>>
Replace == /\ bytes /\ identity' = FALSE
           /\ UNCHANGED <<state,bytes,live,mode,approved,now,deadline,intent,writer,proof,unsafe,early,audited>>
Expire == /\ writer = "none" /\ state = "active" /\ now >= 1
          /\ mode = "enforce" /\ approved /\ ~live
          /\ state' = "recoverable" /\ deadline' = now+7
          /\ UNCHANGED <<bytes,live,identity,mode,approved,now,intent,writer,proof,unsafe,early,audited>>
Prepare == /\ writer = "none" /\ state = "recoverable" /\ now >= deadline
           /\ mode = "enforce" /\ approved /\ ~live /\ identity /\ bytes
           /\ intent' = "issued"
           /\ UNCHANGED <<state,bytes,live,identity,mode,approved,now,deadline,writer,proof,unsafe,early,audited>>
Fence == /\ writer = "none" /\ intent = "issued" /\ mode = "enforce" /\ approved
         /\ writer' = "sweep" /\ proof' = ~live
         /\ UNCHANGED <<state,bytes,live,identity,mode,approved,now,deadline,intent,unsafe,early,audited>>
Unlink == /\ writer = "sweep" /\ intent = "issued" /\ proof /\ identity /\ bytes
          /\ mode = "enforce" /\ approved /\ now >= deadline
          /\ bytes' = FALSE /\ unsafe' = live /\ early' = (now < deadline)
          /\ UNCHANGED <<state,live,identity,mode,approved,now,deadline,intent,writer,proof,audited>>
Finalize == /\ writer = "sweep" /\ intent = "issued" /\ ~bytes
            /\ state' = "deleted" /\ intent' = "committed" /\ audited' = TRUE
            /\ writer' = "none" /\ proof' = FALSE
            /\ UNCHANGED <<bytes,live,identity,mode,approved,now,deadline,unsafe,early>>
Crash == /\ writer = "sweep" /\ writer' = "none" /\ proof' = FALSE
         /\ UNCHANGED <<state,bytes,live,identity,mode,approved,now,deadline,intent,unsafe,early,audited>>
RecoverMissing == /\ writer = "none" /\ intent = "issued" /\ ~bytes /\ ~live
                  /\ mode = "enforce" /\ approved
                  /\ state' = "deleted" /\ intent' = "committed" /\ audited' = TRUE
                  /\ UNCHANGED <<bytes,live,identity,mode,approved,now,deadline,writer,proof,unsafe,early>>
Next == Tick \/ Approve \/ (\E m \in {"preview","off","enforce"}: SetMode(m))
        \/ Arrive \/ EndReference \/ Replace \/ Expire \/ Prepare \/ Fence
        \/ Unlink \/ Finalize \/ Crash \/ RecoverMissing
TypeOK == /\ state \in {"active","recoverable","deleted"}
          /\ now \in 0..16 /\ deadline \in -1..23
          /\ intent \in {"none","issued","committed"} /\ writer \in {"none","sweep"}
NoLiveUnlink == ~unsafe
FullRecovery == ~early
ExplainableBytes == ~bytes => intent \in {"issued","committed"}
TombstoneAudit == state = "deleted" => ~bytes /\ audited /\ intent = "committed"
Spec == Init /\ [][Next]_vars
=============================================================================
