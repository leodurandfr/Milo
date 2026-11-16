# backend/core/updates/data_snapshot.py
"""The user data a Milō update keeps a copy of, and the copy itself.

Every file persisted through `shared/persistence.py`'s versioned API is read by
a release that refuses any other `schema_version` than its own, by design
(fail loud + reset, never migrate). A release that bumps one rewrites the file
in the new schema on its first save, so the release a rollback puts back would
refuse it and exit at every start. The update therefore copies each of these
files before the checkout, and `milo-update-guard` copies them back when it
restores the previous release.

The set is declared here once, keyed by the class that owns the file, and
`tests/architecture/test_update_data_snapshot.py` holds it equal to the classes
that declare a `SCHEMA_VERSION`: a versioned file this set misses is a rollback
that boots into a schema mismatch.
"""
import os
import shutil
from pathlib import Path
from typing import Iterable

from backend.config.constants import (
    HARDWARE_FILE,
    MILO_DATA_DIR,
    MUSIC_LIBRARY_DATA_FILE,
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
}


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
