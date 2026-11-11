"""Pure Coding evidence policy; no executable selection or effect authority.

Only the owned SQL reader authenticates these in-process inputs. Constructing,
parsing or saving them establishes no provenance or lasting permission. Graph
data can select output contracts, never waive trusted review/QA requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from runtime.workflows.sequential_coding_output import CodeChangeDeclaration
from runtime.workflows.sequential_contracts import Closed, OutputRef
from runtime.workflows.sequential_definition import _json
from runtime.workflows.sequential_engine import SequentialRefusal


REQUIRED_ASSESSMENTS = ('independent-code-review@1', 'code-qa@1')
REQUIRED_LINTS = ('Source Boundary Lint', 'Web Lint (Node 24)')


class CodingRefusalCause(StrEnum):
    REVIEW_MISSING = 'sequential_coding_review_missing'
    QA_MISSING = 'sequential_coding_qa_missing'
    STALE = 'sequential_coding_evidence_stale'
    NON_INDEPENDENT = 'sequential_coding_non_independent'
    AUTHORITY_DENIED = 'sequential_authority_denied'
    AUTHORITY_UNVERIFIED = 'sequential_authority_unverified'
    UNVERIFIED = 'sequential_coding_evidence_unverified'
    FINAL_UNAVAILABLE = 'sequential_coding_final_unavailable'
    EFFECT_UNCERTAIN = 'sequential_coding_effect_uncertain'


class CodingEvidenceRefusal(SequentialRefusal):
    """BLOCK -> Held cause only; no selection, reservation or clearance."""

    def __init__(self, cause: CodingRefusalCause) -> None:
        self.cause = cause
        super().__init__(cause.value)


class CodeAssessmentDeclaration(Closed):
    """Assessment's exact subject assertion, not an approval or fetch receipt."""

    format: Literal['workflow-sequential-code-assessment@1']
    change: CodeChangeDeclaration


def read_code_assessment(raw: bytes) -> CodeAssessmentDeclaration:
    _json(raw, 49152)
    return CodeAssessmentDeclaration.model_validate_json(raw, strict=True)


@dataclass(frozen=True)
class SettledCodingResult:
    run_id: str
    ref: OutputRef
    principal: str
    change: CodeChangeDeclaration
    outcome: str
    verdict: str | None
    carrier_digest: str
    terminal_digest: str
    outer_digest: str


@dataclass(frozen=True)
class CurrentCodingEvidence:
    """Transient reader projection; there is deliberately no authenticated flag."""

    run_id: str
    final_visit: str
    final_task: str
    final_principal: str
    final_session: str
    change: CodeChangeDeclaration
    contributors: tuple[str, ...]
    results: tuple[SettledCodingResult, ...]
    definition_digest: str
    start_digest: str
    cas: int
    last_event_digest: str
    input_selection_digest: str


@dataclass(frozen=True)
class CodingAdmissibility:
    """Evidence derivation only; U5b must re-read before disclosure/action.

    In particular this is neither an executable Final selection nor proof of
    successful lints, effective repository access, merge or execution settlement.
    """

    evidence: CurrentCodingEvidence
    development: SettledCodingResult
    review: SettledCodingResult
    qa: SettledCodingResult
    required_lints: tuple[str, ...] = REQUIRED_LINTS


def _require(condition: bool, cause: CodingRefusalCause) -> None:
    if not condition:
        raise CodingEvidenceRefusal(cause)


def mandatory_coding_assessments() -> tuple[str, str]:
    """Independent of node names, inputs, edges and definition requirements."""
    return REQUIRED_ASSESSMENTS


def derive_coding_admissibility(evidence: CurrentCodingEvidence) -> CodingAdmissibility:
    """Select mandatory current evidence or raise, before any effect selection.

    The SQL reader supplies only the currently retained selections after full
    reducer replay, plus ALL retained change contributors across content epochs.
    A caller-built instance is synthetic input, never authenticated evidence.
    """
    selected = {}
    for contract, missing, outcome in (
        ('code-change@1', CodingRefusalCause.UNVERIFIED, 'produced'),
        ('independent-code-review@1', CodingRefusalCause.REVIEW_MISSING, 'APPROVE'),
        ('code-qa@1', CodingRefusalCause.QA_MISSING, 'PASS'),
    ):
        candidates = [result for result in evidence.results if result.ref.contract == contract]
        _require(bool(candidates), missing)
        _require(len(candidates) == 1, CodingRefusalCause.UNVERIFIED)
        result = candidates[0]
        _require(result.run_id == evidence.run_id and result.change == evidence.change
                 and result.ref.epoch == evidence.change.epoch,
                 CodingRefusalCause.STALE)
        _require(result.outcome == outcome and result.verdict == (None if outcome == 'produced' else outcome),
                 CodingRefusalCause.UNVERIFIED)
        selected[contract] = result
    development, review, qa = (selected[contract] for contract in ('code-change@1', *REQUIRED_ASSESSMENTS))
    contributors = set(evidence.contributors)
    _require(development.principal in contributors and bool(contributors), CodingRefusalCause.UNVERIFIED)
    _require(len({development.principal, review.principal, qa.principal, evidence.final_principal}) == 4
             and not contributors.intersection((review.principal, qa.principal, evidence.final_principal)),
             CodingRefusalCause.NON_INDEPENDENT)
    return CodingAdmissibility(evidence, development, review, qa)
