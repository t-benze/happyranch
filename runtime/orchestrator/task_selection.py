"""Task-only composition of the existing executor selection contracts (THR-356)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from runtime.orchestrator.executor_registry import (
    get_registry, reasoning_effort_values, validate_reasoning_selection,
)

if TYPE_CHECKING:
    from runtime.models import TaskRecord
    from runtime.orchestrator.agent_def import AgentDef


class TaskSelectionError(ValueError):
    def __init__(self, executor: str, reason: str, valid_options: tuple[str, ...]) -> None:
        super().__init__(reason)
        self.detail = {"code": "invalid_task_selection", "reason": reason,
                       "executor": executor, "valid_options": list(valid_options)}


def resolve_task_selection(
    agent: AgentDef, model_override: str | None, reasoning_effort_override: str | None,
) -> tuple[str | None, str | None]:
    """Validate the effective pair; finite membership applies ONLY to task effort.

    Agent-default grammar, custom input versions and argv composition remain
    owned by their existing validators. There is deliberately no model catalog.
    """
    name = agent.executor
    valid: tuple[str, ...] = ()
    model = agent.model if model_override is None else model_override
    effort = agent.reasoning_effort if reasoning_effort_override is None else reasoning_effort_override
    try:
        valid = reasoning_effort_values(name)
        profile = get_registry().get_profile(name)
        if profile is None:
            raise ValueError(f"executor {name} is unavailable")
        if model_override is not None and (not isinstance(model_override, str) or not model_override.strip()):
            raise ValueError("model must be a non-empty string or null")
        if profile.kind == "custom":
            from runtime.orchestrator.adapter_store import get_adapter
            from runtime.orchestrator.adapter_contract import AdapterInputOptions
            entry = get_adapter((profile.command_adapter_id or "").removeprefix("custom-adapter:"))
            options = (AdapterInputOptions.model_validate(entry.input_options)
                       if entry is not None and entry.input_options is not None else None)
            if model_override is not None and (options is None or options.model_arg_template is None):
                raise ValueError(f"executor {name} does not support model")
            if reasoning_effort_override is not None and (options is None or options.effort_arg_template is None):
                raise ValueError(f"executor {name} does not support effort")
        if reasoning_effort_override is not None and reasoning_effort_override not in valid:
            raise ValueError(f"executor {name} reasoning_effort must be one of: {', '.join(valid) or '(none)'}")
        validate_reasoning_selection(name, model, effort)
    except ValueError as exc:
        raise TaskSelectionError(name, str(exc), valid) from exc
    return model, effort


def override_fields(task: TaskRecord) -> dict:
    """Additive audit data only for a selected task; historical unset bytes stay."""
    fields = {}
    if task.model_override is not None:
        fields["model_override"] = task.model_override
    if task.reasoning_effort_override is not None:
        fields["reasoning_effort_override"] = task.reasoning_effort_override
    if fields:
        fields["override_source"] = task.override_source
    return fields


def resolve_handoff_selection(
    agent: AgentDef, changes: dict, predecessor: TaskRecord | None = None,
) -> dict:
    """Compose only a linked retry's pair, with per-field null clears.

    Omitted ordinary handoffs keep defaults unstored and unvalidated, as before.
    An explicit handoff edit adopts manager provenance for the resulting tuple.
    """
    model = changes.get("model", predecessor.model_override if predecessor else None)
    effort = changes.get("reasoning_effort", predecessor.reasoning_effort_override if predecessor else None)
    source = "manager" if changes else predecessor.override_source if predecessor else None
    if model is not None or effort is not None or changes or predecessor is not None:
        resolve_task_selection(agent, model, effort)
    if model is None and effort is None:
        source = None
    return {"model_override": model, "reasoning_effort_override": effort,
            "override_source": source}


def selection_view(task: TaskRecord, agent: AgentDef | None) -> dict:
    """Current effective settings; historical actually-run settings stay in audits."""
    return {
        "model_override": task.model_override,
        "reasoning_effort_override": task.reasoning_effort_override,
        "override_source": task.override_source,
        "effective_model": task.model_override if task.model_override is not None else agent.model if agent else None,
        "effective_reasoning_effort": (task.reasoning_effort_override if task.reasoning_effort_override is not None
                                       else agent.reasoning_effort if agent else None),
        "model_source": task.override_source if task.model_override is not None else "agent_default",
        "reasoning_effort_source": task.override_source if task.reasoning_effort_override is not None else "agent_default",
    }
