"""A source on the session model declares its policies and owns no playback flag.

Arms as each source migrates (docs: source architecture, phases 1-4): a module
that defines a `Session` subclass has moved its playback truth onto that
session, so it must say how a pause ends it (IDLE_POLICY), what a multiroom
toggle does to it (REROUTE), what survives it (RESUME_POLICY) and whether a
daemon holds it (SESSION_DAEMON) — and it may no longer write the booleans the
session replaces, which is where two writers used to disagree.
"""
import ast
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[2] / "sources"
POLICIES = ("IDLE_POLICY", "REROUTE", "RESUME_POLICY", "SESSION_DAEMON")
# The booleans the session replaces, and the session itself: a source opens
# and ends it through open_session()/end_session(), never by assignment.
RETIRED_FLAGS = ("_is_playing", "_is_buffering", "_device_connected", "_session")
SOURCE_BASES = ("BaseAudioSource", "MpvAudioSource")


def _base_names(cls: ast.ClassDef):
    for base in cls.bases:
        if isinstance(base, ast.Name):
            yield base.id
        elif isinstance(base, ast.Attribute):
            yield base.attr


def session_violations(tree: ast.Module) -> list[str]:
    """What a module on the session model still owes, as readable lines."""
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if not any("Session" in _base_names(c) for c in classes):
        return []
    problems = []
    for cls in (c for c in classes if set(_base_names(c)) & set(SOURCE_BASES)):
        declared = {
            t.id for n in cls.body if isinstance(n, (ast.Assign, ast.AnnAssign))
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            if isinstance(t, ast.Name)
        }
        problems += [f"{cls.name} does not declare {p}" for p in POLICIES if p not in declared]
    for node in ast.walk(tree):
        for t in _written(node):
            if (isinstance(t, ast.Attribute) and t.attr in RETIRED_FLAGS
                    and isinstance(t.value, ast.Name) and t.value.id == "self"):
                problems.append(f"line {t.lineno} writes self.{t.attr}")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant) and node.args[1].value in RETIRED_FLAGS):
            problems.append(f"line {node.lineno} writes self.{node.args[1].value}")
    return problems


def _written(node):
    """Every target `node` assigns, tuples, lists and starred names unpacked."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
    else:
        targets = []
    while targets:
        t = targets.pop()
        if isinstance(t, (ast.Tuple, ast.List)):
            targets += t.elts
        elif isinstance(t, ast.Starred):
            targets.append(t.value)
        else:
            yield t


def _source_modules():
    return sorted(SOURCES.glob("*/source.py"))


def test_the_scan_reaches_every_source_class():
    """Non-trivial first: ten modules, each with its source class."""
    modules = _source_modules()
    assert len(modules) == 10
    for path in modules:
        tree = ast.parse(path.read_text())
        assert any(
            set(_base_names(c)) & set(SOURCE_BASES)
            for c in tree.body if isinstance(c, ast.ClassDef)
        ), f"no source class found in {path}"


@pytest.mark.parametrize("path", _source_modules(), ids=lambda p: p.parent.name)
def test_a_source_on_the_session_model_is_complete(path):
    assert session_violations(ast.parse(path.read_text())) == []


MIGRATED_BUT_INCOMPLETE = '''
from backend.core.models.session import Session
from backend.shared.mpv_audio_source import MpvAudioSource

class RadioSession(Session):
    pass

class RadioSource(MpvAudioSource):
    IDLE_POLICY = None
    def _on_tick(self):
        self._is_playing = True
'''


def test_the_rule_bites_on_a_migrated_module():
    problems = session_violations(ast.parse(MIGRATED_BUT_INCOMPLETE))
    assert "RadioSource does not declare REROUTE" in problems
    assert "RadioSource does not declare IDLE_POLICY" not in problems
    assert any("writes self._is_playing" in p for p in problems)


def test_the_rule_bites_on_a_session_assigned_by_hand():
    by_hand = MIGRATED_BUT_INCOMPLETE.replace("self._is_playing = True", "self._session = None")
    assert any("writes self._session" in p for p in session_violations(ast.parse(by_hand)))


def test_a_module_not_yet_migrated_owes_nothing():
    legacy = MIGRATED_BUT_INCOMPLETE.replace("class RadioSession(Session):\n    pass\n", "")
    assert session_violations(ast.parse(legacy)) == []


# === What a migrated source declares, read off the class ===

def _migrated_classes():
    """Every source class whose module is on the session model, imported."""
    import importlib
    found = []
    for path in _source_modules():
        tree = ast.parse(path.read_text())
        if not session_violations_scope(tree):
            continue
        module = importlib.import_module(f"backend.sources.{path.parent.name}.source")
        for cls in (c for c in tree.body if isinstance(c, ast.ClassDef)):
            if set(_base_names(cls)) & set(SOURCE_BASES):
                found.append(getattr(module, cls.name))
    return found


def session_violations_scope(tree: ast.Module) -> bool:
    """Whether a module is on the session model (defines a Session subclass)."""
    return any(
        "Session" in name or name.endswith("Session")
        for c in tree.body if isinstance(c, ast.ClassDef)
        for name in _base_names(c)
    )


def test_migrated_sources_are_found():
    """Non-trivial first: phase 1 migrated Radio, Podcast and Music Library,
    phase 2 the CD, phase 3a AirPlay, phase 3b Spotify and Tidal, phase 3c
    Qobuz, phase 3d Mac, phase 4 Bluetooth — every source."""
    names = {cls.__name__ for cls in _migrated_classes()}
    assert {
        "RadioSource", "PodcastSource", "MusicLibrarySource", "CdSource", "AirPlaySource",
        "SpotifySource", "TidalSource", "QobuzSource", "MacSource", "BluetoothSource",
    } <= names


@pytest.mark.parametrize("cls", _migrated_classes(), ids=lambda c: c.__name__)
def test_every_end_reason_is_decided(cls):
    """A session ends for a named reason, and the source's resume policy says,
    for each reason, whether what it played is kept or forgotten — never both,
    never neither (an undecided reason would keep a stale point by accident)."""
    from backend.core.models.session import EndReason
    policy = cls.RESUME_POLICY
    assert policy is not None
    assert not policy.capture_on & policy.forget_on
    assert set(EndReason) - (policy.capture_on | policy.forget_on) == set()


@pytest.mark.parametrize("cls", _migrated_classes(), ids=lambda c: c.__name__)
def test_every_command_declares_its_scope(cls):
    """The scope decides whether a command runs with no session; a command
    without one would silently skip the refusal the base applies."""
    assert set(cls.COMMAND_SCOPES) == set(cls.COMMANDS)


CALLBACK_POSTS = ("_post", "_post_feed", "_post_result", "_submit")
# What builds or queues a mailbox message: a method handed to one of these
# runs inside the actor, which is where it belongs.
MAILBOX = ("Result",) + CALLBACK_POSTS
# Methods handed to another component that are not callbacks into the
# source's state, each with its reason.
NOT_CALLBACKS = {
    "get_navidrome_client": "NetworkShareService's catalog client, a provider that owns no source state",
    "invalidate_album_cache": "NetworkShareService's album-cache reset, which clears no source state",
    "_initialize_once": "Music Library's boot init, run as a task under asyncio.shield so a "
                        "selection's timeout cannot cut it: the startup path, not a callback",
}
# What only the actor may call: publishing, the session's lifecycle, the
# feed's handler and the error banner.
ACTOR_ONLY = ("_publish", "open_session", "end_session", "reconcile", "_handle_feed", "broadcast_error")


def _self_call(node) -> str | None:
    """`m` for a call `self.m(...)`, else None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
        return node.func.attr
    return None


def _is_mailbox(node) -> bool:
    func = node.func if isinstance(node, ast.Call) else None
    return (isinstance(func, ast.Name) and func.id in MAILBOX) or (
        isinstance(func, ast.Attribute) and func.attr in MAILBOX
    )


def _outside_the_mailbox(node):
    """Every node under `node`, skipping what is handed to a mailbox message:
    a lambda posted as a Result runs in the actor, and may publish."""
    yield node
    if _is_mailbox(node):
        return
    for child in ast.iter_child_nodes(node):
        yield from _outside_the_mailbox(child)


def _methods(tree: ast.Module) -> dict:
    """The module's methods, properties left out: `self.p` reads a value."""
    return {
        f.name: f for c in tree.body if isinstance(c, ast.ClassDef)
        for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not any(isinstance(d, ast.Name) and d.id == "property" for d in f.decorator_list)
    }


def handed_callbacks(tree: ast.Module) -> set[str]:
    """The module's methods that run on someone else's task, NOT_CALLBACKS aside."""
    return handed_out(tree) - set(NOT_CALLBACKS)


def handed_out(tree: ast.Module) -> set[str]:
    """The module's methods handed to another component: a bound method passed
    as an argument (a listener, a keyword `on_*=`, a constructor's
    positional), a lambda passed as one that calls a method, or a coroutine
    started as a raw task. What a mailbox message carries is not counted."""
    methods = _methods(tree)
    handed = set()
    for node in _outside_the_mailbox(tree):
        if not isinstance(node, ast.Call) or _is_mailbox(node):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "create_task") or (
                isinstance(func, ast.Name) and func.id == "create_task"):
            handed |= {_self_call(a) for a in node.args} & set(methods)
        for value in node.args + [kw.value for kw in node.keywords]:
            if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                    and value.value.id == "self" and value.attr in methods):
                handed.add(value.attr)
            elif isinstance(value, ast.Lambda):
                handed |= {_self_call(n) for n in ast.walk(value.body)} & set(methods)
    return handed


def callbacks_that_touch_state(tree: ast.Module) -> list[str]:
    """Handed callbacks (`handed_callbacks`) that touch the source from the
    task they run on. Read with every `self._x(...)` they reach in the module,
    short of what they post: each one must post, and none may reach what only
    the actor calls (ACTOR_ONLY)."""
    methods = _methods(tree)
    problems = []
    for name in sorted(handed_callbacks(tree)):
        reached, todo, calls = set(), [name], set()
        while todo:
            current = todo.pop()
            if current in reached:
                continue
            reached.add(current)
            for node in _outside_the_mailbox(methods[current]):
                called = _self_call(node)
                if called is None:
                    continue
                calls.add(called)
                if called in methods:
                    todo.append(called)
        touched = sorted(c for c in calls if c.startswith(ACTOR_ONLY))
        if touched:
            problems.append(f"{name} is a callback and calls {', '.join(touched)}")
        elif not calls & set(CALLBACK_POSTS):
            problems.append(f"{name} is a callback and does not post")
    return problems


@pytest.mark.parametrize("path", [
    p for p in _source_modules() if session_violations_scope(ast.parse(p.read_text()))
] + [SOURCES.parent / "shared" / "mpv_audio_source.py"], ids=lambda p: p.parent.name)
def test_callbacks_post_to_the_mailbox(path):
    assert callbacks_that_touch_state(ast.parse(path.read_text())) == []


def test_the_callback_extractor_finds_the_handed_callbacks():
    """Non-trivial first: a listener handed by position (Music Library's
    storages, the volume listener of AirPlay and Spotify) is a callback as
    much as an `on_*=` keyword, and each named exemption is still handed out
    somewhere — one that is not is a comment, not an exemption."""
    handed = {
        path.parent.name: handed_callbacks(ast.parse(path.read_text()))
        for path in _source_modules()
    }
    assert "broadcast_storages" in handed["music_library"]
    assert "on_volume_changed" in handed["airplay"]
    assert "on_volume_changed" in handed["spotify"]
    assert "_monitor_logs" in handed["spotify"]
    exempted = {
        name for path in _source_modules()
        for name in handed_out(ast.parse(path.read_text()))
    }
    assert set(NOT_CALLBACKS) <= exempted


def test_the_callback_rule_bites():
    drifted = """
class RadioSource(MpvAudioSource):
    def _do_start(self):
        self._shazam = Shazam(on_track_changed=self._on_track)
    async def _on_track(self, track):
        self._publish()
"""
    assert callbacks_that_touch_state(ast.parse(drifted)) == ["_on_track is a callback and calls _publish"]


def test_the_callback_rule_bites_on_a_positional_handoff():
    drifted = """
class MusicLibrarySource(MpvAudioSource):
    def __init__(self):
        self._shares = NetworkShareService(self.broadcast_storages)
    async def broadcast_storages(self, entries):
        self._storages = entries
"""
    assert callbacks_that_touch_state(ast.parse(drifted)) == [
        "broadcast_storages is a callback and does not post"
    ]


def test_the_callback_rule_bites_on_a_lambda():
    drifted = """
class AirPlaySource(BaseAudioSource):
    def set_volume_service(self, volume_service):
        volume_service.add_state_listener(lambda level, muted: self._follow(level))
    def _follow(self, level):
        self._publish_changes()
"""
    assert callbacks_that_touch_state(ast.parse(drifted)) == [
        "_follow is a callback and calls _publish_changes"
    ]


def test_the_callback_rule_bites_on_a_raw_task():
    """Followed through the methods it calls: the reader loop itself only
    parses, the banner is one call further down."""
    drifted = """
class SpotifySource(BaseAudioSource):
    def _start_log_monitor(self):
        self._task = asyncio.create_task(self._monitor_logs())
    async def _monitor_logs(self):
        async for line in follow_unit("milo-spotify"):
            await self._handle_log_line(line)
    async def _handle_log_line(self, line):
        self.broadcast_error("track_load_failed")
"""
    assert callbacks_that_touch_state(ast.parse(drifted)) == [
        "_monitor_logs is a callback and calls broadcast_error"
    ]


def test_the_callback_rule_bites_on_a_publish_before_the_post():
    drifted = """
class TidalSource(BaseAudioSource):
    def _do_start(self):
        self._socket = ControllerSocket(on_event=self._on_event)
    def _on_event(self, event):
        self._publish()
        self._post_feed(event)
"""
    assert callbacks_that_touch_state(ast.parse(drifted)) == [
        "_on_event is a callback and calls _publish"
    ]


def test_what_a_mailbox_message_carries_is_not_a_callback():
    """The right shape stays green: a lambda posted as a Result runs in the
    actor, and may publish from there."""
    posted = """
class SpotifySource(BaseAudioSource):
    def _start_log_monitor(self):
        self._task = asyncio.create_task(self._monitor_logs())
    async def _monitor_logs(self):
        async for line in follow_unit("milo-spotify"):
            self._post_result(lambda line=line: self._handle_log_line(line))
    async def _handle_log_line(self, line):
        self.broadcast_error("track_load_failed")
"""
    assert callbacks_that_touch_state(ast.parse(posted)) == []


@pytest.mark.parametrize("write", [
    "self._session, self._resume = None, None",
    "[self._session, self._resume] = None, None",
    "first, *self._session = []",
    "for self._session in sessions: pass",
    "with opened() as self._session: pass",
    'setattr(self, "_session", None)',
])
def test_the_rule_bites_on_a_session_unpacked_by_hand(write):
    by_hand = MIGRATED_BUT_INCOMPLETE.replace("self._is_playing = True", write)
    assert any("writes self._session" in p for p in session_violations(ast.parse(by_hand)))
