-------------------------- MODULE HomeDaemonRetry --------------------------
EXTENDS Naturals
CONSTANT Limited
VARIABLES daemon, connector, sidecar, attempts
vars == <<daemon, connector, sidecar, attempts>>
Init == /\ daemon = FALSE /\ connector = "starting"
        /\ sidecar = "inactive" /\ attempts = 0
Return == /\ ~daemon /\ daemon' = TRUE
          /\ UNCHANGED <<connector, sidecar, attempts>>
Timeout == /\ connector = "starting" /\ ~daemon
           /\ connector' = IF Limited /\ attempts = 5 THEN "failed" ELSE "starting"
           /\ attempts' = IF attempts < 5 THEN attempts + 1 ELSE attempts
           /\ UNCHANGED <<daemon, sidecar>>
Ready == /\ connector = "starting" /\ daemon
         /\ connector' = "ready" /\ UNCHANGED <<daemon, sidecar, attempts>>
Follow == /\ connector = "ready" /\ sidecar = "inactive"
          /\ sidecar' = "ready" /\ UNCHANGED <<daemon, connector, attempts>>
Next == Return \/ Timeout \/ Ready \/ Follow
Spec == Init /\ [][Next]_vars /\ WF_vars(Return) /\ WF_vars(Ready) /\ WF_vars(Follow)
NoPrematureAdmission == connector = "ready" => daemon
SidecarFollowsConnector == sidecar = "ready" => connector = "ready"
Recovers == <> (connector = "ready" /\ sidecar = "ready")
=============================================================================
