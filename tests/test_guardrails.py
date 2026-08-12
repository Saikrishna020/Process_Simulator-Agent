import pytest

from webapp.orchestrator.guardrails import GuardrailViolation, validate_request
from webapp.orchestrator.schemas import SimulationRequest

LOAN_APP_VALID = dict(
    dataset_name="LoanApp",
    case_id="case_id",
    activity_name="activity",
    resource="resource",
    start_timestamp="start_time",
    end_timestamp="end_time",
    num_simulations=2,
)


def test_valid_known_dataset_passes():
    req = SimulationRequest(**LOAN_APP_VALID)
    validated = validate_request(req)
    assert validated.log_path.name == "LoanApp.csv.gz"
    assert validated.log_path.exists()


def test_path_traversal_outside_raw_data_is_rejected():
    req = SimulationRequest(**{**LOAN_APP_VALID, "dataset_name": "../requirements.txt"})
    with pytest.raises(GuardrailViolation, match="outside raw_data"):
        validate_request(req)


def test_absolute_path_outside_raw_data_is_rejected():
    req = SimulationRequest(**{**LOAN_APP_VALID, "dataset_name": "C:/Windows/win.ini"})
    with pytest.raises(GuardrailViolation):
        validate_request(req)


def test_nonexistent_dataset_is_rejected():
    req = SimulationRequest(**{**LOAN_APP_VALID, "dataset_name": "does_not_exist.csv"})
    with pytest.raises(GuardrailViolation, match="No such file"):
        validate_request(req)


def test_bad_column_name_is_rejected():
    req = SimulationRequest(**{**LOAN_APP_VALID, "case_id": "not_a_real_column"})
    with pytest.raises(GuardrailViolation, match="not_a_real_column"):
        validate_request(req)


def test_num_simulations_out_of_range_is_rejected():
    req = SimulationRequest(**{**LOAN_APP_VALID, "num_simulations": 10_000})
    with pytest.raises(GuardrailViolation, match="num_simulations"):
        validate_request(req)


def test_num_simulations_zero_is_rejected():
    req = SimulationRequest(**{**LOAN_APP_VALID, "num_simulations": 0})
    with pytest.raises(GuardrailViolation, match="num_simulations"):
        validate_request(req)


def test_unregistered_dataset_with_correct_relative_path_and_columns_passes():
    """Novel datasets aren't rejected outright — they just get their header checked for real."""
    req = SimulationRequest(**{**LOAN_APP_VALID, "dataset_name": "LoanApp.csv.gz"})
    validated = validate_request(req)
    assert validated.log_path.exists()
