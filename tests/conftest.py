import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The tests use a scripted fake LLM, but the settings object still requires a key. A fresh checkout
# (CI) has no .env, so supply a placeholder; a real key from .env or the environment takes precedence.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True, scope="session")
def _repo_root_cwd():
    """simulate.py and evaluate_run.py both resolve paths relative to the process cwd."""
    original = os.getcwd()
    os.chdir(REPO_ROOT)
    yield
    os.chdir(original)
