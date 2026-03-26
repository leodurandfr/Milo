# backend/tests/test_version_helpers.py
"""
Tests for version helper utilities (compare_versions, extract_base_tag).
"""

from backend.core.updates.helpers import compare_versions, extract_base_tag


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


class TestExtractBaseTag:
    """Tests for extract_base_tag() git describe output parsing"""

    def test_full_git_describe(self):
        assert extract_base_tag("v0.0.1-347-g14ee633") == "v0.0.1"

    def test_exact_tag(self):
        assert extract_base_tag("v0.0.1") == "v0.0.1"

    def test_tag_without_v(self):
        assert extract_base_tag("1.2.3") == "1.2.3"

    def test_git_describe_without_v(self):
        assert extract_base_tag("1.2.3-10-gabcdef0") == "1.2.3"

    def test_none_input(self):
        assert extract_base_tag(None) is None

    def test_empty_string(self):
        assert extract_base_tag("") is None

    def test_short_hash_only(self):
        # git describe --always with no tags: just a hash
        assert extract_base_tag("14ee633") == "14ee633"

    def test_tag_with_hyphen(self):
        # Tag like "v1.0.0-rc1" with describe suffix
        assert extract_base_tag("v1.0.0-rc1-5-g1234567") == "v1.0.0-rc1"
