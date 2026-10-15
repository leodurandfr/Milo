"""Guardrail: Energy Efficient Ethernet is turned off once, in the PHY, at boot.

EEE's LPI wake-ups drop Snapcast chunks, so Milō keeps it off — through
`dtparam=eee=off` in config.txt, which the board's device tree maps to
`eee-broken-1000t`/`eee-broken-100tx` on the PHY (Pi 5, current Pi 4 trees) or to
`genet.eee=N` (older Pi 4 trees): the PHY never advertises EEE, so the link never
negotiates it in the first place.

It used to be switched off at runtime by `90-milo-network`, and changing EEE
renegotiates a link that is already up. Measured on the Pi 5 server
(2026-10-15): `ethtool --set-eee eth0 eee off` took the link down 0.18 s later
for 3.07 s and NetworkManager restarted DHCP — at every boot, on a switch with
no EEE at all — which pushed eth0's lease back by up to 20 s and opened the
window where milo.local was published on WiFi.
"""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_SH = REPO_ROOT / "provisioning/system.sh"
CONFIGURE_STAGE = REPO_ROOT / "pi-gen/stage-milo/03-configure/00-run.sh"
ROOTFS_TREES = (REPO_ROOT / "rootfs", REPO_ROOT / "milo-client/rootfs")


def _configure(config: Path) -> None:
    subprocess.run(
        [
            "bash", "-c",
            f'log_info() {{ :; }}; log_success() {{ :; }}\n'
            f'source "{SYSTEM_SH}"\n'
            f'boot_config_file() {{ echo "{config}"; }}\n'
            "configure_ethernet_eee\nconfigure_ethernet_eee",
        ],
        check=True,
    )


def test_the_parameter_reaches_every_board_from_any_config_tail(tmp_path):
    """A config.txt can end inside a model filter or an overlay's scope; the
    line must still apply to every board and to the base device tree. Run
    twice, it is written once."""
    config = tmp_path / "config.txt"
    config.write_text("arm_64bit=1\n[pi5]\ndtoverlay=nospi10\ndtoverlay=gpio-ir,gpio_pin=17\n")

    _configure(config)

    lines = [line for line in config.read_text().splitlines() if line and not line.startswith("#")]
    assert lines.count("dtparam=eee=off") == 1
    tail = lines[lines.index("dtparam=eee=off") - 2:]
    assert tail == ["[all]", "dtoverlay=", "dtparam=eee=off"], (
        "dtparam=eee=off is not under [all] with the overlay scope closed: "
        f"{tail}"
    )


def test_the_image_build_turns_eee_off():
    calls = re.findall(r"^\s*configure_ethernet_eee\s*$", CONFIGURE_STAGE.read_text(), re.M)
    assert len(calls) == 1, "the pi-gen configure stage no longer calls configure_ethernet_eee"


def test_nothing_changes_eee_at_runtime():
    """A runtime change is the 3 s link drop at every boot this replaced."""
    scripts = [p for tree in ROOTFS_TREES for p in tree.rglob("*") if p.is_file()]
    assert len(scripts) > 20, "no rootfs file found; the extractor is broken"
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in scripts
        if re.search(r"ethtool\b.*(--set-eee|\s-s\b)", p.read_text(errors="ignore"))
    ]
    assert not offenders, f"these change EEE on a live link: {offenders}"
