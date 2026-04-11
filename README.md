# Multi-Agent Tourism Organization

A one-person company architecture using AI agents to provide online tourism information and booking services for foreign tourists visiting mainland China, Hong Kong, and Macau.

## Architecture Overview

The organization is structured in layers: a founder (human) who sets rules and handles escalations, 4 manager agents who supervise and cross-audit each other, 8 worker agents who execute tasks, and 3 infrastructure services (audit logger, escalation router, knowledge base).

## Documents

| File | Description |
|------|-------------|
| `01-org-charter.md` | Mission, brand voice, risk tolerance, partner standards, compliance requirements |
| `02-system-prompts-managers.md` | System prompts for 4 manager agents with accountability contracts |
| `03-system-prompts-workers.md` | System prompts for 8 worker agents with accountability contracts |
| `04-escalation-rules.md` | 12 routing rules, manager-resolvable categories, peer audit triggers |
| `05-crewai-blueprint.md` | CrewAI implementation blueprint — crew definitions, orchestrator design, tool assignments |

## Design Principles

- Founder defines rules and goals; agents operate autonomously within them
- Agents supervise each other (peer review + hierarchical review)
- Every agent is accountable — performance tracked, scored, and visible
- Separation of duties: no agent both proposes and approves consequential actions
- Escalation channels to founder for defined trigger conditions

## Tech Stack (Planned)

- **Orchestration**: CrewAI (individual crews) + custom orchestrator (inter-crew coordination)
- **Knowledge base**: Shared vector store (RAG)
- **Escalation router**: Rules-based triage engine
- **Audit logger**: Structured JSON logs
- **Performance tracker**: Scoring service with rolling 30-day scorecards
