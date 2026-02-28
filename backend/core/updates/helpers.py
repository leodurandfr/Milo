# backend/core/updates/helpers.py
"""
Shared version utilities for update services.
"""
import re
from typing import Optional


def extract_base_tag(version: Optional[str]) -> Optional[str]:
    """Extracts the base tag from a git describe output.

    'v0.0.1-347-g14ee633' -> 'v0.0.1'
    'v0.0.1' -> 'v0.0.1'
    """
    if not version:
        return None
    # git describe format: <tag>-<N>-g<hash> or just <tag>
    parts = version.rsplit("-", 2)
    if len(parts) == 3 and parts[2].startswith("g"):
        return parts[0]
    return version


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

    except Exception:
        return False
