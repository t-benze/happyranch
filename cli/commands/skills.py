"""Runtime-managed skill policy CLI commands.

Reads the file/YAML-backed skill registry + eligibility policy + exposure
DIRECTLY from disk — no daemon round-trip. All commands are read-only
inspection/validation surfaces as defined in the THR-055 product spec.

Commands:
  skills catalog list       — list all registered skills
  skills catalog validate   — validate registry + eligibility policy
  skills effective          — show effective skills for an agent
  skills policy explain     — explain why a skill is/isn't available
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
    ApprovalState,
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

def _fmt_approval(entry) -> str:
    """Format catalog-approval record for output."""
    pc = entry.policy_class.value if isinstance(entry.policy_class, PolicyClass) else str(entry.policy_class)
    state = entry.approval_state.value if isinstance(entry.approval_state, ApprovalState) else str(entry.approval_state)
    status = entry.status.value if isinstance(entry.status, SkillStatus) else str(entry.status)
    parts = [
        f"id={entry.id}",
        f"version={entry.version}",
        f"policy_class={pc}",
        f"approval_state={state}",
        f"status={status}",
    ]
    if entry.approved_by:
        parts.append(f"approved_by={entry.approved_by}")
    if entry.approved_at:
        parts.append(f"approved_at={entry.approved_at}")
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
                "approval_state": entry.approval_state.value if isinstance(entry.approval_state, ApprovalState) else str(entry.approval_state),
                "status": entry.status.value if isinstance(entry.status, SkillStatus) else str(entry.status),
                "owner": entry.owner,
                "approved_by": entry.approved_by,
                "approved_at": str(entry.approved_at) if entry.approved_at else None,
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
        print(_fmt_approval(entry))
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
                "approval_state": s.skill.approval_state.value if isinstance(s.skill.approval_state, ApprovalState) else str(s.skill.approval_state),
                "approved_by": s.skill.approved_by,
                "approved_at": str(s.skill.approved_at) if s.skill.approved_at else None,
                "allowed_by": [{"scope": r.scope, "id": r.id, "action": r.action} for r in s.allowed_by],
                "denied_by": [{"scope": r.scope, "id": r.id, "action": r.action} for r in s.denied_by],
                "catalog_approved": s.catalog_approved,
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
        print(f"    catalog: approved (by {s.skill.approved_by or 'none'})")
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
        print(f"  approval_state: {entry.approval_state.value}")
        print(f"  status: {entry.status.value}")
        if entry.approved_by:
            print(f"  approved_by: {entry.approved_by}")
        if entry.approved_at:
            print(f"  approved_at: {entry.approved_at}")
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

    Always shows all 5 system contracts with their context predicates and
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
