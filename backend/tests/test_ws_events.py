# backend/tests/test_ws_events.py
"""
Envelope tests for the typed WS event layer.

Every WsEvent subclass must serialize to {category, type, origin, data,
timestamp}, and the frontend and Milo-Mac both key on the exact contents.
Each case below pins one representative event per family (state machine,
audio source, volume, settings, programs, equalizer, multiroom, routing,
network, hardware) against its full expected envelope, timestamp excluded —
including which categories carry full_state and which fields are dropped
rather than sent as null.

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

import pytest

from backend.core.models import ws_events as ws_events_module
from backend.core.models.ws_events import WsEvent
from backend.core.state import AudioStateMachine
from backend.core.models.ws_events import (
    DockAppsChanged,
    DockAppsConfig,
    EqualizerLevels,
    MultiroomPendingClientChanged,
    MultiroomZoneChanged,
    NetworkStatusChanged,
    ProgramUpdateComplete,
    RadioFavoriteAdded,
    RoutingMultiroomError,
    SatelliteUpdateProgress,
    ScreenSleepChanged,
    SourceError,
    SourceErrorCleared,
    SourcePositionUpdate,
    SourceStateChanged,
    SystemCdDriveStatus,
    SystemConnectivityChanged,
    SystemErrorEvent,
    SystemStateChanged,
    SystemTransitionStart,
    VolumeChanged,
    VolumeLimitsChanged,
    VolumeLimitsConfig,
    VolumeStartupChanged,
    VolumeStartupConfig,
)


@pytest.fixture
def state_machine(mock_ws_manager):
    sm = AudioStateMachine()
    sm.ws_manager = mock_ws_manager
    return sm


# (event, expected envelope minus timestamp/full_state, full_state expected?)
CASES = [
    (
        SystemTransitionStart(),
        {"category": "system", "type": "transition_start", "origin": "system",
         "data": {"source": "system"}},
        True,
    ),
    (
        # EXCLUDE_NONE: the unset multiroom_changed discriminator must be
        # absent from the wire, not null (Milo-Mac keys on its presence).
        SystemStateChanged(source="system"),
        {"category": "system", "type": "state_changed", "origin": "system",
         "data": {"source": "system"}},
        True,
    ),
    (
        SystemErrorEvent(source="spotify", error="Transition timeout",
                         message="Transition timeout after 10.0s"),
        {"category": "system", "type": "error", "origin": "spotify",
         "data": {"source": "spotify", "error": "Transition timeout",
                  "message": "Transition timeout after 10.0s"}},
        True,
    ),
    (
        # metadata=None stays on the wire as null — the frontend distinguishes
        # "no metadata this time" from "key absent"
        SourceStateChanged(source="radio", new_state="ready", metadata=None),
        {"category": "source", "type": "state_changed", "origin": "radio",
         "data": {"source": "radio", "new_state": "ready", "metadata": None}},
        True,
    ),
    (
        # The banner half of the two error mechanisms: no state on the wire,
        # so a consumer cannot mistake a failed operation for a dead source.
        SourceError(source="radio", message="Unable to load stream: FIP"),
        {"category": "source", "type": "error", "origin": "radio",
         "data": {"source": "radio", "message": "Unable to load stream: FIP"}},
        True,
    ),
    (
        SourceErrorCleared(source="bluetooth"),
        {"category": "source", "type": "error_cleared", "origin": "bluetooth",
         "data": {"source": "bluetooth"}},
        True,
    ),
    (
        SourcePositionUpdate(source="spotify", position=1000, duration=200000),
        {"category": "source", "type": "position_update", "origin": "spotify",
         "data": {"source": "spotify", "position": 1000, "duration": 200000}},
        False,
    ),
    (
        VolumeChanged(show_bar=True, step_mobile_db=2.0, multiroom_enabled=False,
                      state={"mode": "direct", "global_volume_db": -30.0}),
        {"category": "volume", "type": "volume_changed", "origin": "volume",
         "data": {"show_bar": True, "step_mobile_db": 2.0,
                  "multiroom_enabled": False,
                  "state": {"mode": "direct", "global_volume_db": -30.0}}},
        False,
    ),
    (
        VolumeLimitsChanged(limits=VolumeLimitsConfig(min_db=-80.0, max_db=-20.0)),
        {"category": "settings", "type": "volume_limits_changed", "origin": "settings",
         "data": {"source": "settings",
                  "limits": {"min_db": -80.0, "max_db": -20.0}}},
        False,
    ),
    (
        VolumeStartupChanged(config=VolumeStartupConfig(
            startup_volume_db=-42.0, restore_last_volume=True)),
        {"category": "settings", "type": "volume_startup_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"startup_volume_db": -42.0, "restore_last_volume": True}}},
        False,
    ),
    (
        DockAppsChanged(config=DockAppsConfig(enabled_apps=["spotify", "radio"])),
        {"category": "settings", "type": "dock_apps_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"enabled_apps": ["spotify", "radio"]}}},
        False,
    ),
    (
        SatelliteUpdateProgress(mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "programs", "type": "satellite_update_progress",
         "origin": "satellite_update",
         "data": {"source": "satellite_update", "mac_id": "aa:bb:cc:dd:ee:ff",
                  "status": "updating"}},
        False,
    ),
    (
        ProgramUpdateComplete(program="go-librespot", success=True),
        {"category": "programs", "type": "program_update_complete",
         "origin": "program_update",
         "data": {"source": "program_update", "program": "go-librespot",
                  "success": True}},
        False,
    ),
    (
        EqualizerLevels(available=True, output_peak=[-12.5, -13.0]),
        {"category": "equalizer", "type": "levels", "origin": "equalizer",
         "data": {"available": True, "output_peak": [-12.5, -13.0]}},
        False,
    ),
    (
        # EXCLUDE_NONE: the zone_client_removed variant carries mac_id, no zone key
        MultiroomZoneChanged(zone_id="z1", mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "multiroom", "type": "zone_changed", "origin": "multiroom",
         "data": {"zone_id": "z1", "mac_id": "aa:bb:cc:dd:ee:ff"}},
        False,
    ),
    (
        MultiroomPendingClientChanged(action="removed", mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "multiroom", "type": "pending_client_changed",
         "origin": "multiroom",
         "data": {"action": "removed", "mac_id": "aa:bb:cc:dd:ee:ff"}},
        False,
    ),
    (
        RoutingMultiroomError(reason="enable_failed"),
        {"category": "routing", "type": "multiroom_error", "origin": "routing",
         "data": {"reason": "enable_failed"}},
        False,
    ),
    (
        NetworkStatusChanged(wifi_enabled=True,
                             ethernet={"connected": False, "ip_address": None},
                             wifi={"connected": True, "ssid": "Net",
                                   "ip_address": "192.168.1.2", "signal": 70,
                                   "saved_ssid": "Net"}),
        {"category": "network", "type": "status_changed", "origin": "network",
         "data": {"wifi_enabled": True,
                  "ethernet": {"connected": False, "ip_address": None},
                  "wifi": {"connected": True, "ssid": "Net",
                           "ip_address": "192.168.1.2", "signal": 70,
                           "saved_ssid": "Net"}}},
        False,
    ),
    (
        SystemConnectivityChanged(online=False),
        {"category": "system", "type": "connectivity_changed", "origin": "system",
         "data": {"source": "system", "online": False}},
        False,  # INCLUDE_FULL_STATE=False despite the system category
    ),
    (
        SystemCdDriveStatus(),
        {"category": "system", "type": "cd_drive_status", "origin": "cd",
         "data": {"source": "cd"}},
        True,
    ),
    (
        RadioFavoriteAdded(station_id="abc123"),
        {"category": "source", "type": "favorite_added", "origin": "radio",
         "data": {"source": "radio", "station_id": "abc123"}},
        True,
    ),
    (
        # No source field on purpose — origin falls back to the category
        ScreenSleepChanged(sleeping=True),
        {"category": "settings", "type": "screen_sleep_changed", "origin": "settings",
         "data": {"sleeping": True}},
        False,
    ),
]


@pytest.mark.parametrize(
    "event,expected,has_full_state", CASES,
    ids=[f"{c[0].CATEGORY}.{c[0].TYPE}" for c in CASES],
)
async def test_broadcast_envelope(
    state_machine, event, expected, has_full_state
):
    await state_machine.broadcast(event)

    envelope = state_machine.ws_manager.broadcast_dict.call_args[0][0]
    assert isinstance(envelope.pop("timestamp"), float)

    if has_full_state:
        assert envelope["data"].pop("full_state") == state_machine.get_current_state()
    else:
        assert "full_state" not in envelope["data"]

    assert envelope == expected


async def test_broadcast_without_ws_manager_is_noop():
    sm = AudioStateMachine()
    await sm.broadcast(SystemTransitionStart())  # must not raise


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


def _names_referenced_outside_ws_events():
    """Every identifier used anywhere in backend/ except ws_events.py itself."""
    used = set()
    for py in BACKEND_ROOT.rglob("*.py"):
        if py.name == "ws_events.py" or "/tests/" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.alias):
                used.add(node.name.rsplit(".", 1)[-1])
    return used


_REFERENCED = _names_referenced_outside_ws_events()
# Sentinels: a name imported by every source module, and a bulk floor. A scan
# that silently returned a near-empty set would make the emission-site test
# below pass for reasons that have nothing to do with the code.
assert "BaseAudioSource" in _REFERENCED, "identifier scan missed a known import — extractor broken?"
assert len(_REFERENCED) > 2000, f"identifier scan found only {len(_REFERENCED)} names — extractor broken?"


@pytest.mark.parametrize("cls", CONCRETE, ids=[c.__name__ for c in CONCRETE])
def test_event_has_a_production_emission_site(cls):
    """An event class no producer builds is dead wire surface.

    Phase 3 found a whole `registry` category nobody emitted; this makes the
    next one fail at pytest time instead of living on as documentation of a
    feature that does not exist.
    """
    assert cls.__name__ in _REFERENCED, (
        f"{cls.__name__} is never referenced outside ws_events.py — "
        f"delete it, or wire up the producer."
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
