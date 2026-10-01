"""Compatibility coverage for THR-273 step 5 slice 1 module moves."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("old_path", "new_path"),
    [
        (
            "runtime.daemon.thread_mentions",
            "runtime.infrastructure.thread_mentions",
        ),
        (
            "runtime.daemon.task_scratch_report",
            "runtime.orchestrator.task_scratch_report",
        ),
        (
            "runtime.daemon.task_scratch_coverage",
            "runtime.orchestrator.task_scratch_coverage",
        ),
        (
            "runtime.daemon.task_scratch_evidence",
            "runtime.orchestrator.task_scratch_evidence",
        ),
    ],
)
def test_old_and_new_module_paths_have_identity(
    old_path: str,
    new_path: str,
) -> None:
    assert importlib.import_module(old_path) is importlib.import_module(new_path)


def test_daemon_package_import_resolves_to_relocated_report() -> None:
    from runtime.daemon import task_scratch_report

    assert task_scratch_report is importlib.import_module(
        "runtime.orchestrator.task_scratch_report"
    )


def test_old_path_monkeypatch_is_observed_through_new_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_module = importlib.import_module("runtime.daemon.task_scratch_report")
    new_module = importlib.import_module("runtime.orchestrator.task_scratch_report")
    sentinel = Path("/identity-alias-proof")

    monkeypatch.setattr(old_module, "_PROC_ROOT", sentinel)

    assert new_module._PROC_ROOT is sentinel
