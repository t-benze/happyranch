# Content Team: thin-slice bring-up + team abstraction

## Summary

Bring up the Content Team (Content Manager + Content Writer + Content QA) and, in the same drop, generalize the orchestrator's hardcoded "engineering_head is the only manager" assumption. The Content Manager runs as a peer to the Engineering Head — it owns its own decision loop, its own workers, and can enroll/terminate agents on its team.

Out of scope for this drop: SEO Agent, inter-team handoffs, real cross-audit, task publication pipeline. Artifacts stay in each agent's workspace under `artifacts/<task-id>/`; "publishing" is a later, separate workflow.

In the same drop, we retire `TaskType` (which only ever drove a KB topic tag and a dead cross-audit stub) and replace its steering role with an explicit `team` field on the task.

## Design principles

- **Peer managers, not a hierarchy.** Content Manager has the same authority shape as EH over its own team — delegation, review, escalation, manage-agent within its team, KB delete.
- **Team composition is runtime config, not code.** Code owns the *abstraction* (`TeamManager`, `TeamsRegistry`); the actual roster lives in `<runtime>/teams.yaml`.
- **Protocol is the source of truth for agent permissions.** `### Allow Rules` subsections under each manager heading in `protocol/02-system-prompts-managers.md` replace the code-side `AGENT_EXTRA_ALLOWED_BASH_PREFIXES` constant.
- **No per-agent tool registries.** Content agents get the same `opc *` baseline as engineering agents plus whatever their protocol prompt declares.

## Team abstraction

### Code (`src/orchestrator/teams.py`)

```python
@dataclass(frozen=True)
class TeamManager:
    name: str                  # e.g. "engineering_head", "content_manager"
    team: str                  # e.g. "engineering", "content"
    workers: tuple[str, ...]

class TeamsRegistry:
    def __init__(self, teams: dict[str, TeamManager]): ...

    @classmethod
    def load(cls, runtime: RuntimeDir) -> TeamsRegistry: ...

    def save(self, runtime: RuntimeDir) -> None: ...              # atomic write

    def manager_for_team(self, team: str) -> TeamManager: ...
    def team_for_agent(self, name: str) -> str | None: ...
    def team_for_manager(self, manager_name: str) -> str | None: ...
    def is_team_manager(self, name: str) -> bool: ...
    def add_worker(self, team: str, agent: str) -> None: ...       # mutates + saves
    def remove_worker(self, team: str, agent: str) -> None: ...    # mutates + saves
    def teams(self) -> list[str]: ...
```

### Config (`<runtime>/teams.yaml`)

```yaml
teams:
  engineering:
    manager: engineering_head
    workers: [product_manager, dev_agent, payment_agent, qa_engineer]
  content:
    manager: content_manager
    workers: [content_writer, content_qa]
```

- Created on `opc init` with the default two-team layout declaring both teams and all built-in workers. `opc init-agent` reads the file to decide which workspaces to bootstrap — declaring a worker in `teams.yaml` is what tells `init-agent` "create this workspace."
- Mutated by `manage-agent enroll` / `terminate`: the enrolling manager's team gets the new worker appended. Cross-team enrollment is rejected at the daemon.
- Loaded into `DaemonState.teams: TeamsRegistry` at app startup and after every write.
- Writes serialized by a new `DaemonState.teams_lock` (same pattern as `kb_lock`).

### No `team` field in `agent.yaml` or `agent_enrollments`

The yaml is the single source of truth. Asking every consumer to reconcile three copies (code dict, yaml, DB column) is exactly the kind of denormalization that rots.

## Retiring `TaskType`

### Why retire

`TaskType` is currently a 4-value enum (`IMPLEMENT_FEATURE`, `BUG_FIX`, `PAYMENT_CHANGE`, `GENERAL`). Its only two real effects:

1. `src/daemon/routes/kb.py:202-205` — a three-entry `_TOPIC_FOR_TASK_TYPE` dict mapping it to a KB topic tag.
2. `src/infrastructure/audit_logger.py:111` — `log_cross_audit_stub(task_id, task_type)`, a dead placeholder for Ops Team cross-audit that always auto-approves.

Neither is load-bearing. The EH already treats `task_type` as a hint and ignores it in practice. Replacing routing with an explicit `--team` flag covers the one real use (steering the right manager) more directly.

### Changes

- **Delete** `TaskType` enum from `src/models.py`.
- **Delete** `task_type` field from `TaskRecord`.
- **Delete** `_TOPIC_FOR_TASK_TYPE` and the topic lookup it drives; KB topic is derived from `task.team` (`engineering` → `engineering`, `content` → `content`).
- **Delete** `AuditLogger.log_cross_audit_stub` and its lone call site.
- **Delete** `--task` flag from `opc run`; replace with `--team`.
- **DB migration:** add `team TEXT NOT NULL DEFAULT 'engineering'` column to `tasks`. Leave the old `task_type` column in place for now (backfill-safe); a later migration drops it once there are no historical readers.

## Task routing on `team`

### `opc run` CLI

```
opc run --team engineering --brief "Add Alipay support"          # default stays engineering
opc run --team content     --brief "Write Macau visa guide"
```

`--team` is required only if more than one team exists in `teams.yaml`; defaults to `engineering` otherwise. Invalid team names return `400 unknown_team` listing valid options.

### `run_step` generalization

Today's `run_step` hardcodes `engineering_head` in four places. Replace with registry lookups:

| Site | Current | New |
|------|---------|-----|
| `_default_root_agent` | returns `"engineering_head"` | returns `registry.manager_for_team(task.team).name` |
| decision-parsing gate | `if agent == "engineering_head"` | `if registry.is_team_manager(agent)` |
| capabilities injection | only for EH | for any team manager, listing **only its own team's workers** |
| "other agents" list in prompt | excludes EH's workspace | excludes the calling manager's workspace |
| verdict reviewer hardcode | `"engineering_head"` | whichever manager owns the parent task (`registry.team_for_agent(parent.assigned_agent)` → back to manager) |

### Cross-team delegation

The orchestrator enforces same-team delegation: when manager `M` delegates to worker `W`, `registry.team_for_agent(W)` must equal `registry.team_for_manager(M.name)`. Violations are classified as an EH/CM decision error, fed back into the next step with a diagnostic message, and counted against the 10-step runaway guard. No silent acceptance.

(Both registry methods return `str | None`; comparison is only considered a match when both sides are non-None and equal — `None == None` is not a same-team match.)

Cross-team work is a later feature (Ops cross-audit, CX → Content feedback tickets). The explicit rejection here keeps the invariant loud until we design that surface.

### Capabilities prompt scoping

`build_capabilities_prompt` currently lists every known agent. With multiple managers, each manager must only see its own team (otherwise a content manager would "see" `dev_agent` as a delegation target).

- Input: calling manager name.
- Output: prompt listing only `registry.manager_for_team(team_for_manager(manager_name)).workers`, with their tiers.

## Protocol-driven allow rules

### Markdown format

Under each manager's section in `protocol/02-system-prompts-managers.md`:

```markdown
## Engineering Head

...(existing sections)...

### Allow Rules

Beyond the baseline `opc *` grant, this agent may run:

- `gh pr close`
- `gh pr comment`
- `gh issue close`
- `gh issue comment`
```

Content Manager gets the same subsection with an empty bullet list (or omitted entirely — see parser contract).

### Parser (`src/orchestrator/prompt_loader.py`)

Add:

```python
def allow_rules_for(agent_name: str) -> tuple[str, ...]:
    """Extract the bullet list under an `### Allow Rules` subheading
    inside the agent's role section. Returns () if the subheading is
    absent or empty."""
```

- Reuses existing `_AGENT_SOURCES` dispatch.
- Section detection: locate the `## <Agent Name>` heading, then within it find `### Allow Rules`, then collect `- <prefix>` bullets until the next heading.
- No other markdown semantics — bullets are raw command prefixes.

### `workspace_adapters.py`

Delete `AGENT_EXTRA_ALLOWED_BASH_PREFIXES`. `allow_rules_for_agent(agent, cli=...)` becomes:

```python
def allow_rules_for_agent(agent: str, *, cli: bool) -> list[str]:
    baseline = _render_rule("opc", cli=cli)
    extras = [_render_rule(prefix, cli=cli) for prefix in prompt_loader.allow_rules_for(agent)]
    return [baseline, *extras]
```

`_render_rule("gh pr close", cli=False)` → `"Bash(gh pr close:*)"` (settings.json syntax).
`_render_rule("gh pr close", cli=True)`  → `"Bash(gh pr close *)"` (CLI `--allowedTools` syntax).

### Dynamic agents

`manage-agent` enrollment payloads can include `"allow_rules": ["prefix1", "prefix2"]`. Stored on the `agent_enrollments` row (JSON column). `allow_rules_for` consults the DB registry first (for dynamic agents) before falling back to protocol parsing (for built-ins).

## Content Team MVP flow

Root task: `opc run --team content --brief "Write Macau visa guide for UK tourists"`.

1. **Task enqueued**, `assigned_agent = content_manager`, `team = content`.
2. **CM step 1** — capabilities prompt lists `content_writer` and `content_qa` with tiers. CM decides `{"action": "delegate", "agent": "content_writer", "prompt": "<brief + structured instructions>"}`.
3. **Writer step** — spawns under same task id as a child, produces `artifacts/<task_id>/draft.md`, reports completion with `artifact_dir: "artifacts/<task_id>"` + `status: "completed"` + `summary`.
4. **CM step 2** — reads writer's completion via task history. Decides `{"action": "delegate", "agent": "content_qa", "prompt": "Review this draft. Use opc recall --fetch-artifact to read it. Return PASS / REVISE / REJECT with specific issues."}`.
5. **QA step** — returns a structured verdict in its completion `summary`. (QA's own `decision_json` is NULL — only managers write decisions.)
6. **CM step 3** — parses QA verdict from summary prose:
   - **PASS** → `{"action": "done", "summary": "..."}`. Task completes.
   - **REVISE** → `{"action": "delegate", "agent": "content_writer", "prompt": "<revision feedback>"}`. `revision_count++`.
   - **REJECT** → `{"action": "escalate", "category": "content_quality", "summary": "..."}`.
7. After 2 revision rounds, the orchestrator forces escalation (matches existing EH guard).

Orchestration-step budget (`OPC_MAX_ORCHESTRATION_STEPS`, default 10) is unchanged. A PASS path fits in ~3 steps; a one-revision path in ~5; two-revision path hits ~7.

### Revision tracking

Reuse the existing `revision_count` column on `TaskRecord`. Orchestrator bumps it when `decision.agent == previous_delegate_target_after_qa`. No new tables or columns.

## KB delete — team-manager scope

Per decision in brainstorming: any team manager can delete KB entries; the audit log distinguishes who did it.

- **`src/daemon/routes/kb.py`** — change `if not as_founder and agent != "engineering_head":` to `if not as_founder and not registry.is_team_manager(agent):`.
- **CLI** — `opc kb delete` requires `--rationale` unless `--as-founder` is set. Rationale is stored in the audit payload.
- **Audit** — existing `log_kb_delete(agent=..., slug=..., rationale=...)` already captures agent identity; no schema change.
- **Protocol** — `protocol/06-knowledge-base.md` section on delete authority is updated to "any team manager; founder via `--as-founder`".

## `manage-agent` — team-manager scope

Currently gated to EH via `_require_eh_auth` in `src/daemon/routes/agents.py`. Generalize:

- Rename helper: `_require_team_manager_auth(body, state) -> tuple[str, str]` returning `(manager_name, manager_team)`.
- Accepts either task-path (task_id + session_id, where `task.assigned_agent` is a team manager) or talk-path (open talk with a team manager).
- Enrollment/termination is scoped: the enrolling manager's team is the only team whose workers they can touch. Attempting to enroll a worker "into" another team returns `403 cross_team_forbidden`.
- `enrollments` listing remains founder-visible; managers see only their own team's enrollments (filter by `team = :calling_manager_team`).

## Workspace bootstrap

`opc init-agent` generalizes from the hardcoded list:

- Iterates `TeamsRegistry.all_agents()` (every manager + every worker across all teams) plus dynamic approved enrollments.
- For each, calls the existing `ClaudeWorkspaceAdapter` / `CodexWorkspaceAdapter` with protocol-loaded prompts.
- `_AGENT_SOURCES` in `prompt_loader.py` grows two entries (`content_qa` is already registered):
  - `content_manager` → `02-system-prompts-managers.md`, "Content Manager"
  - `content_writer` → `03-system-prompts-workers.md`, "Content Writer"

## Testing strategy

New unit tests:

- `tests/test_teams.py` — TeamsRegistry load/save, atomic persistence under temp runtime, add/remove worker, manager/team lookups, missing-file defaults.
- `tests/test_prompt_loader_allow_rules.py` — parses `### Allow Rules` bullets for EH; returns () for Content Manager (empty or missing); handles malformed sections gracefully.
- `tests/test_workspace_adapters_allow_rules.py` — `allow_rules_for_agent` composes baseline + protocol-derived extras in both settings.json and CLI syntax.

New daemon tests:

- `tests/daemon/test_manage_agent_team_scoping.py` — CM enrolls `content_writer` successfully; CM attempting to enroll into `engineering` team is rejected 403; EH still works unchanged.
- `tests/daemon/test_kb_delete_team_managers.py` — CM and EH can delete; worker `dev_agent` rejected 403; founder `--as-founder` always succeeds; audit entry records the calling manager's name.
- `tests/daemon/test_tasks_team_routing.py` — `POST /tasks` with `team=content` routes to `content_manager`; unknown team 400s.

New orchestrator tests:

- `tests/orchestrator/test_run_step_content_team.py` — scripted fake-executor flow for CM → writer → QA → PASS; REVISE path with revision_count bump; REJECT path with escalation.
- `tests/orchestrator/test_run_step_cross_team_rejection.py` — CM attempting to delegate to `dev_agent` yields a feedback step + orchestration_step_count increment (not a delegation).

Integration:

- `tests/integration/test_content_team_e2e.py` — daemon + fake Claude binaries scripted for each role; verify end-to-end run, artifact readable via `opc recall --fetch-artifact`, final task state `completed`.

Existing tests to update: every test that passes `task_type` needs to drop the field and pass `team` instead; every test that references `AGENT_EXTRA_ALLOWED_BASH_PREFIXES` or literal `"engineering_head"` in registry-relevant roles gets generalized.

## Migration notes

- **DB migration** (additive; no data loss):
  - `ALTER TABLE tasks ADD COLUMN team TEXT NOT NULL DEFAULT 'engineering';`
  - `agent_enrollments` — add `allow_rules TEXT DEFAULT '[]'` column (JSON array).
- **Runtime state** — `opc init` seeds `teams.yaml`; existing runtimes without the file get it auto-generated from the default layout on daemon startup (idempotent, logged).
- **Backfill** — existing `tasks.task_type` values are ignored; all historical rows get `team='engineering'` by migration default. `task_type` column stays for now and is dropped in a follow-up.
- **Protocol** — `gh` allow-rule bullets added to EH section in `protocol/02-system-prompts-managers.md`. Content Manager section reviewed to confirm `### Allow Rules` subsection exists (even if empty, for parser regularity).

## Rollout order

1. `src/orchestrator/teams.py` + tests (no wiring yet).
2. `teams.yaml` creation in `opc init`; loader in `DaemonState`.
3. Protocol edits: add `### Allow Rules` to EH; remove code-side constant; parser + adapter rewire.
4. Retire `TaskType`: remove enum, field, CLI flag, cross-audit stub, KB topic dict; add `--team` flag; DB migration.
5. `run_step` generalization via registry lookups.
6. KB delete gate generalization.
7. `manage-agent` gate generalization + enrollment team scoping.
8. Content Team bootstrap via `opc init-agent` + prompt loader entries.
9. Content Team MVP flow tests (unit + integration).
10. Update README/CLAUDE.md sections that mention `task_type`, EH-only manage-agent, EH-only KB delete.

## Explicit non-goals

- SEO Agent, Content Manager ↔ other-team handoffs, cross-team audits.
- A publication pipeline that moves `artifacts/<task_id>/` files into a live website or CMS.
- Any change to the talk flow, revisit flow, or dashboard surface.
- Any change to the Codex executor path beyond what already flows through workspace adapters.
