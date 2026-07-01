# backend/tests/test_command_contract.py
"""
Guards for the per-source command contract (BaseAudioSource.COMMANDS).

These keep the COMMANDS registry from drifting away from the if-chain in
_handle_command and from the commands the hardware playback dispatcher sends —
a mismatch there fails silently in production (playback_dispatch swallows
exceptions), so it must be caught here.
"""
import inspect
import pytest
from pydantic import BaseModel

from backend.sources.spotify.source import SpotifySource
from backend.sources.radio.source import RadioSource
from backend.sources.podcast.source import PodcastSource
from backend.sources.cd.source import CdSource
from backend.sources.mac.source import MacSource
from backend.sources.bluetooth.source import BluetoothSource
from backend.sources.airplay.source import AirPlaySource
from backend.sources.dlna.source import DlnaSource

ALL_SOURCES = [
    SpotifySource, RadioSource, PodcastSource, CdSource,
    MacSource, BluetoothSource, AirPlaySource, DlnaSource,
]

# Commands the hardware encoder/IR/BT-remote dispatcher sends per active source
# (backend/hardware/playback_dispatch.py). These MUST stay registered or the
# physical controls break with no user-visible error.
HARDWARE_COMMANDS = {
    SpotifySource: ["playpause", "next", "prev"],
    RadioSource: ["stop_playback", "resume_playback"],
    PodcastSource: ["pause", "resume"],
    CdSource: ["pause", "resume", "next", "prev"],
}


@pytest.mark.parametrize("cls", ALL_SOURCES)
def test_command_models_are_basemodel_or_none(cls):
    """Every COMMANDS value is either None (no params) or a Pydantic model class."""
    for cmd, model in cls.COMMANDS.items():
        assert model is None or (isinstance(model, type) and issubclass(model, BaseModel)), (
            f"{cls.__name__}.COMMANDS['{cmd}'] must be None or a BaseModel subclass"
        )


@pytest.mark.parametrize("cls", ALL_SOURCES)
def test_every_registered_command_has_dispatch_arm(cls):
    """No orphan registry entry: each COMMANDS key is referenced in _handle_command."""
    src = inspect.getsource(cls._handle_command)
    for cmd in cls.COMMANDS:
        assert f'"{cmd}"' in src or f"'{cmd}'" in src, (
            f"{cls.__name__}.COMMANDS has '{cmd}' with no dispatch arm in _handle_command"
        )


@pytest.mark.parametrize("cls,cmds", HARDWARE_COMMANDS.items())
def test_hardware_dispatched_commands_registered(cls, cmds):
    """Commands sent by the hardware dispatcher are registered on their source."""
    for cmd in cmds:
        assert cmd in cls.COMMANDS, (
            f"{cls.__name__}.COMMANDS is missing hardware command '{cmd}' "
            f"(playback_dispatch would fail silently)"
        )
