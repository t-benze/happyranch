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
- **Language**: Python 3.11+
- **Agent framework**: CrewAI (for individual crew execution)
- **Orchestrator**: Custom Python application (manages inter-crew communication, escalation routing, performance scoring)
- **Knowledge base**: Vector store with RAG (consider ChromaDB or similar for local hosting)
- **Database**: SQLite for prototype (audit logs, scorecards, task state)
- **LLM**: Anthropic Claude API (configurable — see config for provider abstraction)
- **Hosting**: Local Mac Mini

## Implementation Order (follow this sequence)
1. **Product & Engineering Crew** — Engineering Head (manager) + Product Manager + Dev Agent + Payment Agent with Claude Code executor. Get spec → implement → review working end-to-end.
2. **Audit logging** — Wrap crew with callbacks that log every task, completion report, and review to SQLite.
3. **Revision loop** — Orchestrator re-runs when Engineering Head returns REVISE. Track revision count. Escalate after 2 rounds.
4. **Agent memory** — Persistent workspaces with learnings write-back, scorecard injection, periodic consolidation.
5. **Performance scoring** — Score agents after each crew run. Store rolling 30-day scorecards. Implement tier-based task chain adjustment.
6. **Content Crew** — Content Writer + QA Agent + SEO Agent + Content Manager.
7. **Ops Crew** — Partner Liaison + Compliance Agent + Operations Manager. Enables real cross-crew audits for payment changes.
8. **Inter-Crew communication** — Orchestrator routes tasks between Crews.
9. **CX Crew** — Support Agent may run as persistent agent for real-time chat, not batch CrewAI.
10. **Feishu integration** — Bot architecture, notification tiers, reply parsing.
11. **Founder dashboard** — Aggregate audit logs, escalation summaries, scorecards into weekly view.

## Key Constraints
- **Three jurisdictions**: Mainland China (PIPL, CSL, DSL), Hong Kong (PDPO), Macau (PDPA) — all must be complied with simultaneously
- **PCI-DSS**: No raw card data storage — ever
- **Political sensitivity**: Any content about China/HK/Macau relations escalates to founder
- **Budget authority**: Auto-approved up to $200 USD single / $100/month recurring. Above that → founder
- **Refund authority**: CX Manager up to $150 USD. Above that → founder
- **Downtime tolerance**: 30 minutes max before escalation

## Project Structure (target)
```
opc-org/
├── CLAUDE.md                          # This file
├── README.md
├── 01-org-charter.md                  # Org charter
├── 02-system-prompts-managers.md      # Manager agent prompts
├── 03-system-prompts-workers.md       # Worker agent prompts
├── 04-escalation-rules.md            # Escalation routing rules
├── 05-crewai-blueprint.md            # CrewAI implementation blueprint
├── pyproject.toml                     # Python project config
├── src/
│   ├── agents/                        # Agent definitions (CrewAI Agent configs)
│   │   ├── content_writer.py
│   │   ├── qa_agent.py
│   │   ├── content_manager.py
│   │   └── ...
│   ├── crews/                         # Crew definitions (CrewAI Crew configs)
│   │   ├── content_crew.py
│   │   ├── product_crew.py
│   │   ├── ops_crew.py
│   │   └── cx_crew.py
│   ├── orchestrator/                  # Custom orchestrator layer
│   │   ├── orchestrator.py            # Main orchestrator
│   │   ├── escalation_router.py       # 12 escalation rules
│   │   ├── inter_crew_comms.py        # Cross-crew task routing
│   │   └── performance_tracker.py     # Scoring and tier management
│   ├── infrastructure/
│   │   ├── audit_logger.py            # Structured JSON logging to SQLite
│   │   ├── knowledge_base.py          # RAG layer with scoped access
│   │   └── database.py                # SQLite setup and queries
│   ├── tools/                         # CrewAI tools for agents
│   │   ├── shared_tools.py            # KB access, escalate, completion report
│   │   ├── content_tools.py           # Web search, source checking
│   │   ├── qa_tools.py                # Link checking, rate checking
│   │   └── ...
│   └── config.py                      # LLM provider, thresholds, feature flags
├── tests/
│   ├── test_content_crew.py
│   ├── test_escalation_router.py
│   └── ...
├── workspaces/                        # Persistent agent workspaces
│   ├── engineering_head/
│   ├── product_manager/
│   ├── dev_agent/
│   ├── payment_agent/
│   └── ...
├── knowledge_base/                    # Static KB content (org charter, SOPs)
│   ├── charter.md
│   └── sops/
└── scripts/
    ├── run_product_crew.py            # CLI to run a product & engineering task
    └── dashboard.py                   # Founder dashboard generator
```

## Code Style
- Type hints on all function signatures
- Dataclasses or Pydantic for structured data
- Docstrings on public functions
- Tests for business logic (escalation rules, scoring, tier calculation)

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., blueprint §2 for crew definitions)
2. Check existing code for patterns to follow
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
