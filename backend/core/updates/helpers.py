# backend/core/updates/helpers.py
"""
Shared utilities for the update services: versions, requirements, durable writes.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# A stable release tag: "v0.2.0". A pre-release carries a suffix — "v0.2.0-rc1",
# "v0.3.0-beta.2" — and is deliberately not one.
STABLE_RELEASE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+$")


def is_stable_release(tag: Optional[str]) -> bool:
    """Whether a tag names a release the fleet may be moved to.

    A pre-release exists to be installed on a unit somebody is watching, by
    somebody who chose it — never to arrive on an appliance in a living room
    because a version number went up. GitHub's `releases/latest` already
    excludes pre-releases, and the publish step marks them as such; this is what
    makes the exclusion **Milō's own property** rather than a behaviour of an
    endpoint. Two spellings of the same guarantee, because the one that costs
    nothing is the one that still holds when the other is changed by accident.

    Anything that is not a plain `X.Y.Z` is refused, pre-release or not: the
    offer's whole contract is that what it names can be checked out and has a
    published frontend beside it, and a tag shape nobody planned for has
    neither.
    """
    return bool(tag and STABLE_RELEASE_TAG_RE.match(tag))


def compare_versions(current: Optional[str], latest: Optional[str]) -> bool:
    """Compares two semver versions (returns True if update available)."""
    if not current or not latest:
        return False

    try:
        def parse_version(version_str):
            clean_version = re.sub(r'[^\d.]', '', version_str)
            parts = clean_version.split('.')
            while len(parts) < 3:
                parts.append('0')
            return [int(p) for p in parts[:3]]

        current_parts = parse_version(current)
        latest_parts = parse_version(latest)

        for i in range(3):
            if latest_parts[i] > current_parts[i]:
                return True
            elif latest_parts[i] < current_parts[i]:
                return False

        return False

    except Exception as e:
        logger.warning(f"Version comparison failed ({current!r} vs {latest!r}): {e}")
        return False


def requirements_moved_up(before: str, after: str) -> bool:
    """Whether the requirements file `after` adds a line to `before` or changes one.

    `pip install -r` never uninstalls, so a release that only drops a line
    needs no pip at all, and neither does one that only edits a comment. A
    changed line counts whichever way its floor moved: a lowered floor is the
    one case where pip then runs for nothing, and telling it from a raise would
    mean parsing specifiers to save a venv copy on a rare release.
    """
    def requirements(text: str) -> set[str]:
        return {re.sub(r"\s+", "", line.split("#", 1)[0]) for line in text.splitlines()} - {""}

    return bool(requirements(after) - requirements(before))


def fsync_directory(directory: Path) -> None:
    """Make a rename or an unlink inside `directory` survive a power cut."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_durably(path: Path, data: dict) -> None:
    """Replace `path` with `data`: a reader, or a power cut, sees the old file
    or the new one, never half of either. Blocking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_directory(path.parent)
