"""The local client's identity, as the backend reads it for itself.

The rule (eth0, wlan0, then the other physical interfaces) is held equal to the
snapclient's `--hostID` by tests/architecture/test_host_id_rule.py. What is
pinned here is `get_local_mac` around it: it reads the sysfs root the config
names, and reads it again on every call. An identity kept from an earlier read
would outlive the one the snapclient passes once an interface comes up late,
and the server's own room would stay unadmitted until a restart.
"""
from backend.config import constants
from backend.core.multiroom import identity


def _iface(root, name, mac):
    (root / name / "device").mkdir(parents=True, exist_ok=True)
    (root / name / "address").write_text(mac + "\n")


def test_the_identity_is_read_from_the_configured_root(tmp_path, monkeypatch):
    _iface(tmp_path, "wlan0", "02:00:00:00:00:0f")
    monkeypatch.setattr(constants, "SYSFS_NET", str(tmp_path))
    assert identity.get_local_mac() == "02:00:00:00:00:0f"


def test_the_identity_follows_an_interface_that_comes_up_late(tmp_path, monkeypatch):
    """The snapclient reads its id anew at every start; the backend must too."""
    _iface(tmp_path, "wlan0", "02:00:00:00:00:0f")
    monkeypatch.setattr(constants, "SYSFS_NET", str(tmp_path))
    assert identity.get_local_mac() == "02:00:00:00:00:0f"

    _iface(tmp_path, "eth0", "02:00:00:00:00:0e")
    assert identity.get_local_mac() == "02:00:00:00:00:0e"
