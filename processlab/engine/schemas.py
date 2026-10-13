from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WorkSchedule(StrictModel):
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_hour: float = Field(ge=0, lt=24)
    end_hour: float = Field(gt=0, le=24)

    @model_validator(mode="after")
    def check(self):
        if self.end_hour <= self.start_hour:
            raise ValueError("Working hours must end after they start.")
        if len(set(self.weekdays)) != len(self.weekdays) or any(d not in range(7) for d in self.weekdays):
            raise ValueError("Weekdays must be unique numbers from 0 (Monday) to 6 (Sunday).")
        return self

    def calendar(self):
        return [[self.start_hour * 3600, self.end_hour * 3600] if d in self.weekdays else None for d in range(7)]


class ResourceChange(StrictModel):
    resource_id: str
    enabled: bool = True
    duration_multiplier: float = Field(default=1, ge=0.1, le=5)
    schedule: WorkSchedule | None = None


class ResourceClone(StrictModel):
    source_id: str
    name: str = Field(min_length=1, max_length=80)
    count: int = Field(default=1, ge=1, le=20)
    duration_multiplier: float = Field(default=1, ge=0.1, le=5)
    schedule: WorkSchedule | None = None


class ActivityChange(StrictModel):
    activity: str
    duration_multiplier: float = Field(default=1, ge=0.1, le=5)
    delay_multiplier: float = Field(default=1, ge=0, le=5)


class ExperimentRequest(StrictModel):
    model_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    name: str = Field(default="My scenario", min_length=1, max_length=100)
    horizon_days: int = Field(default=30, ge=7, le=90)
    repetitions: int = Field(default=3, ge=1, le=10)
    seed: int = Field(default=42, ge=0, le=1_000_000)
    sla_hours: float = Field(default=120, gt=0, le=8760)
    demand_multiplier: float = Field(default=1, ge=0.25, le=3)
    routing: Literal["historical", "pooled"] = "historical"
    resource_changes: list[ResourceChange] = Field(default_factory=list, max_length=200)
    clones: list[ResourceClone] = Field(default_factory=list, max_length=30)
    activity_changes: list[ActivityChange] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_changes(self):
        for values, label in [([r.resource_id for r in self.resource_changes], "resource"),
                              ([a.activity for a in self.activity_changes], "activity")]:
            if len(values) != len(set(values)):
                raise ValueError(f"Only one change per {label} is allowed.")
        if sum(c.count for c in self.clones) > 50:
            raise ValueError("At most 50 additional resources are allowed per scenario.")
        return self
