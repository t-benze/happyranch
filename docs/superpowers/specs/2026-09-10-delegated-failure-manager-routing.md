# Delegated failure manager routing

> Status: implemented
> Current Source: `docs/agent-guides/orchestrator-contracts.md`

An exhausted retry lineage remains durable evidence for the owning manager.
The runtime does not raise `runtime_retry_ceiling`, fail a nested decision
owner upward, or create a successor. The manager decides revised work using
the existing mechanically validated failed-child provenance link, or proposes
escalation through the existing THR-181 authority path. Fanout waits for live
siblings, carriers fail closed, and completed or superseded descendants retire
historical failed leaves.

For a current successor root, the mechanical link may name the same-agent
FAILED child of a predecessor root only through the bounded recorded-
supersession verifier (D/R/H/HT/M producers; at most 20 root records / 19
edges). Final single/fanout spawn repeats the original-claim and lineage reads
after `BEGIN IMMEDIATE`; `Committed` alone permits enqueue, `InvalidLineage`
uses owned atomic feedback, and `LostClaim` writes nothing. The failed child
keeps its original parent and history, and an unresolved local failure cannot
be bypassed by a remote historical link.

Authority-v2 refusal and non-root manager `supersede` are also bounded
delegated-failure sources (THR-277). Non-root authority-v2 refusal records
`authority_v2_refusal_task_failed`, terminalizes the child as FAILED, and wakes
the same owning manager; only structural roots enter the founder-escalation
lifecycle. A still-current non-root owner returning `supersede` likewise fails
that child through the ordinary terminal tail and wakes its parent, without a
successor or manager-supersession row. Cancellation or claim replacement before
the ownership-fenced transition leaves the child and parent untouched.
