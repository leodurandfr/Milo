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

from backend.core.models.audio_state import AudioSource
from backend.hardware import playback_dispatch
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

# QobuzSource and MacSource are listed with an empty COMMANDS registry (playback
# is driven by the sender, not by us) — the per-command loops below are then
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

# The source class behind each AudioSource member, via the package the module
# lives in — `backend.sources.spotify.source` is AudioSource.SPOTIFY. That is
# the same identity `BaseAudioSource.source` derives at runtime from source_id.
SOURCE_BY_ENUM = {
    AudioSource(cls.__module__.split(".")[2]).name: cls for cls in ALL_SOURCES
}
assert len(SOURCE_BY_ENUM) == len(ALL_SOURCES), (
    "two source classes resolved to one enum member — the extractor is broken"
)

DISPATCH_TREE = ast.parse(inspect.getsource(playback_dispatch))


def _audio_source_names(node):
    """AudioSource members a node names: one attribute, or a collection of them."""
    elts = node.elts if isinstance(node, (ast.Set, ast.Tuple, ast.List)) else [node]
    return {
        elt.attr for elt in elts
        if isinstance(elt, ast.Attribute)
        and isinstance(elt.value, ast.Name)
        and elt.value.id == "AudioSource"
    }


def _guard_sets():
    """The module-level `_*_SOURCES` guards, as {name: {enum member names}}."""
    sets = {}
    for node in DISPATCH_TREE.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith("_SOURCES"):
                sets[target.id] = _audio_source_names(node.value)
    return sets


def _function(name):
    for node in ast.walk(DISPATCH_TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"playback_dispatch has no {name}() — the extractor is broken")


def _literal_args(nodes, attr):
    """First-argument string literals of every `.<attr>(...)` call under `nodes`."""
    found = set()
    for node in nodes:
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == attr
                and call.args
                and isinstance(call.args[0], ast.Constant)
            ):
                found.add(call.args[0].value)
    return found


def _play_pause_commands():
    """{enum member: commands} from `dispatch_play_pause`'s if-chain.

    Only each branch's own body is read: an `elif` lives in the previous `If`'s
    `orelse`, so walking the whole node would hand every branch's commands to
    the first source in the chain.
    """
    per_source = {}
    for node in ast.walk(_function("dispatch_play_pause")):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        if not (isinstance(node.test.left, ast.Name) and node.test.left.id == "active_source"):
            continue
        names = set()
        for op, comparator in zip(node.test.ops, node.test.comparators):
            # `not in` is the early-return guard, not a dispatch branch.
            if isinstance(op, (ast.Eq, ast.In)):
                names |= _audio_source_names(comparator)
        commands = _literal_args(node.body, "command")
        for name in names:
            per_source.setdefault(name, set()).update(commands)
    return per_source


def _hardware_commands():
    """Commands the hardware dispatcher sends each source, per source class.

    Derived from `backend/hardware/playback_dispatch.py`, not mirrored: the
    hand-written table this replaces was tied back to the dispatcher by nothing
    at all, so the one file it exists to track could have grown a command — or
    a whole source — with the guard below still green.

    Two shapes to read. Play/pause branches on the source and names its command
    literally. Track navigation does not: `dispatch_track(direction)` forwards
    its argument, so the commands are the literals `_resolve_clicks` calls it
    with, sent to every source in the nav guard set.
    """
    guards = _guard_sets()
    play_pause = _play_pause_commands()
    track = _literal_args([_function("_resolve_clicks")], "dispatch_track")

    per_enum = {}
    for name in guards["_PLAY_PAUSE_SOURCES"]:
        per_enum.setdefault(name, set()).update(play_pause.get(name, set()))
    for name in guards["_TRACK_NAV_SOURCES"]:
        per_enum.setdefault(name, set()).update(track)

    unknown = set(per_enum) - set(SOURCE_BY_ENUM)
    assert not unknown, f"playback_dispatch names {sorted(unknown)}, which no source class claims"
    return {SOURCE_BY_ENUM[name]: sorted(cmds) for name, cmds in per_enum.items()}


# Commands the hardware encoder/IR/BT-remote dispatcher sends per active source.
# These MUST stay registered or the physical controls break with no user-visible
# error — playback_dispatch swallows the exception.
HARDWARE_COMMANDS = _hardware_commands()


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
    A sender-driven source has an empty registry and no if-chain, which is
    correct.
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


def test_hardware_dispatch_extraction_is_not_vacuous():
    """The derivation above, checked before anything is asserted from it.

    Every part of it can fail open: a guard set that stops parsing yields no
    sources, and a branch whose test shape changed yields no commands for the
    source it dispatches — either way the rule below would loop over nothing
    and pass. Both are also real defects on their own terms: a source listed in
    a guard set with no branch to answer it means a button that does nothing.
    """
    guards = _guard_sets()
    assert set(guards) == {"_PLAY_PAUSE_SOURCES", "_TRACK_NAV_SOURCES"}, (
        f"playback_dispatch guard sets parsed as {sorted(guards)} — the extractor is broken"
    )
    for name, members in guards.items():
        assert len(members) >= 5, f"{name} parsed as {sorted(members)} — the extractor is broken"

    covered = set(HARDWARE_COMMANDS)
    for name in guards["_PLAY_PAUSE_SOURCES"] | guards["_TRACK_NAV_SOURCES"]:
        cls = SOURCE_BY_ENUM[name]
        assert cls in covered and HARDWARE_COMMANDS[cls], (
            f"playback_dispatch guards {name} but no command was extracted for it: "
            f"the press reaches the source and dispatches nothing"
        )

    # dispatch_track sends the argument it is handed, so the literals collected
    # from its caller are the commands only while it forwards them untranslated.
    forwarding = _function("dispatch_track")
    parameters = {a.arg for a in forwarding.args.args}
    sent = [
        call.args[0] for call in ast.walk(forwarding)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "command"
        and call.args
    ]
    assert len(sent) == 1 and isinstance(sent[0], ast.Name) and sent[0].id in parameters, (
        "dispatch_track() no longer forwards its own argument to command() — "
        "the track commands derived from _resolve_clicks are no longer what it sends"
    )


@pytest.mark.parametrize("cls,cmds", HARDWARE_COMMANDS.items())
def test_hardware_dispatched_commands_registered(cls, cmds):
    """Commands sent by the hardware dispatcher are registered on their source."""
    for cmd in cmds:
        assert cmd in cls.COMMANDS, (
            f"{cls.__name__}.COMMANDS is missing hardware command '{cmd}' "
            f"(playback_dispatch would fail silently)"
        )
