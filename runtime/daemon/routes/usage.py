"""Read-only Usage v1 Workload and Efficiency routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    OrgConfig,
    load_org_config,
    resolve_org_timezone_display,
)
from runtime.orchestrator.usage_read_model import read_efficiency, read_workload


router = APIRouter(dependencies=[require_token()])


class WindowBounds(BaseModel):
    start_utc: str
    end_utc: str
    start_local: str
    end_local: str


class Delta(BaseModel):
    kind: Literal["absolute", "percent", "new_from_zero", "no_change", "withheld"]
    value: int | float | None
    withheld_reason: str | None


class RuntimeMetric(BaseModel):
    seconds: int | float
    known: int
    total: int


class WorkloadPeriod(BaseModel):
    task_runs: int
    thread_wakes: int
    recorded_runtime: RuntimeMetric
    deliveries: int
    delivery_unclassified_results: int
    replies: int


class WorkloadAgent(BaseModel):
    agent: str
    current: WorkloadPeriod
    previous: WorkloadPeriod | None
    deltas: dict[str, Delta] | None


class WorkloadResponse(BaseModel):
    generated_at: str
    data_through: str
    timezone: str
    current_window: WindowBounds
    previous_window: WindowBounds | None
    agents: list[WorkloadAgent]


class Coverage(BaseModel):
    known: int
    total: int
    ratio: float | None


class TokenMetric(BaseModel):
    value: int | float | None
    n_reported: int
    partial_count: int


class KnownTotal(BaseModel):
    value: int | None
    n_reported: int


class DeclineWaste(BaseModel):
    state: Literal["no_declines", "reported"]
    declined: int
    total: int
    rate: float | None
    usage_known: int
    fresh_input: KnownTotal
    reread: KnownTotal
    output: KnownTotal


class EfficiencyPeriod(BaseModel):
    runs: int
    usage_coverage: Coverage
    fresh_input: TokenMetric
    reread: TokenMetric
    output: TokenMetric
    decline_waste: DeclineWaste | None


class CohortOption(BaseModel):
    executor: str
    model: str | None
    model_unpinned: bool
    current_runs: int
    previous_runs: int


class UnattributedCounts(BaseModel):
    worker_task: int
    manager_decision: int
    thread_reply: int
    thread_followup: int
    dream: int
    task_unclassified: int
    recovery: int


class UnattributedPeriods(BaseModel):
    current: UnattributedCounts
    previous: UnattributedCounts | None


class EfficiencyRow(BaseModel):
    run_type: Literal[
        "worker_task", "manager_decision", "thread_reply", "thread_followup", "dream",
    ]
    current: EfficiencyPeriod
    previous: EfficiencyPeriod | None
    deltas: dict[str, Delta] | None


class EfficiencyResponse(BaseModel):
    generated_at: str
    data_through: str
    timezone: str
    current_window: WindowBounds
    previous_window: WindowBounds | None
    cohorts: list[CohortOption]
    unattributed: UnattributedPeriods
    rows: list[EfficiencyRow]


def _timezone_name(org: OrgDep) -> str:
    try:
        config = load_org_config(OrgPaths(root=org.root))
    except Exception:
        config = OrgConfig()
    return resolve_org_timezone_display(config)[1]


@router.get("/usage/workload", response_model=WorkloadResponse)
def usage_workload(slug: str, org: OrgDep, compare: bool = False) -> dict:
    return read_workload(
        org.db,
        now=datetime.now(timezone.utc),
        timezone_name=_timezone_name(org),
        compare=compare,
    )


@router.get("/usage/efficiency", response_model=EfficiencyResponse)
def usage_efficiency(
    slug: str,
    org: OrgDep,
    compare: bool = False,
    executor: str | None = None,
    model: str | None = None,
    model_unpinned: bool = False,
) -> dict:
    if executor is None:
        if model is not None or model_unpinned:
            raise HTTPException(
                status_code=422,
                detail={"code": "cohort_executor_required"},
            )
    elif (model is None) == (not model_unpinned):
        raise HTTPException(
            status_code=422,
            detail={"code": "exactly_one_model_selector_required"},
        )
    return read_efficiency(
        org.db,
        now=datetime.now(timezone.utc),
        timezone_name=_timezone_name(org),
        compare=compare,
        executor=executor,
        model=model,
        model_unpinned=model_unpinned,
    )
