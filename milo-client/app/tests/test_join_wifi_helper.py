"""`milo-client-join-wifi`, run for real under bash against a stub NetworkManager.

The server pushes its own WiFi at every admission, so whatever it sends is
applied to a satellite that may be associated with that network right now. An
empty PSK reads as an open network: pushed once for a WPA network (a key the
server could not read), it deleted the working wpa-psk profile and created an
open one, and the satellite stayed off WiFi with no standby link until the next
admission. Consumer: `PUT /api/system/wifi` (routes/system.py), which answers
the helper's stderr to `SatelliteWifiService`.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-join-wifi"
)
IFACE_LINE = 'IFACE="wlan0"\n'
SSID = "Freebox-CA3555"
PSK = "correct-horse"

# NetworkManager as the helper reads it: one JSON file of profiles, every call
# appended to a log. Only the invocations the helper makes are modelled; any
# other one fails loudly, so a new nmcli call cannot pass on an empty answer.
NMCLI_STUB = r'''
import json, os, sys

state_path, log_path = os.environ["NM_STATE"], os.environ["NM_LOG"]
with open(state_path) as f:
    state = json.load(f)
args = sys.argv[1:]
with open(log_path, "a") as f:
    f.write(" ".join(args) + "\n")

def save():
    with open(state_path, "w") as f:
        json.dump(state, f)

profiles = state["profiles"]
fields = {
    "802-11-wireless.ssid": "ssid",
    "802-11-wireless-security.psk": "psk",
    "802-11-wireless-security.key-mgmt": "key_mgmt",
    "connection.autoconnect": "autoconnect",
}
words = [a for a in args if a not in ("-s", "-e", "no")]
if words[:2] == ["radio", "wifi"] or words[:2] == ["device", "set"]:
    pass
elif words[0] == "-g" and words[2:4] == ["device", "show"]:
    print(state["active"])
elif words[0] == "-g" and words[1] == "UUID,TYPE" and words[2:4] == ["connection", "show"]:
    for uuid in profiles:
        print(f"{uuid}:802-11-wireless")
elif words[0] == "-g" and words[1] in fields and words[2:4] == ["connection", "show"]:
    print(profiles[words[4]].get(fields[words[1]], ""))
elif words[:2] == ["connection", "delete"]:
    del profiles[words[2]]
    save()
elif words[:2] == ["connection", "add"]:
    opts = dict(zip(words[2::2], words[3::2]))
    profiles[f"new-{len(state['added'])}"] = {
        "ssid": opts["ssid"], "autoconnect": opts["connection.autoconnect"],
        "psk": opts.get("wifi-sec.psk", ""), "key_mgmt": opts.get("wifi-sec.key-mgmt", ""),
    }
    state["added"].append(opts["ssid"])
    save()
else:
    sys.exit(f"nmcli stub: unmodelled call {args!r}")
'''


@pytest.fixture
def world(tmp_path):
    """A copy of the helper on an interface every host has, and the nmcli stub.

    The helper refuses a unit with no wlan0, which CI has not; nmcli is the stub,
    so the interface name reaches nothing real.
    """
    text = HELPER.read_text(encoding="utf-8")
    assert text.count(IFACE_LINE) == 1, f"{IFACE_LINE.strip()!r} not found exactly once"
    helper = tmp_path / "milo-client-join-wifi"
    helper.write_text(text.replace(IFACE_LINE, 'IFACE="lo"\n'), encoding="utf-8")

    stubs = tmp_path / "bin"
    stubs.mkdir()
    nmcli = stubs / "nmcli"
    nmcli.write_text(f"#!{sys.executable}\n{NMCLI_STUB}")
    nmcli.chmod(0o755)
    return {"helper": helper, "stubs": stubs, "state": tmp_path / "nm.json", "log": tmp_path / "nm.log"}


def _run(world, profiles, psk, active=""):
    world["state"].write_text(json.dumps({"profiles": profiles, "active": active, "added": []}))
    result = subprocess.run(
        ["bash", str(world["helper"])],
        input=f"\n{SSID}\n{psk}\n",
        env={"PATH": f"{world['stubs']}:/usr/bin:/bin",
             "NM_STATE": str(world["state"]), "NM_LOG": str(world["log"])},
        capture_output=True, text=True, timeout=30,
    )
    log = world["log"].read_text() if world["log"].exists() else ""
    # Without this, a PATH that missed the stub would drive the host's real nmcli.
    assert "connection show" in log, f"the helper never reached the nmcli stub; stderr: {result.stderr!r}"
    return result, json.loads(world["state"].read_text())["profiles"]


def _wpa(psk=PSK):
    return {"ssid": SSID, "psk": psk, "key_mgmt": "wpa-psk", "autoconnect": "yes"}


def test_an_empty_key_never_replaces_a_secured_profile_with_an_open_one(world):
    result, profiles = _run(world, {"wpa": _wpa()}, psk="", active="wpa")

    assert result.returncode != 0
    assert "open" in result.stderr and SSID in result.stderr
    assert profiles == {"wpa": _wpa()}


def test_the_key_it_already_holds_leaves_the_profile_alone(world):
    result, profiles = _run(world, {"wpa": _wpa()}, psk=PSK, active="wpa")

    assert (result.returncode, result.stdout.strip()) == (0, "unchanged")
    assert profiles == {"wpa": _wpa()}


def test_a_new_key_replaces_the_secured_profile(world):
    result, profiles = _run(world, {"wpa": _wpa("old-password")}, psk=PSK)

    assert (result.returncode, result.stdout.strip()) == (0, "saved")
    assert list(profiles.values()) == [_wpa()]


def test_an_open_network_is_still_kept(world):
    """The refusal is about a secured profile of the same SSID, not about open networks."""
    open_profile = {"ssid": SSID, "psk": "", "key_mgmt": "", "autoconnect": "yes"}

    result, profiles = _run(world, {}, psk="")
    assert (result.returncode, result.stdout.strip()) == (0, "saved")
    assert list(profiles.values()) == [open_profile]

    result, profiles = _run(world, {"open": open_profile}, psk="")
    assert (result.returncode, result.stdout.strip()) == (0, "unchanged")
