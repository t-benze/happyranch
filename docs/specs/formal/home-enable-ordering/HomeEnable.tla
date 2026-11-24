----------------------------- MODULE HomeEnable -----------------------------
EXTENDS Naturals
CONSTANTS EarlyRenewal, StrictCurrent
VARIABLES mode, prestate, shipped, policy, phase, renewals, failedRenewal,
          headscale, timer, target, startCurrent
vars == <<mode, prestate, shipped, policy, phase, renewals, failedRenewal,
          headscale, timer, target, startCurrent>>

Init == /\ mode \in {"forward", "oldIntent", "ready"}
        /\ prestate \in {"staged-inert", "post-disable"}
        /\ shipped \in BOOLEAN
        /\ policy \in {"current", "expired"}
        /\ phase = "capture"
        /\ renewals = 0 /\ failedRenewal = FALSE
        /\ headscale = FALSE /\ timer = FALSE /\ target = FALSE /\ startCurrent = FALSE

Capture == /\ phase = "capture"
           /\ phase' = IF mode = "forward" /\ shipped THEN "intent" ELSE "terminal"
           /\ UNCHANGED <<mode, prestate, shipped, policy, renewals, failedRenewal,
                          headscale, timer, target, startCurrent>>

Renew == /\ phase = "intent" /\ EarlyRenewal
         /\ \E result \in {"current", "noop", "failure"}:
              /\ renewals' = 1
              /\ failedRenewal' = (result = "failure")
              /\ policy' = IF result = "current" THEN "current" ELSE policy
              /\ phase' = IF result = "failure" THEN "terminal" ELSE "renewed"
         /\ UNCHANGED <<mode, prestate, shipped, headscale, timer, target, startCurrent>>

Start == /\ phase = (IF EarlyRenewal THEN "renewed" ELSE "intent")
         /\ (~StrictCurrent \/ policy = "current")
         /\ headscale' = TRUE /\ startCurrent' = (policy = "current") /\ phase' = "running"
         /\ UNCHANGED <<mode, prestate, shipped, policy, renewals, failedRenewal, timer, target>>

RefuseCurrent == /\ phase = "renewed" /\ StrictCurrent /\ policy # "current"
                 /\ phase' = "terminal"
                 /\ UNCHANGED <<mode, prestate, shipped, policy, renewals,
                                failedRenewal, headscale, timer, target, startCurrent>>

EnableTimer == /\ phase = "running" /\ timer' = TRUE /\ phase' = "timer"
               /\ UNCHANGED <<mode, prestate, shipped, policy, renewals,
                              failedRenewal, headscale, target, startCurrent>>
StartTarget == /\ phase = "timer" /\ target' = TRUE /\ phase' = "terminal"
               /\ UNCHANGED <<mode, prestate, shipped, policy, renewals,
                              failedRenewal, headscale, timer, startCurrent>>

Next == Capture \/ Renew \/ Start \/ RefuseCurrent \/ EnableTimer \/ StartTarget
Spec == Init /\ [][Next]_vars
TypeOK == /\ mode \in {"forward", "oldIntent", "ready"}
          /\ prestate \in {"staged-inert", "post-disable"} /\ shipped \in BOOLEAN
          /\ policy \in {"current", "expired"}
          /\ phase \in {"capture", "intent", "renewed", "running", "timer", "terminal"}
          /\ renewals \in 0..1
          /\ \A b \in {failedRenewal, headscale, timer, target, startCurrent}: b \in BOOLEAN
NoStartBeforeRenewal == headscale => renewals = 1
StartRequiresCurrent == headscale => startCurrent
FailureStartsNothing == failedRenewal => ~headscale /\ ~timer /\ ~target
NonForwardReadOnly == mode # "forward" => renewals = 0 /\ ~headscale /\ ~timer /\ ~target
TamperWritesNothing == ~shipped => renewals = 0 /\ ~headscale /\ ~timer /\ ~target
TimerBeforeTarget == target => timer
=============================================================================
