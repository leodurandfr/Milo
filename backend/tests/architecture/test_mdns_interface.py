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

Which interface the dispatcher picks is also run for real, under stubs: the
state file and the conf have two writers each, so it must check both.
"""
import configparser
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCHER = REPO_ROOT / "rootfs/etc/NetworkManager/dispatcher.d/90-milo-network"
SATELLITE_DISPATCHER = REPO_ROOT / "milo-client/rootfs/etc/NetworkManager/dispatcher.d/90-milo-network"
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


# Each stub appends its name and argv to $STUB_LOG. `ip` answers an address only
# for the interfaces in $STUB_LEASED; eth0 reads "unavailable" to NetworkManager
# with no carrier (the copy's carrier file holds 0), so the WiFi fallback's wait
# ends at once.
_STUBS = {
    "ip": 'case " $STUB_LEASED " in *" $4 "*) echo "    inet 192.168.1.10/24 scope global $4" ;; esac',
    "nmcli": (
        'case "$*" in\n'
        '  *GENERAL.STATE*) echo "20 (unavailable)" ;;\n'
        '  *connection*) echo "Home:wlan0" ;;\n'
        "esac"
    ),
    "systemctl": "exit 0",
}


def _run_dispatcher(dispatcher: Path, tmp_path: Path, conf: str, state: str, leased: str, event: str):
    """Run a copy of `dispatcher` on `event dhcp4-change` against a temporary conf
    and state file, under the stubs. Returns the stub calls and the state file."""
    conf_file = tmp_path / "avahi-daemon.conf"
    conf_file.write_text(f"[server]\n{conf}\n")
    state_file = tmp_path / "avahi-interface"
    state_file.write_text(f"{state}\n")
    script = re.sub(r'^AVAHI_CONF=.*$', f'AVAHI_CONF="{conf_file}"', dispatcher.read_text(), count=1, flags=re.M)
    script = re.sub(r'^AVAHI_STATE_FILE=.*$', f'AVAHI_STATE_FILE="{state_file}"', script, count=1, flags=re.M)
    assert str(conf_file) in script and str(state_file) in script, "the dispatcher's paths were not rewritten"
    # The server's wait reads eth0's carrier; never this host's.
    carrier = tmp_path / "carrier"
    carrier.write_text("0\n")
    script = re.sub(r'^ETH0_CARRIER_FILE=.*$', f'ETH0_CARRIER_FILE="{carrier}"', script, count=1, flags=re.M)
    copy = tmp_path / "90-milo-network"
    copy.write_text(script)

    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in _STUBS.items():
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "$STUB_LOG"\n{body}\n')
        stub.chmod(0o755)
    log = tmp_path / "stub.log"

    result = subprocess.run(
        ["bash", str(copy), event, "dhcp4-change"],
        env={"PATH": f"{stubs}:/usr/bin:/bin", "STUB_LOG": str(log), "STUB_LEASED": leased},
        capture_output=True, text=True, timeout=30,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    assert any(call.startswith("ip ") for call in calls), (
        f"the dispatcher never reached the ip stub; calls: {calls!r}, stderr: {result.stderr!r}"
    )
    assert result.returncode == 0, result.stderr
    return calls, state_file


@pytest.mark.parametrize(
    ("conf", "state", "leased", "event", "expected"),
    [
        # milo-first-boot reset the state to eth0 under a conf still on wlan0.
        ("wlan0", "eth0", "wlan0", "wlan0", "wlan0"),
        # An update copied the repo's conf (eth0) under an Avahi left on wlan0.
        ("eth0", "wlan0", "eth0", "eth0", "eth0"),
        # Both agree: a lease renewal must not restart Avahi.
        ("eth0", "eth0", "eth0", "eth0", None),
    ],
)
def test_a_state_reset_is_not_masked_by_a_stale_conf(tmp_path, conf, state, leased, event, expected):
    """milo-first-boot writes the state file and milo-deploy-update the conf, neither
    restarts Avahi, and Avahi's ExecStartPre applies the state. A dispatcher that
    trusted the conf alone left milo.local on no useful link until the next lease
    (hours); one that restarts on every match would restart Avahi at each renewal."""
    calls, state_file = _run_dispatcher(DISPATCHER, tmp_path, f"allow-interfaces={conf}", state, leased, event)
    restarts = [call for call in calls if call.startswith("systemctl ")]

    if expected is None:
        assert restarts == [], f"Avahi restarted on a lease renewal that changed nothing: {restarts!r}"
        return
    assert state_file.read_text().strip() == expected
    # Blocking would wait behind milo-first-boot, which runs Before=avahi-daemon.
    assert restarts == ["systemctl restart --no-block avahi-daemon.service"], restarts


_DENIED = {"eth0": "wlan0", "wlan0": "eth0"}


@pytest.mark.parametrize(
    ("conf", "state", "expected"),
    [
        # The conf is right but the state the next Avahi start applies is not.
        ("eth0", "wlan0", "eth0"),
        # Avahi is on the fallback link while eth0 has its lease back.
        ("wlan0", "wlan0", "eth0"),
        # Both agree: a lease renewal must not restart Avahi.
        ("eth0", "eth0", None),
    ],
)
def test_the_satellite_dispatcher_checks_the_state_too(tmp_path, conf, state, expected):
    """The satellite's twin of the rule above, with its own paths and both keys.

    milo-client-apply-avahi-iface rewrites the conf from the state file only when
    Avahi starts, so a conf that looks right says nothing about what the next
    start applies — an update that changes Avahi's drop-in restarts it. And a
    blocking restart holds NetworkManager's dispatcher queue until Avahi is up.
    """
    calls, state_file = _run_dispatcher(
        SATELLITE_DISPATCHER, tmp_path,
        f"allow-interfaces={conf}\ndeny-interfaces={_DENIED[conf]}", state, leased="eth0", event="eth0",
    )
    restarts = [call for call in calls if call.startswith("systemctl ")]

    if expected is None:
        assert restarts == [], f"Avahi restarted on a lease renewal that changed nothing: {restarts!r}"
        return
    assert state_file.read_text().strip() == expected
    assert restarts == ["systemctl restart --no-block avahi-daemon.service"], restarts


# The wait itself, run for real. NetworkManager answers eth0's state from a
# sequence, one entry per read ($STUB_STATES, the last one repeated), and eth0
# gets its lease at the read numbered $STUB_LEASE_AT (0: never). wlan0 holds its
# lease from the start: this is the boot where WiFi leases first.
_WAIT_STUBS = {
    "ip": (
        'case "$4" in\n'
        '  wlan0) echo "    inet 192.168.1.39/24 scope global wlan0" ;;\n'
        '  eth0) [ -e "$STUB_DIR/eth0_leased" ] && echo "    inet 192.168.1.55/24 scope global eth0" ;;\n'
        "esac"
    ),
    "nmcli": (
        'case "$*" in\n'
        "  *GENERAL.STATE*)\n"
        '    n=$(( $(cat "$STUB_DIR/reads" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_DIR/reads"\n'
        '    set -- $STUB_STATES; i=1; state=$1\n'
        '    for s in "$@"; do [ "$i" -le "$n" ] && state=$s; i=$((i + 1)); done\n'
        '    [ "$STUB_LEASE_AT" -gt 0 ] && [ "$n" -ge "$STUB_LEASE_AT" ] && : > "$STUB_DIR/eth0_leased"\n'
        '    echo "$state (state)" ;;\n'
        '  *connection*) echo "Home:wlan0" ;;\n'
        "esac"
    ),
    "systemctl": "exit 0",
}


def _run_wait(tmp_path: Path, carrier: int, states: str, lease_at: int, settle_s: int = 5):
    """Run the server's dispatcher on wlan0's lease while eth0 moves through `states`.
    Returns the interface it put milo.local on and how many times it read eth0's state."""
    conf_file = tmp_path / "avahi-daemon.conf"
    conf_file.write_text("[server]\nallow-interfaces=eth0\n")
    state_file = tmp_path / "avahi-interface"
    state_file.write_text("none\n")
    carrier_file = tmp_path / "carrier"
    carrier_file.write_text(f"{carrier}\n")
    script = DISPATCHER.read_text()
    for name, value in (
        ("AVAHI_CONF", conf_file), ("AVAHI_STATE_FILE", state_file),
        ("ETH0_CARRIER_FILE", carrier_file), ("ETH0_SETTLE_S", settle_s),
    ):
        script, count = re.subn(rf'^{name}=.*$', f'{name}="{value}"', script, count=1, flags=re.M)
        assert count == 1, f"the dispatcher no longer declares {name}; the rewrite is broken"
    copy = tmp_path / "90-milo-network"
    copy.write_text(script)

    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in _WAIT_STUBS.items():
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\n{body}\n')
        stub.chmod(0o755)
    result = subprocess.run(
        ["bash", str(copy), "wlan0", "dhcp4-change"],
        env={"PATH": f"{stubs}:/usr/bin:/bin", "STUB_DIR": str(tmp_path),
             "STUB_STATES": states, "STUB_LEASE_AT": str(lease_at)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    reads_file = tmp_path / "reads"
    reads = int(reads_file.read_text()) if reads_file.exists() else 0
    assert reads >= 1, f"the dispatcher never read eth0's state; stderr: {result.stderr!r}"
    return state_file.read_text().strip(), reads


def test_a_cable_networkmanager_has_not_started_on_still_wins_milo_local(tmp_path):
    """The carrier lands before eth0 leaves disconnected (30) for prepare (40).

    A single read of 30 in that gap ended the wait, so wlan0's earlier lease
    published milo.local on WiFi, and a satellite resolving it then kept that
    address for its whole session.
    """
    published, reads = _run_wait(tmp_path, carrier=1, states="30 30 50 70", lease_at=4)
    assert published == "eth0", f"milo.local went to {published} after {reads} reads of eth0's state"


def test_an_absent_cable_waits_nothing(tmp_path):
    """A WiFi-only unit runs this on every wlan0 event; it must not wait for a cable that is not there."""
    published, reads = _run_wait(tmp_path, carrier=0, states="20", lease_at=0)
    assert published == "wlan0"
    assert reads == 1, f"no carrier, yet eth0's state was read {reads} times"


def test_a_cable_whose_profile_never_starts_waits_only_the_settle_bound(tmp_path):
    """Carrier but no activation (no autoconnect profile): the wait must still end, on its own bound."""
    published, reads = _run_wait(tmp_path, carrier=1, states="30", lease_at=0, settle_s=1)
    assert published == "wlan0"
    assert reads > 1, "a cable with carrier was given no time to start"
