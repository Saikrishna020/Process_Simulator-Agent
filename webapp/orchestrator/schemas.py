"""Typed contracts passed between the LLM, the guardrails, and the simulator tools.

The LLM only ever fills these in — nothing it produces reaches a filesystem or subprocess call
without passing through guardrails.validate_request() first.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SimulationRequest(BaseModel):
    """A candidate simulation job, as proposed by the LLM (untrusted until validated)."""

    dataset_name: str = Field(description="Name of the dataset to simulate, e.g. 'LoanApp'")
    case_id: str = Field(description="Column name holding the case/instance id")
    activity_name: str = Field(description="Column name holding the activity/task name")
    resource: str = Field(description="Column name holding the resource/agent that did the work")
    start_timestamp: str = Field(description="Column name holding the activity start time")
    end_timestamp: str = Field(description="Column name holding the activity end time")
    num_simulations: int = Field(default=10, description="How many synthetic logs to generate")
    determine_automatically: bool = Field(
        default=True,
        description="Let the simulator auto-pick orchestration/delay settings (recommended)",
    )
    central_orchestration: bool = Field(
        default=False, description="Only used when determine_automatically=False"
    )
    extr_delays: bool = Field(
        default=False, description="Only used when determine_automatically=False"
    )


class ClarificationNeeded(BaseModel):
    """Returned by the LLM instead of a SimulationRequest when intent is underspecified."""

    question: str


class RunManifest(BaseModel):
    """Structured, auditable record of one simulate.py invocation."""

    run_id: str
    dataset_name: str
    request: SimulationRequest
    status: Literal["success", "failed", "timeout"]
    attempt: int
    started_at: datetime
    finished_at: datetime
    exit_code: int | None
    output_dir: str
    simulated_log_files: list[str] = Field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""


class EvaluationSummary(BaseModel):
    dataset_name: str
    num_runs: int
    metrics: dict[str, list[float]]

    def means(self) -> dict[str, float]:
        return {k: (sum(v) / len(v) if v else float("nan")) for k, v in self.metrics.items()}
