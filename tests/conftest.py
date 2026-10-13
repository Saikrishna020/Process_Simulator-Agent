import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The assistant tests drive a scripted fake LLM, so no real provider call is ever made. A
# placeholder key still lets the assistant's code paths build their (unused) client; a real key
# from .env or the environment takes precedence. Process Lab's engine, scenario runs and the ten
# analyst presets need no key at all.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True, scope="session")
def _repo_root_cwd():
    """The analyst and scoring resolve raw_data/ relative to the process cwd."""
    original = os.getcwd()
    os.chdir(REPO_ROOT)
    yield
    os.chdir(original)
