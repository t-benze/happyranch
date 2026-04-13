# CrewAI Implementation Blueprint

Architectural bridge between the org design (charter, system prompts, escalation rules) and a working CrewAI implementation. No code — just the blueprint.

This document was split into focused modules for easier reference during implementation:

| Document | What it covers |
|----------|---------------|
| [05a-crews.md](05a-crews.md) | Concept mapping, all 4 crew definitions, agent tools, what CrewAI handles vs. what you build |
| [05b-agent-runtime.md](05b-agent-runtime.md) | Agent execution model (provider-agnostic executors), memory architecture, lifecycle & scheduling, concurrency, cost profile |
| [05c-orchestrator.md](05c-orchestrator.md) | Orchestrator responsibilities, inter-crew communication patterns, performance tier impact, permission & authority model, task state machine |
| [05d-feishu.md](05d-feishu.md) | Founder interaction via Feishu — hybrid bot architecture, group chat structure, 4 notification tiers, reply parsing, quick commands |
| [05e-dashboard.md](05e-dashboard.md) | Self-hosted founder dashboard (6 pages), REST API endpoints, connection to Feishu, suggested implementation order |

## Quick Reference

- **Start here**: [05a-crews.md](05a-crews.md) §1 for how org concepts map to CrewAI
- **First to implement**: Content Crew (see [05a-crews.md](05a-crews.md) §2.1 and [05e-dashboard.md](05e-dashboard.md) §4)
- **How agents actually run**: [05b-agent-runtime.md](05b-agent-runtime.md)
- **What you build custom**: [05c-orchestrator.md](05c-orchestrator.md) and [05a-crews.md](05a-crews.md) §4
