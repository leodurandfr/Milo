# backend/core/updates/helpers.py
"""
Shared version utilities for update services.
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# A checkout sitting exactly on a release tag. `git describe --tags --always`
# appends "-<n>-g<sha>" the moment HEAD is past the tag, and prints a bare sha
# when no tag is reachable at all — both are trees no release names.
RELEASE_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+$")


def release_tag(described: Optional[str]) -> Optional[str]:
    """The release tag a `git describe --tags --always` output names, or None.

    None is not "unknown": it is "this tree is outside the release channel".
    The update installs a tag, so a tree that is not at one has nothing to move
    between — and a satellite pushed from such a tree carries no release either.
    """
    described = (described or "").strip()
    return described if RELEASE_TAG_RE.match(described) else None


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
