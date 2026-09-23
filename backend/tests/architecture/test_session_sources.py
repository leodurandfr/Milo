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
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign))
            else []
        )
        for t in targets:
            if (isinstance(t, ast.Attribute) and t.attr in RETIRED_FLAGS
                    and isinstance(t.value, ast.Name) and t.value.id == "self"):
                problems.append(f"line {t.lineno} writes self.{t.attr}")
    return problems


def _source_modules():
    return sorted(SOURCES.glob("*/source.py"))


def test_the_scan_reaches_every_source_class():
    """Non-trivial first: eleven modules, each with its source class."""
    modules = _source_modules()
    assert len(modules) == 11
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
    """Non-trivial first: phase 1 migrated Radio, Podcast and Music Library."""
    names = {cls.__name__ for cls in _migrated_classes()}
    assert {"RadioSource", "PodcastSource", "MusicLibrarySource"} <= names


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


def callbacks_that_touch_state(tree: ast.Module) -> list[str]:
    """Methods handed to another component as a callback (`on_*=self._m`, or
    `.subscribe(self._m)`) whose body never posts to the mailbox: they run on
    someone else's task and would touch the source from there."""
    methods = {
        f.name: f for c in tree.body if isinstance(c, ast.ClassDef)
        for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    handed = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        values = [kw.value for kw in node.keywords if kw.arg and kw.arg.startswith("on_")]
        if isinstance(node.func, ast.Attribute) and node.func.attr == "subscribe":
            values += node.args
        for value in values:
            if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                    and value.value.id == "self"):
                handed.add(value.attr)
    problems = []
    for name in sorted(handed):
        body = methods.get(name)
        if body is None:
            continue
        posts = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in CALLBACK_POSTS
            for n in ast.walk(body)
        )
        if not posts:
            problems.append(f"{name} is a callback and does not post")
    return problems


@pytest.mark.parametrize("path", [
    p for p in _source_modules() if session_violations_scope(ast.parse(p.read_text()))
] + [SOURCES.parent / "shared" / "mpv_audio_source.py"], ids=lambda p: p.parent.name)
def test_callbacks_post_to_the_mailbox(path):
    assert callbacks_that_touch_state(ast.parse(path.read_text())) == []


def test_the_callback_rule_bites():
    drifted = """
class RadioSource(MpvAudioSource):
    def _do_start(self):
        self._shazam = Shazam(on_track_changed=self._on_track)
    async def _on_track(self, track):
        self._update_connection_state()
"""
    assert callbacks_that_touch_state(ast.parse(drifted)) == ["_on_track is a callback and does not post"]
