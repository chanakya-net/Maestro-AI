"""Shared native-test fixtures; never discover or launch a real provider."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def fake_agent_path() -> Path:
    return ROOT / "tests" / "fixtures" / "fake_agent.py"
