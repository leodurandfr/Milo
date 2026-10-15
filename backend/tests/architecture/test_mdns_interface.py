"""Structural guardrail: milo.local waits for ethernet's lease on the monotonic clock.

`90-milo-network` decides which interface Avahi publishes milo.local on. At boot
wlan0 can lease before eth0 (2026-10-14: wlan0 at 26.0 s, eth0 at 42.4 s after a
carrier flap), and a satellite that resolves the name in that window keeps the
wlan0 address for its whole session — its watch never cuts a client that
streams — until a wlan0 lease renewal removes it and cuts that one room. So the
WiFi fallback waits a bounded grace while eth0 has a cable and no lease.

That grace is a boot-time wait, and this Pi has no RTC: timesyncd steps the wall
clock mid-boot (35 s into that same boot), which ends a wall-clock wait at once.
A bash script read by NetworkManager's dispatcher has no import to fail, so the
only guard is reading it.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCHER = REPO_ROOT / "rootfs/etc/NetworkManager/dispatcher.d/90-milo-network"


def _code() -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in DISPATCHER.read_text(encoding="utf-8").splitlines()
    )


def test_the_wifi_fallback_waits_for_an_ethernet_lease():
    """Without the wait, milo.local goes to wlan0 whenever its lease lands first."""
    code = _code()
    assert "has_ip eth0" in code, "the dispatcher no longer reads eth0's address; the parse is broken"
    assert "wait_for_eth0_lease" in code, "the WiFi fallback no longer waits for eth0's lease"
    assert "/sys/class/net/eth0/carrier" in code, (
        "the wait no longer depends on eth0 having a cable: an unplugged unit "
        "would wait the whole grace before publishing on WiFi"
    )


def test_the_ethernet_lease_grace_runs_on_the_monotonic_clock():
    """A clock step mid-wait must not end the grace early."""
    code = _code()
    assert "ETH0_LEASE_GRACE_S" in code, "the dispatcher no longer declares its grace"
    for wall_clock in ("SECONDS", "date +%s", "EPOCHREALTIME", "EPOCHSECONDS"):
        assert wall_clock not in code, (
            f"90-milo-network reads {wall_clock}, which a clock step moves; time the grace from /proc/uptime"
        )
    assert "/proc/uptime" in code, "the grace reads no monotonic clock"
