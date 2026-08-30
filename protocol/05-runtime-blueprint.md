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
- **Pre-escalation authority hook (THR-181 Track A)**: before a current manager-owned Engineering root escalates, exactly one audited LLM evaluation runs against immutable release policy. CONTINUE_SAME_ROOT requires the closed routine reason and clean server-derived fences, then atomically returns the same root to pending and mints a single-use lifecycle envelope. The next manager turn uses ordinary configured executor permissions and ordinary manager-decision validation; the envelope is not an exact-action whitelist. Same-root identity, cancellation, replay, CAS, budgets, protected boundaries, and the prohibition on supersession/revisit/fresh-root replacement remain daemon-owned. Evaluator error or ambiguity fails closed to ESCALATE. Full contract: [05c-orchestrator.md](05c-orchestrator.md) §THR-181.
