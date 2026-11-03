"""Structural guardrail: a unit's memory cap is one the kernel can enforce.

Every Milō service unit that declares `MemoryMax=` relies on the cgroup v2 memory
controller, and on a Raspberry Pi that controller is off unless the kernel command
line turns it back on: the firmware's device tree passes `cgroup_disable=memory`
ahead of `cmdline.txt`. systemd accepts the directive anyway — `systemctl show`
reports the cap — but no `memory.max` exists, so a leaking kiosk or a first
Navidrome scan of a large library fills the RAM of the whole unit and the global
OOM killer picks by size: sometimes the leaker, sometimes the backend or mpv, and
the sound stops. Measured on the server and both satellites before this test:
`cgroup.controllers` read `cpuset cpu io pids`.

The reader is the kernel at boot, then pid 1, on the server and on every satellite
alike — both images take their command line from `provisioning/boot-common.sh`.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UNIT_DIRS = (REPO_ROOT / "system", REPO_ROOT / "milo-client" / "system")
BOOT_COMMON = REPO_ROOT / "provisioning" / "boot-common.sh"

MEMORY_MAX = re.compile(r"^\s*MemoryMax=(\S+)", re.MULTILINE)


def _capped_units() -> dict[str, str]:
    caps: dict[str, str] = {}
    for unit_dir in UNIT_DIRS:
        for unit in sorted(unit_dir.glob("*.service")):
            match = MEMORY_MAX.search(unit.read_text())
            if match:
                caps[str(unit.relative_to(REPO_ROOT))] = match.group(1)
    return caps


def _boot_params_common() -> list[str]:
    proc = subprocess.run(
        ["bash", "-c", f'source "{BOOT_COMMON}" && printf %s "$BOOT_PARAMS_COMMON"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"sourcing provisioning/boot-common.sh failed: {proc.stderr}"
    return proc.stdout.split()


def test_declared_memory_caps_are_enforceable():
    """A `MemoryMax=` with the memory controller off bounds nothing.

    Fails when an image would boot with capped units and a command line that never
    re-enables the controller the Raspberry Pi device tree disables: every cap then
    reads as set in `systemctl show` and enforces nothing.
    """
    caps = _capped_units()
    assert caps, "no MemoryMax= found under system/ or milo-client/system/: the extractor is broken"
    assert "system/milo-kiosk.service" in caps, f"the kiosk's cap was not extracted: {sorted(caps)}"

    params = _boot_params_common()
    assert "quiet" in params and "console=tty3" in params, (
        f"BOOT_PARAMS_COMMON did not parse into the expected command line: {params}"
    )

    assert "cgroup_enable=memory" in params, (
        "the Raspberry Pi device tree passes cgroup_disable=memory, and "
        "provisioning/boot-common.sh never re-enables the memory controller, so these "
        f"caps are inert: {', '.join(f'{u} ({v})' for u, v in caps.items())}"
    )
