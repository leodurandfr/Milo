"""A speaker announces the identity its snapclient passes, by one rule.

The launcher takes `--hostID` from `milo-host-id` (bash); the registration POST
names the unit with `_get_mac_address` (Python). If they disagree, the server
keys one speaker twice: the pending entry never matches the snapclient, its
name and settings are lost. The server's half is
backend/tests/architecture/test_host_id_rule.py, and the two scripts are twins.
"""
import os
import subprocess
from pathlib import Path

import pytest

from services import registration

SCRIPT = Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-host-id"
LAUNCHER = SCRIPT.parent / "milo-client-snapclient-launcher"

UNREADABLE = object()

SCENARIOS = {
    "eth0 before wlan0": ({"eth0": ("02:00:00:00:00:e0", True), "wlan0": ("02:00:00:00:00:f0", True)}, "02:00:00:00:00:e0"),
    "a zero eth0 is skipped": ({"eth0": ("00:00:00:00:00:00", True), "wlan0": ("02:00:00:00:00:f0", True)}, "02:00:00:00:00:f0"),
    "an empty eth0 is skipped": ({"eth0": ("", True), "wlan0": ("02:00:00:00:00:f0", True)}, "02:00:00:00:00:f0"),
    "an unreadable eth0 is skipped": ({"eth0": (UNREADABLE, True), "wlan0": ("02:00:00:00:00:f0", True)}, "02:00:00:00:00:f0"),
    "wlan0 alone": ({"wlan0": ("02:00:00:00:00:f0", True)}, "02:00:00:00:00:f0"),
    "a predictable wired name": ({"end0": ("02:00:00:00:00:d0", True), "lo": ("00:00:00:00:00:00", False)}, "02:00:00:00:00:d0"),
    "other interfaces in name order": ({"enx0200000000c0": ("02:00:00:00:00:c0", True), "end0": ("02:00:00:00:00:d0", True)}, "02:00:00:00:00:d0"),
    "eth0 and wlan0 before any other": ({"end0": ("02:00:00:00:00:d0", True), "wlan0": ("02:00:00:00:00:f0", True)}, "02:00:00:00:00:f0"),
    "a tunnel is not an interface of the unit": ({"lo": ("00:00:00:00:00:00", False), "wg0": ("02:00:00:00:00:a0", False)}, None),
    "nothing at all": ({}, None),
}


def _build(root: Path, interfaces: dict) -> None:
    for name, (address, physical) in interfaces.items():
        (root / name).mkdir()
        if physical:
            (root / name / "device").mkdir()
        if address is UNREADABLE:
            (root / name / "address").mkdir()
        else:
            (root / name / "address").write_text(address + "\n")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_registration_announces_the_id_the_snapclient_passes(name, tmp_path, monkeypatch):
    interfaces, expected = SCENARIOS[name]
    _build(tmp_path, interfaces)

    done = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, timeout=10,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MILO_SYSFS_NET": str(tmp_path)},
    )
    assert (done.stdout.strip() or None) == expected

    monkeypatch.setattr(registration, "SYSFS_NET", str(tmp_path))
    if expected is None:
        with pytest.raises(RuntimeError):
            registration._get_mac_address()
    else:
        assert registration._get_mac_address() == expected


def test_the_launcher_takes_its_id_from_the_script():
    assert "$(/usr/local/bin/milo-host-id)" in LAUNCHER.read_text()
