"""The managed blocks the two apply helpers write into config.txt, run for real.

`milo-apply-hardware` (server) and `milo-client-apply-hardware` (satellite) own
`# BEGIN MILO … / # END MILO …` blocks in `/boot/firmware/config.txt`, removed
and appended again on every apply. Both run as root on a vfat partition CI never
sees, so these tests source the real `hardware-helpers.sh` of each tree and run
the real config-writing section of each apply script on a temporary file.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from backend.tests.architecture.test_rootfs_deployment import APPLY_HARDWARE_SCRIPTS

BEGIN_RE = re.compile(r"^# BEGIN (.+)$", re.MULTILINE)
# The config-writing part of an apply script: from its backup to whatever
# follows the blocks (the server's kiosk switch, the satellite's flush).
SECTION_RE = re.compile(r"^backup_config\n(.*?)^(?=# Kiosk\b|sync -f )", re.MULTILINE | re.DOTALL)
SCRIPT_VARIABLES = {
    "milo-apply-hardware": {
        "AUDIO_OVERLAY": "hifiberry-amp4pro",
        "SCREEN_TYPE": "waveshare_8_dsi",
        "IR_ENABLED": "True",
        "IR_GPIO_PIN": "17",
        "POWER_BUTTON_ENABLED": "True",
        "POWER_BUTTON_LED_GPIO": "26",
    },
    "milo-client-apply-hardware": {"AUDIO_OVERLAY": "hifiberry-dac"},
}

# What Raspberry Pi OS ships, with a hand-added stanza after it and the blank
# lines earlier applies left behind (16 in a row measured on the server).
IMAGE_CONFIG = (
    "dtparam=audio=on\n"
    "camera_auto_detect=1\n"
    "\n"
    "[all]\n"
    "dtoverlay=vc4-kms-v3d\n"
    + "\n" * 6
    + "# Hand-added\n"
    "dtparam=eee=off\n"
)


def _bash(helpers: Path, code: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail; source {shlex.quote(str(helpers))}; CONFIG_FILE=config.txt; {code}"],
        cwd=cwd, capture_output=True, text=True, timeout=30, check=False,
    )


def _helpers_of(script: Path) -> Path:
    return script.parents[3] / "usr" / "local" / "lib" / "milo" / "hardware-helpers.sh"


def _config_section(script: Path) -> str:
    found = SECTION_RE.search(script.read_text(encoding="utf-8"))
    assert found and "remove_block" in found.group(1) and "cat >>" in found.group(1), (
        f"could not extract the config.txt section of {script.name}; the extractor is broken"
    )
    return found.group(1)


@pytest.mark.parametrize("script", APPLY_HARDWARE_SCRIPTS, ids=lambda p: p.name)
def test_applying_twice_leaves_config_txt_as_applying_once(script, tmp_path):
    """N applies leave config.txt exactly as one does.

    Each block is appended with a blank line before it, and removing only
    BEGIN..END left that line behind, so every apply added one more: 38 of the
    server's 109 lines were blank. Fails when a second or third apply changes a
    file the first one wrote, or when a run of blank lines survives an apply.
    Consumers: the setup wizard and the Hardware page (server), the satellite's
    `POST /api/hardware/reboot` and its WiFi adoption (satellite).
    """
    section = _config_section(script)
    variables = "; ".join(f"{k}={shlex.quote(v)}" for k, v in SCRIPT_VARIABLES[script.name].items())
    config = tmp_path / "config.txt"
    config.write_text(IMAGE_CONFIG, encoding="utf-8")

    applied = []
    for _ in range(3):
        result = _bash(_helpers_of(script), f"{variables}\n{section}", tmp_path)
        assert result.returncode == 0, result.stderr
        applied.append(config.read_text(encoding="utf-8"))

    tags = BEGIN_RE.findall(applied[0])
    assert tags, f"{script.name} wrote no managed block; the run is broken:\n{applied[0]}"
    assert "dtparam=eee=off" in applied[0], "the apply lost a line it does not own"
    assert "\n\n\n" not in applied[0], f"a run of blank lines survived the apply:\n{applied[0]}"
    assert applied[1] == applied[0], f"a second apply changed config.txt:\n{applied[0]!r}\n{applied[1]!r}"
    assert applied[2] == applied[0], f"a third apply changed config.txt:\n{applied[0]!r}\n{applied[2]!r}"
