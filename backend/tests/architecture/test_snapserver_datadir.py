"""Structural guardrail: snapserver keeps its server.json in RAM.

snapserver 0.35 rewrites server.json two seconds after every control request —
the backend's 30 s `Server.GetStatus` included — while holding the lock its
time-sync answers and every audio chunk also take. On the SD card a stalled
write froze every room at once: 7-18 s at a cold boot on 2026-11-06, 2-4 s in
normal running, with the satellites' 2 s hello and time-sync timeouts firing in
the same second. A unit that drops the flag goes back to `~/.config` on the card
with no error anywhere, so the flag is pinned to the unit's own RuntimeDirectory.
"""

from pathlib import Path

from backend.tests.architecture.test_unit_hardening import service_section

UNIT = Path(__file__).resolve().parents[3] / "system" / "milo-snapserver-multiroom.service"


def test_snapserver_writes_its_server_json_under_its_runtime_directory():
    directives = service_section(UNIT)
    (exec_start,) = directives["ExecStart"]
    assert exec_start.startswith("/usr/bin/snapserver "), exec_start
    (runtime_directory,) = directives.get("RuntimeDirectory", [None])
    assert runtime_directory, f"{UNIT.name} declares no RuntimeDirectory= (a tmpfs under /run)"

    datadirs = [arg.partition("=")[2] for arg in exec_start.split() if arg.startswith("--server.datadir=")]
    assert datadirs == [f"/run/{runtime_directory}"], (
        f"{UNIT.name} must start snapserver with --server.datadir=/run/{runtime_directory}: "
        "without it server.json goes back to ~/.config on the SD card, and a stalled "
        "write there freezes every room"
    )
