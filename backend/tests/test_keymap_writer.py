# backend/tests/test_keymap_writer.py
"""
Unit tests for the Apple Remote keymap writer.
"""
import pytest

from backend.hardware import keymap_writer
from backend.hardware.keymap_writer import (
    APPLE_BUTTON_CMDS,
    APPLE_MANUFACTURER,
    render_keymap,
    write_keymap,
)


class TestRenderKeymap:
    """The TOML output must encode the Phase 1-confirmed scancode layout."""

    def test_header_and_protocol(self):
        toml = render_keymap(0x8D)
        assert "[[protocols]]" in toml
        assert 'name = "milo-apple-remote"' in toml
        assert 'protocol = "nec"' in toml
        assert 'variant = "nec-32"' in toml
        assert "[protocols.scancodes]" in toml

    def test_emits_both_parity_variants_per_button(self):
        toml = render_keymap(0x8D)
        scancode_lines = [
            line for line in toml.splitlines()
            if line.startswith("0x")
        ]
        # 6 buttons * 2 parity variants
        assert len(scancode_lines) == 12

    def test_scancode_format_for_known_device_id(self):
        """Phase 1 reference remote: device_id = 0x8D, Center button observed as 0x04."""
        toml = render_keymap(0x8D)
        # KEY_PLAYPAUSE: 7-bit cmd 0x02 → byte_even = 0x04, byte_odd = 0x05
        assert '0x87ee8d04 = "KEY_PLAYPAUSE"' in toml
        assert '0x87ee8d05 = "KEY_PLAYPAUSE"' in toml

    def test_scancode_format_for_volume_buttons(self):
        toml = render_keymap(0x8D)
        # KEY_VOLUMEUP: 7-bit cmd 0x05 → byte_even = 0x0a, byte_odd = 0x0b
        assert '0x87ee8d0a = "KEY_VOLUMEUP"' in toml
        assert '0x87ee8d0b = "KEY_VOLUMEUP"' in toml
        # KEY_VOLUMEDOWN: 7-bit cmd 0x06 → byte_even = 0x0c, byte_odd = 0x0d
        assert '0x87ee8d0c = "KEY_VOLUMEDOWN"' in toml
        assert '0x87ee8d0d = "KEY_VOLUMEDOWN"' in toml

    def test_device_id_isolates_remote(self):
        """Two different device_ids must produce non-overlapping scancodes."""
        toml_a = render_keymap(0x8D)
        toml_b = render_keymap(0x42)
        scancodes_a = {line for line in toml_a.splitlines() if line.startswith("0x")}
        scancodes_b = {line for line in toml_b.splitlines() if line.startswith("0x")}
        # No overlap — the device_id segregates per-remote keymaps
        assert scancodes_a.isdisjoint(scancodes_b)

    def test_manufacturer_prefix_constant(self):
        toml = render_keymap(0x00)
        # Every emitted scancode must carry the 0x87ee prefix
        for line in toml.splitlines():
            if line.startswith("0x"):
                # bits 31..16 are the manufacturer prefix
                scancode = int(line.split(" = ")[0], 16)
                assert (scancode >> 16) & 0xFFFF == APPLE_MANUFACTURER

    @pytest.mark.parametrize("invalid", [-1, 256, 1000, "8D", None, 3.14])
    def test_rejects_invalid_device_id(self, invalid):
        with pytest.raises((ValueError, TypeError)):
            render_keymap(invalid)

    def test_all_six_buttons_present(self):
        toml = render_keymap(0x01)
        for keycode in APPLE_BUTTON_CMDS.keys():
            assert keycode in toml, f"Missing button: {keycode}"


class TestWriteKeymap:
    """write_keymap should produce a TOML file at the requested path."""

    def test_writes_file(self, tmp_path):
        path = tmp_path / "milo-apple-remote.toml"
        result = write_keymap(0x8D, path=path)

        assert result == path
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert '0x87ee8d04 = "KEY_PLAYPAUSE"' in content

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "milo-apple-remote.toml"
        path.write_text("stale content", encoding="utf-8")

        write_keymap(0x42, path=path)

        content = path.read_text(encoding="utf-8")
        assert "stale content" not in content
        assert '0x87ee4204 = "KEY_PLAYPAUSE"' in content

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "milo-apple-remote.toml"
        write_keymap(0x8D, path=path)
        assert path.exists()
