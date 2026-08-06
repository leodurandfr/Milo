# backend/tests/test_command_contract.py
"""
Guards for the per-source command contract (BaseAudioSource.COMMANDS).

These keep the COMMANDS registry from drifting away from the if-chain in
_handle_command and from the commands the hardware playback dispatcher sends —
a mismatch there fails silently in production (playback_dispatch swallows
exceptions), so it must be caught here.
"""
import ast
import inspect
import textwrap

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
from backend.sources.music_library.source import MusicLibrarySource
from backend.sources.qobuz.source import QobuzSource
from backend.sources.tidal.source import TidalSource

# QobuzSource is listed with an empty COMMANDS registry (Family B: playback is
# driven by the Qobuz sender, not by us) — the per-command loops below are then
# no-ops, which is the correct outcome, not a gap.
ALL_SOURCES = [
    SpotifySource, RadioSource, PodcastSource, CdSource,
    MacSource, BluetoothSource, AirPlaySource, DlnaSource,
    MusicLibrarySource, QobuzSource, TidalSource,
]

# Two sources translate each command into a foreign spelling through a table
# instead of branching on the name — Tidal into the daemon's, Bluetooth into
# AVRCP's — so the two AST guards below have little or no `cmd == "..."` to
# read for them. Their equivalent invariant is asserted directly in
# test_map_dispatch_matches_registry. Bluetooth is a hybrid: `disconnect` stays
# an if-arm because it acts on the link, not on the player.
MAP_DISPATCH_SOURCES = {
    TidalSource: "COMMAND_MAP",
    BluetoothSource: "AVRCP_COMMANDS",
}
IF_CHAIN_SOURCES = [cls for cls in ALL_SOURCES if cls not in MAP_DISPATCH_SOURCES]

# Commands the hardware encoder/IR/BT-remote dispatcher sends per active source
# (backend/hardware/playback_dispatch.py). These MUST stay registered or the
# physical controls break with no user-visible error.
HARDWARE_COMMANDS = {
    SpotifySource: ["playpause", "next", "prev"],
    RadioSource: ["stop", "resume_playback"],
    PodcastSource: ["pause", "resume"],
    CdSource: ["pause", "resume", "next", "prev"],
    MusicLibrarySource: ["pause", "resume", "next", "prev"],
    TidalSource: ["pause", "resume", "next", "prev"],
    BluetoothSource: ["pause", "resume", "next", "prev"],
}


@pytest.mark.parametrize("cls", ALL_SOURCES)
def test_command_models_are_basemodel_or_none(cls):
    """Every COMMANDS value is either None (no params) or a Pydantic model class."""
    for cmd, model in cls.COMMANDS.items():
        assert model is None or (isinstance(model, type) and issubclass(model, BaseModel)), (
            f"{cls.__name__}.COMMANDS['{cmd}'] must be None or a BaseModel subclass"
        )


@pytest.mark.parametrize("cls", IF_CHAIN_SOURCES)
def test_every_registered_command_has_dispatch_arm(cls):
    """No orphan registry entry: each COMMANDS key is referenced in _handle_command."""
    src = inspect.getsource(cls._handle_command)
    for cmd in cls.COMMANDS:
        assert f'"{cmd}"' in src or f"'{cmd}'" in src, (
            f"{cls.__name__}.COMMANDS has '{cmd}' with no dispatch arm in _handle_command"
        )


def _dispatched_commands(cls):
    """Command names the `_handle_command` if-chain actually branches on."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls._handle_command)))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "cmd":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                found.add(comparator.value)
            elif isinstance(comparator, (ast.List, ast.Tuple, ast.Set)):
                found.update(
                    elt.value for elt in comparator.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                )
    return found


@pytest.mark.parametrize("cls", IF_CHAIN_SOURCES)
def test_every_dispatch_arm_is_registered(cls):
    """The reverse of the check above: no arm for an unregistered command.

    An unregistered arm is unreachable — `command()` rejects the name before
    `_handle_command` runs — so it reads as live playback code while being dead.
    Family B sources have an empty registry and no if-chain, which is correct.
    """
    dispatched = _dispatched_commands(cls)
    if not cls.COMMANDS:
        assert not dispatched, (
            f"{cls.__name__} has an empty COMMANDS registry but branches on "
            f"{sorted(dispatched)} — those arms are unreachable"
        )
        return

    assert dispatched, (
        f"no `cmd == ...` comparison found in {cls.__name__}._handle_command "
        f"— the extractor is broken"
    )
    orphans = dispatched - set(cls.COMMANDS)
    assert not orphans, (
        f"{cls.__name__}._handle_command branches on {sorted(orphans)}, which is "
        f"not in COMMANDS — unreachable, command() rejects the name first"
    )


@pytest.mark.parametrize("cls,map_attr", MAP_DISPATCH_SOURCES.items())
def test_map_dispatch_matches_registry(cls, map_attr):
    """A map-dispatching source dispatches exactly the commands it registers.

    Same guarantee the if-chain guards give the others, in the shape this
    dispatch takes: a COMMANDS key reaching neither the map nor an if-arm
    raises KeyError on the first press (the hardware dispatcher swallows it, so
    the encoder button would simply do nothing), and a translated name absent
    from COMMANDS is dead — `command()` rejects it before dispatch.

    The union is what covers the hybrid: Bluetooth answers `disconnect` from an
    if-arm and its four transport commands from the AVRCP table.
    """
    covered = set(getattr(cls, map_attr)) | _dispatched_commands(cls)
    assert covered == set(cls.COMMANDS), (
        f"{cls.__name__}.COMMANDS and its dispatch disagree: "
        f"registry-only={sorted(set(cls.COMMANDS) - covered)}, "
        f"dispatch-only={sorted(covered - set(cls.COMMANDS))}"
    )


@pytest.mark.parametrize("cls,cmds", HARDWARE_COMMANDS.items())
def test_hardware_dispatched_commands_registered(cls, cmds):
    """Commands sent by the hardware dispatcher are registered on their source."""
    for cmd in cmds:
        assert cmd in cls.COMMANDS, (
            f"{cls.__name__}.COMMANDS is missing hardware command '{cmd}' "
            f"(playback_dispatch would fail silently)"
        )
