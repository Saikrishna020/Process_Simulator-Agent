from webapp.orchestrator import tools
from webapp.orchestrator.guardrails import ConcurrencyLimiter, validate_request
from webapp.orchestrator.schemas import SimulationRequest


def test_concurrency_limiter_bounds_parallel_jobs():
    limiter = ConcurrencyLimiter(max_concurrent=1)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False  # second job rejected while first holds the slot
    limiter.release()
    assert limiter.try_acquire() is True
    limiter.release()


def test_run_simulation_loanapp_end_to_end():
    """Real subprocess run against the small LoanApp dataset — no LLM involved.
    Also exercises the source/utils.py mkdir fix (safe to run repeatedly)."""
    req = SimulationRequest(
        dataset_name="LoanApp",
        case_id="case_id",
        activity_name="activity",
        resource="resource",
        start_timestamp="start_time",
        end_timestamp="end_time",
        num_simulations=1,
        determine_automatically=True,
    )
    validated = validate_request(req)
    manifest = tools.run_simulation(validated)

    assert manifest.status == "success", manifest.stderr_tail
    assert manifest.exit_code == 0
    assert len(manifest.simulated_log_files) == 1


def test_evaluate_simulation_reuses_evaluate_run():
    """Depends on test_run_simulation_loanapp_end_to_end having produced output already
    (pytest runs test functions in file order by default)."""
    summary = tools.evaluate_simulation("LoanApp", "LoanApp.csv", num_simulations=1)
    means = summary.means()
    assert set(means) == {"NGD", "AEDD", "CEDD", "REDD", "CTDD"}
    for value in means.values():
        assert value == value  # not NaN
