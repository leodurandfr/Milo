"""The local client's identity: the MAC of eth0, else wlan0, read from sysfs.

Every snapclient launcher passes the same eth0-then-wlan0 MAC as `--hostID`, so
a change of order or a lost fallback here keys the local room under an id no
snapclient announces: the registry, the volume service and the EQ router stop
recognising the server's own speaker.
"""
from backend.core.multiroom import identity


def _sysfs(tmp_path, monkeypatch, **macs):
    for iface, mac in macs.items():
        (tmp_path / iface).mkdir()
        (tmp_path / iface / "address").write_text(mac + "\n")
    monkeypatch.setattr(identity, "SYSFS_NET", str(tmp_path))


def test_eth0_is_read_first(tmp_path, monkeypatch):
    _sysfs(tmp_path, monkeypatch, eth0="02:00:00:00:00:0e", wlan0="02:00:00:00:00:0f")
    assert identity.get_local_mac() == "02:00:00:00:00:0e"


def test_wlan0_stands_in_when_there_is_no_eth0(tmp_path, monkeypatch):
    _sysfs(tmp_path, monkeypatch, wlan0="02:00:00:00:00:0f")
    assert identity.get_local_mac() == "02:00:00:00:00:0f"


def test_no_interface_is_no_identity(tmp_path, monkeypatch):
    _sysfs(tmp_path, monkeypatch)
    assert identity.get_local_mac() is None
