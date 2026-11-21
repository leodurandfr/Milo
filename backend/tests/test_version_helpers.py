# backend/tests/test_version_helpers.py
"""
Tests for the update helpers: compare_versions, requirements_moved_up.
"""
import re
from pathlib import Path

from backend.core.updates.helpers import compare_versions, requirements_moved_up

LOCK = (Path(__file__).resolve().parents[2] / "requirements.lock").read_text()
# The lock cut before each pin, which uv writes at column 0: the header first,
# then one block per package — its pin, its hashes, its `# via` lines.
PACKAGES = re.split(r"(?m)^(?=[A-Za-z0-9])", LOCK)[1:]


class TestCompareVersions:
    """Tests for compare_versions() semver comparison"""

    def test_newer_major(self):
        assert compare_versions("1.0.0", "2.0.0") is True

    def test_newer_minor(self):
        assert compare_versions("1.0.0", "1.1.0") is True

    def test_newer_patch(self):
        assert compare_versions("1.0.0", "1.0.1") is True

    def test_same_version(self):
        assert compare_versions("1.2.3", "1.2.3") is False

    def test_older_version(self):
        assert compare_versions("2.0.0", "1.0.0") is False

    def test_older_minor(self):
        assert compare_versions("1.5.0", "1.4.0") is False

    def test_older_patch(self):
        assert compare_versions("1.0.5", "1.0.3") is False

    def test_with_v_prefix(self):
        assert compare_versions("v1.0.0", "v2.0.0") is True

    def test_mixed_prefix(self):
        assert compare_versions("v1.0.0", "2.0.0") is True

    def test_two_part_version(self):
        assert compare_versions("4.2", "4.3") is True

    def test_two_part_same(self):
        assert compare_versions("4.2", "4.2") is False

    def test_none_current(self):
        assert compare_versions(None, "1.0.0") is False

    def test_none_latest(self):
        assert compare_versions("1.0.0", None) is False

    def test_both_none(self):
        assert compare_versions(None, None) is False

    def test_empty_current(self):
        assert compare_versions("", "1.0.0") is False

    def test_empty_latest(self):
        assert compare_versions("1.0.0", "") is False

    def test_garbage_input(self):
        assert compare_versions("abc", "def") is False

    def test_real_world_go_librespot(self):
        assert compare_versions("0.6.1", "0.7.0") is True

    def test_real_world_snapcast(self):
        assert compare_versions("0.28.0", "0.28.0") is False

    def test_large_version_numbers(self):
        assert compare_versions("1.99.99", "2.0.0") is True

    def test_version_with_extra_parts(self):
        # Only first 3 parts are used
        assert compare_versions("1.0.0.1", "1.0.0.2") is False


class TestRequirementsMovedUp:
    """Whether a Milō update runs pip, and keeps a venv copy for the guard.

    Read against the repo's own requirements.lock — the file the update reads
    out of both commits — so the shapes uv really writes (a pin, one carrying
    an environment marker, its hash lines, its `# via` comments, the header
    naming the compile command) are the ones parsed.
    """

    def test_the_same_lock_needs_no_pip(self):
        assert len(PACKAGES) >= 20, f"only {len(PACKAGES)} packages read from requirements.lock"
        assert requirements_moved_up(LOCK, LOCK) is False

    def test_a_package_added_needs_pip(self):
        added = LOCK + f"newpackage==1.0 \\\n    --hash=sha256:{'a' * 64}\n    # via -r requirements.txt\n"
        assert requirements_moved_up(LOCK, added) is True

    def test_a_pin_moved_needs_pip(self):
        """Either way: a lower pin is a downgrade the venv has to make. The pin
        moved is one carrying a marker, the longest line uv writes."""
        block = next(b for b in PACKAGES if ";" in b.splitlines()[0])
        pin = block.splitlines()[0]
        name, version = pin.split(" ", 1)[0].split("==")
        moved = LOCK.replace(pin, pin.replace(f"{name}=={version}", f"{name}=={version}.1"))
        assert moved != LOCK
        assert requirements_moved_up(LOCK, moved) is True

    def test_a_package_dropped_needs_no_pip(self):
        """pip install -r never uninstalls: the dropped package stays, unused.
        Dropped whole — its pin, its hashes and its `# via` lines."""
        dropped = LOCK.replace(PACKAGES[len(PACKAGES) // 2], "")
        assert dropped != LOCK
        assert requirements_moved_up(LOCK, dropped) is False

    def test_a_comment_or_spacing_edit_needs_no_pip(self):
        """The header's command, the `# via` lines and the indentation of the
        hashes are uv's to rewrite at every compile."""
        edited = (
            LOCK.replace("--generate-hashes", "--generate-hashes --quiet")
            .replace("    # via", "    #   via")
            .replace("    --hash=", "  --hash=")
        )
        assert edited != LOCK
        assert requirements_moved_up(LOCK, edited) is False
