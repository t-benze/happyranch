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

Authority-v2 refusal is also a bounded delegated-failure source (THR-277).
Non-root refusal records `authority_v2_refusal_task_failed`, terminalizes the
child as FAILED, and wakes the same owning manager; only structural roots enter
the founder-escalation lifecycle.
