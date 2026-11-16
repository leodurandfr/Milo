"""One rule names a unit in multiroom, and the server applies it twice.

The snapclient's `--hostID` is printed by `milo-host-id` (a bash script run by
the snapclient unit), while the backend recognizes its own speaker through
`identity.read_host_id` (Python). If the two disagree on a unit, the server's
own room is keyed under an id no snapclient announces: never admitted, no
volume, no EQ, and no error anywhere. The satellite's half (its launcher's twin
of the script against its registration) is held by
milo-client/app/tests/test_host_id.py; `test_twin_files_have_not_drifted` keeps
the two scripts identical.
"""
import os
import subprocess
from pathlib import Path

import pytest

from backend.core.multiroom import identity

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-host-id"

UNREADABLE = object()

# name → interfaces {iface: (address, physical)}, and the identity the rule gives.
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


def build_sysfs(root: Path, interfaces: dict) -> None:
    for name, (address, physical) in interfaces.items():
        (root / name).mkdir()
        if physical:
            (root / name / "device").mkdir()
        if address is UNREADABLE:
            (root / name / "address").mkdir()
        else:
            (root / name / "address").write_text(address + "\n")


def script_answer(root: Path):
    done = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, timeout=10,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MILO_SYSFS_NET": str(root)},
    )
    assert done.returncode in (0, 1), done.stderr
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def test_the_scenarios_tell_the_rules_apart():
    answers = {expected for _, expected in SCENARIOS.values()}
    assert len(answers) >= 4, "the scenarios no longer separate eth0, wlan0, another interface and none"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_script_and_the_backend_name_the_unit_alike(name, tmp_path):
    interfaces, expected = SCENARIOS[name]
    build_sysfs(tmp_path, interfaces)
    assert script_answer(tmp_path) == expected
    assert identity.read_host_id(str(tmp_path)) == expected


def test_the_snapclient_unit_takes_its_id_from_the_script():
    unit = (REPO_ROOT / "system" / "milo-snapclient-multiroom.service").read_text()
    assert "--hostID $(/usr/local/bin/milo-host-id" in unit
