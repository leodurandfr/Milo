# backend/core/updates/helpers.py
"""
Shared version utilities for update services.
"""
import logging
import re
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
