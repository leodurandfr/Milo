"""The managed blocks the two apply helpers write into config.txt, run for real.

`milo-apply-hardware` (server) and `milo-client-apply-hardware` (satellite) own
`# BEGIN MILO … / # END MILO …` blocks in `/boot/firmware/config.txt`, removed
and appended again on every apply. Both run as root on a vfat partition CI never
sees, so these tests source the real `hardware-helpers.sh` of each tree and run
the real validation and config-writing sections of each apply script on a
temporary file.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from backend.hardware.registry import AUDIO_CARDS
from backend.tests.architecture.test_rootfs_deployment import APPLY_HARDWARE_SCRIPTS, HARDWARE_HELPERS

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
    "milo-client-apply-hardware": {"AUDIO_OVERLAY": "hifiberry-dacplushd"},
}
ROLE_OF_SCRIPT = {"milo-apply-hardware": "server", "milo-client-apply-hardware": "client"}
# The validation part of an apply script: from the hardware.json check to the
# first section that acts (the server's EEPROM, the satellite's backup).
VALIDATION_RE = re.compile(r"^validate_hardware_json\n(.*?)^(?=# =+\n# (?:Power-on|Backup))", re.MULTILINE | re.DOTALL)

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


def _role_blocks(helpers: Path, role: str, cwd: Path) -> list[str]:
    result = _bash(helpers, f'printf "%s\\n" "${{{role.upper()}_CONFIG_BLOCKS[@]}}"', cwd)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


@pytest.mark.parametrize("script", APPLY_HARDWARE_SCRIPTS, ids=lambda p: p.name)
def test_each_role_declares_every_block_its_apply_writes(script, tmp_path):
    """The block list a conversion clears is exactly what the role's apply writes.

    `milo-first-boot` clears the blocks of the role a unit leaves from that
    list; a block added to an apply script and not to it survives the
    conversion, and is loaded by a unit that no longer knows it is there.
    """
    written = set(BEGIN_RE.findall(script.read_text(encoding="utf-8")))
    assert written, f"no `# BEGIN` line extracted from {script.name}; the extractor is broken"
    for tree_helpers in HARDWARE_HELPERS.values():
        assert set(_role_blocks(tree_helpers, ROLE_OF_SCRIPT[script.name], tmp_path)) == written


@pytest.mark.parametrize("tree_name", sorted(HARDWARE_HELPERS))
@pytest.mark.parametrize("leaving,staying", [("server", "client"), ("client", "server")])
def test_a_role_change_removes_only_the_blocks_of_the_role_left(tree_name, leaving, staying, tmp_path):
    """`remove_role_blocks <role>` takes out that role's blocks and nothing else.

    A unit that ran the server wizard and is then adopted as a satellite kept
    the server's blocks: the satellite's apply removes only `MILO CLIENT
    AUDIO`, so the satellite booted the server's card, and nothing ever took
    out the screen block (the DSI panel overlay, or the forced HDMI mode), the
    IR pin or the power LED pin of a unit that drives none of them. The revert
    to a server is the same in the other direction.
    """
    helpers = HARDWARE_HELPERS[tree_name]
    blocks = {role: _role_blocks(helpers, role, tmp_path) for role in ("server", "client")}
    assert blocks["server"] and blocks["client"], f"role block lists not extracted: {blocks}"
    config = tmp_path / "config.txt"
    config.write_text(
        IMAGE_CONFIG
        + "".join(f"\n# BEGIN {tag}\ndtparam=x\n# END {tag}\n" for tag in blocks["server"] + blocks["client"]),
        encoding="utf-8",
    )

    result = _bash(helpers, f"remove_role_blocks {leaving}", tmp_path)

    assert result.returncode == 0, result.stderr
    left = BEGIN_RE.findall(config.read_text(encoding="utf-8"))
    assert left == blocks[staying], f"after leaving the {leaving} role, config.txt holds {left}"
    assert "dtparam=eee=off" in config.read_text(encoding="utf-8")


def _validation_section(script: Path) -> str:
    found = VALIDATION_RE.search(script.read_text(encoding="utf-8"))
    assert found and "read_json" in found.group(1) and "validate_audio_overlay" in found.group(1), (
        f"could not extract the validation section of {script.name}; the extractor is broken"
    )
    return found.group(1)


@pytest.mark.parametrize("script", APPLY_HARDWARE_SCRIPTS, ids=lambda p: p.name)
def test_only_the_server_refuses_to_apply_with_no_audio_card(script, tmp_path):
    """The server plays only through a HAT; a satellite may be set up before its card.

    `audio.overlay` is absent from hardware.json for the "none" card. The
    server's apply exits non-zero on it before it touches the EEPROM or
    config.txt, and the backend answers the refusal and restores the previous
    hardware.json; the satellite's apply accepts it, since its pairing writes
    "none" until the card is picked. Fails when either side moves — the
    server writing a config.txt with no card, or the satellite refusing its
    pairing default. Consumers: the server's setup wizard and Hardware page,
    the satellite's `POST /api/hardware/reboot` and its first-boot conversion.
    """
    section = _validation_section(script)
    hardware = tmp_path / "hardware.json"
    (tmp_path / "config.txt").write_text(IMAGE_CONFIG, encoding="utf-8")

    def validate(audio_id: str) -> subprocess.CompletedProcess:
        # Shaped as the backend writes it: no overlay key for a card without one.
        overlay = AUDIO_CARDS[audio_id]["overlay"]
        audio = {"id": audio_id, **({"overlay": overlay} if overlay else {})}
        hardware.write_text(json.dumps({"audio": audio}), encoding="utf-8")
        return _bash(_helpers_of(script), f"HARDWARE_FILE=hardware.json\n{section}", tmp_path)

    card = next(audio_id for audio_id, entry in AUDIO_CARDS.items() if entry["overlay"])
    with_card = validate(card)
    assert with_card.returncode == 0, f"{script.name} refused {card}, which the registry offers: {with_card.stderr}"

    without_card = validate("none")
    if ROLE_OF_SCRIPT[script.name] == "server":
        assert without_card.returncode != 0, f"{script.name} accepted a unit with no audio card"
    else:
        assert without_card.returncode == 0, f"{script.name} refused the pairing default: {without_card.stderr}"
