------------------------ MODULE SharedArtifacts ------------------------
EXTENDS Naturals, Integers, FiniteSets, TLC
CONSTANT BadReservationRelease
Slots == 1..2
Quota == 5
DefaultGrant == 2160
ReferenceGrant == 4320
Timeout == 24
Times == {0,24,48,2160,2184,4320,4344}
VARIABLES phase, row, staged, public, reservation, size, state, expiry,
          grantAt, bound, now, current, lastAction, previousExpiry
vars == <<phase,row,staged,public,reservation,size,state,expiry,grantAt,
          bound,now,current,lastAction,previousExpiry>>
Sum(f) == f[1] + f[2]
Physical == Sum([s \in Slots |-> IF staged[s] \/ public[s] THEN size[s] ELSE 0])
Used == Physical + Sum(reservation)
Init == /\ phase = [s \in Slots |-> "absent"]
        /\ row = [s \in Slots |-> FALSE]
        /\ staged = [s \in Slots |-> FALSE]
        /\ public = [s \in Slots |-> FALSE]
        /\ reservation = [s \in Slots |-> 0]
        /\ size = [s \in Slots |-> 0]
        /\ state = [s \in Slots |-> "absent"]
        /\ expiry = [s \in Slots |-> 0]
        /\ grantAt = [s \in Slots |-> 0]
        /\ bound = [s \in Slots |-> FALSE]
        /\ now = 0 /\ current = 0
        /\ lastAction = "init" /\ previousExpiry = expiry
Reserve(s,n,provisional) ==
    /\ phase[s] \in {"absent","aborted"}
    /\ s = 1 \/ (s = 2 /\ current = 1 /\ state[1] = "active")
    /\ Used + n <= Quota
    /\ phase' = [phase EXCEPT ![s] = "reserved"]
    /\ row' = [row EXCEPT ![s] = TRUE]
    /\ reservation' = [reservation EXCEPT ![s] = n]
    /\ size' = [size EXCEPT ![s] = n]
    /\ state' = [state EXCEPT ![s] = IF provisional THEN "pending" ELSE "active"]
    /\ expiry' = [expiry EXCEPT ![s] = IF provisional THEN 0 ELSE now + DefaultGrant]
    /\ grantAt' = [grantAt EXCEPT ![s] = now]
    /\ bound' = [bound EXCEPT ![s] = ~provisional]
    /\ lastAction' = "reserve" /\ previousExpiry' = expiry
    /\ UNCHANGED <<staged,public,now,current>>
Stage(s) ==
    /\ phase[s] = "reserved"
    /\ phase' = [phase EXCEPT ![s] = "prepared"]
    /\ staged' = [staged EXCEPT ![s] = TRUE]
    /\ reservation' = [reservation EXCEPT ![s] = IF BadReservationRelease THEN @ ELSE 0]
    /\ lastAction' = "stage" /\ previousExpiry' = expiry
    /\ UNCHANGED <<row,public,size,state,expiry,grantAt,bound,now,current>>
Commit(s) ==
    /\ phase[s] = "prepared"
    /\ phase' = [phase EXCEPT ![s] = "committed"]
    /\ staged' = [staged EXCEPT ![s] = FALSE]
    /\ public' = [t \in Slots |-> t = s]
    /\ state' = [t \in Slots |-> IF public[t] /\ t # s THEN "replaced" ELSE state[t]]
    /\ reservation' = [reservation EXCEPT ![s] = 0]
    /\ current' = s
    /\ lastAction' = "commit" /\ previousExpiry' = expiry
    /\ UNCHANGED <<row,size,expiry,grantAt,bound,now>>
CrashAndReconcile(s) ==
    /\ phase[s] \in {"reserved","prepared"}
    /\ phase' = [phase EXCEPT ![s] = "aborted"]
    /\ staged' = [staged EXCEPT ![s] = FALSE]
    /\ reservation' = [reservation EXCEPT ![s] = 0]
    /\ state' = [state EXCEPT ![s] = "aborted"]
    /\ lastAction' = "recover" /\ previousExpiry' = expiry
    /\ UNCHANGED <<row,public,size,expiry,grantAt,bound,now,current>>
Bind(s) ==
    /\ phase[s] = "committed" /\ state[s] = "pending" /\ ~bound[s]
    /\ expiry[s] = 0 \/ now < expiry[s]
    /\ state' = [state EXCEPT ![s] = "active"]
    /\ bound' = [bound EXCEPT ![s] = TRUE]
    /\ grantAt' = [grantAt EXCEPT ![s] = now]
    /\ expiry' = [expiry EXCEPT ![s] = now + DefaultGrant]
    /\ lastAction' = "bind" /\ previousExpiry' = expiry
    /\ UNCHANGED <<phase,row,staged,public,reservation,size,now,current>>
Tick(t) ==
    /\ t \in Times /\ t > now
    /\ now' = t
    /\ expiry' = [s \in Slots |-> IF state[s] = "pending" /\ expiry[s] = 0 /\ t >= grantAt[s]+Timeout
                                 THEN grantAt[s]+DefaultGrant ELSE expiry[s]]
    /\ lastAction' = "timeout" /\ previousExpiry' = expiry
    /\ UNCHANGED <<phase,row,staged,public,reservation,size,state,grantAt,bound,current>>
Expire(s) ==
    /\ public[s] /\ state[s] \in {"active","pending"}
    /\ expiry[s] > 0 /\ now >= expiry[s]
    /\ state' = [state EXCEPT ![s] = "recoverable"]
    /\ lastAction' = "expire" /\ previousExpiry' = expiry
    /\ UNCHANGED <<phase,row,staged,public,reservation,size,expiry,grantAt,bound,now,current>>
Grant(s,grant,restore) ==
    /\ public[s] /\ grant \in {DefaultGrant,ReferenceGrant}
    /\ IF restore THEN state[s] = "recoverable" ELSE state[s] = "active"
    /\ state' = [state EXCEPT ![s] = "active"]
    /\ grantAt' = [grantAt EXCEPT ![s] = now]
    /\ expiry' = [expiry EXCEPT ![s] = now+grant]
    /\ lastAction' = IF restore THEN "restore" ELSE "renew"
    /\ previousExpiry' = expiry
    /\ UNCHANGED <<phase,row,staged,public,reservation,size,bound,now,current>>
Download == /\ lastAction' = "download" /\ previousExpiry' = expiry
            /\ UNCHANGED <<phase,row,staged,public,reservation,size,state,expiry,grantAt,bound,now,current>>
Next == (\E s \in Slots,n \in 1..3,p \in BOOLEAN: Reserve(s,n,p))
        \/ (\E s \in Slots: Stage(s) \/ Commit(s) \/ CrashAndReconcile(s) \/ Bind(s) \/ Expire(s))
        \/ (\E s \in Slots,g \in {DefaultGrant,ReferenceGrant},r \in BOOLEAN: Grant(s,g,r))
        \/ (\E t \in Times: Tick(t)) \/ Download
NoBytesWithoutRow == \A s \in Slots: (staged[s] \/ public[s]) => row[s]
CountedOnce == \A s \in Slots: (staged[s] \/ public[s]) => reservation[s] = 0
QuotaBound == Used <= Quota
BoundedExpiry == \A s \in Slots: expiry[s] = 0 \/ expiry[s] <= grantAt[s]+ReferenceGrant
DownloadsNeverRenew == lastAction = "download" => expiry = previousExpiry
ReleasedOnRecovery == \A s \in Slots: phase[s] = "aborted" => reservation[s] = 0 /\ ~staged[s]
TypeOK == /\ current \in 0..2 /\ now \in Times /\ row \in [Slots -> BOOLEAN]
          /\ phase \in [Slots -> {"absent","reserved","prepared","committed","aborted"}]
Spec == Init /\ [][Next]_vars
=============================================================================
