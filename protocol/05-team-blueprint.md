# Team Implementation Blueprint

Architectural bridge between the org design (charter, system prompts, escalation rules) and the actual runtime — a Python daemon that spawns Claude Code agent sessions, an EH-driven orchestration loop, and a shared knowledge base. No framework dependency.

This document was split into focused modules for easier reference during implementation:

| Document | What it covers |
|----------|---------------|
| [05a-teams.md](05a-teams.md) | Team definitions (Content, Product & Engineering, Ops, CX), the tasks each team owns, and the tools available to each agent |
| [05b-agent-runtime.md](05b-agent-runtime.md) | Agent execution model (Claude Code subprocess executor), memory architecture, lifecycle & scheduling, concurrency, cost profile |
| [05c-orchestrator.md](05c-orchestrator.md) | Orchestrator responsibilities, inter-team communication, performance tier impact, permission & authority model, task state machine |
| [05d-feishu.md](05d-feishu.md) | Founder interaction via Feishu — hybrid bot architecture, group chat structure, 4 notification tiers, reply parsing, quick commands |
| [05e-dashboard.md](05e-dashboard.md) | Self-hosted founder dashboard (6 pages), REST API endpoints, connection to Feishu, suggested implementation order |

## Quick Reference

- **Start here**: [05a-teams.md](05a-teams.md) §1 for how org concepts map to runtime primitives
- **First to implement**: Content Team (see [05a-teams.md](05a-teams.md) §2.1 and [05e-dashboard.md](05e-dashboard.md) §4)
- **How agents actually run**: [05b-agent-runtime.md](05b-agent-runtime.md)
- **What the orchestrator owns**: [05c-orchestrator.md](05c-orchestrator.md) and [05a-teams.md](05a-teams.md) §4
