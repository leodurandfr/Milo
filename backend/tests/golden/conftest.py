import pytest

from backend.core import audio_source
from backend.tests.golden.harness import EPOCH, GOLDEN_WALL


@pytest.fixture(autouse=True)
def golden_wall(monkeypatch):
    """Anchors stamped on the golden wall clock, reset for each scenario, so a
    recording never holds a real clock. A world with a VirtualClock replaces
    it (`use_virtual_wall`)."""
    GOLDEN_WALL[0] = EPOCH
    monkeypatch.setattr(audio_source, "wall_time", lambda: GOLDEN_WALL[0])
