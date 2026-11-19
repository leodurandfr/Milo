# backend/tests/architecture/test_audio_normalization.py
"""The loudness normalization has one switch per mpv source, and one place
that applies it.

`SettingsService.defaults["audio"]["normalization"]` declares the switches;
`MpvAudioSource` reads `audio.normalization.<source_id>` before every load.
A source moved onto mpv with no switch would read None and play unfiltered
with nothing on the settings page to say so; a switch naming a source that is
not on mpv would be a toggle that does nothing. The two wire models carry one
field per switch, and the settings page lists the stored keys — so all four
are held equal here, derived from the classes, never from a list typed here.

And no unit puts a filter in mpv's command line: one there sits under the
setting, out of its reach, which is what this replaced.
"""
import importlib
import re
from pathlib import Path

from backend.api.models import AudioNormalizationRequest
from backend.core.models.audio_state import AudioSource
from backend.core.models.settings_config import AudioNormalizationConfig
from backend.core.settings import SettingsService
from backend.shared.mpv_audio_source import MpvAudioSource

SYSTEM = Path(__file__).resolve().parents[3] / "system"


def _mpv_sources():
    """The source ids whose class plays through mpv (each source package
    exports exactly one name)."""
    found = set()
    for source in AudioSource:
        if source is AudioSource.NONE:
            continue
        package = importlib.import_module(f"backend.sources.{source.value}")
        (name,) = package.__all__
        if issubclass(getattr(package, name), MpvAudioSource):
            found.add(source.value)
    return found


def test_the_derivation_reads_something():
    sources = _mpv_sources()
    assert sources, "no mpv source found — the base class moved"
    assert sources != {s.value for s in AudioSource if s is not AudioSource.NONE}, \
        "every source derived — the rule reads nothing"


def test_every_mpv_source_has_a_switch_and_nothing_else_does():
    declared = set(SettingsService().defaults["audio"]["normalization"])
    assert declared == _mpv_sources()


def test_the_wire_models_carry_one_field_per_switch():
    declared = set(SettingsService().defaults["audio"]["normalization"])
    assert set(AudioNormalizationConfig.model_fields) == declared
    assert set(AudioNormalizationRequest.model_fields) == declared


def test_no_unit_puts_a_filter_in_mpv():
    units = [p for p in SYSTEM.glob("milo-*.service") if "/usr/bin/mpv" in p.read_text()]
    assert len(units) == len(_mpv_sources()), "the mpv units were not all found"
    offenders = [
        p.name for p in units
        if re.search(r"--(?:af|lavfi-complex)\b|loudnorm", p.read_text().split("ExecStart=", 1)[1])
    ]
    assert offenders == [], f"an audio filter is out of the setting's reach in {offenders}"
