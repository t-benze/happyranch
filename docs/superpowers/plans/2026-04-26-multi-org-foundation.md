# Multi-Org Foundation Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the protocol/runtime mixing problem at its foundation: move org-specific content out of `protocol/` into a per-runtime `org/` folder, replace the hardcoded `_AGENT_SOURCES` + `agent_enrollments` table with a file-based agent loader, and migrate the existing dev runtime end-to-end. The single-org daemon stays single-org after this plan; multi-tenancy is Plan 2.

**Architecture:** Agents become pure markdown files with YAML frontmatter under `<runtime>/org/agents/<name>.md` (and `_pending/<name>.md` for awaiting-approval). A new `prompt_loader` API (`AgentDef`, `load_agent`, `list_agents`, `write_pending_agent`, `approve_agent`, `reject_agent`) replaces the protocol-markdown parser and the DB enrollment table. `RuntimeDir` gains `slug`, `org_dir`, `agents_dir`, `pending_agents_dir`. The HK/Macau tourism org content moves to `examples/orgs/hk-macau-tourism/org/` as the bootstrap source for `opc init --from`. A one-shot `opc migrate-to-org-runtime` script converts existing dev runtimes.

**Tech Stack:** Python 3.11+, `uv`, FastAPI, Pydantic v2, SQLite (WAL), PyYAML (already in deps), pytest. No new runtime deps.

**Plan 2 scope (deferred):** `DaemonState` registry of `OrgState`s, path-prefixed routes (`/orgs/{slug}/...`), `--org` flag on every per-org CLI command, removal of `state.runtime` / `cmd_use`, skill template substitution (`{ORG_SLUG}`).

**Spec reference:** `docs/superpowers/specs/2026-04-26-multi-org-runtime-design.md` is the authoritative source. This plan implements §3 (Architecture summary, all bullets except daemon multi-tenancy), §4 (Repo layout — both `protocol/` cut and `examples/orgs/`), §5 (Runtime org folder shape, sections 5.1–5.4), §8 (Prompt loader & enrollment internals, sections 8.1–8.3 and 8.5 minus skill template substitution), §9 (Migration plan, full).

---

## Why this plan exists separately

The spec covers two largely independent subsystems: **agent identity refactor** (this plan — file-based, single-org) and **daemon multi-tenancy** (Plan 2 — concurrent orgs, CLI `--org`). Plan 1 leaves the system fully working with one org. Plan 2 swings the daemon over to per-org `OrgState` and adds `--org` everywhere. Splitting them keeps each plan reviewable and lets Plan 2 build on Plan 1's locked-in loader API.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/orchestrator/agent_def.py` | `AgentDef` dataclass + frontmatter parse/render helpers |
| `src/orchestrator/migration.py` | `opc migrate-to-org-runtime` implementation (called by CLI) |
| `protocol/00-completion-contract.md` | Universal completion-report format, EH decision schema, callback list (extracted from current per-agent prompts) |
| `examples/orgs/hk-macau-tourism/org/charter.md` | (was `protocol/01-org-charter.md`) |
| `examples/orgs/hk-macau-tourism/org/escalation-rules.md` | (was `protocol/04-escalation-rules.md`) |
| `examples/orgs/hk-macau-tourism/org/teams.yaml` | engineering + content teams |
| `examples/orgs/hk-macau-tourism/org/agents/<name>.md` × 8 | One per agent currently in `02/03-system-prompts-*.md` |
| `tests/test_agent_def.py` | AgentDef parse / render unit tests |
| `tests/test_migration.py` | (already exists; extend; rename if needed) — migration script tests |
| `tests/test_examples_org_tree.py` | Sanity-check that the in-repo example tree is loadable as a runtime |

### Modified files

| Path | What changes |
|---|---|
| `src/runtime.py` | `slug` property (reads `opc.yaml`), `org_dir`, `agents_dir`, `pending_agents_dir`; `init` writes `slug:` + `created_at:` + `schema_version:` to `opc.yaml`; `init` creates `org/agents/_pending/`; `teams_config_path` returns `<runtime>/org/teams.yaml` |
| `src/orchestrator/teams.py` | Delete `DEFAULT_LAYOUT`; `seed_default` becomes `seed_empty` (writes `teams: {}`); `TeamsRegistry.load` no longer falls back to default — empty file = empty registry |
| `src/orchestrator/prompt_loader.py` | **Rewrite from scratch.** New API: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`, `allow_rules_for_agent` (returns just the prefix list). `_AGENT_SOURCES`, `load_system_prompt`, `load_all_prompts`, `allow_rules_for(protocol_dir, ...)` deleted. |
| `src/orchestrator/workspace_adapters.py` | `allow_rules_for_agent` switches from `(settings, name, *, cli, db)` to `(runtime, name, *, cli)`; reads frontmatter via the new loader; DB-fallback branch removed. `build_settings_json` and adapter ctors take `runtime` instead of relying on `settings.get_protocol_dir()` for allow rules. |
| `src/orchestrator/orchestrator.py` | Drop `DEFAULT_LAYOUT` import + default. `Orchestrator.__init__` requires `teams: TeamsRegistry`. |
| `src/daemon/routes/agents.py` | `init_agents`, `manage_agent`, `approve_agent`, `reject_agent`, `list_enrollments`, `backfill_enrollments` rewrite their DB enrollment calls to file-based loader calls. |
| `src/infrastructure/database.py` | New schema migration drops `agent_enrollments` table when migration script runs (gated behind `migrate_to_org_runtime`). The unconditional table-create stays so fresh DBs still get the (empty) table for one release; migration script drops it. (We will fully remove the table create in Plan 2.) |
| `src/cli.py` | Add `cmd_migrate_to_org_runtime` and wire `migrate-to-org-runtime` subcommand. |
| `protocol/05-team-blueprint.md` | Renamed → `protocol/05-runtime-blueprint.md`. References to org-specific files deleted. |
| `protocol/05c-orchestrator.md`, `protocol/05e-dashboard.md` | De-org: HK/Macau-specific examples → generic placeholders. |
| `CLAUDE.md` (project root) | Reflect new layout: `<runtime>/org/`, example tree, `migrate-to-org-runtime` CLI. |
| `tests/test_runtime.py`, `tests/test_prompt_loader.py`, `tests/test_prompt_loader_allow_rules.py`, `tests/test_workspace_adapters.py`, `tests/test_workspace_adapters_allow_rules.py`, `tests/test_context_builder.py`, `tests/test_orchestrator.py`, `tests/test_teams.py`, `tests/daemon/test_routes_agents.py` | Updated to use file-based agent fixtures in `tmp_path` runtimes. |

### Deleted files (final phase only)

| Path | Reason |
|---|---|
| `protocol/01-org-charter.md` | Org-specific content; lives in `examples/orgs/hk-macau-tourism/org/charter.md` |
| `protocol/02-system-prompts-managers.md` | Org-specific; per-agent files in `examples/.../agents/` |
| `protocol/03-system-prompts-workers.md` | Same |
| `protocol/04-escalation-rules.md` | Org-specific |
| `protocol/05a-teams.md` | Org-specific team layout |

---

## Phase 0: Build the example org tree (no code changes yet)

Goal: Stage `examples/orgs/hk-macau-tourism/org/` so subsequent phases have a real target to load. **The original `protocol/01..04-*.md` and `protocol/05a-teams.md` stay in place during Phase 0** — they're deleted in Phase 8 once code no longer reads them.

### Task 0.1: Create the example tree skeleton

**Files:**
- Create: `examples/orgs/hk-macau-tourism/org/agents/.gitkeep`

- [ ] **Step 1: Create directories**

```bash
mkdir -p examples/orgs/hk-macau-tourism/org/agents
touch examples/orgs/hk-macau-tourism/org/agents/.gitkeep
```

- [ ] **Step 2: Verify**

```bash
ls examples/orgs/hk-macau-tourism/org/
```
Expected: `agents`

- [ ] **Step 3: Commit**

```bash
git add examples/orgs/hk-macau-tourism/org/
git commit -m "chore(examples): scaffold hk-macau-tourism org tree"
```

### Task 0.2: Copy charter + escalation-rules verbatim into example tree

**Files:**
- Read: `protocol/01-org-charter.md`
- Read: `protocol/04-escalation-rules.md`
- Create: `examples/orgs/hk-macau-tourism/org/charter.md`
- Create: `examples/orgs/hk-macau-tourism/org/escalation-rules.md`

These are reference docs read by humans, not parsed by code. Copy verbatim; agents already reference them by their content, not their path.

- [ ] **Step 1: Copy files**

```bash
cp protocol/01-org-charter.md examples/orgs/hk-macau-tourism/org/charter.md
cp protocol/04-escalation-rules.md examples/orgs/hk-macau-tourism/org/escalation-rules.md
```

- [ ] **Step 2: Verify content matches**

```bash
diff protocol/01-org-charter.md examples/orgs/hk-macau-tourism/org/charter.md && echo OK
diff protocol/04-escalation-rules.md examples/orgs/hk-macau-tourism/org/escalation-rules.md && echo OK
```
Expected: `OK` printed twice.

- [ ] **Step 3: Commit**

```bash
git add examples/orgs/hk-macau-tourism/org/charter.md examples/orgs/hk-macau-tourism/org/escalation-rules.md
git commit -m "chore(examples): copy charter + escalation rules to example tree"
```

### Task 0.3: Build `examples/orgs/hk-macau-tourism/org/teams.yaml`

**Files:**
- Read: `protocol/05a-teams.md` (to confirm team layout)
- Create: `examples/orgs/hk-macau-tourism/org/teams.yaml`

- [ ] **Step 1: Write the file**

```yaml
teams:
  engineering:
    manager: engineering_head
    workers:
      - product_manager
      - dev_agent
      - payment_agent
      - qa_engineer
  content:
    manager: content_manager
    workers:
      - content_writer
      - content_qa
```

- [ ] **Step 2: Sanity check with PyYAML**

```bash
uv run python -c "import yaml; print(sorted(yaml.safe_load(open('examples/orgs/hk-macau-tourism/org/teams.yaml'))['teams'].keys()))"
```
Expected: `['content', 'engineering']`

- [ ] **Step 3: Commit**

```bash
git add examples/orgs/hk-macau-tourism/org/teams.yaml
git commit -m "chore(examples): teams layout for hk-macau-tourism"
```

### Task 0.4: Extract per-agent files (8 agents)

For each agent, the body is the existing fenced ```` ``` ```` block from `protocol/02-system-prompts-managers.md` or `protocol/03-system-prompts-workers.md` under the matching `## <Heading>`. The frontmatter is filled from `_AGENT_SOURCES` and the team layout above. `enrolled_*` are `null` for these founder-authored agents.

**Files to create (one per agent):**

| File | Source heading | team | role | executor | extras |
|---|---|---|---|---|---|
| `examples/orgs/hk-macau-tourism/org/agents/engineering_head.md` | `## Engineering Head` in `protocol/02-system-prompts-managers.md` | engineering | manager | claude | `allow_rules: ["gh pr close", "gh pr comment", "gh issue close", "gh issue comment"]` |
| `examples/orgs/hk-macau-tourism/org/agents/content_manager.md` | `## Content Manager` in `protocol/02-system-prompts-managers.md` | content | manager | claude | `allow_rules: []` |
| `examples/orgs/hk-macau-tourism/org/agents/product_manager.md` | `## Product Manager` in `protocol/03-system-prompts-workers.md` | engineering | worker | claude | `allow_rules: []` |
| `examples/orgs/hk-macau-tourism/org/agents/dev_agent.md` | `## Dev Agent` in `protocol/03-system-prompts-workers.md` | engineering | worker | claude | `allow_rules: []` |
| `examples/orgs/hk-macau-tourism/org/agents/payment_agent.md` | `## Payment Agent` in `protocol/03-system-prompts-workers.md` | engineering | worker | claude | `allow_rules: []` |
| `examples/orgs/hk-macau-tourism/org/agents/qa_engineer.md` | `## QA Engineer` in `protocol/03-system-prompts-workers.md` | engineering | worker | claude | `allow_rules: []` |
| `examples/orgs/hk-macau-tourism/org/agents/content_writer.md` | `## Content Writer` in `protocol/03-system-prompts-workers.md` | content | worker | claude | `allow_rules: []` |
| `examples/orgs/hk-macau-tourism/org/agents/content_qa.md` | `## Content QA` in `protocol/03-system-prompts-workers.md` | content | worker | claude | `allow_rules: []` |

For Engineering Head, the `allow_rules` list comes from the existing `### Allow Rules` subsection in `protocol/02-system-prompts-managers.md`. Verify it matches before writing; the four entries above are the current values per the project CLAUDE.md.

The frontmatter format is locked to:

```markdown
---
name: <agent_name>
team: <team>
role: worker | manager
executor: claude
allow_rules: []           # or list of strings
repos: {}                 # founder-authored agents have no repos by default
enrolled_by: null
enrolled_at_task: null
enrolled_at: null
---

<full system prompt body, exactly as inside the protocol fenced block>
```

The body must be the *unfenced* contents — strip the ```` ``` ```` markers when copying.

- [ ] **Step 1: Write a one-shot extractor (NOT committed) for repeatability**

Use a temporary script under the worktree to extract all 8 files at once. This keeps the manual step short and identical for every agent:

```bash
cat > /tmp/extract_agents.py <<'EOF'
import re
from pathlib import Path

SRC_ROOT = Path("protocol")
DST = Path("examples/orgs/hk-macau-tourism/org/agents")
DST.mkdir(parents=True, exist_ok=True)

# (filename, heading, name, team, role, allow_rules)
SPECS = [
    ("02-system-prompts-managers.md", "Engineering Head", "engineering_head", "engineering", "manager",
     ["gh pr close", "gh pr comment", "gh issue close", "gh issue comment"]),
    ("02-system-prompts-managers.md", "Content Manager",  "content_manager",  "content",     "manager", []),
    ("03-system-prompts-workers.md",  "Product Manager",  "product_manager",  "engineering", "worker",  []),
    ("03-system-prompts-workers.md",  "Dev Agent",        "dev_agent",        "engineering", "worker",  []),
    ("03-system-prompts-workers.md",  "Payment Agent",    "payment_agent",    "engineering", "worker",  []),
    ("03-system-prompts-workers.md",  "QA Engineer",      "qa_engineer",      "engineering", "worker",  []),
    ("03-system-prompts-workers.md",  "Content Writer",   "content_writer",   "content",     "worker",  []),
    ("03-system-prompts-workers.md",  "Content QA",       "content_qa",       "content",     "worker",  []),
]

def extract_body(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    assert m, f"heading not found: {heading}"
    rest = text[m.end():]
    fence = re.search(r"```\n(.*?)```", rest, re.DOTALL)
    assert fence, f"fence not found after {heading}"
    return fence.group(1).strip() + "\n"

for filename, heading, name, team, role, allow_rules in SPECS:
    text = (SRC_ROOT / filename).read_text()
    body = extract_body(text, heading)
    if allow_rules:
        rules_block = "\n".join(f"  - {r!r}" for r in allow_rules)
        rules_yaml = "allow_rules:\n" + rules_block
    else:
        rules_yaml = "allow_rules: []"
    fm = (
        "---\n"
        f"name: {name}\n"
        f"team: {team}\n"
        f"role: {role}\n"
        "executor: claude\n"
        f"{rules_yaml}\n"
        "repos: {}\n"
        "enrolled_by: null\n"
        "enrolled_at_task: null\n"
        "enrolled_at: null\n"
        "---\n\n"
    )
    (DST / f"{name}.md").write_text(fm + body)
    print(f"wrote {DST / (name + '.md')}")
EOF
uv run python /tmp/extract_agents.py
```

- [ ] **Step 2: Verify all 8 files exist with non-empty bodies**

```bash
for f in examples/orgs/hk-macau-tourism/org/agents/*.md; do
  echo "=== $f ==="
  head -12 "$f"
  echo "(body bytes: $(wc -c < "$f"))"
done
```
Expected: All 8 files present, each with frontmatter starting with `---`, name matching filename stem, body bytes ≥ 800.

- [ ] **Step 3: Verify allow_rules for engineering_head**

```bash
grep -A 6 "^allow_rules:" examples/orgs/hk-macau-tourism/org/agents/engineering_head.md
```
Expected: 4 bullets matching the gh pr/issue close/comment list.

- [ ] **Step 4: Delete the temp extractor**

```bash
rm /tmp/extract_agents.py
```

- [ ] **Step 5: Commit**

```bash
git add examples/orgs/hk-macau-tourism/org/agents/
git commit -m "chore(examples): per-agent files for hk-macau-tourism"
```

### Task 0.5: Create `protocol/00-completion-contract.md`

This file extracts the duplicated completion contract block currently inlined in every agent prompt. The orchestrator will append it at session-build time once Phase 5 lands.

**Files:**
- Create: `protocol/00-completion-contract.md`

- [ ] **Step 1: Write the file**

```markdown
# Completion Contract

This document is appended to every agent's system prompt by the orchestrator at session-build time. It defines the universal completion-report format, the Engineering Head decision schema, and the agent-callback command list. The contract is identical for every agent except where explicitly noted.

## Task completion report

When you finish a task, write your completion payload to `/tmp/completion-<task_id>.json` and call back via:

```
opc report-completion --from-file /tmp/completion-<task_id>.json
```

The `--from-file` form is mandatory across executors — multi-line `opc` invocations are blocked by the shared permission matcher.

Payload shape:

```json
{
  "task_id": "<the task_id from the prompt>",
  "session_id": "<the session_id from the prompt>",
  "agent": "<this agent's name>",
  "status": "completed",
  "summary": "<short prose summary of what you did>"
}
```

The summary should include:
- **Confidence** — how sure you are the work is correct (high/med/low + one-line reason).
- **Risks flagged** — anything the reviewer should look at hardest.
- **Dependencies** — work this depends on or blocks.
- **Suggested reviewer focus** — which file(s) or which aspect to review first.

## Blocker path

Use `"status": "blocked"` when you cannot finish and need the orchestrator to route around you. Put the blocker reason in `summary` — the orchestrator reads it verbatim when deciding the next step.

## Engineering Head decision field (manager-only)

Engineering Head sessions must additionally include a structured `decision` object. `summary` stays prose; the orchestrator parses `decision` directly.

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "engineering_head",
  "status": "completed",
  "summary": "<what you did or concluded this step>",
  "decision": {
    "action": "delegate",
    "agent": "<target agent name>",
    "brief": "<child task brief>"
  }
}
```

`decision.action` is one of:
- `"delegate"` — spawn a child task on another agent (also set `agent` + `brief`).
- `"done"` — terminal; the root task finishes here.
- `"escalate"` — surface to the founder for resolution (also set `reason`).

## Mid-task learnings

Durable lessons go through:
```
opc learning --agent <you> --session-id <sid> --task-id <task_id> --text "..."
```

Cross-agent reference / precedent material belongs in the Knowledge Base (`opc kb add --from-file ...`), not in `learnings.md`.

## Other agent-side callbacks

| Command | Purpose |
|---|---|
| `opc report-completion --from-file ...` | End-of-task callback (mandatory). |
| `opc learning --agent ... --session-id ... --task-id ... --text ...` | Durable per-agent operational lesson. |
| `opc manage-repo {add\|remove\|update} --agent ... --repo-name ... [--url ...]` | Add/remove/update a repo clone in your workspace. |
| `opc manage-agent --from-file ...` | (Engineering Head + Content Manager) enroll/update/terminate an agent within your team. |
| `opc kb add --agent ... --from-file ...` | Contribute a knowledge-base entry. |
| `opc kb update <slug> --agent ... --from-file ...` | Update an existing entry. |
```

- [ ] **Step 2: Verify file exists**

```bash
wc -l protocol/00-completion-contract.md
```
Expected: ≥ 60 lines.

- [ ] **Step 3: Commit**

```bash
git add protocol/00-completion-contract.md
git commit -m "docs(protocol): add 00-completion-contract.md (extracted from per-agent prompts)"
```

---

## Phase 1: RuntimeDir slug + org/ skeleton

### Task 1.1: Test that `RuntimeDir.slug` reads from `opc.yaml`

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py`:

```python
# ---------------------------------------------------------------------------
# slug + org folder
# ---------------------------------------------------------------------------


def test_init_writes_slug_to_opc_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt", slug="hk-tourism")
    data = yaml.safe_load(rt.marker_file.read_text())
    assert data["slug"] == "hk-tourism"
    assert data["schema_version"] == 1
    assert "created_at" in data


def test_slug_property_reads_opc_yaml(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="hk-tourism")
    loaded = RuntimeDir.load(rt.root)
    assert loaded.slug == "hk-tourism"


def test_init_creates_org_skeleton(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert rt.org_dir.is_dir()
    assert rt.agents_dir.is_dir()
    assert rt.pending_agents_dir.is_dir()


def test_teams_config_path_under_org(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert rt.teams_config_path == rt.root / "org" / "teams.yaml"


def test_init_idempotent_keeps_slug(tmp_path: Path) -> None:
    rt1 = RuntimeDir.init(tmp_path / "rt", slug="alpha")
    rt2 = RuntimeDir.init(tmp_path / "rt", slug="beta")  # second call ignored
    assert rt2.slug == "alpha"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_runtime.py -v -k "slug or org_skeleton or teams_config_path or idempotent_keeps_slug"
```
Expected: 5 FAILs (no `slug` parameter, missing properties).

### Task 1.2: Implement `RuntimeDir` slug + org_dir + skeleton

**Files:**
- Modify: `src/runtime.py`

- [ ] **Step 1: Rewrite `src/runtime.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml


class RuntimeDir:
    """Value object representing a self-describing OPC runtime folder.

    The presence of an ``opc.yaml`` marker file distinguishes a valid
    runtime directory from an arbitrary path. The marker file carries the
    runtime's slug, creation timestamp, and schema version.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._cached_slug: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._path

    @property
    def db_path(self) -> Path:
        return self._path / "opc.db"

    @property
    def workspaces_dir(self) -> Path:
        return self._path / "workspaces"

    @property
    def marker_file(self) -> Path:
        return self._path / "opc.yaml"

    @property
    def org_dir(self) -> Path:
        return self._path / "org"

    @property
    def agents_dir(self) -> Path:
        return self.org_dir / "agents"

    @property
    def pending_agents_dir(self) -> Path:
        return self.agents_dir / "_pending"

    @property
    def teams_config_path(self) -> Path:
        return self.org_dir / "teams.yaml"

    @property
    def slug(self) -> str:
        if self._cached_slug is not None:
            return self._cached_slug
        if not self.marker_file.exists():
            raise ValueError(f"{self.marker_file} missing")
        data = yaml.safe_load(self.marker_file.read_text()) or {}
        slug = data.get("slug")
        if not isinstance(slug, str) or not slug:
            raise ValueError(f"{self.marker_file} missing slug")
        self._cached_slug = slug
        return slug

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return True if the marker file exists."""
        return self.marker_file.exists()

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def init(cls, path: Path, *, slug: str | None = None) -> RuntimeDir:
        """Create a runtime directory at *path*.

        On first creation, writes ``opc.yaml`` with the supplied ``slug``,
        a ``created_at`` timestamp, and ``schema_version: 1``. Subsequent
        calls are idempotent — the existing slug is preserved.

        Creates the ``workspaces/`` and ``org/agents/_pending/`` sub-directories.
        """
        instance = cls(path)
        instance.root.mkdir(parents=True, exist_ok=True)

        if not instance.marker_file.exists():
            if slug is None:
                raise ValueError("slug is required to initialize a new runtime")
            payload = {
                "slug": slug,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "schema_version": 1,
            }
            instance.marker_file.write_text(yaml.safe_dump(payload, sort_keys=False))

        instance.workspaces_dir.mkdir(parents=True, exist_ok=True)
        instance.org_dir.mkdir(parents=True, exist_ok=True)
        instance.agents_dir.mkdir(parents=True, exist_ok=True)
        instance.pending_agents_dir.mkdir(parents=True, exist_ok=True)

        # Deferred import: teams.py imports RuntimeDir, so the import lives
        # inside the function to avoid a cycle at module-load time.
        from src.orchestrator.teams import TeamsRegistry
        TeamsRegistry.seed_empty(instance)
        return instance

    @classmethod
    def load(cls, path: Path) -> RuntimeDir:
        """Load an existing runtime directory from *path*.

        Raises ``ValueError`` if the marker file is absent.
        """
        instance = cls(path)
        if not instance.is_valid():
            raise ValueError(
                f"{path} is not a valid OPC runtime directory "
                f"(missing {instance.marker_file})"
            )
        return instance
```

- [ ] **Step 2: Run new tests**

```bash
uv run pytest tests/test_runtime.py -v
```
Expected: All slug/org_skeleton/teams_config_path/idempotent tests PASS. Existing tests may fail because they call `RuntimeDir.init(path)` without a slug — fix in next step.

- [ ] **Step 3: Update existing test fixtures to pass slug**

In `tests/test_runtime.py`, update every `RuntimeDir.init(...)` call to include `slug="test"`:

```python
def test_init_creates_marker_file(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime", slug="test")
    assert rt.marker_file.exists()


def test_init_creates_workspaces_dir(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime", slug="test")
    assert rt.workspaces_dir.is_dir()


def test_init_idempotent(tmp_path: Path) -> None:
    """Calling init twice must not destroy existing data."""
    rt_dir = tmp_path / "runtime"
    rt1 = RuntimeDir.init(rt_dir, slug="test")
    sentinel = rt1.workspaces_dir / "sentinel.txt"
    sentinel.write_text("keep me")
    rt2 = RuntimeDir.init(rt_dir, slug="test")
    assert rt2.marker_file.exists()
    assert rt2.workspaces_dir.is_dir()
    assert sentinel.exists()


def test_load_valid_runtime(tmp_path: Path) -> None:
    rt_dir = tmp_path / "runtime"
    RuntimeDir.init(rt_dir, slug="test")
    loaded = RuntimeDir.load(rt_dir)
    assert loaded.root == rt_dir.resolve()
    assert loaded.is_valid()
```

(`test_load_invalid_runtime_raises`, `test_db_path_derived_from_root`, `test_workspaces_dir_derived_from_root` need no slug.)

For the existing teams.yaml seeding tests — `test_init_seeds_default_teams_yaml` and `test_init_does_not_overwrite_existing_teams_yaml`: rewrite to assert empty seeding.

```python
def test_init_seeds_empty_teams_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    assert rt.teams_config_path.exists()
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert data == {"teams": {}}


def test_init_does_not_overwrite_existing_teams_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    rt.teams_config_path.write_text(
        "teams:\n  custom:\n    manager: custom_mgr\n    workers: []\n"
    )
    RuntimeDir.init(tmp_path / "rt", slug="test")
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert set(data["teams"].keys()) == {"custom"}
```

- [ ] **Step 4: Run all runtime tests**

```bash
uv run pytest tests/test_runtime.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): slug + org/ skeleton on RuntimeDir"
```

### Task 1.3: Strip `DEFAULT_LAYOUT` from teams.py + add `seed_empty`

**Files:**
- Modify: `src/orchestrator/teams.py`
- Modify: `tests/test_teams.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_teams.py`, replace any `DEFAULT_LAYOUT` references with empty-seed expectations. Add:

```python
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


def test_seed_empty_writes_empty_teams_block(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    # init already calls seed_empty; calling again is idempotent.
    TeamsRegistry.seed_empty(rt)
    import yaml
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert data == {"teams": {}}


def test_load_returns_empty_registry_when_no_teams(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    reg = TeamsRegistry.load(rt)
    assert reg.teams() == []
    assert reg.team_for_agent("anybody") is None


def test_load_reads_runtime_team_file(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    rt.teams_config_path.write_text(
        "teams:\n"
        "  eng:\n"
        "    manager: alice\n"
        "    workers: [bob, carol]\n"
    )
    reg = TeamsRegistry.load(rt)
    assert reg.teams() == ["eng"]
    m = reg.manager_for_team("eng")
    assert m.name == "alice"
    assert m.workers == ("bob", "carol")
```

- [ ] **Step 2: Run to fail**

```bash
uv run pytest tests/test_teams.py -v -k "seed_empty or returns_empty or reads_runtime_team_file"
```
Expected: FAILs (`seed_empty` undefined; `load` falls back to defaults).

- [ ] **Step 3: Modify `src/orchestrator/teams.py`**

Replace top of file:

```python
"""Team registry: who manages whom, loaded from <runtime>/org/teams.yaml."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.runtime import RuntimeDir


@dataclass(frozen=True)
class TeamManager:
    name: str
    team: str
    workers: tuple[str, ...]


class TeamsRegistry:
    def __init__(self, teams: dict[str, TeamManager], runtime: RuntimeDir | None = None) -> None:
        self._teams = dict(teams)
        self._runtime = runtime

    # ---- construction ----

    @classmethod
    def load(cls, runtime: RuntimeDir) -> "TeamsRegistry":
        path = runtime.teams_config_path
        if not path.exists():
            return cls({}, runtime=runtime)
        raw = yaml.safe_load(path.read_text()) or {}
        layout = raw.get("teams") or {}
        return cls._from_layout(layout, runtime)

    @classmethod
    def _from_layout(cls, layout: dict[str, dict[str, object]], runtime: RuntimeDir | None = None) -> "TeamsRegistry":
        teams: dict[str, TeamManager] = {}
        for team_name, entry in layout.items():
            manager = entry.get("manager")
            workers = tuple(entry.get("workers") or ())
            if not isinstance(manager, str) or not manager:
                raise ValueError(f"team {team_name!r} missing manager")
            teams[team_name] = TeamManager(name=manager, team=team_name, workers=workers)
        return cls(teams, runtime=runtime)

    @classmethod
    def seed_empty(cls, runtime: RuntimeDir) -> None:
        """Write an empty ``teams: {}`` block to *runtime* if it doesn't exist."""
        if runtime.teams_config_path.exists():
            return
        cls({}, runtime=runtime).save(runtime)
```

Keep the rest of the file (save, lookups, mutation methods) unchanged.

- [ ] **Step 4: Run teams tests**

```bash
uv run pytest tests/test_teams.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/teams.py tests/test_teams.py
git commit -m "refactor(teams): drop DEFAULT_LAYOUT, seed empty registry"
```

---

## Phase 2: AgentDef + frontmatter parser

### Task 2.1: Test AgentDef parse round-trip

**Files:**
- Test: `tests/test_agent_def.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_def.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.orchestrator.agent_def import (
    AgentDef,
    AgentParseError,
    parse_agent_text,
    render_agent_text,
)


SAMPLE = """\
---
name: dev_agent
team: engineering
role: worker
executor: claude
allow_rules:
  - "gh issue close"
repos:
  my-opc: https://github.com/example/my-opc.git
enrolled_by: engineering_head
enrolled_at_task: TASK-042
enrolled_at: 2026-04-15T08:00:00Z
---

You are the Dev Agent. Your responsibilities are X, Y, Z.
"""


def test_parse_full_frontmatter() -> None:
    agent = parse_agent_text(SAMPLE, expected_name="dev_agent")
    assert agent.name == "dev_agent"
    assert agent.team == "engineering"
    assert agent.role == "worker"
    assert agent.executor == "claude"
    assert agent.allow_rules == ("gh issue close",)
    assert agent.repos == {"my-opc": "https://github.com/example/my-opc.git"}
    assert agent.enrolled_by == "engineering_head"
    assert agent.enrolled_at_task == "TASK-042"
    assert agent.enrolled_at == datetime(2026, 4, 15, 8, 0, 0, tzinfo=timezone.utc)
    assert "Dev Agent" in agent.system_prompt


def test_parse_minimal_frontmatter() -> None:
    text = (
        "---\n"
        "name: minimal\n"
        "team: content\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n"
        "body\n"
    )
    agent = parse_agent_text(text, expected_name="minimal")
    assert agent.allow_rules == ()
    assert agent.repos == {}
    assert agent.enrolled_by is None
    assert agent.enrolled_at_task is None
    assert agent.enrolled_at is None


def test_parse_rejects_filename_mismatch() -> None:
    with pytest.raises(AgentParseError, match="name mismatch"):
        parse_agent_text(SAMPLE, expected_name="other_agent")


@pytest.mark.parametrize("bad", [
    "no frontmatter at all",
    "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n",  # no closing fence
])
def test_parse_rejects_malformed_frontmatter(bad: str) -> None:
    with pytest.raises(AgentParseError):
        parse_agent_text(bad, expected_name="x")


def test_parse_rejects_invalid_role() -> None:
    text = (
        "---\nname: x\nteam: t\nrole: bogus\nexecutor: claude\n---\nbody\n"
    )
    with pytest.raises(AgentParseError, match="role"):
        parse_agent_text(text, expected_name="x")


def test_parse_rejects_invalid_executor() -> None:
    text = (
        "---\nname: x\nteam: t\nrole: worker\nexecutor: gpt\n---\nbody\n"
    )
    with pytest.raises(AgentParseError, match="executor"):
        parse_agent_text(text, expected_name="x")


def test_parse_rejects_empty_body() -> None:
    text = "---\nname: x\nteam: t\nrole: worker\nexecutor: claude\n---\n\n"
    with pytest.raises(AgentParseError, match="empty body"):
        parse_agent_text(text, expected_name="x")


def test_render_round_trip() -> None:
    agent = parse_agent_text(SAMPLE, expected_name="dev_agent")
    text2 = render_agent_text(agent)
    agent2 = parse_agent_text(text2, expected_name="dev_agent")
    assert agent == agent2


def test_render_omits_null_optional_fields() -> None:
    agent = AgentDef(
        name="x",
        team="t",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=None,
        system_prompt="hello\n",
    )
    text = render_agent_text(agent)
    assert "enrolled_by:" in text
    assert "null" in text  # YAML emits null explicitly
```

- [ ] **Step 2: Run to fail**

```bash
uv run pytest tests/test_agent_def.py -v
```
Expected: ImportError on `src.orchestrator.agent_def`.

### Task 2.2: Implement `agent_def.py`

**Files:**
- Create: `src/orchestrator/agent_def.py`

- [ ] **Step 1: Write the module**

```python
"""AgentDef dataclass and frontmatter parsing for <runtime>/org/agents/<name>.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml


_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_REPO_KEY_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)", re.DOTALL)


class AgentParseError(ValueError):
    """Raised when an agent file cannot be parsed or fails validation."""


Role = Literal["worker", "manager"]
Executor = Literal["claude", "codex"]


@dataclass(frozen=True)
class AgentDef:
    name: str
    team: str
    role: Role
    executor: Executor
    allow_rules: tuple[str, ...]
    repos: dict[str, str]
    enrolled_by: str | None
    enrolled_at_task: str | None
    enrolled_at: datetime | None
    system_prompt: str


def _parse_iso(ts: object) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not isinstance(ts, str):
        raise AgentParseError(f"enrolled_at must be a string or datetime, got {type(ts).__name__}")
    try:
        # PyYAML may emit naive datetimes; accept both Z suffix and +00:00.
        normalized = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AgentParseError(f"enrolled_at: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_agent_text(text: str, *, expected_name: str) -> AgentDef:
    """Parse a markdown-with-YAML-frontmatter agent file.

    Raises AgentParseError if structure or validation fails.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise AgentParseError("no leading frontmatter or missing closing fence")

    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise AgentParseError(f"malformed YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise AgentParseError("frontmatter must be a mapping")

    body = m.group(2).lstrip("\n")
    if not body.strip():
        raise AgentParseError("empty body")

    for required in ("name", "team", "role", "executor"):
        if required not in fm:
            raise AgentParseError(f"missing frontmatter field: {required}")

    name = fm["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise AgentParseError(f"invalid name: {name!r}")
    if name != expected_name:
        raise AgentParseError(f"name mismatch: file says {name!r}, expected {expected_name!r}")

    team = fm["team"]
    if not isinstance(team, str) or not team:
        raise AgentParseError("team must be a non-empty string")

    role = fm["role"]
    if role not in ("worker", "manager"):
        raise AgentParseError(f"role must be 'worker' or 'manager', got {role!r}")

    executor = fm["executor"]
    if executor not in ("claude", "codex"):
        raise AgentParseError(f"executor must be 'claude' or 'codex', got {executor!r}")

    raw_rules = fm.get("allow_rules") or []
    if not isinstance(raw_rules, list):
        raise AgentParseError("allow_rules must be a list")
    for r in raw_rules:
        if not isinstance(r, str) or not r:
            raise AgentParseError(f"allow_rules entries must be non-empty strings, got {r!r}")
    allow_rules: tuple[str, ...] = tuple(raw_rules)

    raw_repos = fm.get("repos") or {}
    if not isinstance(raw_repos, dict):
        raise AgentParseError("repos must be a mapping")
    repos: dict[str, str] = {}
    for k, v in raw_repos.items():
        if not isinstance(k, str) or not _REPO_KEY_RE.match(k):
            raise AgentParseError(f"invalid repo key: {k!r}")
        if not isinstance(v, str) or not v:
            raise AgentParseError(f"repo {k!r} url must be a non-empty string")
        repos[k] = v

    enrolled_by = fm.get("enrolled_by")
    if enrolled_by is not None and not isinstance(enrolled_by, str):
        raise AgentParseError("enrolled_by must be a string or null")

    enrolled_at_task = fm.get("enrolled_at_task")
    if enrolled_at_task is not None and not isinstance(enrolled_at_task, str):
        raise AgentParseError("enrolled_at_task must be a string or null")

    enrolled_at = _parse_iso(fm.get("enrolled_at"))

    return AgentDef(
        name=name,
        team=team,
        role=role,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        allow_rules=allow_rules,
        repos=repos,
        enrolled_by=enrolled_by,
        enrolled_at_task=enrolled_at_task,
        enrolled_at=enrolled_at,
        system_prompt=body if body.endswith("\n") else body + "\n",
    )


def render_agent_text(agent: AgentDef) -> str:
    """Inverse of parse_agent_text. Renders frontmatter + body."""
    fm: dict[str, object] = {
        "name": agent.name,
        "team": agent.team,
        "role": agent.role,
        "executor": agent.executor,
        "allow_rules": list(agent.allow_rules),
        "repos": dict(agent.repos),
        "enrolled_by": agent.enrolled_by,
        "enrolled_at_task": agent.enrolled_at_task,
        "enrolled_at": (
            agent.enrolled_at.isoformat().replace("+00:00", "Z")
            if agent.enrolled_at is not None
            else None
        ),
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    body = agent.system_prompt if agent.system_prompt.endswith("\n") else agent.system_prompt + "\n"
    return f"---\n{fm_text}\n---\n\n{body}"


def parse_agent_file(path: Path) -> AgentDef:
    """Read and parse an agent file. The filename stem is the expected name."""
    return parse_agent_text(path.read_text(), expected_name=path.stem)
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/test_agent_def.py -v
```
Expected: PASS.

- [ ] **Step 3: Sanity-check the example tree parses**

```bash
uv run python -c "
from pathlib import Path
from src.orchestrator.agent_def import parse_agent_file
for p in sorted(Path('examples/orgs/hk-macau-tourism/org/agents').glob('*.md')):
    a = parse_agent_file(p)
    print(p.name, '->', a.name, a.team, a.role, len(a.system_prompt), 'bytes')
"
```
Expected: 8 lines, each with role and team matching the table in Task 0.4.

- [ ] **Step 4: Commit**

```bash
git add src/orchestrator/agent_def.py tests/test_agent_def.py
git commit -m "feat(agent_def): AgentDef + frontmatter parser"
```

---

## Phase 3: New `prompt_loader` API

### Task 3.1: Test the new loader

**Files:**
- Create: `tests/test_prompt_loader.py` (replace existing)

- [ ] **Step 1: Write the failing test (replacing existing file)**

Replace `tests/test_prompt_loader.py` with:

```python
"""Tests for the file-based prompt_loader API."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator import prompt_loader
from src.orchestrator.agent_def import AgentDef
from src.runtime import RuntimeDir


def _write_agent(runtime: RuntimeDir, name: str, **fm) -> Path:
    """Helper: write a minimal valid agent file."""
    parts = [
        "---",
        f"name: {name}",
        f"team: {fm.get('team', 'engineering')}",
        f"role: {fm.get('role', 'worker')}",
        f"executor: {fm.get('executor', 'claude')}",
    ]
    if "allow_rules" in fm:
        parts.append("allow_rules:")
        for r in fm["allow_rules"]:
            parts.append(f"  - {r!r}")
    else:
        parts.append("allow_rules: []")
    parts.append("repos: {}")
    parts.append("enrolled_by: null")
    parts.append("enrolled_at_task: null")
    parts.append("enrolled_at: null")
    parts.append("---")
    parts.append("")
    parts.append(fm.get("body", f"You are {name}.\n"))
    pending = fm.get("pending", False)
    target_dir = runtime.pending_agents_dir if pending else runtime.agents_dir
    path = target_dir / f"{name}.md"
    path.write_text("\n".join(parts))
    return path


def test_load_agent_returns_agentdef(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "dev_agent", role="worker", team="engineering")
    agent = prompt_loader.load_agent(rt, "dev_agent")
    assert agent is not None
    assert agent.name == "dev_agent"
    assert agent.role == "worker"


def test_load_agent_returns_none_when_missing(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert prompt_loader.load_agent(rt, "nope") is None


def test_load_agent_does_not_return_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "draft", pending=True)
    assert prompt_loader.load_agent(rt, "draft") is None


def test_list_agents_excludes_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "active1")
    _write_agent(rt, "active2")
    _write_agent(rt, "draft", pending=True)
    names = sorted(a.name for a in prompt_loader.list_agents(rt))
    assert names == ["active1", "active2"]


def test_list_pending_only_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "active1")
    _write_agent(rt, "draft", pending=True)
    names = sorted(a.name for a in prompt_loader.list_pending(rt))
    assert names == ["draft"]


def test_write_pending_agent_creates_file(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    agent = AgentDef(
        name="newbie",
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="engineering_head",
        enrolled_at_task="TASK-9",
        enrolled_at=None,
        system_prompt="You are newbie.\n",
    )
    path = prompt_loader.write_pending_agent(rt, agent)
    assert path == rt.pending_agents_dir / "newbie.md"
    assert path.exists()
    reloaded = prompt_loader.list_pending(rt)
    assert len(reloaded) == 1 and reloaded[0].name == "newbie"


def test_write_pending_agent_atomic_overwrite(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    agent = AgentDef(
        name="newbie", team="engineering", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=None, system_prompt="v1\n",
    )
    prompt_loader.write_pending_agent(rt, agent)
    agent2 = AgentDef(**{**agent.__dict__, "system_prompt": "v2\n"})
    prompt_loader.write_pending_agent(rt, agent2)
    out = (rt.pending_agents_dir / "newbie.md").read_text()
    assert "v2" in out and "v1" not in out


def test_approve_agent_moves_pending_to_active(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "newbie", pending=True)
    agent = prompt_loader.approve_agent(rt, "newbie")
    assert agent.name == "newbie"
    assert (rt.agents_dir / "newbie.md").exists()
    assert not (rt.pending_agents_dir / "newbie.md").exists()


def test_approve_agent_404_when_no_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    with pytest.raises(FileNotFoundError):
        prompt_loader.approve_agent(rt, "nope")


def test_approve_agent_409_when_active_already_exists(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "dup")  # already active
    _write_agent(rt, "dup", pending=True)  # somehow also pending
    with pytest.raises(FileExistsError):
        prompt_loader.approve_agent(rt, "dup")


def test_reject_agent_unlinks_pending(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "drop", pending=True)
    prompt_loader.reject_agent(rt, "drop")
    assert not (rt.pending_agents_dir / "drop.md").exists()


def test_reject_agent_404_when_missing(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    with pytest.raises(FileNotFoundError):
        prompt_loader.reject_agent(rt, "nope")
```

- [ ] **Step 2: Run to fail**

```bash
uv run pytest tests/test_prompt_loader.py -v
```
Expected: ImportError on the new functions, or many FAILs.

### Task 3.2: Replace `prompt_loader.py` with the new API

**Files:**
- Modify: `src/orchestrator/prompt_loader.py`

- [ ] **Step 1: Rewrite the file**

Replace `src/orchestrator/prompt_loader.py` with:

```python
"""File-based agent loader.

Reads agents from <runtime>/org/agents/<name>.md (active) and
<runtime>/org/agents/_pending/<name>.md (awaiting approval). Replaces
the previous protocol-markdown parser and the agent_enrollments table.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.orchestrator.agent_def import (
    AgentDef,
    AgentParseError,
    parse_agent_file,
    render_agent_text,
)
from src.runtime import RuntimeDir


__all__ = [
    "AgentDef",
    "AgentParseError",
    "load_agent",
    "list_agents",
    "list_pending",
    "load_pending_agent",
    "write_pending_agent",
    "approve_agent",
    "reject_agent",
    "allow_rules_for_agent",
]


def _agent_path(runtime: RuntimeDir, name: str, *, pending: bool) -> Path:
    parent = runtime.pending_agents_dir if pending else runtime.agents_dir
    return parent / f"{name}.md"


def load_agent(runtime: RuntimeDir, name: str) -> AgentDef | None:
    """Return the active agent, or None if missing.

    Pending agents are NOT returned by this function — use load_pending_agent.
    """
    path = _agent_path(runtime, name, pending=False)
    if not path.exists():
        return None
    return parse_agent_file(path)


def load_pending_agent(runtime: RuntimeDir, name: str) -> AgentDef | None:
    path = _agent_path(runtime, name, pending=True)
    if not path.exists():
        return None
    return parse_agent_file(path)


def _list_dir(directory: Path) -> list[AgentDef]:
    if not directory.exists():
        return []
    out: list[AgentDef] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".md" and not entry.name.startswith("."):
            out.append(parse_agent_file(entry))
    return out


def list_agents(runtime: RuntimeDir) -> list[AgentDef]:
    """All active agents under <runtime>/org/agents/ (excluding _pending/)."""
    return _list_dir(runtime.agents_dir)


def list_pending(runtime: RuntimeDir) -> list[AgentDef]:
    return _list_dir(runtime.pending_agents_dir)


def write_pending_agent(runtime: RuntimeDir, agent: AgentDef) -> Path:
    """Atomically write a pending agent file. Overwrites if the slug is reused."""
    runtime.pending_agents_dir.mkdir(parents=True, exist_ok=True)
    target = _agent_path(runtime, agent.name, pending=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{agent.name}.", suffix=".md", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(render_agent_text(agent))
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return target


def approve_agent(runtime: RuntimeDir, name: str) -> AgentDef:
    """Atomically move <name>.md from _pending/ to the active directory.

    Raises:
      FileNotFoundError: if no pending file exists.
      FileExistsError: if an active agent with the same name already exists
        (caller should resolve manually before retrying).
    """
    pending = _agent_path(runtime, name, pending=True)
    if not pending.exists():
        raise FileNotFoundError(f"no pending agent: {name}")
    active = _agent_path(runtime, name, pending=False)
    if active.exists():
        raise FileExistsError(f"active agent already exists: {name}")
    runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    os.replace(pending, active)
    return parse_agent_file(active)


def reject_agent(runtime: RuntimeDir, name: str) -> None:
    pending = _agent_path(runtime, name, pending=True)
    if not pending.exists():
        raise FileNotFoundError(f"no pending agent: {name}")
    pending.unlink()


def allow_rules_for_agent(runtime: RuntimeDir, name: str) -> tuple[str, ...]:
    """Return the agent's declared Bash allow-rule prefixes (just the prefixes;
    Bash(...) wrapping is added by workspace_adapters._format_allow_rule).

    Returns () for an unknown agent.
    """
    agent = load_agent(runtime, name)
    if agent is None:
        return ()
    return agent.allow_rules
```

- [ ] **Step 2: Run prompt_loader tests**

```bash
uv run pytest tests/test_prompt_loader.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator/prompt_loader.py tests/test_prompt_loader.py
git commit -m "feat(prompt_loader): file-based AgentDef API"
```

---

## Phase 4: Workspace adapter integration

### Task 4.1: Test allow_rules_for_agent's new signature

**Files:**
- Modify: `tests/test_workspace_adapters_allow_rules.py`
- Modify: `tests/test_prompt_loader_allow_rules.py`

- [ ] **Step 1: Inspect existing tests**

```bash
cat tests/test_workspace_adapters_allow_rules.py tests/test_prompt_loader_allow_rules.py | head -200
```

- [ ] **Step 2: Rewrite `tests/test_prompt_loader_allow_rules.py`**

Replace the file's contents with:

```python
"""Tests for prompt_loader.allow_rules_for_agent."""
from __future__ import annotations

from pathlib import Path

from src.orchestrator import prompt_loader
from src.runtime import RuntimeDir


def _write(runtime: RuntimeDir, name: str, allow_rules: list[str]) -> None:
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    text = (
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )
    (runtime.agents_dir / f"{name}.md").write_text(text)


def test_returns_empty_for_unknown_agent(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert prompt_loader.allow_rules_for_agent(rt, "ghost") == ()


def test_returns_declared_rules(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write(rt, "eh", ["gh pr close", "gh issue close"])
    assert prompt_loader.allow_rules_for_agent(rt, "eh") == ("gh pr close", "gh issue close")


def test_returns_empty_when_field_empty(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write(rt, "dev", [])
    assert prompt_loader.allow_rules_for_agent(rt, "dev") == ()
```

- [ ] **Step 3: Rewrite `tests/test_workspace_adapters_allow_rules.py`**

Replace with:

```python
"""Tests for workspace_adapters.allow_rules_for_agent."""
from __future__ import annotations

from pathlib import Path

from src.orchestrator.workspace_adapters import allow_rules_for_agent
from src.runtime import RuntimeDir


def _write_agent(rt: RuntimeDir, name: str, allow_rules: list[str]) -> None:
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    (rt.agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )


def test_baseline_only_when_agent_none(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    rules = allow_rules_for_agent(rt, None, cli=False)
    assert rules == ["Bash(opc:*)"]


def test_baseline_plus_extras_settings_form(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "eh", ["gh pr close", "gh issue close"])
    rules = allow_rules_for_agent(rt, "eh", cli=False)
    assert rules == [
        "Bash(opc:*)",
        "Bash(gh pr close:*)",
        "Bash(gh issue close:*)",
    ]


def test_baseline_plus_extras_cli_form(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    _write_agent(rt, "eh", ["gh pr close"])
    rules = allow_rules_for_agent(rt, "eh", cli=True)
    assert rules == ["Bash(opc *)", "Bash(gh pr close *)"]


def test_unknown_agent_gets_baseline_only(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    rules = allow_rules_for_agent(rt, "ghost", cli=False)
    assert rules == ["Bash(opc:*)"]
```

- [ ] **Step 4: Run to fail**

```bash
uv run pytest tests/test_prompt_loader_allow_rules.py tests/test_workspace_adapters_allow_rules.py -v
```
Expected: FAIL — `allow_rules_for_agent` still has old signature `(settings, name, *, cli, db)`.

### Task 4.2: Update `workspace_adapters.allow_rules_for_agent`

**Files:**
- Modify: `src/orchestrator/workspace_adapters.py`

- [ ] **Step 1: Replace allow_rules_for_agent**

In `src/orchestrator/workspace_adapters.py`, replace the existing `allow_rules_for_agent` function (currently at lines 30–63) with:

```python
def allow_rules_for_agent(
    runtime: "RuntimeDir", agent_name: str | None, *, cli: bool,
) -> list[str]:
    """Build the Bash allow-rule list for ``agent_name``.

    Baseline ``opc`` is always included (the agent-callback channel).
    Additional prefixes come from the agent's ``allow_rules`` frontmatter
    field in ``<runtime>/org/agents/<name>.md``.
    """
    from src.orchestrator import prompt_loader
    rules = [_format_allow_rule("opc", cli=cli)]
    if agent_name is None:
        return rules
    for prefix in prompt_loader.allow_rules_for_agent(runtime, agent_name):
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules
```

Add at the top of the file (with the other imports):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime import RuntimeDir
```

Remove the now-unused `from src.infrastructure.database import Database` import.

- [ ] **Step 2: Replace `build_settings_json`**

Update the function signature and body:

```python
def build_settings_json(
    runtime: "RuntimeDir",
    repo_names: list[str],
    agent_name: str | None = None,
) -> dict:
    """Build .claude/settings.json with a git pull hook for all repos."""
    if repo_names:
        pull_cmds = " && ".join(
            f"(cd repos/{name} && git pull --ff-only 2>/dev/null; true)"
            for name in repo_names
        )
        hooks = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Grep|Glob",
                    "hooks": [
                        {"type": "command", "command": pull_cmds, "once": True}
                    ],
                }
            ]
        }
    else:
        hooks = {}

    return {
        "permissions": {
            "allow": allow_rules_for_agent(runtime, agent_name, cli=False),
        },
        "hooks": hooks,
    }
```

- [ ] **Step 3: Update `ClaudeWorkspaceAdapter` and `CodexWorkspaceAdapter`**

Both adapters take a `runtime: RuntimeDir` in their constructor, alongside `settings`:

```python
class ClaudeWorkspaceAdapter:
    """Bootstrap and maintain Claude Code workspaces."""

    provider_name = "claude"

    def __init__(self, settings: Settings, runtime: "RuntimeDir") -> None:
        self._settings = settings
        self._runtime = runtime
        self._persistent = PersistentWorkspaceSetup(settings)

    def write_settings_json(
        self,
        workspace: Path,
        repo_names: list[str] | None = None,
        agent_name: str | None = None,
    ) -> None:
        claude_dir = workspace / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_data = build_settings_json(
            self._runtime, repo_names or [], agent_name=agent_name,
        )
        (claude_dir / "settings.json").write_text(
            json.dumps(settings_data, indent=2) + "\n"
        )

    # ... rest unchanged
```

The Codex adapter constructor mirrors the Claude one:

```python
class CodexWorkspaceAdapter:
    provider_name = "codex"

    def __init__(self, settings: Settings, runtime: "RuntimeDir") -> None:
        self._settings = settings
        self._runtime = runtime
        self._persistent = PersistentWorkspaceSetup(settings)
```

The Codex adapter's call to `ClaudeWorkspaceAdapter(self._settings)._build_sections(...)` (line 389) becomes `ClaudeWorkspaceAdapter(self._settings, self._runtime)._build_sections(...)`.

- [ ] **Step 4: Run adapter allow-rules tests**

```bash
uv run pytest tests/test_prompt_loader_allow_rules.py tests/test_workspace_adapters_allow_rules.py -v
```
Expected: PASS.

- [ ] **Step 5: Run all workspace-adapter tests (will fail elsewhere)**

```bash
uv run pytest tests/test_workspace_adapters.py -v
```
Expected: FAILs because constructors changed. Update next step.

- [ ] **Step 6: Update `tests/test_workspace_adapters.py` callers**

Wherever a test constructs `ClaudeWorkspaceAdapter(settings)` or `CodexWorkspaceAdapter(settings)`, change to `ClaudeWorkspaceAdapter(settings, runtime)` / `CodexWorkspaceAdapter(settings, runtime)`. Use `RuntimeDir.init(tmp_path / "rt", slug="test")` to build the runtime fixture.

For tests that call `build_settings_json(settings, ...)`, change the first arg to `runtime`.

Run again until green:

```bash
uv run pytest tests/test_workspace_adapters.py -v
```
Expected: PASS.

- [ ] **Step 7: Update `ContextBuilder` (caller of adapters)**

Open `src/orchestrator/context_builder.py`. Update wherever it constructs `ClaudeWorkspaceAdapter(settings)` / `CodexWorkspaceAdapter(settings)` to pass `runtime`. The simplest change: pass `runtime` into the `ContextBuilder` ctor and forward it. Adjust callers accordingly.

```python
# src/orchestrator/context_builder.py
class ContextBuilder:
    def __init__(self, settings: Settings, runtime: RuntimeDir) -> None:
        self._settings = settings
        self._runtime = runtime
        self._claude = ClaudeWorkspaceAdapter(settings, runtime)
        self._codex = CodexWorkspaceAdapter(settings, runtime)
    ...
```

Then update every `ContextBuilder(state.settings)` call site (search results from earlier listing — `src/daemon/routes/agents.py` lines 272, 330, 447, 551 and `src/orchestrator/run_step.py`) to `ContextBuilder(state.settings, state.runtime)` (or the equivalent local `runtime` reference).

```bash
uv run pytest tests/test_context_builder.py -v
```
Expected: PASS (after updating tests' ContextBuilder constructions to pass runtime).

- [ ] **Step 8: Commit**

```bash
git add src/orchestrator/workspace_adapters.py src/orchestrator/context_builder.py \
    tests/test_workspace_adapters.py tests/test_workspace_adapters_allow_rules.py \
    tests/test_prompt_loader_allow_rules.py tests/test_context_builder.py
git commit -m "refactor(workspace_adapters): allow_rules read frontmatter, ctors take runtime"
```

---

## Phase 5: Orchestrator integration

### Task 5.1: Drop `DEFAULT_LAYOUT` from Orchestrator.__init__

**Files:**
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_orchestrator.py`:

```python
def test_orchestrator_requires_teams() -> None:
    import pytest
    from pathlib import Path
    from src.config import Settings
    from src.infrastructure.database import Database
    from src.orchestrator.orchestrator import Orchestrator
    from src.runtime import RuntimeDir
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rt = RuntimeDir.init(Path(td) / "rt", slug="x")
        db = Database(rt.db_path)
        settings = Settings()
        with pytest.raises(TypeError):
            Orchestrator(db=db, settings=settings, runtime=rt)  # missing teams
```

- [ ] **Step 2: Run to fail**

```bash
uv run pytest tests/test_orchestrator.py -v -k "requires_teams"
```
Expected: FAIL — Orchestrator currently has a default for `teams`.

- [ ] **Step 3: Modify `src/orchestrator/orchestrator.py`**

Change the import at line 28:
```python
from src.orchestrator.teams import TeamsRegistry
```
(remove `, DEFAULT_LAYOUT`)

Change the constructor signature at line 51 to make `teams` required:

```python
class Orchestrator:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        runtime: RuntimeDir,
        teams: TeamsRegistry,
    ) -> None:
        self._db = db
        self._settings = settings
        self._runtime = runtime
        self._audit = AuditLogger(db)
        self._tracker = PerformanceTracker(db, settings)
        self._teams = teams
        self._queue = None
        self._sessions = None
```

- [ ] **Step 4: Run orchestrator tests**

```bash
uv run pytest tests/test_orchestrator.py -v
```
Expected: PASS for the `requires_teams` test. Other tests may fail because they construct `Orchestrator` without `teams`. Update fixtures to pass `TeamsRegistry.load(rt)`.

- [ ] **Step 5: Update remaining Orchestrator construction sites**

Search and update:

```bash
uv run python -c "
import subprocess
out = subprocess.check_output(['git', 'grep', '-l', 'Orchestrator(']).decode()
print(out)
"
```

In each test/source that builds an `Orchestrator`, supply `teams=TeamsRegistry.load(runtime)`. The daemon's existing wiring already passes a `teams` argument; verify by inspecting `src/daemon/state.py` and `src/daemon/app.py`.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "refactor(orchestrator): teams is now required (no DEFAULT_LAYOUT)"
```

### Task 5.2: Replace remaining `_AGENT_SOURCES` callsites

There are three call sites in `src/daemon/routes/agents.py` (lines 271, 331, 605) and the orchestrator's prompt-builder path. They were grep'd above. Each needs to switch from `load_all_prompts(protocol_dir)` / `load_system_prompt(protocol_dir, name)` to `prompt_loader.load_agent(runtime, name)` / `prompt_loader.list_agents(runtime)`.

We'll do agents.py in Phase 6 since that's where the route logic lives. Run a grep here to confirm orchestrator/run_step.py has no direct call:

- [ ] **Step 1: Confirm orchestrator side has no protocol-loader calls**

```bash
uv run python -c "
import subprocess
out = subprocess.check_output(['git', 'grep', '-n', 'load_system_prompt\\|load_all_prompts\\|_AGENT_SOURCES', 'src/orchestrator/']).decode()
print(out or '(none)')
"
```
Expected: `(none)`.

- [ ] **Step 2: No-op commit checkpoint** (skip if nothing to commit)

---

## Phase 6: Agents route updates (file-based)

This phase rewrites the four DB-enrollment-using endpoints in `src/daemon/routes/agents.py`. The route paths stay where they are (Plan 2 path-prefixes them under `/orgs/{slug}/...`).

Audit logging stays — both `log_agent_managed` and `log_agent_backfilled` are unaffected. Only the persistence layer changes.

### Task 6.1: Test the file-based manage_agent enroll path

**Files:**
- Modify: `tests/daemon/test_routes_agents.py`

- [ ] **Step 1: Inspect the existing test**

```bash
grep -n "test_manage_agent\|test_enroll\|test_approve\|test_reject" tests/daemon/test_routes_agents.py | head -50
```

- [ ] **Step 2: Add failing tests for file-based enroll/approve/reject flow**

Append to `tests/daemon/test_routes_agents.py`:

```python
def test_manage_agent_enroll_writes_pending_file(client, daemon_state):
    """Enrolling an agent writes <runtime>/org/agents/_pending/<name>.md."""
    # eh-authenticated session is set up by the fixture; see existing patterns.
    payload = {
        "action": "enroll",
        "name": "fresh_dev",
        "task_id": "TASK-001",
        "session_id": daemon_state["eh_session_id"],
        "description": "Test agent",
        "system_prompt": "You are fresh_dev.\n",
        "executor": "claude",
        "repos": {},
    }
    r = client.post("/agents/manage", json=payload, headers=daemon_state["auth_headers"])
    assert r.status_code == 200
    rt = daemon_state["runtime"]
    assert (rt.pending_agents_dir / "fresh_dev.md").exists()
    # And no DB enrollment row exists.
    assert daemon_state["db"].get_enrollment("fresh_dev") is None  # legacy table empty


def test_approve_agent_moves_file(client, daemon_state):
    # First enroll
    client.post("/agents/manage", json={
        "action": "enroll", "name": "approve_me",
        "task_id": "TASK-001", "session_id": daemon_state["eh_session_id"],
        "description": "x", "system_prompt": "body\n", "repos": {}, "executor": "claude",
    }, headers=daemon_state["auth_headers"])
    rt = daemon_state["runtime"]
    assert (rt.pending_agents_dir / "approve_me.md").exists()

    r = client.post("/agents/approve_me/approve", headers=daemon_state["auth_headers"])
    assert r.status_code == 200
    assert not (rt.pending_agents_dir / "approve_me.md").exists()
    assert (rt.agents_dir / "approve_me.md").exists()


def test_reject_agent_unlinks_file(client, daemon_state):
    client.post("/agents/manage", json={
        "action": "enroll", "name": "reject_me",
        "task_id": "TASK-001", "session_id": daemon_state["eh_session_id"],
        "description": "x", "system_prompt": "body\n", "repos": {}, "executor": "claude",
    }, headers=daemon_state["auth_headers"])
    rt = daemon_state["runtime"]
    assert (rt.pending_agents_dir / "reject_me.md").exists()

    r = client.post("/agents/reject_me/reject", headers=daemon_state["auth_headers"])
    assert r.status_code == 200
    assert not (rt.pending_agents_dir / "reject_me.md").exists()
```

If the existing `daemon_state` fixture doesn't already set up an EH session id, follow the pattern from existing tests in the same file (search for `eh_session_id` or `session_id` in the file).

- [ ] **Step 3: Run to fail**

```bash
uv run pytest tests/daemon/test_routes_agents.py -v -k "writes_pending_file or moves_file or unlinks_file"
```
Expected: FAILs because `manage_agent` still uses DB.

### Task 6.2: Rewrite the four enrollment-touching endpoints

**Files:**
- Modify: `src/daemon/routes/agents.py`

These rewrites preserve the audit logs, the cross-team checks, and the `state.teams.add_worker` / `remove_worker` flows. Only the persistence backend changes.

- [ ] **Step 1: Update imports**

At the top of `src/daemon/routes/agents.py`, replace the `prompt_loader` import line with:

```python
from src.orchestrator import prompt_loader
from src.orchestrator.agent_def import AgentDef, AgentParseError
```

Remove the now-unused `from src.orchestrator.prompt_loader import load_all_prompts, load_system_prompt`.

- [ ] **Step 2: Rewrite `init_agents` (lines 252–315)**

Replace its body with:

```python
@router.post("/agents/init")
async def init_agents(body: InitBody, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    if body.agent is None:
        ws_dir = state.runtime.workspaces_dir
        known: set[str] = set()
        if state.teams is not None:
            known.update(state.teams.all_agents())
        if ws_dir.exists():
            known.update(d.name for d in ws_dir.iterdir() if d.is_dir())
        for a in prompt_loader.list_agents(state.runtime):
            known.add(a.name)
        targets = sorted(known)
    else:
        targets = [body.agent]

    async def gen():
        ctx = ContextBuilder(state.settings, state.runtime)
        for agent_name in targets:
            workspace = state.runtime.workspaces_dir / agent_name
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent_name, "phase": "starting"})}
            try:
                agent_def = prompt_loader.load_agent(state.runtime, agent_name)
                if agent_def is None:
                    yield {"data": _json.dumps({
                        "agent": agent_name, "phase": "error",
                        "detail": f"no agent file at <runtime>/org/agents/{agent_name}.md",
                    })}
                    return
                had_agent_config = (workspace / "agent.yaml").exists()
                write_default_agent_config(workspace)
                if not had_agent_config:
                    set_executor(workspace, agent_def.executor)
                cfg = load_agent_config(workspace)
                provider = cfg.get("executor") or agent_def.executor or "claude"
                # Repos are sourced from agent.yaml first, fall back to AgentDef.
                repos = cfg.get("repos") or agent_def.repos or {}
                for repo_name, url in repos.items():
                    yield {"data": _json.dumps({
                        "agent": agent_name, "phase": "repo_cloning",
                        "repo": repo_name,
                    })}
                    ok = await asyncio.to_thread(
                        ctx.clone_repo, workspace, repo_name, url,
                    )
                    yield {"data": _json.dumps({
                        "agent": agent_name,
                        "phase": "repo_ready" if ok else "repo_failed",
                        "repo": repo_name,
                    })}
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, agent_name,
                    agent_def.system_prompt, provider=provider,
                )
                await asyncio.to_thread(
                    ctx.create_agent_dirs, workspace, agent_name,
                )
            except Exception as exc:
                yield {"data": _json.dumps({
                    "agent": agent_name, "phase": "error", "detail": str(exc),
                })}
                return
            yield {"data": _json.dumps({"agent": agent_name, "phase": "done"})}
        yield {"data": _json.dumps({"phase": "all_done"})}

    return EventSourceResponse(gen())
```

- [ ] **Step 3: Rewrite `manage_repo` system_prompt source (lines 318–363)**

Replace the prompt lookup near the top:

```python
agent_def = prompt_loader.load_agent(state.runtime, agent_name)
agent_prompt = agent_def.system_prompt if agent_def is not None else ""
ctx = ContextBuilder(state.settings, state.runtime)
```

Drop the `prompts = load_all_prompts(...)` line.

- [ ] **Step 4: Rewrite `manage_agent` (lines 366–497)**

Replace the entire endpoint body (preserve the function signature and `_require_team_manager_auth` call):

```python
@router.post("/agents/manage")
async def manage_agent(body: ManageAgentBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    manager_name, manager_team = _require_team_manager_auth(body, state)

    scope_id = body.talk_id if body.talk_id is not None else body.task_id
    assert scope_id is not None
    source = "talk" if body.talk_id is not None else "task"
    audit = AuditLogger(state.db)

    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(status_code=422, detail=f"invalid agent name: {body.name!r}")

    rt = state.runtime

    if body.action == ManageAgentAction.enroll:
        if not body.description or not body.system_prompt:
            raise HTTPException(status_code=422, detail="description and system_prompt required for enroll")
        if prompt_loader.load_agent(rt, body.name) is not None:
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} already exists")
        if prompt_loader.load_pending_agent(rt, body.name) is not None:
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} already pending")
        async with state.teams_lock:
            target_team = body.target_team or manager_team
            if target_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "requested_team": target_team,
                    },
                )
            agent = AgentDef(
                name=body.name,
                team=target_team,
                role="worker",
                executor=body.executor or "claude",
                allow_rules=tuple(body.allow_rules or ()),
                repos=dict(body.repos or {}),
                enrolled_by=manager_name,
                enrolled_at_task=body.task_id,
                enrolled_at=datetime.now(timezone.utc),
                system_prompt=body.system_prompt,
            )
            prompt_loader.write_pending_agent(rt, agent)
            state.teams.add_worker(target_team, body.name)
            state.teams.save(rt)
        audit.log_agent_managed(
            scope_id=scope_id, action="enroll", name=body.name,
            source=source, actor=manager_name,
        )
        return {"ok": True, "status": "pending"}

    elif body.action == ManageAgentAction.update:
        existing = prompt_loader.load_agent(rt, body.name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        async with state.teams_lock:
            agent_team = state.teams.team_for_agent(body.name) if state.teams is not None else None
            if agent_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "agent_team": agent_team,
                    },
                )
        # Build updated AgentDef.
        updated = AgentDef(
            name=existing.name,
            team=existing.team,
            role=existing.role,
            executor=body.executor or existing.executor,
            allow_rules=existing.allow_rules,  # update doesn't change allow_rules
            repos=dict(body.repos) if body.repos is not None else existing.repos,
            enrolled_by=existing.enrolled_by,
            enrolled_at_task=existing.enrolled_at_task,
            enrolled_at=existing.enrolled_at,
            system_prompt=body.system_prompt or existing.system_prompt,
        )
        from src.orchestrator.agent_def import render_agent_text
        target = rt.agents_dir / f"{body.name}.md"
        # Atomic overwrite.
        fd, tmp = tempfile.mkstemp(prefix=f".{body.name}.", suffix=".md", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(render_agent_text(updated))
            os.replace(tmp, target)
        except Exception:
            try: os.unlink(tmp)
            except FileNotFoundError: pass
            raise
        if body.system_prompt:
            workspace = rt.workspaces_dir / body.name
            if workspace.exists():
                ctx = ContextBuilder(state.settings, rt)
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, body.name, updated.system_prompt,
                )
        if body.executor is not None:
            workspace = rt.workspaces_dir / body.name
            if workspace.exists():
                await asyncio.to_thread(set_executor, workspace, body.executor)
        audit.log_agent_managed(
            scope_id=scope_id, action="update", name=body.name,
            source=source, actor=manager_name,
        )
        return {"ok": True}

    elif body.action == ManageAgentAction.terminate:
        existing = prompt_loader.load_agent(rt, body.name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        async with state.teams_lock:
            agent_team = state.teams.team_for_agent(body.name) if state.teams is not None else None
            if agent_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "agent_team": agent_team,
                    },
                )
            (rt.agents_dir / f"{body.name}.md").unlink()
            state.teams.remove_worker(manager_team, body.name)
            state.teams.save(rt)
        workspace = rt.workspaces_dir / body.name
        if workspace.exists():
            shutil.rmtree(workspace)
        audit.log_agent_managed(
            scope_id=scope_id, action="terminate", name=body.name,
            source=source, actor=manager_name,
        )
        return {"ok": True}

    raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")
```

Add to imports at the top of the file:
```python
import os
import tempfile
from datetime import datetime, timezone
```

- [ ] **Step 5: Rewrite `list_enrollments` (lines 500–526)**

```python
@router.get("/agents/enrollments")
def list_enrollments(
    request: Request,
    enrollment_status: str | None = Query(default=None, alias="status"),
    team: str | None = Query(default=None),
) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    rt = state.runtime
    if enrollment_status == "pending":
        agents = prompt_loader.list_pending(rt)
    elif enrollment_status == "approved":
        agents = prompt_loader.list_agents(rt)
    elif enrollment_status is None:
        agents = prompt_loader.list_pending(rt) + prompt_loader.list_agents(rt)
    else:
        agents = []  # rejected/terminated have no on-disk representation
    if team is not None:
        agents = [a for a in agents if a.team == team]

    def status_for(a: AgentDef) -> str:
        return "pending" if (rt.pending_agents_dir / f"{a.name}.md").exists() else "approved"

    return {"enrollments": [
        {
            "name": a.name,
            "description": "",  # not stored in file format
            "status": status_for(a),
            "created_at": a.enrolled_at.isoformat() if a.enrolled_at else None,
        }
        for a in agents
    ]}
```

- [ ] **Step 6: Rewrite `approve_agent` and add `reject_agent` (lines 529–564)**

```python
@router.post("/agents/{agent_name}/approve")
async def approve_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    rt = state.runtime
    pending = prompt_loader.load_pending_agent(rt, agent_name)
    if pending is None:
        raise HTTPException(status_code=404, detail=f"no pending agent {agent_name!r}")
    try:
        agent = prompt_loader.approve_agent(rt, agent_name)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"active agent {agent_name!r} already exists")

    workspace = rt.workspaces_dir / agent_name
    workspace.mkdir(parents=True, exist_ok=True)
    write_default_agent_config(workspace)
    set_executor(workspace, agent.executor)
    if agent.repos:
        for repo_name, url in agent.repos.items():
            add_repo(workspace, repo_name, url)
    ctx = ContextBuilder(state.settings, rt)
    for repo_name, url in agent.repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)
    await asyncio.to_thread(
        ctx.ensure_workspace_ready, workspace, agent_name,
        agent.system_prompt,
        provider=load_agent_config(workspace).get("executor") or "claude",
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, agent_name)
    return {"ok": True}


@router.post("/agents/{agent_name}/reject")
async def reject_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    rt = state.runtime
    if prompt_loader.load_pending_agent(rt, agent_name) is None:
        raise HTTPException(status_code=404, detail=f"no pending agent {agent_name!r}")
    prompt_loader.reject_agent(rt, agent_name)
    return {"ok": True}
```

- [ ] **Step 7: Rewrite `backfill_enrollments`**

This route was for founder recovery of pre-existing workspaces into the DB. With files-as-source-of-truth, the equivalent is to write `<runtime>/org/agents/<name>.md` for each workspace. The simplest version: if the workspace has an `agent.yaml` with an executor, but no agent file exists in `org/agents/`, prompt the user to write one manually. Reduce the endpoint to a no-op + deprecation message:

```python
@router.post("/agents/backfill-enrollments")
def backfill_enrollments(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    return {
        "backfilled": [],
        "skipped_already_enrolled": [],
        "skipped_unknown_prompt": [],
        "deprecated": True,
        "note": "Backfill is now done via `opc migrate-to-org-runtime`. Pre-existing workspaces without org/agents/<name>.md should be reconstructed by the founder manually.",
    }
```

- [ ] **Step 8: Run agent route tests**

```bash
uv run pytest tests/daemon/test_routes_agents.py -v
```
Expected: PASS for the new file-based tests. Existing tests that depend on DB enrollment behavior need updating — fix them by:
- Changing `state.db.get_enrollment(name)` checks to `prompt_loader.load_agent(state.runtime, name)`.
- Removing assertions about `agent_enrollments` rows.

Iterate until all tests in this file pass.

- [ ] **Step 9: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "refactor(routes/agents): file-based enrollment (no agent_enrollments table)"
```

### Task 6.3: Other call sites — `list_approved_agent_names`

`src/daemon/state.py` and `src/daemon/routes/agents.py:264` use `state.db.list_approved_agent_names()`. Replace with `[a.name for a in prompt_loader.list_agents(state.runtime)]`.

- [ ] **Step 1: Grep**

```bash
grep -rn "list_approved_agent_names" src/ tests/
```

- [ ] **Step 2: Replace each call**

Update every caller. Then mark `Database.list_approved_agent_names` deprecated (a one-line `# deprecated: use prompt_loader.list_agents` is fine — full removal in Plan 2).

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/ -v -m "not integration"
```
Expected: green or near-green. Fix remaining cascade.

- [ ] **Step 4: Commit**

```bash
git add src/ tests/
git commit -m "refactor: replace list_approved_agent_names with prompt_loader.list_agents"
```

---

## Phase 7: Migration script

### Task 7.1: Test the migration script (dry-run)

**Files:**
- Modify or replace: `tests/test_migration.py`

The existing `tests/test_migration.py` covers an older migration; verify by reading it. Add a new test class for `migrate_to_org_runtime`:

- [ ] **Step 1: Append to `tests/test_migration.py`**

```python
"""Migration: <runtime>/teams.yaml + agent_enrollments → <runtime>/org/."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.orchestrator.migration import migrate_to_org_runtime, MigrationResult
from src.runtime import RuntimeDir


def _build_legacy_runtime(tmp_path: Path, *, with_enrollments: bool = True) -> Path:
    """Construct a pre-org-cut runtime at tmp_path/legacy."""
    rt_root = tmp_path / "legacy"
    rt_root.mkdir()
    # opc.yaml without slug (the pre-cut shape).
    (rt_root / "opc.yaml").write_text("")
    (rt_root / "workspaces").mkdir()
    # teams.yaml at the OLD location (root, not under org/).
    (rt_root / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
    )
    # SQLite with legacy agent_enrollments table.
    db_path = rt_root / "opc.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
      CREATE TABLE agent_enrollments (
        name TEXT PRIMARY KEY,
        description TEXT,
        system_prompt TEXT,
        repos TEXT,
        executor TEXT,
        allow_rules TEXT,
        status TEXT,
        created_at TEXT
      );
    """)
    if with_enrollments:
        conn.execute(
            "INSERT INTO agent_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("custom_dev", "A custom dev", "You are custom_dev.\n",
             '{"my-opc": "https://github.com/x/x.git"}', "claude",
             '[]', "approved", "2026-04-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO agent_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("draft_writer", "Draft", "Body\n", '{}', "claude", '[]',
             "pending", "2026-04-02T00:00:00Z"),
        )
    conn.commit()
    conn.close()
    return rt_root


def test_dryrun_emits_planned_actions(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    result = migrate_to_org_runtime(
        rt_root, slug="hk-tourism",
        i_have_a_backup=True, apply=False,
    )
    assert isinstance(result, MigrationResult)
    assert result.applied is False
    assert any("write opc.yaml" in step for step in result.planned)
    assert any("move teams.yaml" in step for step in result.planned)
    assert any("custom_dev" in step for step in result.planned)
    assert any("draft_writer" in step for step in result.planned)
    # Filesystem unchanged.
    assert not (rt_root / "org").exists()
    assert (rt_root / "teams.yaml").exists()


def test_apply_writes_org_tree(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    result = migrate_to_org_runtime(
        rt_root, slug="hk-tourism",
        i_have_a_backup=True, apply=True,
    )
    assert result.applied is True
    rt = RuntimeDir.load(rt_root)
    assert rt.slug == "hk-tourism"
    # teams.yaml moved.
    assert rt.teams_config_path.exists()
    assert not (rt_root / "teams.yaml").exists()
    # Approved enrollment exported to active agents/.
    assert (rt.agents_dir / "custom_dev.md").exists()
    # Pending enrollment exported to _pending/.
    assert (rt.pending_agents_dir / "draft_writer.md").exists()
    # agent_enrollments table dropped.
    conn = sqlite3.connect(rt.db_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_enrollments'")
    assert cur.fetchone() is None
    conn.close()


def test_apply_idempotent(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=True, apply=True)
    second = migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=True, apply=True)
    assert second.already_migrated is True


def test_aborts_without_backup_flag(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    with pytest.raises(ValueError, match="i_have_a_backup"):
        migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=False, apply=True)


def test_aborts_when_slug_disagrees_with_existing(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path)
    (rt_root / "opc.yaml").write_text("slug: existing\n")
    with pytest.raises(ValueError, match="slug.*disagrees"):
        migrate_to_org_runtime(rt_root, slug="other", i_have_a_backup=True, apply=True)


def test_strips_completion_contract_block(tmp_path: Path) -> None:
    rt_root = _build_legacy_runtime(tmp_path, with_enrollments=False)
    # Insert one enrollment whose system_prompt has the canonical contract block.
    conn = sqlite3.connect(rt_root / "opc.db")
    body = (
        "You are role_x.\n\nResponsibilities: do X.\n\n"
        "## Task completion report\n\n"
        "Format: confidence, risks, ...\n"
        "(this whole section should be stripped on migration.)\n"
    )
    conn.execute(
        "INSERT INTO agent_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("role_x", "x", body, "{}", "claude", "[]", "approved", "2026-04-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    migrate_to_org_runtime(rt_root, slug="x", i_have_a_backup=True, apply=True)
    rt = RuntimeDir.load(rt_root)
    text = (rt.agents_dir / "role_x.md").read_text()
    assert "Task completion report" not in text
    assert "do X" in text
```

- [ ] **Step 2: Run to fail**

```bash
uv run pytest tests/test_migration.py -v -k "to_org_runtime or strips_completion or apply_idempotent or aborts"
```
Expected: ImportError on `src.orchestrator.migration`.

### Task 7.2: Implement `src/orchestrator/migration.py`

**Files:**
- Create: `src/orchestrator/migration.py`

- [ ] **Step 1: Write the module**

```python
"""One-shot: <runtime>/teams.yaml + agent_enrollments → <runtime>/org/.

Run via: opc migrate-to-org-runtime <runtime-path> --slug <slug> --i-have-a-backup [--apply]
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.orchestrator.agent_def import AgentDef, render_agent_text


_CONTRACT_HEADING_RE = re.compile(
    r"\n## Task completion report\b.*\Z",
    re.DOTALL,
)


@dataclass
class MigrationResult:
    runtime_path: Path
    slug: str
    applied: bool
    already_migrated: bool = False
    planned: list[str] = field(default_factory=list)
    exported_approved: list[str] = field(default_factory=list)
    exported_pending: list[str] = field(default_factory=list)


def _strip_contract_block(prompt: str) -> str:
    """Remove the canonical `## Task completion report` block (and everything
    after it within the prompt). Returns the prompt unchanged if not present.
    """
    return _CONTRACT_HEADING_RE.sub("\n", prompt).rstrip() + "\n"


def _load_existing_marker(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return raw if isinstance(raw, dict) else {}


def _enrollment_to_agent(row: sqlite3.Row | tuple, *, team: str, role: str) -> AgentDef:
    """Convert an agent_enrollments row into an AgentDef."""
    cols = {
        "name": row[0], "description": row[1], "system_prompt": row[2],
        "repos": row[3], "executor": row[4], "allow_rules": row[5],
        "status": row[6], "created_at": row[7],
    }
    repos = json.loads(cols["repos"]) if cols["repos"] else {}
    allow_rules = json.loads(cols["allow_rules"]) if cols["allow_rules"] else []
    enrolled_at: datetime | None = None
    if cols["created_at"]:
        try:
            enrolled_at = datetime.fromisoformat(cols["created_at"].replace("Z", "+00:00"))
        except ValueError:
            enrolled_at = None
    body = _strip_contract_block(cols["system_prompt"] or "")
    if not body.strip():
        body = "Migrated agent — system prompt was empty.\n"
    return AgentDef(
        name=cols["name"],
        team=team,
        role=role,  # type: ignore[arg-type]
        executor=cols["executor"] or "claude",
        allow_rules=tuple(allow_rules),
        repos=dict(repos),
        enrolled_by=None,  # not recorded in the legacy table
        enrolled_at_task=None,
        enrolled_at=enrolled_at,
        system_prompt=body,
    )


def migrate_to_org_runtime(
    runtime_path: Path,
    *,
    slug: str,
    i_have_a_backup: bool,
    apply: bool = False,
) -> MigrationResult:
    """Migrate a pre-org-cut runtime in place.

    Steps:
      1. Validate flags.
      2. Detect already-migrated → return early.
      3. Validate slug consistency with existing opc.yaml (if any).
      4. Plan / apply: write opc.yaml, create org/ skeleton, move teams.yaml,
         export enrollments, drop agent_enrollments table.
    """
    if not i_have_a_backup:
        raise ValueError(
            "migrate_to_org_runtime requires --i-have-a-backup to acknowledge "
            "that you've backed up the runtime folder."
        )
    runtime_path = runtime_path.resolve()
    marker = runtime_path / "opc.yaml"
    if not marker.exists():
        raise ValueError(f"{marker} missing — not a valid pre-cut runtime")
    existing = _load_existing_marker(marker)
    existing_slug = existing.get("slug")
    if existing_slug and existing_slug != slug:
        raise ValueError(
            f"opc.yaml slug ({existing_slug!r}) disagrees with --slug ({slug!r})"
        )
    org_dir = runtime_path / "org"
    teams_old = runtime_path / "teams.yaml"
    teams_new = org_dir / "teams.yaml"

    db_path = runtime_path / "opc.db"
    table_present = False
    rows: list[tuple] = []
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_enrollments'"
            )
            table_present = cur.fetchone() is not None
            if table_present:
                cur2 = conn.execute(
                    "SELECT name, description, system_prompt, repos, executor, "
                    "allow_rules, status, created_at FROM agent_enrollments"
                )
                rows = [tuple(r) for r in cur2.fetchall()]
        finally:
            conn.close()

    # Detect already-migrated: org/teams.yaml present AND no enrollments table.
    if teams_new.exists() and not table_present:
        return MigrationResult(
            runtime_path=runtime_path, slug=slug,
            applied=False, already_migrated=True,
        )

    planned: list[str] = []
    if not existing_slug:
        planned.append(f"write opc.yaml with slug={slug}, schema_version=1")
    planned.append(f"create org/ skeleton at {org_dir}")
    if teams_old.exists():
        planned.append(f"move teams.yaml → {teams_new}")
    elif not teams_new.exists():
        planned.append(f"seed empty {teams_new}")
    for r in rows:
        if r[6] == "approved":
            planned.append(f"export approved enrollment: {r[0]}")
        elif r[6] == "pending":
            planned.append(f"export pending enrollment: {r[0]}")
        # rejected/terminated: skipped silently
    if table_present:
        planned.append("drop agent_enrollments table")

    if not apply:
        return MigrationResult(
            runtime_path=runtime_path, slug=slug,
            applied=False, planned=planned,
        )

    # APPLY ----------------------------------------------------------------
    # 1. Write/update opc.yaml.
    if not existing_slug:
        payload = {
            "slug": slug,
            "created_at": existing.get("created_at")
                or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "schema_version": 1,
        }
        marker.write_text(yaml.safe_dump(payload, sort_keys=False))

    # 2. Create org/ skeleton.
    (org_dir / "agents" / "_pending").mkdir(parents=True, exist_ok=True)

    # 3. Move teams.yaml.
    if teams_old.exists() and not teams_new.exists():
        shutil.move(str(teams_old), str(teams_new))
    elif not teams_new.exists():
        teams_new.write_text("teams: {}\n")

    # 4. Load teams to determine team membership.
    teams_layout = (yaml.safe_load(teams_new.read_text()) or {}).get("teams") or {}

    def lookup_team_role(name: str) -> tuple[str, str]:
        for team_name, entry in teams_layout.items():
            if entry.get("manager") == name:
                return team_name, "manager"
            if name in (entry.get("workers") or []):
                return team_name, "worker"
        # Default for orphan rows: place under engineering as worker.
        return "engineering", "worker"

    exported_approved: list[str] = []
    exported_pending: list[str] = []
    for r in rows:
        status = r[6]
        if status not in ("approved", "pending"):
            continue
        team, role = lookup_team_role(r[0])
        agent = _enrollment_to_agent(r, team=team, role=role)
        target_dir = (
            org_dir / "agents" / "_pending" if status == "pending" else org_dir / "agents"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{agent.name}.md").write_text(render_agent_text(agent))
        (exported_pending if status == "pending" else exported_approved).append(agent.name)

    # 5. Drop the table.
    if table_present:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE agent_enrollments")
            conn.commit()
        finally:
            conn.close()

    return MigrationResult(
        runtime_path=runtime_path, slug=slug,
        applied=True, planned=planned,
        exported_approved=exported_approved,
        exported_pending=exported_pending,
    )
```

- [ ] **Step 2: Run migration tests**

```bash
uv run pytest tests/test_migration.py -v -k "to_org_runtime or strips_completion or apply_idempotent or aborts"
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator/migration.py tests/test_migration.py
git commit -m "feat(migration): opc migrate-to-org-runtime script"
```

### Task 7.3: Wire the CLI subcommand

**Files:**
- Modify: `src/cli.py`

- [ ] **Step 1: Add the subcommand**

Find an existing `cmd_*` function for shape reference. Add:

```python
def cmd_migrate_to_org_runtime(args: argparse.Namespace) -> int:
    """`opc migrate-to-org-runtime <path> --slug <slug> --i-have-a-backup [--apply]`."""
    from src.orchestrator.migration import migrate_to_org_runtime
    try:
        result = migrate_to_org_runtime(
            Path(args.runtime_path).expanduser().resolve(),
            slug=args.slug,
            i_have_a_backup=args.i_have_a_backup,
            apply=args.apply,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if result.already_migrated:
        print(f"already migrated: {result.runtime_path}")
        return 0
    if not result.applied:
        print(f"DRY-RUN — would apply {len(result.planned)} actions:")
        for step in result.planned:
            print(f"  - {step}")
        print("\nRe-run with --apply to execute.")
        return 0
    print(f"migrated runtime: {result.runtime_path}")
    print(f"slug: {result.slug}")
    print(f"approved exports ({len(result.exported_approved)}): "
          f"{', '.join(result.exported_approved) or '(none)'}")
    print(f"pending exports ({len(result.exported_pending)}): "
          f"{', '.join(result.exported_pending) or '(none)'}")
    return 0
```

In the argparse setup, add:

```python
mig = sub.add_parser(
    "migrate-to-org-runtime",
    help="One-shot: migrate <runtime>/teams.yaml + agent_enrollments → <runtime>/org/.",
)
mig.add_argument("runtime_path")
mig.add_argument("--slug", required=True)
mig.add_argument("--i-have-a-backup", action="store_true",
                 help="Mandatory acknowledgment that the runtime is backed up.")
mig.add_argument("--apply", action="store_true",
                 help="Execute the migration. Without this, the command is a dry run.")
mig.set_defaults(func=cmd_migrate_to_org_runtime)
```

- [ ] **Step 2: Manual smoke**

```bash
uv run opc migrate-to-org-runtime --help
```
Expected: argparse-rendered help showing flags.

- [ ] **Step 3: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): opc migrate-to-org-runtime subcommand"
```

---

## Phase 8: Repo-side cleanup

This phase deletes the now-unused org-specific protocol files and de-orgs the remaining design docs. **Do this last** — the prior phases must be green first because the example tree depends on these source files.

### Task 8.1: Verify no code path still reads the old protocol files

- [ ] **Step 1: Grep for direct references**

```bash
grep -rn "01-org-charter\|02-system-prompts\|03-system-prompts\|04-escalation-rules\|05a-teams" src/ tests/
```
Expected: only matches inside docstring/comment context (or empty). If any code still loads these paths, fix it before proceeding.

- [ ] **Step 2: Run full test suite to confirm green**

```bash
uv run pytest tests/ -v -m "not integration"
```
Expected: PASS.

### Task 8.2: Delete obsolete protocol files

**Files (delete):**
- `protocol/01-org-charter.md`
- `protocol/02-system-prompts-managers.md`
- `protocol/03-system-prompts-workers.md`
- `protocol/04-escalation-rules.md`
- `protocol/05a-teams.md`

- [ ] **Step 1: Delete**

```bash
git rm protocol/01-org-charter.md protocol/02-system-prompts-managers.md \
       protocol/03-system-prompts-workers.md protocol/04-escalation-rules.md \
       protocol/05a-teams.md
```

- [ ] **Step 2: Run full test suite again**

```bash
uv run pytest tests/ -v -m "not integration"
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(protocol): remove org-specific files (now in examples/orgs/hk-macau-tourism/org/)"
```

### Task 8.3: Rename + de-org the remaining design docs

**Files:**
- Rename: `protocol/05-team-blueprint.md` → `protocol/05-runtime-blueprint.md`
- Modify: `protocol/05c-orchestrator.md` (HK/Macau examples → generic)
- Modify: `protocol/05e-dashboard.md` (HK/Macau examples → generic)

- [ ] **Step 1: Rename**

```bash
git mv protocol/05-team-blueprint.md protocol/05-runtime-blueprint.md
```

- [ ] **Step 2: Update references inside the renamed file**

Open `protocol/05-runtime-blueprint.md`. Update the table of split documents:
- Remove the row pointing to `05a-teams.md`
- Confirm `05b-agent-runtime.md`, `05c-orchestrator.md`, `05e-dashboard.md` rows still match.
- Drop the "Quick Reference" bullets that reference `05a-teams.md`.

- [ ] **Step 3: De-org `05c` and `05e`**

Skim each file. Replace any HK/Macau-specific examples (e.g., references to "Hong Kong PDPO" or "engineering_head" used as a hardcoded example) with neutral placeholders ("a manager agent", "the configured team manager"). Surgical edits — don't restructure.

- [ ] **Step 4: Update `protocol/05.md` index file** (if it exists)

```bash
grep -rn "05a-teams\|05-team-blueprint" protocol/ docs/
```

Update each match to either remove or rename.

- [ ] **Step 5: Commit**

```bash
git add protocol/
git commit -m "docs(protocol): rename 05-team-blueprint → 05-runtime-blueprint and de-org 05c/05e"
```

### Task 8.4: Update `CLAUDE.md` (project root)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Edit the design-documents section**

In `CLAUDE.md`, replace the `01-04` and `05a` bullets in the "Design Documents" section so the list shows only the surviving system-kernel files:

```markdown
- `00-completion-contract.md` — Universal completion-report format, EH decision schema, agent-callback list
- `05-runtime-blueprint.md` — Index pointing to the split blueprint documents:
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, performance tiers, permissions, task state machine
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order
- `06-knowledge-base.md` — Shared KB rules
```

- [ ] **Step 2: Update the directory layout section**

Replace the `<runtime-dir>/` tree with the new shape (showing `org/`):

```
<runtime-dir>/
|-- opc.yaml                           # marker (slug, created_at, schema_version)
|-- opc.db                             # per-runtime SQLite
|-- org/                               # editable org content
|   |-- charter.md                     # reference doc
|   |-- escalation-rules.md            # reference doc
|   |-- teams.yaml                     # team layout
|   +-- agents/
|       |-- <name>.md                  # active agents
|       +-- _pending/<name>.md         # awaiting founder approval
|-- workspaces/<agent>/...
|-- kb/...
+-- talks/...
```

Reference the new example tree in the "Architecture Summary" section: bootstrap a runtime via `opc init <path> --slug <slug> --from examples/orgs/hk-macau-tourism`.

Add the new CLI subcommand to the "Running the Daemon + CLI" section:

```bash
opc migrate-to-org-runtime <path> --slug <slug> --i-have-a-backup --apply
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): update for org/ folder + migrate-to-org-runtime"
```

### Task 8.5: Add an integration sanity test for the example tree

**Files:**
- Create: `tests/test_examples_org_tree.py`

- [ ] **Step 1: Write the test**

```python
"""Sanity: the in-repo example tree is a loadable runtime org/ folder."""
from __future__ import annotations

import shutil
from pathlib import Path

from src.orchestrator import prompt_loader
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "orgs" / "hk-macau-tourism"


def test_example_tree_parses_cleanly(tmp_path: Path) -> None:
    rt_root = tmp_path / "rt"
    rt = RuntimeDir.init(rt_root, slug="hk-tourism")
    # Replace seed contents with the example tree.
    shutil.rmtree(rt.org_dir)
    shutil.copytree(EXAMPLE_ROOT / "org", rt.org_dir)
    # Loader should now see all 8 agents.
    agents = sorted(a.name for a in prompt_loader.list_agents(rt))
    assert agents == [
        "content_manager", "content_qa", "content_writer",
        "dev_agent", "engineering_head", "payment_agent",
        "product_manager", "qa_engineer",
    ]
    # Teams registry loads without error.
    teams = TeamsRegistry.load(rt)
    assert sorted(teams.teams()) == ["content", "engineering"]
    eh = prompt_loader.load_agent(rt, "engineering_head")
    assert eh is not None
    assert eh.role == "manager"
    assert eh.team == "engineering"
    assert "gh pr close" in eh.allow_rules
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/test_examples_org_tree.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_examples_org_tree.py
git commit -m "test: sanity-check examples/orgs/hk-macau-tourism loads as a runtime"
```

---

## Final verification

- [ ] **Step 1: Full unit test suite green**

```bash
uv run pytest tests/ -v -m "not integration"
```
Expected: PASS.

- [ ] **Step 2: Integration tests green**

```bash
uv run pytest tests/ -v -m integration
```
Expected: PASS. If any fail, they typically rely on the legacy `agent_enrollments` table or hardcoded `_AGENT_SOURCES` lookups — fix by switching to file-based loader at the test fixture layer.

- [ ] **Step 3: Manual smoke — migrate the dev runtime**

```bash
# Adjust path to your own dev runtime:
DEV_RUNTIME=$HOME/runtimes/hk-tourism-dev
cp -r "$DEV_RUNTIME" "${DEV_RUNTIME}.backup-$(date +%s)"   # explicit backup before --i-have-a-backup
uv run opc migrate-to-org-runtime "$DEV_RUNTIME" --slug hk-tourism --i-have-a-backup
# Read the dry-run output, then:
uv run opc migrate-to-org-runtime "$DEV_RUNTIME" --slug hk-tourism --i-have-a-backup --apply
```
Expected: dry-run lists planned actions, apply prints summary with exported counts.

- [ ] **Step 4: Manual smoke — start daemon + run a task**

```bash
scripts/daemon.sh start
uv run opc agents
uv run opc run --brief "Smoke test after migration."
```
Expected: daemon serves the migrated runtime; agents listed; task completes (or escalates cleanly).

---

## Self-review checklist

**1. Spec coverage:**
- [§3 Architecture summary, agent identity bullets] → Phases 2+3 (AgentDef, prompt_loader rewrite).
- [§3 Enrollment files-only] → Phases 6+7 (route rewrite + migration drops table).
- [§4 Repo layout] → Phase 0 (example tree) + Phase 8 (delete + rename + de-org).
- [§5.1 opc.yaml shape] → Phase 1 (slug + created_at + schema_version).
- [§5.2 Per-agent file format + validation] → Phase 2 (parser).
- [§5.3 Pending → approved transition] → Phase 3 (`approve_agent` uses `os.replace`).
- [§5.4 Loader rules] → Phase 3 (list_agents excludes `_pending/`).
- [§8.1 New API] → Phase 3.
- [§8.2 Frontmatter parser] → Phase 2.
- [§8.3 Completion-contract appending] → **NOT in this plan**. The contract file is created in Phase 0; the orchestrator's prompt-builder will read it in Plan 2 (or as a separate small follow-up). Marked as a known gap below.
- [§8.5 Workspace adapter changes] → Phase 4 (sans skill template substitution which is Plan 2).
- [§9 Migration plan] → Phase 7.

**Known gaps (intentionally deferred to Plan 2):**
- Skill template substitution (`{ORG_SLUG}`) — irrelevant in single-org mode.
- Cross-org concurrency (`OrgState`, `DaemonState` registry).
- CLI `--org` flag.
- Path-prefixed routes.
- Orchestrator prompt-builder appending `00-completion-contract.md` content at session-build time. The contract file is staged in Phase 0; wiring it in is a one-line change in Plan 2 alongside the prompt-builder revisits there. This is a deliberate split — Plan 1 leaves agent prompts unchanged in their existing inlined form so the migration is a pure structural move with zero behavioral diff.

**2. Placeholder scan:** None of "TBD", "TODO", "implement later", "fill in details", "Add appropriate error handling", "similar to Task N" appear above. Every code step shows the actual code; every command step shows the actual command and expected output.

**3. Type consistency:**
- `AgentDef` field names match across Phases 2, 3, 6, 7 (`name`, `team`, `role`, `executor`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `system_prompt`).
- `prompt_loader` function names: `load_agent`, `list_agents`, `list_pending`, `load_pending_agent`, `write_pending_agent`, `approve_agent`, `reject_agent`, `allow_rules_for_agent` — used consistently in Phases 3, 4, 6.
- `RuntimeDir` properties added in Phase 1 (`slug`, `org_dir`, `agents_dir`, `pending_agents_dir`, redirected `teams_config_path`) — used consistently in Phases 3, 6, 7.
- `migrate_to_org_runtime` signature `(path, *, slug, i_have_a_backup, apply)` matches in Phase 7 module + Phase 7.3 CLI.
- Workspace adapter constructors take `(settings, runtime)` consistently in Phase 4.
