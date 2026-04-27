# Runtime Implementation Blueprint

Architectural bridge between the org design (charter, system prompts, escalation rules) and the actual runtime — a Python daemon that spawns Claude Code agent sessions, an EH-driven orchestration loop, and a shared knowledge base. No framework dependency.

This document was split into focused modules for easier reference during implementation:

| Document | What it covers |
|----------|---------------|
| [05b-agent-runtime.md](05b-agent-runtime.md) | Agent execution model (Claude Code subprocess executor), memory architecture, lifecycle & scheduling, concurrency, cost profile |
| [05c-orchestrator.md](05c-orchestrator.md) | Orchestrator responsibilities, inter-team communication, performance tier impact, permission & authority model, task state machine |
| [05e-dashboard.md](05e-dashboard.md) | Self-hosted founder dashboard (6 pages), REST API endpoints, suggested implementation order |

## Quick Reference

- **How agents actually run**: [05b-agent-runtime.md](05b-agent-runtime.md)
- **What the orchestrator owns**: [05c-orchestrator.md](05c-orchestrator.md)
