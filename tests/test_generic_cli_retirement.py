"""Founder-approved direct retirement contract for legacy generic profiles."""

import re
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cli.commands.executors import cmd_executors_register, cmd_executors_runtime_register
from runtime.daemon.routes.executors import (
    ExecutorRegisterRequest,
    register_executor,
    runtime_register_executor,
)
from runtime.orchestrator.executor_registry import ExecutorRegistry


GUIDANCE = "command_adapter_id='custom-adapter:<id>'"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"command_adapter_id": "generic-cli"},
        {"command_adapter": "generic-cli"},
        {"argv_template": ["tool", "{prompt}"]},
    ],
)
def test_registry_rejects_retired_profile_forms_before_binding(config):
    with patch.object(ExecutorRegistry, "_validate_custom_adapter_binding") as binding:
        with pytest.raises(ValueError, match="custom-adapter:<id>"):
            ExecutorRegistry.validate_custom_profile_config("legacy", config)
    binding.assert_not_called()


@pytest.mark.parametrize("handler", [cmd_executors_register, cmd_executors_runtime_register])
def test_cli_legacy_writers_fail_before_network(handler, capsys):
    with patch("cli.client.client.OpcClient.from_env") as client:
        with pytest.raises(SystemExit):
            handler(Namespace())
    client.assert_not_called()
    assert GUIDANCE in capsys.readouterr().err


@pytest.mark.parametrize("endpoint", [register_executor, runtime_register_executor])
def test_api_legacy_writers_fail_before_request_state(endpoint):
    class NoTouchRequest:
        def __getattribute__(self, name):
            raise AssertionError(f"request state touched: {name}")

    args = [NoTouchRequest(), ExecutorRegisterRequest()]
    if endpoint is register_executor:
        args.append(object())
    with pytest.raises(HTTPException) as exc:
        endpoint(*args)
    assert exc.value.status_code == 422
    assert GUIDANCE in exc.value.detail


def test_builtin_catalog_parity_after_retirement():
    assert ExecutorRegistry().list_profile_names() == ["claude", "codex", "opencode", "pi"]


def test_canonical_execution_model_has_only_registered_custom_adapter_contract():
    protocol = (
        Path(__file__).parents[1] / "protocol" / "05b-agent-runtime.md"
    ).read_text()
    section = protocol.split("### Per-agent executor selection", 1)[1].split(
        "**Per-agent model override", 1
    )[0]
    section = " ".join(section.split())

    required = (
        "command_adapter_id: custom-adapter:<id>",
        "founder-approved adapter",
        "server-authoritative eligibility",
        "SHA-256 checks",
        "direct-connect flow",
        "There is no automatic or versioned fallback",
        "reassignment to a built-in executor",
        "re-registration of a valid approved custom-adapter profile",
    )
    forbidden = (
        "Any agentic CLI",
        "argv templates",
        "builds per-profile subprocess launches generically",
        "founder-minted scoped token",
        "four-step conformance challenge",
    )

    assert all(fragment in section for fragment in required)
    assert all(fragment not in section for fragment in forbidden)


def test_all_canonical_current_docs_reject_retired_generic_profile_promises():
    root = Path(__file__).parents[1]
    surfaces = {
        "README": (root / "README.md").read_text(),
        "executor guide": (
            root / "docs" / "agent-guides" / "agent-executors-and-permissions.md"
        ).read_text(),
        "runtime protocol": (root / "protocol" / "05b-agent-runtime.md").read_text(),
        "bundled manage-agent skill": (
            root / "protocol" / "skills" / "manage-agent" / "SKILL.md"
        ).read_text(),
        "runtime manage-agent skill": (
            root / "runtime" / "skills" / "manage-agent" / "SKILL.md"
        ).read_text(),
    }
    forbidden = (
        "org-config custom profiles",
        "additional agentic CLIs can be registered",
        "custom profiles for any agentic CLI",
        "three phases — **Mint**",
        "four required check-in steps",
    )

    for name, body in surfaces.items():
        assert "custom-adapter:<id>" in body, name
        assert not any(fragment in body for fragment in forbidden), name


def test_repository_has_only_classified_retired_generic_profile_references():
    root = Path(__file__).parents[1]
    source_suffixes = {".md", ".py", ".ts", ".tsx", ".yaml", ".yml"}
    retired = re.compile(
        r"generic[-_]cli|GenericCli|argv_template|"
        r"default\s*=\s*[`'\"]?generic-cli",
        re.IGNORECASE,
    )
    allowed = {
        # Actionable rejection and this deterministic negative contract.
        "runtime/orchestrator/executor_registry.py",
        "tests/test_generic_cli_retirement.py",
        # Unrelated negative/legacy-shaped fixtures; none are executable paths.
        "tests/test_authority_continue_envelope.py",
        "tests/test_org_config.py",
        "tests/daemon/test_adapter_remove.py",
        "tests/daemon/test_direct_connect_authority.py",
        "tests/daemon/test_direct_connect_commit.py",
        "tests/daemon/test_org_state.py",
        # Explicitly superseded historical specs and dated provenance.
        "docs/superpowers/specs/2026-07-19-custom-cli-adapter-envelope-design.md",
        "docs/superpowers/specs/2026-07-24-unified-adapter-runtime-architecture.md",
        "docs/superpowers/specs/2026-07-25-phase-0-executor-inventory.md",
        "docs/superpowers/plans/2026-06-02-thread-claude-session-resume.md",
    }

    residual_files = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in source_suffixes:
            continue
        relative = path.relative_to(root).as_posix()
        if any(part in {".git", ".claude", "node_modules"} for part in path.parts):
            continue
        if retired.search(path.read_text(encoding="utf-8")):
            residual_files.add(relative)

    assert residual_files <= allowed, sorted(residual_files - allowed)
    assert "runtime/orchestrator/executors.py" not in residual_files
    assert "web/src" not in {path.rsplit("/", 1)[0] for path in residual_files}


def test_repository_rejects_stale_custom_profile_registration_workflow():
    root = Path(__file__).parents[1]
    current_surfaces = (
        root / "README.md",
        root / "docs" / "agent-guides" / "agent-executors-and-permissions.md",
        root / "protocol" / "05b-agent-runtime.md",
        root / "runtime" / "orchestrator" / "runtime_executor_store.py",
        root / "web" / "src",
    )
    forbidden = (
        "consumes a fully-conformant token and writes the profile",
        "all four steps recorded",
        "migrate_legacy_org_profiles",
        "candidate-driven Mint/Conform/Register profile creation",
    )

    repository_residuals = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".py", ".ts", ".tsx"}:
            continue
        if any(part in {".git", ".claude", "node_modules"} for part in path.parts):
            continue
        body = path.read_text(encoding="utf-8")
        if any(fragment in body for fragment in forbidden[:3]):
            repository_residuals.add(path.relative_to(root).as_posix())
    assert repository_residuals <= {"tests/test_generic_cli_retirement.py"}

    for surface in current_surfaces:
        paths = surface.rglob("*") if surface.is_dir() else (surface,)
        body = "\n".join(
            path.read_text(encoding="utf-8")
            for path in paths
            if path.is_file() and path.suffix in {".md", ".py", ".ts", ".tsx"}
        )
        if surface.name == "agent-executors-and-permissions.md":
            # The active guide keeps one explicit retirement notice, not a workflow.
            allowed_notice = "candidate-driven Mint/Conform/Register profile creation"
            body = body.replace(allowed_notice, "")
        assert not any(fragment in body for fragment in forbidden), str(surface)

    route = (root / "runtime" / "daemon" / "routes" / "executors.py").read_text()
    assert "POST /api/v1/orgs/{slug}/executors/register" in route
    assert "Legacy executor profile registration is retired" in route
