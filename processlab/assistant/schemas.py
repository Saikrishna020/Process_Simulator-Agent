"""Typed contracts passed between the LLM, the guardrails, and the engine.

The LLM only ever fills these in — nothing it produces reaches the engine, a filesystem path or
a job queue without passing through guardrails.validate_scenario() first.

These are deliberately **name-based**, not id-based: the model's internal ids (`r50`,
`clone_0_1`) are an implementation detail the LLM has no business inventing. It names people and
activities the way a human would ("User_87", "W_Call after offers") and
guardrails.validate_scenario resolves those names against the selected model, rejecting anything
it cannot match. That keeps the "LLM proposes, code validates" boundary intact.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ScenarioClone(Strict):
    """Add copies of an existing resource."""

    source_resource_name: str = Field(description="Name of the resource to copy, e.g. 'User_87'")
    count: int = Field(default=1, ge=1, le=20, description="How many copies to add")
    name: str | None = Field(
        default=None, max_length=80,
        description="Optional label for the copies; defaults to 'Copy of <source>'",
    )
    duration_multiplier: float = Field(
        default=1.0, ge=0.1, le=5,
        description="Speed of the copies relative to the source; 0.8 = 20% faster",
    )


class ScenarioResourceChange(Strict):
    """Remove a resource, or change how fast it works."""

    resource_name: str = Field(description="Name of an existing resource, e.g. 'User_87'")
    enabled: bool = Field(default=True, description="false removes the resource entirely")
    duration_multiplier: float = Field(
        default=1.0, ge=0.1, le=5,
        description="Processing time relative to history; 0.8 = 20% faster",
    )


class ScenarioActivityChange(Strict):
    """Change an activity's processing time or its residual (external) delay."""

    activity: str = Field(description="Exact activity name, e.g. 'W_Call after offers'")
    duration_multiplier: float = Field(
        default=1.0, ge=0.1, le=5, description="Processing time relative to history")
    delay_multiplier: float = Field(
        default=1.0, ge=0, le=5,
        description="Residual delay relative to history; 0.5 halves customer/external waiting",
    )


class ScenarioRequest(Strict):
    """A candidate what-if experiment, as proposed by the LLM (untrusted until validated).

    There is no model_id field on purpose: the model is whichever one the user has selected in
    the UI, taken from session state. The LLM cannot redirect a run at a different dataset.
    """

    name: str = Field(default="Assistant scenario", min_length=1, max_length=100)
    horizon_days: int = Field(default=30, ge=7, le=90, description="Days of arrivals to simulate")
    repetitions: int = Field(default=3, ge=1, le=10, description="Paired replications")
    seed: int = Field(default=42, ge=0, le=1_000_000)
    sla_hours: float = Field(default=120, gt=0, le=8760, description="Deadline per case, in hours")
    demand_multiplier: float = Field(
        default=1.0, ge=0.25, le=3, description="1.5 = 50% more cases arrive")
    routing: Literal["historical", "pooled"] = Field(
        default="historical",
        description="'historical' keeps observed handoffs; 'pooled' sends each task to the "
                    "earliest available qualified resource",
    )
    clones: list[ScenarioClone] = Field(default_factory=list, max_length=30)
    resource_changes: list[ScenarioResourceChange] = Field(default_factory=list, max_length=200)
    activity_changes: list[ScenarioActivityChange] = Field(default_factory=list, max_length=100)

    def describes_a_change(self) -> bool:
        """An all-defaults request reproduces the baseline exactly — usually not what was meant."""
        return bool(self.clones or self.resource_changes or self.activity_changes
                    or self.demand_multiplier != 1.0 or self.routing != "historical")


class ClarificationNeeded(Strict):
    """Returned by the LLM instead of a ScenarioRequest when intent is underspecified."""

    question: str


class RunManifest(BaseModel):
    """Structured, auditable record of one assistant-triggered experiment."""

    run_id: str
    dataset: str
    model_id: str
    scenario_name: str
    request: dict
    status: Literal["success", "failed", "busy", "timeout"]
    started_at: datetime
    finished_at: datetime
    job_id: str | None = None
    error: str | None = None
