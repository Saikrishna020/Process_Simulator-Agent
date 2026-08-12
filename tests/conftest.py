import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True, scope="session")
def _repo_root_cwd():
    """simulate.py and evaluate_run.py both resolve paths relative to the process cwd."""
    original = os.getcwd()
    os.chdir(REPO_ROOT)
    yield
    os.chdir(original)
