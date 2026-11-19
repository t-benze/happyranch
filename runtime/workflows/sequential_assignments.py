"""Inert Start@2 assignments. Launch recheck is deliberately unmounted (S4b)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from runtime.orchestrator.agent_def import AgentDef
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.task_selection import (
    TaskSelectionError, resolve_handoff_selection, resolve_task_selection,
)
from runtime.workflows.authority import AuthorityAdmissionCapture, generic_assignment_authority
from runtime.workflows.sequential_contracts import GenericStartCommand, GenericStepOverride
from runtime.workflows.sequential_definition import GenericCompiledDefinition


@dataclass(frozen=True)
class AssignmentCapture:
    """@2 discovery, including adapter option reads, before admission leases."""

    authority: AuthorityAdmissionCapture
    start_bytes: bytes
    snapshot_bytes: bytes


class AssignmentRefusal(ValueError):
    """Finite cause with the offending definition key, never a fallback."""

    def __init__(self, cause: str, *, role: str | None = None, node: str | None = None,
                 detail: dict | None = None) -> None:
        self.cause, self.role, self.node, self.detail = cause, role, node, detail
        super().__init__('sequential_assignment_' + cause + ':' + (node or role or 'run'))


def assignment_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf8')


def assignment_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def selection_agent(value: dict) -> AgentDef:
    """Project an authenticated authority principal into the existing selector."""
    return AgentDef(name=value['name'], team=value['team'], role=value['role'], executor=value['executor'],
        model=value['model'], reasoning_effort=value.get('reasoning_effort'), allow_rules=(), repos={},
        enrolled_by=None, enrolled_at_task=None, enrolled_at=None, system_prompt='')


def _executor(agent: AgentDef) -> dict:
    profile = get_registry().get_profile(agent.executor)
    if profile is None:
        raise AssignmentRefusal('executor_unavailable')
    return dict(name=profile.name, kind=profile.kind, workspace_adapter_id=profile.workspace_adapter_id,
                command_adapter_id=profile.command_adapter_id)


def _roles(compiled: GenericCompiledDefinition, command: GenericStartCommand, defaults: tuple,
           authority: dict, team: str) -> list[dict]:
    requirements = dict(compiled.definition.roles)
    explicit = {item.role: item.principal for item in command.role_overrides}
    saved = {item.role: item.principal for item in defaults}
    for pairs in (command.role_overrides, defaults):
        seen = set()
        for item in pairs:
            if item.role not in requirements or item.role in seen:
                raise AssignmentRefusal('role_key', role=item.role)
            seen.add(item.role)
    agents = {agent['name']: agent for agent in authority['agents']}
    teams = {item['name']: item for item in authority['teams']}
    resolved = []
    for role, requirement in compiled.definition.roles:
        principal = explicit.get(role, saved.get(role, requirement.default_agent))
        origin = 'run' if role in explicit else 'org' if role in saved else 'bundled'
        if principal is None:
            raise AssignmentRefusal('missing_assignment', role=role)
        agent = agents.get(principal)
        if agent is None or agent['status'] != 'active' or not agent['executor']:
            raise AssignmentRefusal('principal_unavailable', role=role)
        member = teams.get(agent['team'])
        if agent['team'] != team or member is None:
            raise AssignmentRefusal('team_scope', role=role)
        if requirement.eligibility == 'registered-team-manager':
            valid = agent['role'] == 'manager' and member['manager'] == dict(kind='agent', principal=principal)
        else:
            valid = agent['role'] == 'worker' and principal in member['workers']
        if not valid:
            raise AssignmentRefusal('role_ineligible', role=role)
        resolved.append(dict(role=role, principal=principal, origin=origin))
    selected = {item['role']: item['principal'] for item in resolved}
    for role, requirement in compiled.definition.roles:
        if any(selected[role] == selected[other] for other in requirement.independent_of):
            raise AssignmentRefusal('independence', role=role)
    return resolved


def resolve_node_assignment(agent: AgentDef, *, node: str, role: str,
                            edit: GenericStepOverride | None = None,
                            previous: dict | None = None) -> dict:
    """THR-356 field presence, including retry carry and explicit null clears."""
    changes = {} if edit is None else edit.model_dump(include={'model', 'reasoning_effort'}, exclude_unset=True)
    predecessor = None
    if previous is not None:
        if (previous['node'], previous['role'], previous['principal']) != (node, role, agent.name):
            raise AssignmentRefusal('retry_target', role=role, node=node)
        # The shared handoff owner reads only this native tuple/provenance.
        from runtime.models import TaskRecord
        predecessor = TaskRecord(id='assignment-predecessor', brief='', **previous['selection'])
    try:
        selection = resolve_handoff_selection(agent, changes, predecessor)
        effective = resolve_task_selection(agent, selection['model_override'], selection['reasoning_effort_override'])
        executor = _executor(agent)
    except TaskSelectionError as exc:
        raise AssignmentRefusal('invalid_selection', role=role, node=node, detail=exc.detail) from None
    except AssignmentRefusal as exc:
        raise AssignmentRefusal(exc.cause, role=role, node=node) from None
    requested = dict(previous['requested']) if previous is not None else {}
    requested.update(changes)
    origins = dict(previous['origins']) if previous is not None else dict(model='agent_default', reasoning_effort='agent_default')
    origins.update({key: 'clear' if value is None else 'run' for key, value in changes.items()})
    return dict(node=node, role=role, principal=agent.name, executor=executor, requested=requested,
                origins=origins, selection=selection, effective=dict(model=effective[0], reasoning_effort=effective[1]))


def parse_assignment_start(raw: bytes) -> GenericStartCommand:
    """Keep the @2 ABI parser, adding offending-key diagnostics for duplicates."""
    from runtime.workflows.sequential_contracts import DEFINITION_LIMIT
    from runtime.workflows.sequential_definition import _json, parse_generic_start
    value = _json(raw, DEFINITION_LIMIT)
    for field, key, cause in (('role_overrides', 'role', 'role_key'), ('step_overrides', 'node', 'node_key')):
        seen = set()
        for item in value.get(field, []) if type(value.get(field)) is list else []:
            if type(item) is dict and type(item.get(key)) is str:
                if item[key] in seen:
                    raise AssignmentRefusal(cause, **{key: item[key]})
                seen.add(item[key])
    return parse_generic_start(raw)


def validate_assignment_keys(compiled: GenericCompiledDefinition, command: GenericStartCommand) -> None:
    for items, key, known in ((command.role_overrides, 'role', dict(compiled.definition.roles)),
                               (command.step_overrides, 'node', {node.id for node in compiled.definition.nodes})):
        seen = set()
        for item in items:
            value = getattr(item, key)
            if value not in known or value in seen:
                raise AssignmentRefusal(key + '_key', **{key: value})
            seen.add(value)


def resolve_assignments(compiled: GenericCompiledDefinition, command: GenericStartCommand, *,
                        defaults: tuple, capture: AuthorityAdmissionCapture, org_slug: str,
                        team: str) -> dict:
    """Resolve all nodes before allocation, using permanent IDs from authority."""
    validate_assignment_keys(compiled, command)
    authority = generic_assignment_authority(capture, org_slug=org_slug)
    roles = _roles(compiled, command, defaults, authority, team)
    targets = {item['role']: item['principal'] for item in roles}
    agents = {item['name']: item for item in authority['agents']}
    nodes = {node.id: node for node in compiled.definition.nodes}
    edits = {}
    for edit in command.step_overrides:
        if edit.node not in nodes or edit.node in edits:
            raise AssignmentRefusal('node_key', node=edit.node)
        edits[edit.node] = edit
    assignments = [resolve_node_assignment(selection_agent(agents[targets[node.role]]), node=node.id,
        role=node.role, edit=edits.get(node.id)) for node in compiled.definition.nodes]
    return dict(format='workflow-sequential-resolved-assignments@2', org_slug=org_slug, team=team,
                authority=authority, roles=roles, nodes=assignments)


def recheck_launch_assignment(assignment: dict, target: AgentDef) -> AssignmentRefusal | None:
    """Pure current-executor/options refusal; S4b mounts this, S4a owns Held.

    This function never writes a task/row, launches, holds or substitutes a pair.
    A success is selection syntax only, never provider availability or authority.
    """
    role, node = assignment['role'], assignment['node']
    if target.name != assignment['principal']:
        return AssignmentRefusal('principal_changed', role=role, node=node)
    try:
        resolve_task_selection(target, assignment['selection']['model_override'],
                               assignment['selection']['reasoning_effort_override'])
        if _executor(target) != assignment['executor']:
            return AssignmentRefusal('executor_drift', role=role, node=node)
    except TaskSelectionError as exc:
        return AssignmentRefusal('invalid_selection', role=role, node=node, detail=exc.detail)
    except AssignmentRefusal as exc:
        return AssignmentRefusal(exc.cause, role=role, node=node)
    return None


def validate_assignment_snapshot(value: dict, compiled: GenericCompiledDefinition,
                                 command: GenericStartCommand, *, defaults: tuple, org_slug: str) -> None:
    """Historical validation uses pinned defaults, never today's executor catalog."""
    from runtime.workflows.sequential_history import _require
    _require(value.keys() == {'format', 'org_slug', 'team', 'authority', 'roles', 'nodes'}
             and value['format'] == 'workflow-sequential-resolved-assignments@2'
             and value['org_slug'] == org_slug and value['authority']['org_slug'] == org_slug,
             'assignment_snapshot_invalid')
    from runtime.workflows.authority import validate_generic_assignment_authority
    validate_generic_assignment_authority(value['authority'], org_slug=org_slug)
    roles = _roles(compiled, command, defaults, value['authority'], value['team'])
    _require(value['roles'] == roles, 'assignment_roles_conflict')
    agents = {item['name']: item for item in value['authority']['agents']}
    targets = {item['role']: item['principal'] for item in roles}
    edits = {item.node: item.model_dump(include={'model', 'reasoning_effort'}, exclude_unset=True)
             for item in command.step_overrides}
    _require(len(value['nodes']) == len(compiled.definition.nodes), 'assignment_nodes_conflict')
    for node, stored in zip(compiled.definition.nodes, value['nodes'], strict=True):
        requested = edits.get(node.id, {})
        principal = targets[node.role]
        agent = agents[principal]
        selection = dict(model_override=requested.get('model'), reasoning_effort_override=requested.get('reasoning_effort'),
                         override_source='manager' if any(v is not None for v in requested.values()) else None)
        _require(stored.keys() == {'node', 'role', 'principal', 'executor', 'requested', 'origins', 'selection', 'effective'}
                 and (stored['node'], stored['role'], stored['principal']) == (node.id, node.role, principal)
                 and stored['requested'] == requested and stored['selection'] == selection
                 and stored['origins'] == {key: ('clear' if requested[key] is None else 'run')
                     if key in requested else 'agent_default' for key in ('model', 'reasoning_effort')}
                 and stored['effective'] == {key: requested.get(key) if requested.get(key) is not None
                     else agent.get(key) for key in ('model', 'reasoning_effort')}
                 and stored['executor'].keys() == {'name', 'kind', 'workspace_adapter_id', 'command_adapter_id'}
                 and stored['executor']['name'] == agent['executor'], 'assignment_node_conflict')
