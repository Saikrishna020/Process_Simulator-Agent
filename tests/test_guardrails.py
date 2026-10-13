"""Deterministic rejection of invalid assistant proposals — no LLM involved.

These cover the boundary that matters: the LLM supplies human *names* and free-form numbers,
and code resolves/validates them against the selected model before anything can run.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from processlab.assistant.guardrails import (
    GuardrailViolation, _resolve_within_raw_data, validate_scenario,
)
from processlab.assistant.schemas import (
    ScenarioActivityChange, ScenarioClone, ScenarioRequest, ScenarioResourceChange,
)
from processlab.engine.model import MODEL_VERSION

MONDAY = datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()
CALENDAR = [[9 * 3600, 17 * 3600] for _ in range(5)] + [None, None]


@pytest.fixture
def model():
    """Two resources; only User_87 can Validate, so removing it strands that activity.

    'W_Shortened completion ' reproduces the real BPI 2017 trailing-space quirk.
    """
    def resource(rid, name, activities):
        return dict(id=rid, name=name, template_id=rid, calendar=deepcopy(CALENDAR),
                    event_count=5, case_count=5, observed_load_pct=10.0, handoffs=[],
                    activities={a: dict(count=5, samples=[3600]) for a in activities})

    return dict(
        model_id="a" * 24, version=MODEL_VERSION, dataset="LoanApp", source_hash="abc",
        assumptions=[], start_timestamp=MONDAY,
        historical_cycle_hours=dict(mean=1, median=1, p90=1),
        resources=[resource("r0", "User_87", ["Review", "Validate"]),
                   resource("r1", "User_5", ["Review", "W_Shortened completion "])],
        activities=[dict(name="Review", event_count=10), dict(name="Validate", event_count=5),
                    dict(name="W_Shortened completion ", event_count=5)],
        templates=[[["Review", "r0", 0]]],
        arrival_days=[[[9 * 3600] * 4]] + [[[]] for _ in range(6)],
    )


# --- path containment: a dataset is still a filesystem read -------------------------------

def test_path_traversal_outside_raw_data_is_rejected():
    with pytest.raises(GuardrailViolation, match="outside raw_data"):
        _resolve_within_raw_data("../processlab/main.py")


def test_absolute_path_outside_raw_data_is_rejected():
    with pytest.raises(GuardrailViolation, match="outside raw_data"):
        _resolve_within_raw_data("C:/Windows/System32/drivers/etc/hosts")


def test_nonexistent_file_under_raw_data_is_rejected():
    with pytest.raises(GuardrailViolation, match="No such file"):
        _resolve_within_raw_data("not_a_real_log.csv")


def test_unregistered_dataset_is_rejected(model):
    model = {**model, "dataset": "NotRegistered"}
    with pytest.raises(GuardrailViolation, match="not registered"):
        validate_scenario(model, ScenarioRequest())


# --- name resolution: the LLM names people, code resolves ids ------------------------------

def test_names_resolve_to_internal_ids_and_the_llm_never_supplies_one(model):
    validated = validate_scenario(model, ScenarioRequest(
        clones=[ScenarioClone(source_resource_name="User_87", count=2)],
        resource_changes=[ScenarioResourceChange(resource_name="User_5",
                                                 duration_multiplier=0.8)]))
    assert validated.request.clones[0].source_id == "r0"
    assert validated.request.resource_changes[0].resource_id == "r1"
    assert validated.resolved_names == {"User_87": "r0", "User_5": "r1"}


def test_resolution_is_case_insensitive_and_tolerates_the_trailing_space_quirk(model):
    validated = validate_scenario(model, ScenarioRequest(
        resource_changes=[ScenarioResourceChange(resource_name="user_87",
                                                 duration_multiplier=0.9)],
        activity_changes=[ScenarioActivityChange(activity="w_shortened completion",
                                                 delay_multiplier=0.5)]))
    assert validated.request.resource_changes[0].resource_id == "r0"
    # Resolved back to the real name, trailing space included — the engine matches on it.
    assert validated.request.activity_changes[0].activity == "W_Shortened completion "


def test_unknown_resource_is_rejected_with_a_suggestion(model):
    with pytest.raises(GuardrailViolation) as exc:
        validate_scenario(model, ScenarioRequest(
            clones=[ScenarioClone(source_resource_name="User_8")]))
    assert "Unknown resource" in str(exc.value)
    assert "User_87" in str(exc.value)  # close-match suggestion, not a silent substitution


def test_unknown_activity_is_rejected(model):
    with pytest.raises(GuardrailViolation, match="Unknown activity"):
        validate_scenario(model, ScenarioRequest(
            activity_changes=[ScenarioActivityChange(activity="Approve", delay_multiplier=0.5)]))


def test_a_clone_gets_a_default_label_derived_from_its_source(model):
    validated = validate_scenario(model, ScenarioRequest(
        clones=[ScenarioClone(source_resource_name="User_87", count=3)]))
    assert validated.request.clones[0].name == "Copy of User_87"
    assert validated.request.clones[0].count == 3


# --- the engine's own rules, reused rather than reimplemented -----------------------------

def test_removing_the_only_qualified_resource_is_rejected(model):
    with pytest.raises(GuardrailViolation, match="Validate"):
        validate_scenario(model, ScenarioRequest(
            resource_changes=[ScenarioResourceChange(resource_name="User_87", enabled=False)]))


def test_duplicate_changes_for_one_resource_are_rejected(model):
    with pytest.raises(GuardrailViolation, match="[Oo]nly one change"):
        validate_scenario(model, ScenarioRequest(resource_changes=[
            ScenarioResourceChange(resource_name="User_87", duration_multiplier=0.8),
            ScenarioResourceChange(resource_name="User_87", duration_multiplier=0.5)]))


def test_too_many_added_resources_are_rejected(model):
    with pytest.raises(GuardrailViolation, match="50 additional"):
        validate_scenario(model, ScenarioRequest(clones=[
            ScenarioClone(source_resource_name="User_87", name=f"copy {i}", count=20)
            for i in range(3)]))


# --- the schema's own bounds stop absurd requests before resolution -----------------------

@pytest.mark.parametrize("field,value", [
    ("demand_multiplier", 99),        # cap is 3
    ("demand_multiplier", 0),         # floor is 0.25
    ("horizon_days", 365),            # cap is 90
    ("horizon_days", 1),              # floor is 7
    ("repetitions", 100),             # cap is 10
    ("sla_hours", 0),                 # must be > 0
])
def test_out_of_range_settings_are_rejected_by_the_schema(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScenarioRequest(**{field: value})


def test_the_schema_forbids_unknown_fields():
    from pydantic import ValidationError

    # An LLM inventing a lever it imagines exists must fail loudly, not be ignored.
    with pytest.raises(ValidationError):
        ScenarioRequest(add_a_new_process_step="Approve twice")


def test_an_unchanged_scenario_is_flagged_as_describing_no_change(model):
    assert ScenarioRequest().describes_a_change() is False
    assert ScenarioRequest(demand_multiplier=1.5).describes_a_change() is True
    assert ScenarioRequest(routing="pooled").describes_a_change() is True
