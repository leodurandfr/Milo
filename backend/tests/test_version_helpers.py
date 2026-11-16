# backend/tests/test_version_helpers.py
"""
Tests for the update helpers: compare_versions, requirements_moved_up.
"""
from pathlib import Path

from backend.core.updates.helpers import compare_versions, requirements_moved_up

REQUIREMENTS = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text()


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

    Read against the repo's own requirements.txt, so the comment and marker
    shapes it really carries are the ones parsed.
    """

    def test_the_same_file_needs_no_pip(self):
        assert REQUIREMENTS.strip(), "requirements.txt not read"
        assert requirements_moved_up(REQUIREMENTS, REQUIREMENTS) is False

    def test_a_requirement_added_needs_pip(self):
        assert requirements_moved_up(REQUIREMENTS, REQUIREMENTS + "\nnewpackage>=1.0\n") is True

    def test_a_floor_raised_needs_pip(self):
        first = next(line for line in REQUIREMENTS.splitlines() if ">=" in line)
        name, floor = first.split("#")[0].strip().split(">=")
        raised = REQUIREMENTS.replace(first, f"{name}>={floor}.1")
        assert requirements_moved_up(REQUIREMENTS, raised) is True

    def test_a_requirement_dropped_needs_no_pip(self):
        """pip install -r never uninstalls: the dropped package stays, unused."""
        dropped = "\n".join(REQUIREMENTS.splitlines()[1:])
        assert requirements_moved_up(REQUIREMENTS, dropped) is False

    def test_a_comment_or_spacing_edit_needs_no_pip(self):
        commented = "\n".join(
            line.replace(">=", " >= ") + "  # reworded" if line and not line.startswith("#") else line
            for line in REQUIREMENTS.splitlines()
        )
        assert requirements_moved_up(REQUIREMENTS, commented) is False
