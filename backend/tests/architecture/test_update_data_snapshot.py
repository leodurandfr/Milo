"""Structural guardrail: a Milō update keeps a copy of every versioned file.

A class that declares `SCHEMA_VERSION` owns a file its release refuses in any
other schema, and the release a rollback brings back is that refusal: it exits
at every start until the file is deleted by hand. The update copies the set
`core/updates/data_snapshot.py` declares and the guard copies it back, so a
versioned file missing from that set is a rollback that does not boot. What
breaks when this fails: the new owner's file is not restored by a rollback.

Doctrine note: every extractor asserts its own output is non-trivial first.
"""
import ast
from pathlib import Path

from backend.config import constants
from backend.config.constants import MILO_DATA_DIR
from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.data_snapshot import VERSIONED_DATA_FILES

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = BACKEND_ROOT / "dependencies.py"


def _schema_version_owners(sources: dict[str, str]) -> dict[str, str]:
    """{"module.Class": module source} for every class with a class-level
    `SCHEMA_VERSION` assignment, annotated or not."""
    owners = {}
    for module, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                targets = (
                    statement.targets if isinstance(statement, ast.Assign)
                    else [statement.target] if isinstance(statement, ast.AnnAssign) else []
                )
                if any(isinstance(t, ast.Name) and t.id == "SCHEMA_VERSION" for t in targets):
                    owners[f"{module}.{node.name}"] = source
    return owners


def _backend_sources() -> dict[str, str]:
    sources = {}
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        relative = path.relative_to(BACKEND_ROOT.parent)
        if relative.parts[1] == "tests":
            continue
        parts = relative.with_suffix("").parts
        sources[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path.read_text()
    return sources


def _spellings(path: Path) -> set[str]:
    """How code can name `path`: its literal, or a constant bound to it."""
    names = {name for name, value in vars(constants).items() if name.isupper() and value == path}
    return names | {repr(str(path)), f'"{path}"'}


def _missing(owners: dict[str, str], declared: dict[str, Path]) -> list[str]:
    return sorted(set(owners) - set(declared))


def test_every_versioned_file_is_in_the_snapshot_and_nothing_else():
    owners = _schema_version_owners(_backend_sources())
    assert len(owners) >= 5 and "backend.core.settings.SettingsService" in owners, (
        f"SCHEMA_VERSION owners not read: {sorted(owners)}"
    )

    assert _missing(owners, VERSIONED_DATA_FILES) == [], "versioned files the update does not keep"
    assert sorted(set(VERSIONED_DATA_FILES) - set(owners)) == [], (
        "the snapshot names an owner that no longer declares SCHEMA_VERSION"
    )


def test_each_declared_path_is_the_one_its_owner_names():
    """The owner's module names the path (a literal or a constant), or, for a
    store handed its path at construction, `dependencies.py` does."""
    owners = _schema_version_owners(_backend_sources())
    wiring = DEPENDENCIES.read_text()
    wrong = [
        f"{owner}: {path}"
        for owner, path in VERSIONED_DATA_FILES.items()
        if owner in owners and not any(
            spelling in owners[owner] or spelling in wiring for spelling in _spellings(path)
        )
    ]
    assert wrong == []


def test_the_snapshot_restores_inside_the_data_directory():
    """The guard runs as milo and copies each file back to its path: only
    under the data directory is that a path it may write, and two entries for
    one path would restore one over the other."""
    paths = list(VERSIONED_DATA_FILES.values())
    assert all(path.is_relative_to(MILO_DATA_DIR) for path in paths), paths
    assert len(set(paths)) == len(paths)
    assert tuple(PROGRAMS["milo"]["data_files"]) == tuple(paths)


def test_the_check_goes_red_on_an_owner_left_out():
    source = "class NewStore:\n    SCHEMA_VERSION: int = 1\n"
    owners = _schema_version_owners({"backend.sources.new.data": source})

    assert _missing(owners, VERSIONED_DATA_FILES) == ["backend.sources.new.data.NewStore"]
