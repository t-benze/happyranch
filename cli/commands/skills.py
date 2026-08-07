"""Runtime-managed skill policy CLI commands.

Reads the file/YAML-backed skill registry + eligibility policy + exposure
DIRECTLY from disk — no daemon round-trip. All commands are read-only
inspection/validation surfaces as defined in the THR-055 product spec.

Commands:
  skills catalog list       — list all registered skills
  skills catalog validate   — validate registry + eligibility policy
  skills effective          — show effective skills for an agent
  skills policy explain     — explain why a skill is/isn't available
  skills propose            — submit a custom-skill proposal (agent-only)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from runtime.skills.registry import SkillRegistry
from runtime.skills.resolver import EligibilityResolver
from runtime.skills.exposure import catalog_gate, resolve_exposed_skills
from runtime.skills.models import (
    ExposedSkill,
    PolicyClass,
    SkillStatus,
)


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _default_skills_root() -> Path:
    """Return the default skills root: <repo_root>/runtime/skills/"""
    # cli/commands/skills.py -> cli/commands/ -> cli/ -> <repo_root>/
    cli_dir = Path(__file__).resolve().parent.parent
    repo_root = cli_dir.parent
    return repo_root / "runtime" / "skills"


def _default_policy_path() -> Path | None:
    """Return the default eligibility policy path (org config skills section)."""
    cli_dir = Path(__file__).resolve().parent.parent
    repo_root = cli_dir.parent
    path = repo_root / "org" / "config.yaml"
    return path if path.is_file() else None


def _load_eligibility_policy(policy_path: Path | None) -> dict:
    """Load the skills eligibility block from an org config YAML.

    Returns the ``skills`` dict, or {} if not present.
    """
    if policy_path is None or not policy_path.is_file():
        return {}
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw.get("skills", {})


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------

def _fmt_entry(entry) -> str:
    """Format a skill entry for output."""
    pc = entry.policy_class.value if isinstance(entry.policy_class, PolicyClass) else str(entry.policy_class)
    status = entry.status.value if isinstance(entry.status, SkillStatus) else str(entry.status)
    parts = [
        f"id={entry.id}",
        f"version={entry.version}",
        f"policy_class={pc}",
        f"status={status}",
    ]
    return "  ".join(parts)


def _fmt_provenance(rules: list) -> list[str]:
    """Format eligibility provenance rules."""
    return [f"  {r.scope}({r.id}) {r.action}: {r.skill_id}" for r in rules]


def _fmt_blocked(skill_id: str, gate: str, reason: str) -> str:
    """Format a blocked-reason line."""
    return f"{skill_id}: BLOCKED by {gate} — {reason}"


# ---------------------------------------------------------------------------
# Command: skills catalog list
# ---------------------------------------------------------------------------

def cmd_skills_catalog_list(args: argparse.Namespace) -> None:
    """List all skills in the registry."""
    skills_root = Path(args.skills_root) if args.skills_root else _default_skills_root()
    registry = SkillRegistry(skills_root=skills_root)
    entries = registry.list_all()

    if args.json:
        output = []
        for entry in sorted(entries, key=lambda e: e.id):
            output.append({
                "id": entry.id,
                "name": entry.name,
                "version": entry.version,
                "description": entry.description,
                "policy_class": entry.policy_class.value if isinstance(entry.policy_class, PolicyClass) else str(entry.policy_class),
                "status": entry.status.value if isinstance(entry.status, SkillStatus) else str(entry.status),
                "owner": entry.owner,
                "when_to_use": entry.when_to_use,
            })
        print(json.dumps(output, indent=2))
        return

    if not entries:
        print("(no skills registered)")
        return

    print(f"Skills root: {skills_root}")
    print(f"Total: {len(entries)} skill(s)\n")
    for entry in sorted(entries, key=lambda e: e.id):
        print(_fmt_entry(entry))
        print(f"  name: {entry.name}")
        print(f"  description: {entry.description}")
        print(f"  when_to_use: {entry.when_to_use}")
        print()


# ---------------------------------------------------------------------------
# Command: skills catalog validate
# ---------------------------------------------------------------------------

def cmd_skills_catalog_validate(args: argparse.Namespace) -> None:
    """Validate the registry and eligibility policy.

    Checks:
    - Malformed or missing skill.yaml fields
    - Unknown skill ids in eligibility policy (warnings, not errors)
    - Skills that fail the catalog gate (visible but flagged)
    """
    skills_root = Path(args.skills_root) if args.skills_root else _default_skills_root()
    policy_path = Path(args.policy_path) if args.policy_path else _default_policy_path()
    registry = SkillRegistry(skills_root=skills_root)
    all_entries = registry.list_all()
    all_ids = {e.id for e in all_entries}

    warnings: list[str] = []
    errors: list[str] = []

    # 1. Validate each skill.yaml for required fields
    for entry in sorted(all_entries, key=lambda e: e.id):
        missing = []
        if not entry.description:
            missing.append("description")
        if not entry.when_to_use:
            missing.append("when_to_use")
        if missing:
            warnings.append(f"{entry.id}: missing required field(s): {', '.join(missing)}")
        if entry.skill_md_path is None:
            warnings.append(f"{entry.id}: missing SKILL.md")

    # 2. Check catalog gate for each entry
    gate_failures = []
    for entry in sorted(all_entries, key=lambda e: e.id):
        gate = catalog_gate(entry)
        if not gate.passed:
            gate_failures.append(_fmt_blocked(entry.id, "catalog_gate", gate.reason))

    # 3. Validate eligibility policy if available
    policy = _load_eligibility_policy(policy_path)
    if policy:
        resolver = EligibilityResolver(policy)
        policy_warnings = resolver.validate(all_entries)
        warnings.extend(policy_warnings)

    # --- Output ---
    if args.json:
        output = {
            "skills_root": str(skills_root),
            "policy_path": str(policy_path) if policy_path else None,
            "total_skills": len(all_entries),
            "ids": sorted(all_ids),
            "warnings": warnings,
            "errors": errors,
            "catalog_gate_failures": gate_failures,
        }
        print(json.dumps(output, indent=2))
        return

    print(f"Skills root: {skills_root}")
    if policy_path:
        print(f"Policy path: {policy_path}")
    print(f"Total skills in catalog: {len(all_entries)}")
    if all_ids:
        print(f"IDs: {', '.join(sorted(all_ids))}")
    print()

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
        print()

    if gate_failures:
        print(f"CATALOG GATE FAILURES ({len(gate_failures)}):")
        for gf in gate_failures:
            print(f"  {gf}")
        print()

    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  {w}")
        print()

    if not errors and not gate_failures and not warnings:
        print("All checks passed.")


# ---------------------------------------------------------------------------
# Command: skills effective --agent <name>
# ---------------------------------------------------------------------------

def cmd_skills_effective(args: argparse.Namespace) -> None:
    """Show effective skills for an agent after both gates.

    Includes a distinct "System Contracts (runtime-injected)" section separate
    from the managed catalog skills. When ``--context`` is provided, only
    system contracts matching that session context are shown.
    """
    if not args.agent:
        print("error: --agent <name> is required", file=sys.stderr)
        sys.exit(1)

    skills_root = Path(args.skills_root) if args.skills_root else _default_skills_root()
    policy_path = Path(args.policy_path) if args.policy_path else _default_policy_path()
    registry = SkillRegistry(skills_root=skills_root)
    policy = _load_eligibility_policy(policy_path)
    resolver = EligibilityResolver(policy)

    org = args.org or "happyranch"
    team = args.team or "engineering"
    agent = args.agent

    # ── System Contracts (runtime-injected) ──────────────────────────
    # Always shown, clearly separated from managed catalog skills.
    _print_system_contracts_section(args, org, agent)

    exposed = resolve_exposed_skills(registry, resolver, org=org, team=team, agent=agent)

    # Also compute blocked skills for diagnostic visibility
    all_entries = registry.list_all()
    eligible = resolver.resolve(all_entries, org=org, team=team, agent=agent)
    eligible_ids = {r.skill.id for r in eligible}
    blocked = resolver.get_blocked(all_entries, org=org, team=team, agent=agent)

    # Also compute catalog-gate failures for diagnostic visibility
    catalog_ok: dict[str, bool] = {}
    for entry in all_entries:
        catalog_ok[entry.id] = catalog_gate(entry).passed

    if args.json:
        effective_list = []
        for s in exposed:
            effective_list.append({
                "id": s.skill.id,
                "name": s.skill.name,
                "version": s.skill.version,
                "policy_class": s.skill.policy_class.value if isinstance(s.skill.policy_class, PolicyClass) else str(s.skill.policy_class),
                "allowed_by": [{"scope": r.scope, "id": r.id, "action": r.action} for r in s.allowed_by],
                "denied_by": [{"scope": r.scope, "id": r.id, "action": r.action} for r in s.denied_by],
            })

        blocked_list = []
        for skill_id, rules in blocked.items():
            entry = registry.get(skill_id)
            blocked_list.append({
                "id": skill_id,
                "name": entry.name if entry else skill_id,
                "version": entry.version if entry else "?",
                "denied_by": [{"scope": r.scope, "id": r.id, "action": r.action} for r in rules],
                "catalog_ok": catalog_ok.get(skill_id, False),
            })

        # ── System contracts (for JSON output) ─────────────────────
        from runtime.skills.system_contracts import (
            SessionContext,
            list_system_contracts,
            resolve_system_contracts_for_session,
        )
        all_sc = list_system_contracts()
        system_contracts_json = []
        for sc in all_sc:
            system_contracts_json.append({
                "id": sc.id,
                "name": sc.name,
                "description": sc.description,
                "when_to_use": sc.when_to_use,
                "source_path": sc.source_path,
                "contexts": [c.value for c in sc.contexts],
                "requires_repo": sc.requires_repo,
            })

        output = {
            "agent": agent,
            "org": org,
            "team": team,
            "system_contracts": system_contracts_json,
            "effective_skills": effective_list,
            "blocked_skills": blocked_list,
        }
        print(json.dumps(output, indent=2))
        return

    print(f"Agent: {agent}")
    print(f"Org: {org}")
    print(f"Team: {team}")
    print()

    print(f"Effective skills ({len(exposed)}):")
    if not exposed:
        print("  (none)")
    for s in exposed:
        print(f"  {s.skill.id}@{s.skill.version}  {s.skill.name}")
        print(f"    policy_class={_fmt_pc(s.skill.policy_class)}")
        print(f"    catalog: present (status={s.skill.status.value})")
        for r in s.allowed_by:
            print(f"    eligibility: {r.scope}({r.id}) ALLOW")
        print(f"    when_to_use: {s.skill.when_to_use}")
        print()

    print(f"Blocked skills ({len(blocked)}):")
    if not blocked:
        # Also report catalog-gate failures for skills not in resolver scope
        gate_blocked = [
            e for e in all_entries
            if not catalog_ok.get(e.id, False) and e.id not in blocked
        ]
        if gate_blocked:
            for entry in sorted(gate_blocked, key=lambda e: e.id):
                gate = catalog_gate(entry)
                print(f"  {_fmt_blocked(entry.id, 'catalog_gate', gate.reason)}")
        else:
            print("  (none)")
    else:
        for skill_id, rules in blocked.items():
            entry = registry.get(skill_id)
            version = entry.version if entry else "?"
            if not catalog_ok.get(skill_id, False):
                gate = catalog_gate(entry) if entry else None
                reason = gate.reason if gate else "catalog gate failed"
                print(f"  {_fmt_blocked(skill_id, 'catalog_gate', reason)}")
            for r in rules:
                print(f"  {_fmt_blocked(skill_id, 'eligibility_gate', f'{r.scope}({r.id}) DENY')}")


# ---------------------------------------------------------------------------
# Command: skills policy explain <skill_id> --agent <name>
# ---------------------------------------------------------------------------

def cmd_skills_policy_explain(args: argparse.Namespace) -> None:
    """Explain why a specific skill is or isn't available to an agent."""
    if not args.agent:
        print("error: --agent <name> is required", file=sys.stderr)
        sys.exit(1)

    skill_id = args.skill_id
    skills_root = Path(args.skills_root) if args.skills_root else _default_skills_root()
    policy_path = Path(args.policy_path) if args.policy_path else _default_policy_path()
    registry = SkillRegistry(skills_root=skills_root)
    entry = registry.get(skill_id)

    if entry is None:
        print(f"Skill not found in registry: {skill_id}")
        sys.exit(1)

    org = args.org or "happyranch"
    team = args.team or "engineering"
    agent = args.agent

    policy = _load_eligibility_policy(policy_path)
    resolver = EligibilityResolver(policy)

    # Check both gates
    catalog = catalog_gate(entry)

    all_entries = registry.list_all()
    eligible = resolver.resolve(all_entries, org=org, team=team, agent=agent)
    eligible_ids = {r.skill.id for r in eligible if r.is_allowed}
    blocked = resolver.get_blocked(all_entries, org=org, team=team, agent=agent)

    # Also compute what scope allows/denies this specific skill
    org_policy = policy.get("org", {})
    org_allows = skill_id in org_policy.get("allow", [])
    org_denies = skill_id in org_policy.get("deny", [])

    teams_policy = policy.get("teams", {})
    team_policy_data = teams_policy.get(team, {})
    team_allows = skill_id in team_policy_data.get("allow", [])
    team_denies = skill_id in team_policy_data.get("deny", [])

    agents_policy = policy.get("agents", {})
    agent_policy_data = agents_policy.get(agent, {})
    agent_allows = skill_id in agent_policy_data.get("allow", [])
    agent_denies = skill_id in agent_policy_data.get("deny", [])

    # Determine if effectively available
    is_eligible = skill_id in eligible_ids
    is_denied = skill_id in blocked
    is_exposed = catalog.passed and is_eligible

    if args.json:
        output = {
            "skill_id": skill_id,
            "name": entry.name,
            "version": entry.version,
            "agent": agent,
            "org": org,
            "team": team,
            "catalog_gate": {
                "passed": catalog.passed,
                "reason": catalog.reason,
            },
            "eligibility": {
                "passed": is_eligible,
                "org": {"allow": org_allows, "deny": org_denies},
                "team": team,
                "team_policy": {"allow": team_allows, "deny": team_denies},
                "agent": agent,
                "agent_policy": {"allow": agent_allows, "deny": agent_denies},
                "deny_rules": [{"scope": r.scope, "id": r.id} for r in blocked.get(skill_id, [])],
            },
            "is_exposed": is_exposed,
        }
        print(json.dumps(output, indent=2))
        return

    print(f"Skill: {skill_id}@{entry.version}")
    print(f"  Name: {entry.name}")
    print(f"  Policy class: {_fmt_pc(entry.policy_class)}")
    print(f"  Description: {entry.description}")
    print()

    # Catalog gate
    print("--- Catalog Gate ---")
    if catalog.passed:
        print(f"  PASS: {catalog.reason}")
        print(f"  status: {entry.status.value}")
    else:
        print(f"  FAIL: {catalog.reason}")
    print()

    # Eligibility gate
    print("--- Eligibility Gate ---")
    print(f"  Resolution scope: org={org}, team={team}, agent={agent}")
    print()

    print("  Org policy:")
    print(f"    allow: {'✓' if org_allows else '✗'}")
    print(f"    deny:  {'✗' if org_denies else '✓ (not denied)'}")
    print()

    print(f"  Team policy ({team}):")
    print(f"    allow: {'✓' if team_allows else '✗'}")
    print(f"    deny:  {'✗' if team_denies else '✓ (not denied)'}")
    print()

    print(f"  Agent policy ({agent}):")
    print(f"    allow: {'✓' if agent_allows else '✗'}")
    print(f"    deny:  {'✗' if agent_denies else '✓ (not denied)'}")
    print()

    # Effective resolution
    any_explicit_allow = org_allows or team_allows or agent_allows
    any_deny = org_denies or team_denies or agent_denies

    if any_deny:
        print("  → ELIGIBILITY: DENIED (deny wins over allow)")
        print("  Deny provenance:")
        for scope, denies in [("org", org_denies), (f"team({team})", team_denies), (f"agent({agent})", agent_denies)]:
            if denies:
                print(f"    {scope}: {skill_id}")
    elif any_explicit_allow:
        print("  → ELIGIBILITY: ALLOWED")
        print("  Allow provenance:")
        for scope, allows in [("org", org_allows), (f"team({team})", team_allows), (f"agent({agent})", agent_allows)]:
            if allows:
                print(f"    {scope}: {skill_id}")
    else:
        # No explicit allow rules → resolver returns entries but is_allowed
        # requires at least one explicit allow (len(allowed_by) > 0)
        print("  → ELIGIBILITY: NOT EXPLICITLY ALLOWED (no allow rules in policy)")
    print()

    # Final result
    print("--- Result ---")
    if is_exposed:
        print(f"  ✓ {skill_id} IS available to {agent}")
    else:
        if not catalog.passed:
            print(f"  ✗ {skill_id} is NOT available to {agent}")
            print(f"    Blocked by: catalog gate")
        elif not is_eligible:
            print(f"  ✗ {skill_id} is NOT available to {agent}")
            print(f"    Blocked by: eligibility gate")
        else:
            print(f"  ✗ {skill_id} is NOT available to {agent}")
            print(f"    Blocked by: unknown reason")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_system_contracts_section(
    args: argparse.Namespace, org: str, agent: str,
) -> None:
    """Print the system-contracts section for ``skills effective``.

    Always shows all 6 system contracts with their context predicates and
    repo requirement. When ``--context`` is provided, marks which contracts
    would be injected for that session context (respecting the repo check
    if ``--workspace`` is also given).
    """
    from runtime.skills.system_contracts import (
        SessionContext,
        list_system_contracts,
        resolve_system_contracts_for_session,
    )

    all_contracts = list_system_contracts()

    if args.json:
        contracts_json = []
        for sc in all_contracts:
            contracts_json.append({
                "id": sc.id,
                "name": sc.name,
                "description": sc.description,
                "when_to_use": sc.when_to_use,
                "source_path": sc.source_path,
                "contexts": [c.value for c in sc.contexts],
                "requires_repo": sc.requires_repo,
            })
        return  # JSON handled inline in cmd_skills_effective

    print("System Contracts (runtime-injected):")
    print(f"  Total: {len(all_contracts)} contract(s)")
    print()

    # If context is specified, resolve which contracts would be injected
    injected_ids: set[str] = set()
    if getattr(args, "context", None):
        ctx = SessionContext(args.context)
        workspace = Path(args.workspace) if getattr(args, "workspace", None) else Path("/nonexistent")
        resolved = resolve_system_contracts_for_session(ctx, workspace=workspace)
        injected_ids = {sc.id for sc in resolved}
        print(f"  Context filter: {args.context}")
        if getattr(args, "workspace", None):
            print(f"  Workspace: {args.workspace}")
        print()

    for sc in all_contracts:
        marker = ""
        if injected_ids:
            marker = "  ← INJECTED" if sc.id in injected_ids else "  (not in context)"
        contexts_str = ", ".join(c.value for c in sc.contexts)
        repo_note = " [requires repos]" if sc.requires_repo else ""
        print(f"  {sc.id}  ({sc.name}){marker}")
        print(f"    description: {sc.description}")
        print(f"    when_to_use: {sc.when_to_use}")
        print(f"    contexts: {contexts_str}{repo_note}")
        print(f"    source: {sc.source_path}")
        print()


def _fmt_pc(pc) -> str:
    """Format policy class for display."""
    return pc.value if isinstance(pc, PolicyClass) else str(pc)


# ---------------------------------------------------------------------------
# Command: skills propose --from-file <path> --session-id <session-id>
# ---------------------------------------------------------------------------

def cmd_skills_propose(args: argparse.Namespace) -> None:
    """Submit a custom-skill proposal via the agent-only session-bound route.

    Agent callers must supply only their opaque active session ID — the
    server derives org, task_id, and agent_name from the SessionTracker
    context. The proposal file must contain only package metadata/content
    (slug, name, description, skill_md, version, policy_class, references,
    assets, purpose, target_agent_suggestion). It must NOT contain org,
    agent, task, session, proposer_agent, eligibility, or permission
    identity — any such fields are rejected by the server.

    This command does NOT send the master bearer token; it uses the
    session-binding authentication path exclusively.
    """
    if not args.from_file:
        print("error: --from-file <path> is required", file=sys.stderr)
        sys.exit(1)
    if not args.session_id:
        print("error: --session-id <session-id> is required", file=sys.stderr)
        sys.exit(1)

    # Read proposal file
    try:
        body = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading proposal file {args.from_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Reject forbidden identity fields in the proposal body
    forbidden = {"org", "agent", "agent_name", "task_id", "task",
                 "session_id", "session", "proposer_agent", "proposer",
                 "actor", "eligibility", "permission", "identity"}
    for key in forbidden:
        if key in body:
            print(
                f"error: proposal file must not contain identity field '{key}'. "
                f"Org, agent, task, and session identity are derived from the "
                f"server's verified session context.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build a minimal token-free transport — this route uses
    # opaque session-binding, NOT the master bearer token.
    import httpx
    from cli.client.client import port_file

    port_path = port_file()
    if not port_path.exists():
        print("error: daemon not running — start it with scripts/daemon.sh start",
              file=sys.stderr)
        sys.exit(1)
    port = port_path.read_text().strip()
    base_url = f"http://127.0.0.1:{port}"
    # Deliberately NO Authorization header — this is the agent
    # session-binding path. bearer-free by construction.
    token_free_client = httpx.Client(
        base_url=base_url,
        headers={"X-HappyRanch-Surface": "cli"},
        timeout=30.0,
    )

    # Resolve org for routing (the server cross-checks against session context)
    from cli._shared import resolve_org_slug
    try:
        r = token_free_client.get("/api/v1/orgs")
        available = [o["slug"] for o in r.json().get("orgs", [])] if r.status_code == 200 else []
    except Exception:
        available = []
    org = resolve_org_slug(args_org=getattr(args, 'org', None), available=available)

    resp = token_free_client.post(
        f"/api/v1/orgs/{org}/skill-lifecycle/proposals/agent",
        json=body,
        params={"session_id": args.session_id},
    )

    if resp.status_code == 201:
        result = resp.json()
        print(f"Proposal submitted successfully.")
        print(f"  skill_id:  {result['skill_id']}")
        print(f"  version_id: {result['version_id']}")
        print(f"  version:   {result['version']}")
        print(f"  status:    {result['status']}")
        print(f"  content_hash: {result['content_hash']}")
        if result.get("content_artifact_key"):
            print(f"  artifact:  {result['content_artifact_key']}")
        print()
        print("This proposal is now visible to the founder for review and publication.")
    else:
        detail = resp.json().get("detail", resp.text)
        print(f"error ({resp.status_code}): {detail}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Command: skills create --from-file <path> --session-id <session-id>
# ---------------------------------------------------------------------------

def cmd_skills_create(args: argparse.Namespace) -> None:
    """Create a custom skill via the agent-only session-bound B1 route.

    Agent callers must supply only their opaque active session ID — the
    server derives org, task_id, and agent_name from the SessionTracker
    context. The package file must contain only package metadata/content
    (slug, name, skill_md, version, policy_class, description, references,
    assets). It must NOT contain org, agent, task, session, proposer_agent,
    eligibility, or permission identity — any such fields are rejected by
    the server.

    This is a token-free transport. The CLI builds a plain HTTP POST with
    no Authorization header. The server derives identity from the session
    binding.

    This is an ADDITIONAL verified-agent authoring path (§B1). The created
    skill enters PROPOSED status and is hidden by default. B2 eligibility,
    human editor, effective visibility, and migration/cutover are deferred.
    """
    if not args.from_file:
        print("error: --from-file <path> is required", file=sys.stderr)
        sys.exit(1)
    if not args.session_id:
        print("error: --session-id <session-id> is required", file=sys.stderr)
        sys.exit(1)

    # Read package file
    try:
        body = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading package file {args.from_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Reject forbidden identity fields in the package body
    forbidden = {"org", "org_slug", "agent", "agent_name", "task_id", "task",
                 "session_id", "session", "proposer_agent", "proposer",
                 "actor", "eligibility", "permission", "permissions", "identity"}
    for key in forbidden:
        if key in body:
            print(
                f"error: package file must not contain identity field '{key}'. "
                f"Org, agent, task, and session identity are derived from the "
                f"server's verified session context.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build a minimal token-free transport — this route uses
    # opaque session-binding, NOT the master bearer token.
    import httpx
    from cli.client.client import port_file

    port_path = port_file()
    if not port_path.exists():
        print("error: daemon not running — start it with scripts/daemon.sh start",
              file=sys.stderr)
        sys.exit(1)
    port = port_path.read_text().strip()
    base_url = f"http://127.0.0.1:{port}"
    # Deliberately NO Authorization header — this is the agent
    # session-binding path. bearer-free by construction.
    token_free_client = httpx.Client(
        base_url=base_url,
        headers={"X-HappyRanch-Surface": "cli"},
        timeout=30.0,
    )

    # Resolve org for routing (the server cross-checks against session context)
    from cli._shared import resolve_org_slug
    try:
        r = token_free_client.get("/api/v1/orgs")
        available = [o["slug"] for o in r.json().get("orgs", [])] if r.status_code == 200 else []
    except Exception:
        available = []
    org = resolve_org_slug(args_org=getattr(args, 'org', None), available=available)

    resp = token_free_client.post(
        f"/api/v1/orgs/{org}/skills/agent",
        json=body,
        params={"session_id": args.session_id},
    )

    if resp.status_code == 201:
        result = resp.json()
        print(f"Skill created successfully.")
        print(f"  skill_id:  {result['skill_id']}")
        print(f"  version_id: {result['version_id']}")
        print(f"  version:   {result['version']}")
        print(f"  status:    {result['status']}")
        print(f"  content_hash: {result['content_hash']}")
        if result.get("content_artifact_key"):
            print(f"  artifact:  {result['content_artifact_key']}")
        print()
        print("This skill is now in PROPOSED status and hidden by default.")
        print("It will not be visible to any agent until a founder configures eligibility (B2).")
    else:
        detail = resp.json().get("detail", resp.text)
        print(f"error ({resp.status_code}): {detail}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Command: skills recover <slug> <version> <content_hash>
# ---------------------------------------------------------------------------


def cmd_skills_recover(args: argparse.Namespace) -> None:
    """Operator-invoked one-step recovery for a corrupted canonical package.

    Validates identity/path inputs and ledger provenance, revalidates
    member SHA-256 hashes against the ArtifactStore, then deletes the
    corrupted canonical package. The next materialization will rebuild
    from the ArtifactStore (which must be verified against the release
    source for same-owner deployments).

    Operator surface only — no automatic recovery from same-UID sources.
    """
    import httpx
    from cli.client.client import port_file

    port_path = port_file()
    if not port_path.exists():
        print("error: daemon not running — start it with scripts/daemon.sh start",
              file=sys.stderr)
        sys.exit(1)
    port = port_path.read_text().strip()

    org = getattr(args, 'org', None)

    # Resolve org slug
    from cli._shared import resolve_org_slug
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            timeout=10.0,
        ) as client:
            r = client.get("/api/v1/orgs")
            available = [o["slug"] for o in r.json().get("orgs", [])] \
                if r.status_code == 200 else []
    except Exception:
        available = []
    org = resolve_org_slug(args_org=org, available=available)

    # Validate local inputs before calling the daemon
    slug = args.slug.strip()
    version = args.version.strip()
    content_hash = args.content_hash.strip()

    if not slug or not version or not content_hash:
        print("error: slug, version, and content_hash must all be non-empty",
              file=sys.stderr)
        sys.exit(1)

    import re
    if not re.match(r"^[a-f0-9]{64}$", content_hash):
        print(
            "error: content_hash must be exactly 64 lowercase hex characters",
            file=sys.stderr,
        )
        sys.exit(1)

    # Confirm with operator before deletion
    print(f"Recovery target:")
    print(f"  slug:         {slug}")
    print(f"  version:      {version}")
    print(f"  content_hash: {content_hash[:16]}...")
    print()
    print("This will DELETE the corrupted canonical package from disk.")
    print("The next daemon launch/materialization will rebuild from the")
    print(
        "ArtifactStore (which must be verified against the release\n"
        "source for same-owner deployments)."
    )
    print()

    try:
        response = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = "n"

    if response not in ("y", "yes"):
        print("Aborted.")
        sys.exit(0)

    # Call the daemon recovery endpoint
    token_path = port_path.parent / "daemon.token"
    if not token_path.exists():
        print("error: daemon auth token not found", file=sys.stderr)
        sys.exit(1)
    token = token_path.read_text().strip()

    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-HappyRanch-Surface": "cli",
        },
        timeout=30.0,
    ) as client:
        resp = client.post(
            f"/api/v1/orgs/{org}/skills/recover",
            json={
                "slug": slug,
                "version": version,
                "content_hash": content_hash,
            },
        )

        if resp.status_code == 200:
            result = resp.json()
            print(f"✓ {result['message']}")
        else:
            detail = resp.json().get("detail", resp.text)
            print(f"error ({resp.status_code}): {detail}", file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Register subcommands
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the 'skills' subcommand family."""
    p = sub.add_parser("skills", help="Runtime-managed skill policy inspection")
    skills_sub = p.add_subparsers(dest="skills_command", required=True)

    # --- skills catalog list ---
    p_cat_list = skills_sub.add_parser("catalog", help="Skill catalog operations")
    cat_sub = p_cat_list.add_subparsers(dest="catalog_command", required=True)

    p_list = cat_sub.add_parser("list", help="List all registered skills")
    p_list.add_argument("--skills-root", help="Path to skills directory (default: runtime/skills/)")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")
    p_list.set_defaults(func=cmd_skills_catalog_list)

    p_validate = cat_sub.add_parser("validate", help="Validate registry and eligibility policy")
    p_validate.add_argument("--skills-root", help="Path to skills directory")
    p_validate.add_argument("--policy", dest="policy_path", help="Path to eligibility policy YAML")
    p_validate.add_argument("--json", action="store_true", help="Output as JSON")
    p_validate.set_defaults(func=cmd_skills_catalog_validate)

    # --- skills effective --agent <name> ---
    p_eff = skills_sub.add_parser("effective", help="Show effective skills for an agent")
    p_eff.add_argument("--agent", required=True, help="Agent name")
    p_eff.add_argument("--org", help="Org slug (default: happyranch)")
    p_eff.add_argument("--team", help="Team name (default: engineering)")
    p_eff.add_argument("--skills-root", help="Path to skills directory")
    p_eff.add_argument("--policy", dest="policy_path", help="Path to eligibility policy YAML")
    p_eff.add_argument("--json", action="store_true", help="Output as JSON")
    p_eff.add_argument(
        "--context",
        choices=["task", "thread", "wake", "dream"],
        help="Session context for system-contract filtering",
    )
    p_eff.add_argument("--workspace", help="Agent workspace path (for repo-capable check)")
    p_eff.set_defaults(func=cmd_skills_effective)

    # --- skills policy explain <skill_id> --agent <name> ---
    p_explain = skills_sub.add_parser("policy", help="Policy operations")
    pol_sub = p_explain.add_subparsers(dest="policy_command", required=True)

    p_exp = pol_sub.add_parser("explain", help="Explain why a skill is/isn't available to an agent")
    p_exp.add_argument("skill_id", help="Skill ID (e.g., hr:standard-skill)")
    p_exp.add_argument("--agent", required=True, help="Agent name")
    p_exp.add_argument("--org", help="Org slug (default: happyranch)")
    p_exp.add_argument("--team", help="Team name (default: engineering)")
    p_exp.add_argument("--skills-root", help="Path to skills directory")
    p_exp.add_argument("--policy", dest="policy_path", help="Path to eligibility policy YAML")
    p_exp.add_argument("--json", action="store_true", help="Output as JSON")
    p_exp.set_defaults(func=cmd_skills_policy_explain)

    # --- skills propose --from-file <path> --session-id <session-id> ---
    p_propose = skills_sub.add_parser(
        "propose",
        help="Submit a custom-skill proposal (agent-only, session-bound)",
    )
    p_propose.add_argument(
        "--from-file", dest="from_file", required=True,
        help="Path to proposal JSON file (package metadata/content only)",
    )
    p_propose.add_argument(
        "--session-id", dest="session_id", required=True,
        help="Opaque active session ID (from task context)",
    )
    p_propose.add_argument("--org", help="Org slug (default: auto-detect)")
    p_propose.set_defaults(func=cmd_skills_propose)

    # --- skills create --from-file <path> --session-id <session-id> ---
    p_create = skills_sub.add_parser(
        "create",
        help="Create a custom skill (agent-only, session-bound, B1)",
    )
    p_create.add_argument(
        "--from-file", dest="from_file", required=True,
        help="Path to package JSON file (metadata/content only, no identity fields)",
    )
    p_create.add_argument(
        "--session-id", dest="session_id", required=True,
        help="Opaque active session ID (from task context)",
    )
    p_create.add_argument("--org", help="Org slug (default: auto-detect)")
    p_create.set_defaults(func=cmd_skills_create)

    # --- skills recover <slug> <version> <content_hash> ---
    p_recover = skills_sub.add_parser(
        "recover",
        help="Operator recovery: delete a corrupted canonical package "
             "(next materialization rebuilds from ArtifactStore)",
    )
    p_recover.add_argument("slug", help="Skill slug (e.g., hr:test-skill)")
    p_recover.add_argument("version", help="Package version (e.g., 1.0.0)")
    p_recover.add_argument(
        "content_hash",
        help="Content hash from lifecycle ledger (64 lowercase hex chars)",
    )
    p_recover.add_argument("--org", help="Org slug (default: auto-detect)")
    p_recover.set_defaults(func=cmd_skills_recover)
