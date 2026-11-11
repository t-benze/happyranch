"""Unmounted local Final journal boundary; no merge, runner or CLI registration.

U5b2b must supply the genuine selected manager's fresh-selection adapter before
using this core. A callback constructed by a diagnostic is synthetic authority.
Existing historical Coding interpretation remains entirely in its own adapter.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cli.commands.workflow_coding_finalize import _literal_directory, _write
from runtime.workflows.sequential_coding_final_records import (
    CodingFinalIntent, CodingFinalSelection, FinalLintObservation, read_final_intent, read_final_selection,
)
from runtime.workflows.sequential_history import _bytes

Reselect = Callable[[], CodingFinalSelection]


def _current(selected: CodingFinalSelection, reselect: Reselect) -> None:
    # Parsing/saved metadata is never admission. The trusted adapter must re-run
    # the original authority/live/subject/evidence boundary on every call.
    read_final_selection(_bytes(selected.model_dump(mode='json')))
    if reselect() != selected:
        raise ValueError('sequential_final_selection_changed')


def attempt_directory(selected: CodingFinalSelection) -> Path:
    """Only original native workspace + Final task; no key/cwd/path selector."""
    return Path(selected.native.workspace_root) / 'output' / selected.task_id / 'sequential-coding-finalize'


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class FinalAttempt:
    selected: CodingFinalSelection
    directory: Path
    device: int
    inode: int

    def _same(self) -> None:
        _literal_directory(self.directory)
        current = self.directory.stat()
        if (current.st_dev, current.st_ino) != (self.device, self.inode):
            raise ValueError('sequential_final_attempt_changed')

    def persist_intent(self, *, operation_key: str, checks: tuple[FinalLintObservation, ...],
                       created_at: str, reselect: Reselect) -> CodingFinalIntent:
        """Save exact pins/checks/intent before a future separately owned action.

        Bad/missing lint evidence cannot produce an intent. Any partial write
        leaves the one reservation in place; this method never removes/replays it.
        No action, callback or sequential business-Held row is produced here.
        """
        intent = CodingFinalIntent(format='workflow-sequential-coding-final-intent@1',
            operation_key=operation_key, selected=self.selected, checks=checks, created_at=created_at)
        raw = _bytes(intent.model_dump(mode='json'))
        read_final_intent(raw)  # Compatible closed reader precedes local publication.
        _current(self.selected, reselect)
        self._same()
        _write(self.directory / 'selected.json', self.selected.model_dump(mode='json'))
        _write(self.directory / 'checks.json', dict(checks=[check.model_dump(mode='json') for check in checks]))
        _write(self.directory / 'intent.json', intent.model_dump(mode='json'))
        _sync_directory(self.directory)
        self._same()
        _current(self.selected, reselect)
        return intent


def reserve_attempt(selected: CodingFinalSelection, *, reselect: Reselect) -> FinalAttempt:
    """Exclusive original-task fence. Existing/partial directories always refuse.

    Refusal before fresh selection creates no directory/intent/action. A refusal
    after reservation retains it, including cancellation or failed recording.
    Literal metadata checks are observations, not a hostile shared-user fence.
    """
    _current(selected, reselect)
    directory = attempt_directory(selected)
    workspace = _literal_directory(Path(selected.native.workspace_root))
    _literal_directory(directory.parent, create=True)
    directory.mkdir()  # No exist_ok, operation-key directory or alternate cwd.
    # Persist every possibly new parent link below the original existing
    # workspace before returning this reservation to a future action owner.
    _sync_directory(workspace)
    _sync_directory(workspace / 'output')
    _sync_directory(directory.parent)
    _sync_directory(directory)
    current = directory.stat()
    result = FinalAttempt(selected, directory, current.st_dev, current.st_ino)
    result._same()
    _current(selected, reselect)
    return result
