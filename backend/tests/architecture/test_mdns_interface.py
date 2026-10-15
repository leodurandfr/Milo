"""Structural guardrail: milo.local waits for ethernet's lease, as NetworkManager tells it.

`90-milo-network` decides which interface Avahi publishes milo.local on. At boot
wlan0 can lease before eth0 (2026-10-14: wlan0 at 26.0 s, eth0 at 42.4 s), and a
satellite that resolves the name in that window keeps the wlan0 address for its
whole session. So the WiFi fallback waits while NetworkManager is still bringing
eth0 up — and `90-milo-ethernet-ipv4.conf` (ipv4.required-timeout) is what keeps
eth0 "activating" until it has IPv4: out of the box NetworkManager calls a link
active on IPv6 alone, 12 s before its IPv4 lease on the server, measured.

The wait is a boot-time wait, and this Pi has no RTC: timesyncd steps the wall
clock mid-boot, which ends a wall-clock wait at once. A bash script and an ini
file read by NetworkManager have no import to fail, so the guard is reading them.
"""
import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCHER = REPO_ROOT / "rootfs/etc/NetworkManager/dispatcher.d/90-milo-network"
ETHERNET_IPV4 = REPO_ROOT / "rootfs/etc/NetworkManager/conf.d/90-milo-ethernet-ipv4.conf"


def _code() -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in DISPATCHER.read_text(encoding="utf-8").splitlines()
    )


def test_the_wifi_fallback_waits_while_eth0_is_activating():
    """Without the wait, milo.local goes to wlan0 whenever its lease lands first."""
    code = _code()
    assert "has_ip eth0" in code, "the dispatcher no longer reads eth0's address; the parse is broken"
    assert "wait_for_eth0_lease" in code, "the WiFi fallback no longer waits for eth0's lease"
    assert "GENERAL.STATE" in code, (
        "the wait no longer follows NetworkManager's state for eth0: an unplugged "
        "cable or a profile that never activates would wait for nothing"
    )


def test_a_wired_link_is_active_only_with_ipv4():
    """The wait ends when eth0 stops activating; NetworkManager must not end
    that on IPv6 alone, or the WiFi lease wins the race again."""
    config = configparser.ConfigParser()
    config.read(ETHERNET_IPV4)
    sections = [name for name in config.sections() if name.startswith("connection")]
    assert sections, f"{ETHERNET_IPV4.name}: no [connection*] section; the parse is broken"
    wired = [config[name] for name in sections if config[name].get("match-device") == "type:ethernet"]
    assert wired, "no connection defaults apply to ethernet devices"
    assert int(wired[0].get("ipv4.required-timeout", "0")) > 0, (
        "ethernet links no longer wait for IPv4 before they count as active"
    )


def test_the_wait_runs_on_the_monotonic_clock():
    """A clock step mid-wait must not end it early."""
    code = _code()
    assert "ETH0_ACTIVATION_CAP_S" in code, "the dispatcher no longer declares its cap"
    for wall_clock in ("SECONDS", "date +%s", "EPOCHREALTIME", "EPOCHSECONDS"):
        assert wall_clock not in code, (
            f"90-milo-network reads {wall_clock}, which a clock step moves; time the wait from /proc/uptime"
        )
    assert "/proc/uptime" in code, "the wait reads no monotonic clock"
