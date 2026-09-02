"""Founder-approved direct retirement contract for legacy generic profiles."""

from argparse import Namespace
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
