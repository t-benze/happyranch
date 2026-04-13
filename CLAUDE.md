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

The following documents are in the `protocol/` folder.

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
- **Orchestrator**: Custom Python application (EH-driven orchestration loop, performance scoring)
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode (audit logs, scorecards, task state)
- **Knowledge base**: Vector store with RAG (planned — not yet implemented)
- **LLM**: Anthropic Claude via Claude Code CLI
- **Hosting**: Local Mac Mini

## Implementation Order (follow this sequence)
1. ~~**Product & Engineering Crew**~~ done — Engineering Head + Product Manager + Dev Agent + Payment Agent with Claude Code executor. EH-driven orchestration loop (EH decides each step: delegate, handle directly, or escalate). Audit logging, agent memory, performance scoring all implemented.
2. ~~**Audit logging**~~ done — SQLite-backed audit logger with session start/end, completion reports, orchestration steps, escalations.
3. ~~**EH-driven orchestration**~~ done — Engineering Head analyzes each task and decides the approach. No hardcoded task chains. Max 10 orchestration steps before escalation.
4. ~~**Agent memory**~~ done — Persistent workspaces with CLAUDE.md, learnings.md, scorecard.md, recent_tasks.md. Context builder regenerates identity on tier changes.
5. ~~**Performance scoring**~~ done — Rolling 30-day scorecards, green/yellow/red tiers, exposed to EH via capabilities prompt.
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
- **Budget authority**: Auto-approved up to $200 USD single / $100/month recurring. Above that -> founder
- **Refund authority**: CX Manager up to $150 USD. Above that -> founder
- **Downtime tolerance**: 30 minutes max before escalation

## Directory Layout

Source code and protocol docs live in the repo. Runtime data lives in `~/.opc/` (configurable via `OPC_DATA_DIR`).

```
~/projects/my-opc/                     # Source code (this repo)
|-- CLAUDE.md
|-- pyproject.toml
|-- protocol/                          # Org charter, system prompts, escalation rules, blueprint
|   |-- 01-org-charter.md
|   |-- 02-system-prompts-managers.md
|   |-- 03-system-prompts-workers.md
|   |-- 04-escalation-rules.md
|   +-- 05*.md
|-- src/
|   |-- cli.py                         # Unified CLI entry point (`opc` command)
|   |-- config.py                      # Settings (OPC_ env prefix, paths, thresholds)
|   |-- models.py                      # Pydantic models + StrEnums
|   |-- orchestrator/
|   |   |-- orchestrator.py            # EH-driven loop: ask Engineering Head, execute decisions
|   |   |-- capabilities.py            # Builds capabilities prompt for EH decision sessions
|   |   |-- executor.py                # Spawns `claude -p` subprocess, reads completion_report.json
|   |   |-- performance_tracker.py     # 30-day rolling scorecards, tier calculation
|   |   |-- context_builder.py         # Generates CLAUDE.md + .claude/settings.json per agent workspace
|   |   +-- prompt_loader.py           # Parses system prompts from protocol markdown
|   |-- infrastructure/
|   |   |-- database.py                # SQLite (WAL mode), 4 tables, typed CRUD
|   |   +-- audit_logger.py            # Semantic logging (session, verdict, escalation, orchestration steps)
|   |-- agents/                        # Agent definitions (future)
|   |-- crews/                         # Crew definitions (future)
|   +-- tools/                         # Agent tools (future)
|-- tests/                             # 98 tests across 10 files
+-- docs/superpowers/
    |-- specs/                         # Design specs
    +-- plans/                         # Implementation plans

~/.opc/                                # Runtime data (OPC_DATA_DIR)
|-- opc.db                             # SQLite database
+-- workspaces/
    |-- engineering_head/
    |   |-- CLAUDE.md                  # Generated from protocol/02-system-prompts-managers.md
    |   |-- .claude/settings.json      # Permissions + PreToolUse hook (git pull all repos)
    |   |-- repos/                     # Git clones (supports multiple repos)
    |   |   |-- my-opc/
    |   |   +-- web-app/               # (if configured via OPC_REPOS)
    |   |-- learnings.md
    |   |-- scorecard.md
    |   +-- recent_tasks.md
    |-- product_manager/
    |   |-- ...
    |   +-- specs/
    |-- dev_agent/
    |   +-- ...
    +-- payment_agent/
        |-- ...
        +-- proposals/
```

## Configuration

All settings use the `OPC_` environment variable prefix. Defaults work out of the box for single-repo setups.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_DATA_DIR` | `~/.opc` | Runtime data directory (database, workspaces) |
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_DB_PATH` | `opc.db` | SQLite database filename (relative to data dir) |
| `OPC_WORKSPACES_DIR` | `workspaces` | Workspaces dirname (relative to data dir) |
| `OPC_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `OPC_REPOS` | *(auto-detected)* | Git repos for agent clones, JSON dict: `{"name": "url", ...}` |
| `OPC_MAX_ORCHESTRATION_STEPS` | `10` | Max EH decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

Multi-repo example in `.env`:
```
OPC_REPOS={"my-opc": "https://github.com/t-benze/my-opc.git", "web-app": "https://github.com/t-benze/web-app.git"}
```

If `OPC_REPOS` is not set, `opc init-agent` auto-detects the current git remote as a single repo.

## Code Style
- Type hints on all function signatures
- Pydantic v2 models for structured data, StrEnum for enumerations
- Tests for business logic (escalation rules, scoring, tier calculation)
- `from __future__ import annotations` in all source files

## Running Tests
```bash
uv run pytest tests/ -v
```

## Running the CLI
```bash
opc run --brief "Explore the payment module"                    # EH decides approach
opc run --task implement_feature --brief "Add Alipay support"   # with task type hint
opc tasks                    # list recent tasks
opc status TASK-001          # show task details
opc agents [--detail]        # show performance tiers
opc init-agent               # initialize all agent workspaces (repo clones + system prompts)
opc init-agent dev_agent     # initialize a specific agent
opc --db /path/to/db <cmd>   # use custom database
```

## Maintaining Documentation
- **README.md** is for end users of the system — only usage-related content (setup, CLI commands, configuration, agent workspaces). No developer internals, code style, directory layout, or implementation details.
- **CLAUDE.md** is for developers and AI agents working on the codebase — architecture, code patterns, directory layout, implementation order.

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., blueprint in `protocol/05c-orchestrator.md`)
2. Check existing code for patterns to follow — especially `src/orchestrator/` for the established patterns
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
