# backend/tests/architecture/test_audio_normalization.py
"""The loudness normalization has one switch per mpv source, applied by
`MpvAudioSource`, plus Spotify's, applied through go-librespot's config — and
no other.

`SettingsService.defaults["audio"]["normalization"]` declares the switches.
`MpvAudioSource` reads `audio.normalization.<source_id>` before every load and
sets mpv's filter. SpotifySource reads its own (`NORMALIZATION_SETTINGS_KEY`)
before every go-librespot start and writes it, negated, as config.yml's
`normalisation_disabled`, which the daemon reads once, at its start.

A source moved onto mpv with no switch would read None and play unfiltered
with nothing on the settings page to say so; a switch naming a source that
neither mpv nor its own daemon config applies would be a toggle that does
nothing; and a Spotify switch that stops reaching the config key is the same
dead toggle, one that still reads "saved". The two wire models carry one field
per switch (plus, on the config, the switch the running go-librespot read, and
on the request the action that restarts it), and the settings page lists the
stored keys — so all of them are held equal here, derived from the classes,
never from a list typed here.

And no unit puts a filter in mpv's command line: one there sits under the
setting, out of its reach, which is what this replaced.
"""
import importlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import yaml

from backend.api.models import AudioNormalizationRequest
from backend.core.models.audio_state import AudioSource
from backend.core.models.settings_config import AudioNormalizationConfig
from backend.core.settings import SettingsService
from backend.shared.mpv_audio_source import MpvAudioSource

SYSTEM = Path(__file__).resolve().parents[3] / "system"

# go-librespot's own name for the switch, upstream's contract rather than
# Milō's: v0.10.7's config_schema.json declares `normalisation_disabled`
# (boolean, default false), and daemon/app.go hands the player
# `NormalisationEnabled: !cfg.NormalisationDisabled` once, at start.
GO_LIBRESPOT_KEY = "normalisation_disabled"


def _source_classes():
    """Each source id → the one class its package exports."""
    classes = {}
    for source in AudioSource:
        if source is AudioSource.NONE:
            continue
        package = importlib.import_module(f"backend.sources.{source.value}")
        (name,) = package.__all__
        classes[source.value] = getattr(package, name)
    return classes


def _mpv_sources():
    """The source ids whose class plays through mpv."""
    return {sid for sid, cls in _source_classes().items() if issubclass(cls, MpvAudioSource)}


def _daemon_config_sources():
    """The source ids whose class is not on mpv and owns a normalization
    switch of its own, written into its daemon's config."""
    return {
        sid for sid, cls in _source_classes().items()
        if not issubclass(cls, MpvAudioSource) and hasattr(cls, "NORMALIZATION_SETTINGS_KEY")
    }


def _declared():
    return set(SettingsService().defaults["audio"]["normalization"])


def test_the_derivation_reads_something():
    sources = _mpv_sources()
    assert sources, "no mpv source found — the base class moved"
    assert sources != set(_source_classes()), "every source derived — the rule reads nothing"
    assert _daemon_config_sources(), "no daemon-config switch found — SpotifySource's key moved"


def test_every_mpv_source_and_spotify_have_a_switch_and_nothing_else_does():
    assert _declared() == _mpv_sources() | {AudioSource.SPOTIFY.value}


def test_spotify_is_the_only_source_whose_daemon_config_takes_a_switch():
    """Tidal and Qobuz are out of the setting by the owner's decision: a
    switch for them names a key nothing here writes."""
    assert _daemon_config_sources() == {AudioSource.SPOTIFY.value}


def test_each_daemon_config_switch_is_read_under_its_own_name():
    classes = _source_classes()
    for sid in _daemon_config_sources():
        assert classes[sid].NORMALIZATION_SETTINGS_KEY == f"audio.normalization.{sid}"


async def test_spotifys_switch_reaches_go_librespots_key_negated(tmp_path):
    """Driven through the one write that runs before every daemon start, for
    each value of the switch: the stored setting must land on upstream's key,
    the other way round."""
    spotify = _source_classes()[AudioSource.SPOTIFY.value]
    config = tmp_path / "config.yml"
    for on in (True, False):
        config.write_text("server:\n  address: localhost\n  port: 3678\n")
        stored = {spotify.NORMALIZATION_SETTINGS_KEY: on, "spotify.crossfade_duration": 0}
        settings = Mock(get_setting=AsyncMock(side_effect=stored.get))
        source = spotify({"config_path": str(config)}, settings_service=settings)
        await source._apply_managed_config()
        written = yaml.safe_load(config.read_text())
        assert GO_LIBRESPOT_KEY in written, "the switch never reached go-librespot's config"
        assert written[GO_LIBRESPOT_KEY] is (not on)


def test_the_wire_models_carry_one_field_per_switch():
    declared = _declared()
    applied = {f"{sid}_applied" for sid in _daemon_config_sources()}
    assert set(AudioNormalizationConfig.model_fields) == declared | applied
    assert set(AudioNormalizationRequest.model_fields) == declared | {"apply_now"}


def test_no_unit_puts_a_filter_in_mpv():
    units = [p for p in SYSTEM.glob("milo-*.service") if "/usr/bin/mpv" in p.read_text()]
    assert len(units) == len(_mpv_sources()), "the mpv units were not all found"
    offenders = [
        p.name for p in units
        if re.search(r"--(?:af|lavfi-complex)\b|loudnorm", p.read_text().split("ExecStart=", 1)[1])
    ]
    assert offenders == [], f"an audio filter is out of the setting's reach in {offenders}"
