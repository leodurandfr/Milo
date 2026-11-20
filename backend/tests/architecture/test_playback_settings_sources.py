# backend/tests/architecture/test_playback_settings_sources.py
"""The sources Settings > Audio playback names under each setting are the
sources the setting acts on.

The page reads frontend/src/constants/playbackSettingsSources.json; what the
settings actually do is decided here, by each source's class. A source that
gains or loses a resume point, or the auto-stop, leaves the page naming the
wrong sources — a user who sets a long resume window then expects an AirPlay
track to come back, and reads it as a bug. Derived from the class attributes
the base acts on, never from a list typed here.
"""
import importlib
import json
from pathlib import Path

from backend.core.models.audio_state import AudioSource
from backend.core.models.session import EndReason

PAGE_LIST = Path(__file__).resolve().parents[3] / "frontend/src/constants/playbackSettingsSources.json"


def _source_classes():
    """{source id: class}: each source package exports exactly one name."""
    classes = {}
    for source in AudioSource:
        if source is AudioSource.NONE:
            continue
        package = importlib.import_module(f"backend.sources.{source.value}")
        (name,) = package.__all__
        classes[source.value] = getattr(package, name)
    return classes


def _acted_on():
    classes = _source_classes()
    assert len(classes) == len(AudioSource) - 1, "source discovery drift"
    return {
        # The pause timer is armed only where the idle policy ends a long pause
        # (base _idle_follows_phase), which is what AUTO_STOP_SUPPORTED says.
        "auto_stop": {sid for sid, cls in classes.items() if cls.AUTO_STOP_SUPPORTED},
        # The resume window acts on what survives a departure: the point a
        # source switch keeps.
        "resume": {
            sid for sid, cls in classes.items()
            if cls.RESUME_POLICY is not None and EndReason.SOURCE_SWITCH in cls.RESUME_POLICY.capture_on
        },
    }


def test_the_derivation_reads_something():
    """A derivation that found nothing would let an empty list pass."""
    acted_on = _acted_on()
    for setting, sources in acted_on.items():
        assert sources, f"{setting}: no source derived — the attributes moved"
        assert sources != set(_source_classes()), f"{setting}: every source derived — the rule reads nothing"


def test_the_page_names_the_sources_each_setting_acts_on():
    listed = json.loads(PAGE_LIST.read_text())
    for setting, sources in _acted_on().items():
        named = set(listed[setting])
        assert named == sources, (
            f"Settings > Audio playback names {sorted(named)} under {setting}, "
            f"the sources say {sorted(sources)}: update {PAGE_LIST.name}"
        )
