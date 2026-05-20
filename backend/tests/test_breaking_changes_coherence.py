"""Coherence test between SCHEMA_VERSION constants and BREAKING_CHANGES.md.

For each module that exposes a SCHEMA_VERSION (settings, equalizer, hardware,
podcast), this test asserts that BREAKING_CHANGES.md has an entry for the
current version. Prevents a silent SCHEMA_VERSION bump that forgets to
document what changed and what user state is reset on boot.

The protocol is "no migration code, fail-loud on mismatch, document the
reset in BREAKING_CHANGES.md" (see CLAUDE.md §"Persisted-data schema bumps").
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BREAKING_CHANGES_PATH = REPO_ROOT / "BREAKING_CHANGES.md"

# (module label, source file declaring SCHEMA_VERSION, persisted JSON basename).
# The JSON basename matches the heading format used in BREAKING_CHANGES.md
# (`## YYYY-MM-DD — <basename>.json schema_version ... -> N`).
TRACKED_MODULES = [
    ("equalizer", "backend/core/equalizer/service.py", "equalizer"),
    ("settings", "backend/core/settings.py", "settings"),
    ("hardware", "backend/hardware/service.py", "hardware"),
    ("podcast", "backend/sources/podcast/data.py", "podcast_data"),
]


def _read_schema_version(file_path: Path) -> int:
    """Extract the integer value of the SCHEMA_VERSION class attribute."""
    content = file_path.read_text()
    match = re.search(r"^\s*SCHEMA_VERSION\s*:\s*int\s*=\s*(\d+)", content, re.MULTILINE)
    if not match:
        match = re.search(r"^\s*SCHEMA_VERSION\s*=\s*(\d+)", content, re.MULTILINE)
    if not match:
        pytest.fail(f"SCHEMA_VERSION not found in {file_path}")
    return int(match.group(1))


def _read_documented_versions(content: str, json_basename: str) -> set[int]:
    """Return the set of versions documented for <json_basename>.json.

    Recognised heading formats (case-insensitive on the filename):
        ## YYYY-MM-DD - <basename>.json schema_version -> 2
        ## YYYY-MM-DD - <basename>.json schema_version 1 -> 2
        ## YYYY-MM-DD - <basename>.json schema_version A -> B

    The dash before the filename can be either an ASCII '-' or an em dash '—';
    the arrow can be '->', '→', or 'to' — accept any whitespace around.
    """
    pattern = (
        rf"^##\s+\S+\s+[-—]\s+{re.escape(json_basename)}\.json\s+schema_version\s*"
        rf"(?:\d+\s*)?(?:->|→|to)\s*(\d+)"
    )
    return {int(m) for m in re.findall(pattern, content, re.MULTILINE | re.IGNORECASE)}


def test_breaking_changes_md_exists():
    """BREAKING_CHANGES.md at repo root is the contract for schema bumps."""
    assert BREAKING_CHANGES_PATH.exists(), (
        f"{BREAKING_CHANGES_PATH} missing. RFC 19 requires it at the repo root."
    )


@pytest.mark.parametrize(
    "module_label,source_file,json_basename",
    TRACKED_MODULES,
    ids=[m[0] for m in TRACKED_MODULES],
)
def test_schema_version_is_documented(module_label, source_file, json_basename):
    """Each module's current SCHEMA_VERSION must be documented in BREAKING_CHANGES.md."""
    current_version = _read_schema_version(REPO_ROOT / source_file)
    documented = _read_documented_versions(
        BREAKING_CHANGES_PATH.read_text(), json_basename
    )

    assert current_version in documented, (
        f"Module '{module_label}' is at SCHEMA_VERSION={current_version} "
        f"({source_file}) but no '{json_basename}.json schema_version ... -> "
        f"{current_version}' entry in {BREAKING_CHANGES_PATH.name}. "
        f"Bumping SCHEMA_VERSION resets user data on the next boot — document "
        f"the reason, the `rm` command, and the impact. See RFC 19."
    )
