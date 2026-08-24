# backend/tests/test_keymap_writer.py
"""
Unit tests for the Apple Remote keymap writer.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from backend.hardware.keymap_writer import (
    APPLE_BUTTON_CMDS,
    APPLE_MANUFACTURER,
    apply_keymap,
    clear_kernel_keymap,
    render_keymap,
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


class TestApplyKeymap:
    """`apply_keymap()` — the only privileged step of the pairing flow.

    argv IS the contract here: /etc/sudoers.d/milo-ir-remote grants exactly
    `/usr/local/bin/milo-apply-ir-keymap`, so a renamed helper or a shell-out
    that skips sudo is a permission denial on the appliance and nothing else.
    """

    @pytest.mark.asyncio
    async def test_the_toml_is_piped_on_stdin_to_the_sudoers_helper(self):
        """Never a write to /etc/rc_keymaps/ from here — the helper owns that
        path, and the milo user cannot write it."""
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec",
                   return_value=proc) as spawn:
            await apply_keymap(0x8D)

        assert spawn.await_args.args == (
            "sudo", "-n", "/usr/local/bin/milo-apply-ir-keymap",
        )
        assert spawn.await_args.kwargs["stdin"] is asyncio.subprocess.PIPE
        piped = proc.communicate.await_args.kwargs["input"].decode("utf-8")
        assert piped == render_keymap(0x8D)

    @pytest.mark.asyncio
    async def test_a_helper_that_exits_non_zero_raises_with_its_stderr(self):
        """The caller turns this into the wizard's `error` status; swallowing
        it would report a pairing the kernel never took."""
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"cannot open /etc/rc_keymaps"))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(RuntimeError, match="cannot open /etc/rc_keymaps"):
                await apply_keymap(0x8D)

    @pytest.mark.asyncio
    async def test_an_invalid_device_id_is_refused_before_any_spawn(self):
        with patch("asyncio.create_subprocess_exec") as spawn:
            with pytest.raises(ValueError):
                await apply_keymap(0x1FF)
        spawn.assert_not_called()


class TestClearKernelKeymap:
    """`clear_kernel_keymap()` — the unpair half of the same helper."""

    @pytest.mark.asyncio
    async def test_the_helper_is_invoked_with_the_clear_argument(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec",
                   return_value=proc) as spawn:
            await clear_kernel_keymap()

        assert spawn.await_args.args == (
            "sudo", "-n", "/usr/local/bin/milo-apply-ir-keymap", "--clear",
        )

    @pytest.mark.asyncio
    async def test_a_failed_clear_raises(self):
        """`unpair()` deliberately swallows this — it must still be raised, or
        the swallow has nothing to catch and the failure is invisible."""
        proc = AsyncMock()
        proc.returncode = 2
        proc.communicate = AsyncMock(return_value=(b"no keymap loaded", b""))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(RuntimeError, match="no keymap loaded"):
                await clear_kernel_keymap()
