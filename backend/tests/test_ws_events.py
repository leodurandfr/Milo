# backend/tests/test_ws_events.py
"""
Envelope tests for the typed WS event layer.

Every WsEvent subclass must serialize to {category, type, origin, data,
timestamp}, and the frontend and Milo-Mac both key on the exact contents.
Each case below pins one representative event per family (state machine,
audio source, volume, settings, programs, equalizer, multiroom, routing,
network, hardware) against its full expected envelope, timestamp excluded —
including which fields are dropped rather than sent as null, and that no
event carries `full_state` any more: the audio state is an event of its own
(`source/state`), whose `data` IS the state.

The second half sweeps *every* subclass rather than a representative, and
enforces the conventions CLAUDE.md states for the category: a unique
(CATEGORY, TYPE), a category from the declared nine, a `source` field on
source-category events, snake_case fields, a docstring naming the consumers,
and an emission site in production code. A new event that forgets one of those
fails here instead of reaching a client.
"""
import ast
import inspect
from pathlib import Path
from typing import get_args

import pytest

from backend.core.models import ws_events as ws_events_module
from backend.core.models.ws_events import WsEvent
from backend.core.models.audio_state import AudioSource
from backend.core.models.audio_wire import PositionAnchor
from backend.core.models.session import EndReason
from backend.core.state import AudioStateMachine
from backend.core.models.ws_events import (
    AudioStateChanged,
    DockAppsChanged,
    DockAppsConfig,
    DockVolumeChanged,
    DockVolumeConfig,
    EqualizerLevels,
    MultiroomPendingClientChanged,
    MultiroomZoneChanged,
    MiloUpdatePhase,
    NetworkStatusChanged,
    ProgramUpdateComplete,
    ProgramUpdateProgress,
    RadioFavoriteAdded,
    RoutingMultiroomError,
    SatelliteUpdateProgress,
    ScreenSleepChanged,
    SourceError,
    SourceErrorReason,
    SourceErrorCleared,
    SourcePosition,
    SourceSessionEnded,
    VolumeChanged,
    VolumeLimitsChanged,
    VolumeLimitsConfig,
    VolumeStartupChanged,
    VolumeStartupConfig,
)
from backend.tests.contracts.emission_sites import emitted_in_backend

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def state_machine(mock_ws_manager):
    sm = AudioStateMachine()
    sm.ws_manager = mock_ws_manager
    return sm


# (event, expected envelope minus timestamp). AudioStateChanged is built from
# a state machine's own state, so it has its own test below.
CASES = [
    (
        SourcePosition(source="podcast", session_id="s-1",
                       position=PositionAnchor(ms=61000, at=1700000000.25, rate=1.0)),
        {"category": "source", "type": "position", "origin": "podcast",
         "data": {"source": "podcast", "session_id": "s-1",
                  "position": {"ms": 61000, "at": 1700000000.25, "rate": 1.0}}},
    ),
    (
        # The reason reaches the wire as its value: podcastStore and
        # musicLibraryStore compare it to "eof".
        SourceSessionEnded(source="music_library", session_id="s-2", reason=EndReason.EOF),
        {"category": "source", "type": "session_ended", "origin": "music_library",
         "data": {"source": "music_library", "session_id": "s-2", "reason": "eof"}},
    ),
    (
        # The banner half of the two error mechanisms: no state on the wire,
        # so a consumer cannot mistake a failed operation for a dead source.
        SourceError(source="radio", reason=SourceErrorReason.STREAM_LOAD_FAILED),
        {"category": "source", "type": "error", "origin": "radio",
         "data": {"source": "radio", "reason": "stream_load_failed"}},
    ),
    (
        SourceErrorCleared(source="bluetooth"),
        {"category": "source", "type": "error_cleared", "origin": "bluetooth",
         "data": {"source": "bluetooth"}},
    ),
    (
        VolumeChanged(display="bar", multiroom_enabled=False,
                      state={"mode": "direct", "global_volume_db": -30.0}),
        {"category": "volume", "type": "volume_changed", "origin": "volume",
         "data": {"display": "bar",
                  "multiroom_enabled": False,
                  "state": {"mode": "direct", "global_volume_db": -30.0}}},
    ),
    (
        VolumeLimitsChanged(limits=VolumeLimitsConfig(min_db=-80.0, max_db=-20.0)),
        {"category": "settings", "type": "volume_limits_changed", "origin": "settings",
         "data": {"source": "settings",
                  "limits": {"min_db": -80.0, "max_db": -20.0}}},
    ),
    (
        VolumeStartupChanged(config=VolumeStartupConfig(
            startup_volume_db=-42.0, restore_last_volume=True)),
        {"category": "settings", "type": "volume_startup_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"startup_volume_db": -42.0, "restore_last_volume": True}}},
    ),
    (
        DockAppsChanged(config=DockAppsConfig(enabled_apps=["spotify", "radio"])),
        {"category": "settings", "type": "dock_apps_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"enabled_apps": ["spotify", "radio"]}}},
    ),
    (
        DockVolumeChanged(config=DockVolumeConfig(
            milo_screen=False, desktop_browser=True, phone_browser=True, ios_app=False)),
        {"category": "settings", "type": "dock_volume_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"milo_screen": False, "desktop_browser": True,
                             "phone_browser": True, "ios_app": False}}},
    ),
    (
        SatelliteUpdateProgress(mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "programs", "type": "satellite_update_progress",
         "origin": "satellite_update",
         "data": {"source": "satellite_update", "mac_id": "aa:bb:cc:dd:ee:ff",
                  "status": "updating"}},
    ),
    (
        # Every other program's progress carries the Milō fields as nulls.
        ProgramUpdateProgress(program="go-librespot"),
        {"category": "programs", "type": "program_update_progress",
         "origin": "program_update",
         "data": {"source": "program_update", "program": "go-librespot",
                  "status": "updating", "phase": None, "from_version": None,
                  "to_version": None, "item": None, "index": None, "total": None}},
    ),
    (
        ProgramUpdateProgress(program="milo", phase="dependencies", from_version="v0.1.4",
                              to_version="v0.1.5", item="Spotify Connect", index=1, total=3),
        {"category": "programs", "type": "program_update_progress",
         "origin": "program_update",
         "data": {"source": "program_update", "program": "milo",
                  "status": "updating", "phase": "dependencies", "from_version": "v0.1.4",
                  "to_version": "v0.1.5", "item": "Spotify Connect", "index": 1,
                  "total": 3}},
    ),
    (
        ProgramUpdateComplete(program="go-librespot", success=False, error="Failed to stop service"),
        {"category": "programs", "type": "program_update_complete",
         "origin": "program_update",
         "data": {"source": "program_update", "program": "go-librespot",
                  "success": False, "error": "Failed to stop service"}},
    ),
    (
        EqualizerLevels(available=True, output_peak=[-12.5, -13.0]),
        {"category": "equalizer", "type": "levels", "origin": "equalizer",
         "data": {"available": True, "output_peak": [-12.5, -13.0]}},
    ),
    (
        # EXCLUDE_NONE: the zone_client_removed variant carries mac_id, no zone key
        MultiroomZoneChanged(action="client_removed", zone_id="z1", mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "multiroom", "type": "zone_changed", "origin": "multiroom",
         "data": {"action": "client_removed", "zone_id": "z1", "mac_id": "aa:bb:cc:dd:ee:ff"}},
    ),
    (
        MultiroomPendingClientChanged(action="removed", mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "multiroom", "type": "pending_client_changed",
         "origin": "multiroom",
         "data": {"action": "removed", "mac_id": "aa:bb:cc:dd:ee:ff"}},
    ),
    (
        RoutingMultiroomError(reason="enable_failed"),
        {"category": "routing", "type": "multiroom_error", "origin": "routing",
         "data": {"reason": "enable_failed"}},
    ),
    (
        NetworkStatusChanged(wifi_enabled=True,
                             ethernet={"connected": False, "ip_address": None},
                             wifi={"connected": True, "ssid": "Net",
                                   "ip_address": "192.168.1.2", "signal": 70,
                                   "saved_ssid": "Net"},
                             hotspot_active=False),
        {"category": "network", "type": "status_changed", "origin": "network",
         "data": {"wifi_enabled": True,
                  "ethernet": {"connected": False, "ip_address": None},
                  "wifi": {"connected": True, "connecting": False, "ssid": "Net",
                           "ip_address": "192.168.1.2", "signal": 70,
                           "saved_ssid": "Net"},
                  "hotspot_active": False}},
    ),
    (
        RadioFavoriteAdded(station_id="abc123"),
        {"category": "source", "type": "favorite_added", "origin": "radio",
         "data": {"source": "radio", "station_id": "abc123"}},
    ),
    (
        # No source field on purpose — origin falls back to the category
        ScreenSleepChanged(sleeping=True),
        {"category": "settings", "type": "screen_sleep_changed", "origin": "settings",
         "data": {"sleeping": True}},
    ),
]


@pytest.mark.parametrize(
    "event,expected", CASES,
    ids=[f"{c[0].CATEGORY}.{c[0].TYPE}" for c in CASES],
)
async def test_broadcast_envelope(state_machine, event, expected):
    await state_machine.broadcast(event)

    envelope = state_machine.ws_manager.broadcast_dict.call_args[0][0]
    assert isinstance(envelope.pop("timestamp"), float)
    assert envelope == expected


async def test_the_state_event_carries_the_state_itself(state_machine):
    """`source/state`'s `data` is the whole AudioState — the same object
    GET /api/audio/state returns — and its origin is the selected source.
    unifiedAudioStore parses `data` with the state's strict schema, and
    Milo-Mac decodes it with the same decoder as the REST read: a wrapper key
    or a missing field breaks both."""
    state_machine.system_state.active_source = AudioSource.RADIO

    await state_machine.broadcast(AudioStateChanged(**state_machine.state().model_dump()))

    envelope = state_machine.ws_manager.broadcast_dict.call_args[0][0]
    assert (envelope["category"], envelope["type"], envelope["origin"]) == (
        "source", "state", "radio",
    )
    assert envelope["data"] == state_machine.get_current_state()
    assert envelope["data"]["source"] == "radio"


async def test_broadcast_without_ws_manager_is_noop():
    sm = AudioStateMachine()
    await sm.broadcast(SourceErrorCleared(source="radio"))  # must not raise


# --------------------------------------------------------------------------- #
# Whole-surface sweep: the conventions, on every subclass.
# --------------------------------------------------------------------------- #

# CLAUDE.md § Core code rules: the nine categories, closed set.
CATEGORIES = {
    "source", "system", "routing", "equalizer", "multiroom", "volume",
    "settings", "programs", "network",
}

# (category, type) pairs served by more than one class ON PURPOSE: a union
# discriminated by a payload field, documented at its declaration site. Anything
# not listed here that collides is an accident — two producers of one wire pair
# whose payloads drift apart with nothing to catch it.
DECLARED_UNIONS = {
    # discriminated by data.source (radio | podcast)
    ("source", "favorite_added"),
    ("source", "favorite_removed"),
}

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _subclasses():
    """Every WsEvent subclass, split into concrete (has TYPE) and base-only."""
    concrete, bases = [], []
    for obj in vars(ws_events_module).values():
        if not (isinstance(obj, type) and issubclass(obj, WsEvent) and obj is not WsEvent):
            continue
        (concrete if getattr(obj, "TYPE", None) else bases).append(obj)
    return concrete, bases


CONCRETE, BASE_ONLY = _subclasses()

# The extractor must fail loudly rather than pass on an empty surface: a broken
# scan would make every parametrized test below vacuously green.
assert len(CONCRETE) > 50, f"only {len(CONCRETE)} concrete WsEvent classes found — extractor broken?"
assert BASE_ONLY, "no abstract base found — extractor broken?"

ALL_EVENTS = CONCRETE + BASE_ONLY
_IDS = [c.__name__ for c in ALL_EVENTS]


def test_category_type_pairs_are_unique():
    """Two classes on one wire pair means two payload shapes, silently diverging.

    Consumers dispatch on (category, type) alone, so a collision that is not a
    declared union is a bug: whichever producer runs second wins.
    """
    seen = {}
    for cls in CONCRETE:
        pair = (cls.CATEGORY, cls.TYPE)
        if pair in DECLARED_UNIONS:
            continue
        assert pair not in seen, (
            f"{cls.__name__} and {seen[pair]} both emit {pair}. Give one a "
            f"distinct type, or add the pair to DECLARED_UNIONS with a "
            f"discriminator documented at both declaration sites."
        )
        seen[pair] = cls.__name__


def test_declared_unions_really_collide():
    """A stale entry here would hide a real collision on that pair forever."""
    for pair in DECLARED_UNIONS:
        owners = [c.__name__ for c in CONCRETE if (c.CATEGORY, c.TYPE) == pair]
        assert len(owners) > 1, (
            f"{pair} is listed as a declared union but only {owners} emits it — "
            f"drop the entry so the uniqueness check covers it again."
        )


@pytest.mark.parametrize("cls", ALL_EVENTS, ids=_IDS)
def test_category_is_one_of_the_nine(cls):
    """The frontend WS client routes on category; an unknown one is dropped."""
    assert cls.CATEGORY in CATEGORIES, f"{cls.__name__}: unknown category {cls.CATEGORY!r}"


@pytest.mark.parametrize("cls", ALL_EVENTS, ids=_IDS)
def test_source_category_declares_a_source_field(cls):
    """`origin` falls back to CATEGORY without one, so per-source consumers
    (radioStore, podcastStore, the error banner) could not tell events apart."""
    if cls.CATEGORY != "source":
        return
    assert "source" in cls.model_fields, f"{cls.__name__}: source-category event with no `source` field"


@pytest.mark.parametrize("cls", ALL_EVENTS, ids=_IDS)
def test_no_event_carries_full_state(cls):
    """The state travels once, as `source/state`: an event that still
    declared `full_state` would be a second copy of it, drifting from the
    first with nothing to catch it."""
    assert "full_state" not in cls.model_fields, f"{cls.__name__} carries full_state"


@pytest.mark.parametrize("cls", ALL_EVENTS, ids=_IDS)
def test_fields_are_snake_case(cls):
    """A camelCase field would reach the wire as-is and break every consumer
    that reads the documented snake_case key."""
    camel = [f for f in cls.model_fields if any(c.isupper() for c in f)]
    assert not camel, f"{cls.__name__}: non-snake_case fields {camel}"


@pytest.mark.parametrize("cls", ALL_EVENTS, ids=_IDS)
def test_docstring_names_the_consumers(cls):
    """The model IS the payload documentation (module docstring). Without a
    consumer named, the next person to change a field cannot tell what breaks."""
    doc = (cls.__doc__ or "").strip()
    assert len(doc) > 20, f"{cls.__name__}: docstring must name its consumers, got {doc!r}"


_EMITTED = emitted_in_backend(BACKEND_ROOT, {cls.__name__ for cls in CONCRETE})
# Sentinels: a floor, and one class per emission form the scan must read on the
# real tree: built (SourcePosition), handed to a builder (SatelliteUpdateProgress),
# held in a factory table (MultiroomClientStateChanged). A scan that lost a form
# would turn its classes red; one that found nothing would be caught here first.
assert len(_EMITTED) >= 60, f"emission scan found only {len(_EMITTED)} classes — extractor broken?"
for _known in ("SourcePosition", "SatelliteUpdateProgress", "MultiroomClientStateChanged"):
    assert _known in _EMITTED, f"emission scan missed {_known} — extractor broken?"


@pytest.mark.parametrize("cls", CONCRETE, ids=[c.__name__ for c in CONCRETE])
def test_event_has_a_production_emission_site(cls):
    """An event class no producer builds is dead wire surface.

    Phase 3 found a whole `registry` category nobody emitted; this makes the
    next one fail at pytest time instead of living on as documentation of a
    feature that does not exist.
    """
    assert cls.__name__ in _EMITTED, (
        f"{cls.__name__} is never built outside ws_events.py (a TRIGGERS tuple, "
        f"an isinstance or an import only reads it) — delete it, or wire up the producer."
    )


def test_every_settings_config_model_is_shared_not_redeclared():
    """The `config`/`limits` payloads must come from core/models/settings_config.

    A settings category has one shape on two surfaces (`GET /api/settings/bulk`
    and its `settings/<name>_changed` event). Redeclaring it inside ws_events.py
    is what let the two drift apart before, so the model file is pinned as the
    single home.
    """
    source = inspect.getsource(ws_events_module)
    tree = ast.parse(source)
    local_models = {
        node.name for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name != "WsEvent"  # the envelope base itself, not a payload
        and any(ast.unparse(b) == "BaseModel" for b in node.bases)
    }
    assert not local_models, (
        f"{sorted(local_models)} declare a payload shape inside ws_events.py — "
        f"move it to core/models/settings_config.py and import it, so /bulk and "
        f"the event cannot describe different shapes."
    )


def test_every_phase_the_update_service_announces_is_one_the_event_carries():
    """A phase the model refuses fails inside the update service's listener,
    which logs it and drops the event: the update screen then sits on the step
    before, with nothing on the wire to say why. Read from the source, both
    the phases the install and the post-commit announce and the ones a
    restarted process rebuilds from the journal."""
    source = (REPO / "backend" / "core" / "updates" / "update.py").read_text()
    tree = ast.parse(source)
    announced = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_milo_progress"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    state = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "milo_update_state"
    )
    # Every string the function assigns to `phase`, conditional branches included.
    rebuilt = {
        constant.value
        for node in ast.walk(state)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "phase" for target in node.targets)
        for constant in ast.walk(node.value)
        if isinstance(constant, ast.Constant) and isinstance(constant.value, str)
    }
    assert len(announced) >= 5, f"the _milo_progress extraction looks broken: {announced}"
    assert {"verify", "dependencies", "restart"} <= rebuilt, (
        f"the milo_update_state extraction looks broken: {rebuilt}"
    )
    unknown = (announced | rebuilt) - set(get_args(MiloUpdatePhase))
    assert not unknown, f"phases ProgramUpdateProgress refuses: {sorted(unknown)}"
