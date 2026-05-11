# backend/hardware/keymap_writer.py
"""
rc_keymap(5) writer for the Apple Remote (1st gen, white).

Given a captured device_id (the per-remote pairing byte at bits 15..8 of the
32-bit Apple NEC scancode), generate a TOML keymap that targets only that
remote and load it into the kernel via the sudoers-protected helper.

The 6 button command bytes are documented in §5.3 of docs/plans/remote-controls.md.
For each button we emit both parity variants because the Apple variant disables
the standard NEC checksum check and the kernel emits whichever parity the
remote's device_id yields.

The backend never writes to /etc/rc_keymaps/ directly — it pipes the TOML
content via stdin to /usr/local/bin/milo-apply-ir-keymap (sudoers-protected),
which writes the file atomically and reloads the kernel state in a single
privileged step. This avoids needing /etc/rc_keymaps/ writable by the milo user.
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# Apple manufacturer prefix as emitted by the rc-core NEC decoder on Linux
# 6.x (byte-swapped form of Apple's transmitted custom code 0xEE 0x87).
APPLE_MANUFACTURER = 0x87EE

# 7-bit command code per button. Each command can yield two byte values
# depending on the parity bit (LSB) which is bound to the remote's device_id.
APPLE_BUTTON_CMDS: Dict[str, int] = {
    "KEY_HOMEPAGE":     0x01,  # Menu
    "KEY_PLAYPAUSE":    0x02,  # Center
    "KEY_NEXTSONG":     0x03,  # Right
    "KEY_PREVIOUSSONG": 0x04,  # Left
    "KEY_VOLUMEUP":     0x05,  # Up
    "KEY_VOLUMEDOWN":   0x06,  # Down
}

APPLY_HELPER = Path("/usr/local/bin/milo-apply-ir-keymap")


def _validate_device_id(device_id: int) -> None:
    if not isinstance(device_id, int) or not (0 <= device_id <= 0xFF):
        raise ValueError(f"device_id must be an 8-bit integer (0..255), got {device_id!r}")


def render_keymap(device_id: int) -> str:
    """Render a TOML keymap for the given device_id.

    Emits 12 scancodes (6 buttons × 2 parity variants) under a single
    `nec-32` protocol block.
    """
    _validate_device_id(device_id)

    lines: List[str] = [
        "[[protocols]]",
        'name = "milo-apple-remote"',
        'protocol = "nec"',
        'variant = "nec-32"',
        "",
        "[protocols.scancodes]",
    ]
    for keycode, cmd7 in APPLE_BUTTON_CMDS.items():
        # Both parity variants: cmd<<1 (parity 0) and (cmd<<1)|1 (parity 1)
        byte_even = (cmd7 << 1) & 0xFF
        byte_odd = byte_even | 0x01
        scancode_even = (APPLE_MANUFACTURER << 16) | (device_id << 8) | byte_even
        scancode_odd = (APPLE_MANUFACTURER << 16) | (device_id << 8) | byte_odd
        lines.append(f'0x{scancode_even:08x} = "{keycode}"')
        lines.append(f'0x{scancode_odd:08x} = "{keycode}"')
    lines.append("")
    return "\n".join(lines)


async def apply_keymap(device_id: int) -> None:
    """Generate the keymap for `device_id` and load it into the kernel.

    The TOML content is piped via stdin to the sudoers-protected helper,
    which writes it atomically under /etc/rc_keymaps/ and reloads the
    rc-core keymap.

    Raises RuntimeError if the helper exits non-zero so callers can surface
    a user-facing error instead of silently leaving the kernel in the old
    state.
    """
    _validate_device_id(device_id)
    content = render_keymap(device_id)

    cmd = ["sudo", "-n", str(APPLY_HELPER)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=content.encode("utf-8"))
    if proc.returncode != 0:
        out = (stderr or stdout or b"").decode(errors="replace").strip()
        raise RuntimeError(
            f"milo-apply-ir-keymap failed (exit {proc.returncode}): {out}"
        )
    logger.info("Kernel rc-core keymap loaded for device_id=0x%02X", device_id)


async def clear_kernel_keymap() -> None:
    """Clear the active kernel keymap and remove the on-disk file.

    Invokes the sudoers-protected helper with the special `--clear` argument.
    """
    cmd = ["sudo", "-n", str(APPLY_HELPER), "--clear"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        out = (stderr or stdout or b"").decode(errors="replace").strip()
        raise RuntimeError(
            f"milo-apply-ir-keymap --clear failed (exit {proc.returncode}): {out}"
        )
    logger.info("Kernel rc-core keymap cleared")
