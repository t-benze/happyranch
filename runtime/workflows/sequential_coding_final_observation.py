"""Closed U5b2b2 Final callback content; syntax is not effect authority.

Only a successful original command and its separate remote observation belong
in a merged carrier. Failed/missing recording stays local and uses causal BLOCK.
"""
from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator

from runtime.workflows.sequential_coding_final_records import (
    CodingFinalCommandResult, CodingFinalIntent, CodingFinalRemoteObservation,
    read_final_command, read_final_intent, read_final_observation, record_digest,
)
from runtime.workflows.sequential_coding_output import CodeChangeDeclaration
from runtime.workflows.sequential_contracts import Closed, Digest
from runtime.workflows.sequential_definition import _json
from runtime.workflows.sequential_history import _bytes

FINAL_OBSERVATION_LIMIT = 49152


class CodingFinalObservation(Closed):
    format: Literal['workflow-sequential-coding-final-observation@1']
    change: CodeChangeDeclaration
    selection_digest: Digest
    intent: CodingFinalIntent
    command: CodingFinalCommandResult
    remote: CodingFinalRemoteObservation

    @model_validator(mode='after')
    def original_success(self) -> Self:
        selected = self.intent.selected
        if (self.change != selected.change or self.selection_digest != record_digest(selected)
                or self.command.succeeded is not True or self.remote.source != 'separate_read'):
            raise ValueError('sequential_final_observation_conflict')
        # Reuse all existing record digest/identity/time checks. In particular,
        # a later MERGED read cannot relabel a failed or missing command receipt.
        read_final_intent(_bytes(self.intent.model_dump(mode='json')))
        read_final_command(_bytes(self.command.model_dump(mode='json')), self.intent)
        read_final_observation(_bytes(self.remote.model_dump(mode='json')), selected, self.command)
        return self


def read_final_callback_content(raw: bytes) -> CodingFinalObservation:
    """Pure, bounded canonical JSON parser; no carrier or native proof implied."""
    _json(raw, FINAL_OBSERVATION_LIMIT)
    result = CodingFinalObservation.model_validate_json(raw, strict=True)
    if _bytes(result.model_dump(mode='json')) != raw:
        raise ValueError('sequential_final_observation_not_canonical')
    return result
