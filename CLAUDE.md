# Project: OPC Multi-Agent Tourism Organization

## What This Is
A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting mainland China, Hong Kong SAR, and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## Architecture Summary
- **Layer 1**: Founder (human) — sets rules, handles escalations, reviews weekly dashboard
- **Layer 2**: 4 Manager Agents — Content Manager, Engineering Head, Operations Manager, CX Manager
- **Layer 3**: 9 Worker Agents — Content Writer, SEO Agent, QA Agent, Product Manager, Dev Agent, Payment Agent, Partner Liaison, Compliance Agent, Support Agent
- **Infrastructure**: Audit Logger, Escalation Router, Knowledge Base

Agents operate autonomously within defined authority. Managers cross-audit each other (peer review). No agent both proposes and approves consequential actions (maker-checker pattern).

## Design Documents (read these first)

The following documents are in the protocol folder.

- `01-org-charter.md` — Mission, brand voice, risk tolerance, budget caps, partner standards, compliance requirements across 3 jurisdictions
- `02-system-prompts-managers.md` — Full system prompts for all 4 manager agents with accountability contracts and performance tiers
- `03-system-prompts-workers.md` — Full system prompts for all 8 worker agents with accountability contracts and performance tiers
- `04-escalation-rules.md` — 12 routing rules (priority-ordered), manager-resolvable categories, peer audit triggers, structured request/response formats
- `05-crewai-blueprint.md` — Index pointing to the split blueprint documents:
  - `05a-crews.md` — Concept mapping, crew definitions, agent tools, CrewAI boundary
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, performance tiers, permissions, task state machine
  - `05d-feishu.md` — Founder interaction via Feishu, bot architecture, notification tiers
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order

## Tech Stack
- **Language**: Python 3.11+ (currently running 3.13)
- **Package manager**: `uv`
- **Agent executor**: Claude Code CLI (`claude -p "<prompt>" --permission-mode auto`) — no CrewAI dependency for now
- **Orchestrator**: Custom Python application (manages task routing, revision loop, performance scoring)
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode (audit logs, scorecards, task state)
- **Knowledge base**: Vector store with RAG (planned — not yet implemented)
- **LLM**: Anthropic Claude via Claude Code CLI
- **Hosting**: Local Mac Mini

## Implementation Order (follow this sequence)
1. ~~**Product & Engineering Crew**~~ ✅ — Engineering Head + Product Manager + Dev Agent + Payment Agent with Claude Code executor. Orchestrator, audit logging, revision loop, agent memory, performance scoring all implemented.
2. ~~**Audit logging**~~ ✅ — SQLite-backed audit logger with session start/end, completion reports, review verdicts, escalations, cross-audit stubs.
3. ~~**Revision loop**~~ ✅ — Max 2 revision rounds before escalation. Engineering Head reviews, routes feedback to target agent.
4. ~~**Agent memory**~~ ✅ — Persistent workspaces with CLAUDE.md, learnings.md, scorecard.md, recent_tasks.md. Context builder regenerates identity on tier changes.
5. ~~**Performance scoring**~~ ✅ — Rolling 30-day scorecards, green/yellow/red tiers, tier-dependent task chain adjustment.
6. **Content Crew** — Content Writer + QA Agent + SEO Agent + Content Manager.
7. **Ops Crew** — Partner Liaison + Compliance Agent + Operations Manager. Enables real cross-crew audits for payment changes.
8. **Inter-Crew communication** — Orchestrator routes tasks between Crews.
9. **CX Crew** — Support Agent may run as persistent agent for real-time chat, not batch.
10. **Feishu integration** — Bot architecture, notification tiers, reply parsing.
11. **Founder dashboard** — Aggregate audit logs, escalation summaries, scorecards into weekly view.

## Key Constraints
- **Three jurisdictions**: Mainland China (PIPL, CSL, DSL), Hong Kong (PDPO), Macau (PDPA) — all must be complied with simultaneously
- **PCI-DSS**: No raw card data storage — ever
- **Political sensitivity**: Any content about China/HK/Macau relations escalates to founder
- **Budget authority**: Auto-approved up to $200 USD single / $100/month recurring. Above that → founder
- **Refund authority**: CX Manager up to $150 USD. Above that → founder
- **Downtime tolerance**: 30 minutes max before escalation

## Project Structure
```
opc-org/
├── CLAUDE.md                          # This file
├── 01-org-charter.md                  # Org charter
├── 02-system-prompts-managers.md      # Manager agent prompts
├── 03-system-prompts-workers.md       # Worker agent prompts
├── 04-escalation-rules.md            # Escalation routing rules
├── 05-crewai-blueprint.md            # Blueprint index → 05a-05e
├── 05a-crews.md                       # Crew definitions, agent tools
├── 05b-agent-runtime.md              # Executor model, memory, lifecycle
├── 05c-orchestrator.md               # Orchestrator, tiers, permissions, state machine
├── 05d-feishu.md                      # Feishu bot architecture
├── 05e-dashboard.md                   # Dashboard layout, API endpoints
├── pyproject.toml                     # Python project config (uv / hatchling)
├── src/
│   ├── cli.py                         # Unified CLI entry point (`opc` command)
│   ├── config.py                      # Settings (OPC_ env prefix, paths, thresholds)
│   ├── models.py                      # Pydantic models + StrEnums (TaskStatus, TaskType, AgentName, etc.)
│   ├── orchestrator/
│   │   ├── orchestrator.py            # Main loop — create task, build chain, run steps, review loop
│   │   ├── executor.py                # Spawns `claude -p` subprocess, reads completion_report.json
│   │   ├── task_router.py             # Builds tier-dependent task chains per task type
│   │   ├── revision_loop.py           # Max-rounds escalation logic
│   │   ├── performance_tracker.py     # 30-day rolling scorecards, tier calculation
│   │   └── context_builder.py         # Generates CLAUDE.md + .claude/settings.json per agent workspace
│   ├── infrastructure/
│   │   ├── database.py                # SQLite (WAL mode), 4 tables, typed CRUD
│   │   └── audit_logger.py            # Semantic logging (session, verdict, escalation, cross-audit)
│   ├── agents/                        # Agent definitions (future — Content/Ops/CX crews)
│   ├── crews/                         # Crew definitions (future)
│   └── tools/                         # Agent tools (future)
├── tests/                             # 78 tests across 10 files
│   ├── conftest.py                    # Shared fixtures (tmp_dir, test_settings)
│   ├── test_models.py
│   ├── test_database.py
│   ├── test_audit_logger.py
│   ├── test_task_router.py
│   ├── test_revision_loop.py
│   ├── test_performance_tracker.py
│   ├── test_context_builder.py
│   ├── test_executor.py
│   ├── test_orchestrator.py
│   └── test_cli.py
├── workspaces/                        # Persistent agent workspaces (created at runtime)
├── docs/superpowers/
│   ├── specs/                         # Design specs
│   └── plans/                         # Implementation plans
└── knowledge_base/                    # Static KB content (future)
```

## Code Style
- Type hints on all function signatures
- Pydantic v2 models for structured data, StrEnum for enumerations
- Tests for business logic (escalation rules, scoring, tier calculation)
- `from __future__ import annotations` in all source files

## Running Tests
```bash
uv run pytest tests/ -v
# or with the venv directly:
.venv313/bin/python -m pytest tests/ -v
```

## Running the CLI
```bash
opc run --task implement_feature --brief "Add Alipay support" [--verbose]
opc tasks                    # list recent tasks
opc status TASK-001          # show task details
opc agents [--detail]        # show performance tiers
opc init                     # initialize workspaces + database
opc --db /path/to/db <cmd>   # use custom database
```

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., blueprint §2 for crew definitions)
2. Check existing code for patterns to follow — especially `src/orchestrator/` for the established patterns
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
