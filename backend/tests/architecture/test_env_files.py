"""Structural guardrail: every env file the backend writes is the one a unit reads.

An env file's path is declared twice: by the Python writer (`ScreenController.
KIOSK_ENV_PATH`, `RoutingEnv.PATH`, `MacEnv.PATH`, `SnapclientEnv.PATH`) and by
the unit's `EnvironmentFile=` line in `system/`. Nothing confronted the two.
Three of the readers take the file with a leading `-`, so systemd starts the
unit without it and says nothing: a kiosk whose path drifted opens at 100 %
while the Screen setting shows 125 %, roc-recv gets empty latency arguments,
and the local snapclient falls back to the unit's own buffer.

Both sides are read from the tree, never imported: the session-wide conftest
fixture repoints the four attributes to a temporary folder before any test
runs, so an imported class would carry a path this test never ships. The
writers are parsed out of their source by `ast`, the readers out of `system/`
(the tree the image build copies verbatim), and the set of units that read
`routing.env` is derived rather than listed. The reverse direction is checked
too: a unit whose `routing.env` line drifted would simply leave the derived set
and the forward check would stay green, so every `EnvironmentFile=` a unit
reads must name a writer.

Doctrine note (same as `test_source_service_units.py`): each extractor asserts
its own output is non-trivial first, so a broken glob or a moved constant fails
loudly instead of passing on an empty surface.
"""
import ast
from pathlib import Path

import pytest

from backend.tests.architecture.test_unit_hardening import service_section

REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_ROOT = REPO_ROOT / "system"

# name -> (writer source, class, attribute, the unit that reads it), the unit
# None when the readers are derived from system/ (routing.env has one per source).
WRITERS = {
    "kiosk": ("backend/hardware/screen.py", "ScreenController", "KIOSK_ENV_PATH", "milo-kiosk.service"),
    "mac": ("backend/core/multiroom/routing.py", "MacEnv", "PATH", "milo-mac.service"),
    "snapclient": (
        "backend/core/multiroom/routing.py", "SnapclientEnv", "PATH", "milo-snapclient-multiroom.service",
    ),
    "routing": ("backend/core/multiroom/routing.py", "RoutingEnv", "PATH", None),
}

# Env files a unit reads that the backend does not write, with the reason.
WRITTEN_ELSEWHERE = {
    "/etc/environment": "the OS's own file, owned by pam_env and the base image",
}


def _writer_path(module_file: str, class_name: str, attr: str) -> str:
    """The string literal `class_name.attr` is assigned in `module_file`."""
    tree = ast.parse((REPO_ROOT / module_file).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == attr for t in stmt.targets)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    return stmt.value.value
    raise AssertionError(f"no string assignment {class_name}.{attr} in {module_file}")


def _writer_paths() -> dict[str, str]:
    return {name: _writer_path(f, cls, attr) for name, (f, cls, attr, _unit) in WRITERS.items()}


def _env_files() -> dict[str, list[str]]:
    """Every shipped unit's `EnvironmentFile=` paths, the optional `-` stripped.

    An empty assignment resets the list, as systemd reads it. Asserts its own
    floor, so no test built on it can pass on an empty surface.
    """
    readers = {}
    for unit in sorted(SYSTEM_ROOT.glob("*.service")):
        paths = []
        for value in service_section(unit).get("EnvironmentFile", []):
            paths = [*paths, value.removeprefix("-")] if value else []
        if paths:
            readers[unit.name] = paths
    lines = sum(len(paths) for paths in readers.values())
    assert lines >= 13, f"only {lines} EnvironmentFile= lines under {SYSTEM_ROOT}: the extraction is broken"
    return readers


def test_the_extraction_is_not_empty():
    """A broken glob, parser or moved constant must fail here, not pass on nothing."""
    _env_files()
    writers = _writer_paths()
    assert len(writers) == len(WRITERS)
    assert all(p.startswith("/") for p in writers.values()), writers


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_every_env_file_a_service_writes_is_the_one_its_unit_reads(name):
    """A writer whose path drifted from its unit's leaves the setting unapplied, silently."""
    path = _writer_paths()[name]
    unit = WRITERS[name][3]
    readers = _env_files()
    if unit is None:
        derived = sorted(u for u, paths in readers.items() if path in paths)
        assert derived, f"{path} ({name}) is written, but no unit under system/ reads it"
        return
    assert unit in readers and path in readers[unit], (
        f"{WRITERS[name][1]}.{WRITERS[name][2]} writes {path}, but {unit} reads "
        f"{readers.get(unit, [])}: the unit starts without it, and with a leading '-' says nothing"
    )


def test_every_env_file_a_unit_reads_has_a_writer():
    """A unit reading a file nobody writes runs on defaults, or never starts."""
    known = set(_writer_paths().values()) | set(WRITTEN_ELSEWHERE)
    orphans = {
        unit: [p for p in paths if p not in known]
        for unit, paths in _env_files().items()
        if any(p not in known for p in paths)
    }
    assert not orphans, (
        f"units reading an env file no writer produces: {orphans} -- align the unit with its "
        "writer, or list an OS-owned file in WRITTEN_ELSEWHERE with its reason"
    )
