# backend/core/updates/data_snapshot.py
"""The user data a Milō update keeps a copy of, and the copy itself.

Every file persisted through `shared/persistence.py`'s versioned API is read by
a release that takes no other `schema_version` than its own, by design (never
migrate): it sets any other aside and starts that section from its defaults. A
release that bumps one rewrites the file in the new schema on its first save, so
the release a rollback puts back would set it aside — the user's data reaching
it only as a `.bad` copy. The update therefore copies each of these files before
the checkout, and `milo-update-guard` copies them back when it restores the
previous release.

The set is declared here once, keyed by the class that owns the file, and
`tests/architecture/test_update_data_snapshot.py` holds it equal to the classes
that declare a `SCHEMA_VERSION`: a versioned file this set misses is a rollback
that boots on that file's defaults.

The return the channel offers after a release is withdrawn is not that
rollback: it is an ordinary install, the copy of the data from before the
withdrawn release was dropped when that release committed, so the release it
puts back would set aside every file the withdrawn release moved to another
schema. So a return is judged first, by the offer and again by the install
(`VersionService._schemas_crossed`): each release's own copy of this file says
which owner reads which file (`declared_data_files`), each owner's source at
that release says its `SCHEMA_VERSION` (`declared_schema_version`), and the two
releases are paired by file — never by class, which a release may move.
"""
import ast
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Optional

from backend.config.constants import (
    HARDWARE_FILE,
    MILO_DATA_DIR,
    MUSIC_LIBRARY_DATA_FILE,
    NOTICES_FILE,
    PUSH_TOKENS_FILE,
    SETTINGS_FILE,
    SPOTIFY_HISTORY_FILE,
    SPOTIFY_PROFILES_FILE,
)
from backend.core.updates.helpers import write_json_durably
from backend.core.updates.milo_journal import (
    DATA_MANIFEST_NAME,
    KEY_DATA_COPY,
    KEY_DATA_FILES,
    KEY_DATA_PATH,
)

VERSIONED_DATA_FILES: dict[str, Path] = {
    "backend.core.settings.SettingsService": SETTINGS_FILE,
    "backend.hardware.service.HardwareService": HARDWARE_FILE,
    "backend.core.push.token_registry.PushTokenRegistry": PUSH_TOKENS_FILE,
    "backend.core.equalizer.service.CamillaDSPService": MILO_DATA_DIR / "equalizer.json",
    "backend.sources.radio.data.StationDataService": MILO_DATA_DIR / "radio_data.json",
    "backend.sources.podcast.data.PodcastDataService": MILO_DATA_DIR / "podcast_data.json",
    "backend.sources.music_library.data.MusicLibraryDataService": MUSIC_LIBRARY_DATA_FILE,
    "backend.sources.spotify.profiles.SpotifyProfiles": SPOTIFY_PROFILES_FILE,
    "backend.sources.spotify.history.SpotifyHistory": SPOTIFY_HISTORY_FILE,
    "backend.core.system.notices.SetAsideNotices": NOTICES_FILE,
}


# Where a release declares the set and the paths it is built from, as git
# holds them at that release: read there, never imported, since the release a
# return goes to is not the one that runs.
SNAPSHOT_SOURCE = "backend/core/updates/data_snapshot.py"
CONSTANTS_SOURCE = "backend/config/constants.py"


def owner_source(owner: str) -> str:
    """The repo-relative source file declaring `owner`, a key of
    `VERSIONED_DATA_FILES` ("module.Class")."""
    return owner.rpartition(".")[0].replace(".", "/") + ".py"


class _Unresolvable(Exception):
    pass


def declared_data_files(snapshot_source: str, constants_source: str) -> Optional[dict[str, str]]:
    """{owner: its file's path} as `VERSIONED_DATA_FILES` reads in
    `snapshot_source` (this module, at some release), its paths resolved over
    the module-level assignments of `constants_source` and of the module itself.

    None when the set cannot be read whole — not Python, no such dict, an
    empty one, or a path built otherwise than from string literals, `Path(…)`
    and `/` over names it can resolve: an owner left unpaired would be a file
    the return check never looks at.
    """
    try:
        trees = [ast.parse(constants_source), ast.parse(snapshot_source)]
    except (SyntaxError, ValueError):
        return None
    names: dict[str, ast.expr] = {}
    declared = None
    for tree in trees:
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target, value = statement.targets[0], statement.value
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                target, value = statement.target, statement.value
            else:
                continue
            if isinstance(target, ast.Name):
                names[target.id] = value
                if tree is trees[1] and target.id == "VERSIONED_DATA_FILES":
                    declared = value

    def resolve(node: ast.expr, depth: int = 0) -> str:
        if depth > 20:
            raise _Unresolvable()
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return resolve(names[node.id], depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return str(PurePosixPath(resolve(node.left, depth + 1)) / resolve(node.right, depth + 1))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path"
                and len(node.args) == 1 and not node.keywords):
            return str(PurePosixPath(resolve(node.args[0], depth + 1)))
        raise _Unresolvable()

    if not isinstance(declared, ast.Dict) or not declared.keys:
        return None
    files = {}
    try:
        for key, value in zip(declared.keys, declared.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            files[key.value] = resolve(value)
    except _Unresolvable:
        return None
    return files


def declared_schema_version(source: str, class_name: str) -> Optional[int]:
    """The `SCHEMA_VERSION` class `class_name` declares in `source` (annotated
    or not), or None when the source holds no such class, no such integer
    assignment, or is not Python at all."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for statement in node.body:
            if isinstance(statement, ast.Assign):
                targets, value = statement.targets, statement.value
            elif isinstance(statement, ast.AnnAssign):
                targets, value = [statement.target], statement.value
            else:
                continue
            if (any(isinstance(t, ast.Name) and t.id == "SCHEMA_VERSION" for t in targets)
                    and isinstance(value, ast.Constant) and type(value.value) is int):
                return value.value
    return None


def schema_changes(
    running: Mapping[str, tuple[str, Optional[int]]],
    target: Mapping[str, tuple[str, Optional[int]]],
) -> Optional[list[str]]:
    """The running owners whose file the target release would set aside, each
    side given as {owner: (its file, the SCHEMA_VERSION it declares)}.

    Paired by file, not by owner: a release that moved a class (and bumped it)
    still reads the same file under another name. A file the target declares
    no owner for is one it never reads, so it loses nothing; a file both
    declare with a version either side could not read is None — "cannot
    tell", never "nothing changed".
    """
    by_file = {path: version for path, version in target.values()}
    changed = []
    for owner, (path, version) in running.items():
        if path not in by_file:
            continue
        if version is None or by_file[path] is None:
            return None
        if by_file[path] != version:
            changed.append(owner)
    return sorted(changed)


def take_snapshot(files: Iterable[Path], dest: Path) -> list[dict]:
    """Copy each of `files` into `dest`, then write the manifest naming where
    each copy goes back. Blocking; `dest` must not exist.

    Copies keep their mode (the Spotify files are 0600) and the directory is
    0700 for the same reason. A file that does not exist is recorded as such:
    the release that comes next may create it in its own schema, and the
    restore has to remove it then. Returns the manifest's entries.
    """
    dest.mkdir(parents=True, mode=0o700)
    entries = []
    for index, path in enumerate(Path(p) for p in files):
        copy = None
        if path.is_file():
            copy = f"{index:02d}-{path.name}"
            shutil.copy2(path, dest / copy)
            with open(dest / copy, "rb") as f:
                os.fsync(f.fileno())
        entries.append({KEY_DATA_PATH: str(path), KEY_DATA_COPY: copy})
    write_json_durably(dest / DATA_MANIFEST_NAME, {KEY_DATA_FILES: entries})
    return entries
