# Runtime Implementation Blueprint

Architectural bridge between the org design (charter, system prompts, escalation rules) and the actual runtime — a Python daemon that spawns Claude Code agent sessions, an EH-driven orchestration loop, and a shared knowledge base. No framework dependency.

This document was split into focused modules for easier reference during implementation:

| Document | What it covers |
|----------|---------------|
| [05b-agent-runtime.md](05b-agent-runtime.md) | Agent execution model (Claude Code subprocess executor), memory architecture, lifecycle & scheduling, concurrency, cost profile |
| [05c-orchestrator.md](05c-orchestrator.md) | Orchestrator responsibilities, inter-team communication, permission & authority model, task state machine |
| [05e-dashboard.md](05e-dashboard.md) | Self-hosted founder dashboard (6 pages), REST API endpoints, suggested implementation order |

## Quick Reference

- **How agents actually run**: [05b-agent-runtime.md](05b-agent-runtime.md)
- **What the orchestrator owns**: [05c-orchestrator.md](05c-orchestrator.md)
- **Pre-escalation authority hook (THR-181 Track A)**: before a current manager-owned Engineering root's proposed escalation is committed, the orchestrator runs exactly one audited LLM authority evaluation of the proposed reason against the immutable, release-controlled policy `engineering/pre-escalation-authority@v1` (`runtime/orchestrator/authority_policy.py`, `runtime/orchestrator/authority.py`, wired at the `run_step_impl` escalate commit point). The policy is semantic authority only; server-owned mechanical fences are non-overridable. Semantic results are `ESCALATE` (existing path) and `CONTINUE_SAME_ROOT` (named clause + exact permitted action; same-root return to pending + re-enqueue). Everything else fails closed to ESCALATE. Full contract: [05c-orchestrator.md](05c-orchestrator.md) §THR-181.
