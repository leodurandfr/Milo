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
RETIRED_FLAGS = ("_is_playing", "_is_buffering", "_device_connected")
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


def test_a_module_not_yet_migrated_owes_nothing():
    legacy = MIGRATED_BUT_INCOMPLETE.replace("class RadioSession(Session):\n    pass\n", "")
    assert session_violations(ast.parse(legacy)) == []
