"""`milo-client-set-timezone`, run for real under bash against a stub timedatectl.

The helper is granted to the app through sudo with no argument at all, so the
zone it reads on stdin is the one input reaching root that sudo checks nothing
of: whatever the shape check lets through is handed to `timedatectl
set-timezone`, and a zone that resolves to no compiled zone leaves
/etc/localtime pointing at nothing, every timestamp on the unit silently UTC.
And the server pushes at every admission, so a helper that sets an unchanged
zone again writes /etc/localtime at every reconnection. Consumer:
`PUT /api/system/timezone` (routes/system.py), which answers exit 2 as a 400 to
`SatelliteTimezoneService`.
"""

import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-set-timezone"
)
ZONEINFO_LINE = 'ZONEINFO_DIR="/usr/share/zoneinfo"\n'
TZIF = b"TZif2" + b"\0" * 40
LONG_NAME = "A" * 70

# timedated as the helper reaches it: the zone in force in one file, every call
# appended to a log. Only the two invocations the helper makes are modelled; any
# other one fails loudly, so a new call cannot pass on an empty answer.
TIMEDATECTL_STUB = r'''
import os, sys

state, log = os.environ["TZ_STATE"], os.environ["TZ_LOG"]
args = sys.argv[1:]
with open(log, "a") as f:
    f.write(" ".join(args) + "\n")
if args == ["show", "--property=Timezone", "--value"]:
    print(open(state).read())
elif len(args) == 2 and args[0] == "set-timezone":
    open(state, "w").write(args[1])
else:
    sys.exit(f"timedatectl stub: unmodelled call {args!r}")
'''


@pytest.fixture
def world(tmp_path):
    """A copy of the helper reading a zoneinfo tree of our own, and the timedatectl stub.

    The tree holds what the real one holds besides zones: a link to
    /etc/localtime, a text file that is not compiled, and — what tzdata never
    ships but a traversal would reach — a link leading out of the tree.
    """
    zoneinfo = tmp_path / "zoneinfo"
    for zone in ("Europe/Paris", "Etc/UTC", "America/Argentina/Salta", "Etc/GMT+5"):
        (zoneinfo / zone).parent.mkdir(parents=True, exist_ok=True)
        (zoneinfo / zone).write_bytes(TZIF)
    (zoneinfo / "Europe" / "Belfast").symlink_to("../Etc/UTC")
    (zoneinfo / "Europe" / "notes").write_text("# Al Jazeera, not a zone\n")
    outside = tmp_path / "outside"
    outside.write_bytes(TZIF)
    (zoneinfo / "Europe" / "Escape").symlink_to(outside)
    (zoneinfo / "localtime").symlink_to(zoneinfo / "Europe" / "Paris")
    # Each refused by one guard alone: a compiled zone under a name past the
    # length cap, and a FIFO, which `head` would wait on forever.
    (zoneinfo / "Etc" / LONG_NAME).write_bytes(TZIF)
    os.mkfifo(zoneinfo / "Europe" / "Pipe")

    text = HELPER.read_text(encoding="utf-8")
    assert text.count(ZONEINFO_LINE) == 1, f"{ZONEINFO_LINE.strip()!r} not found exactly once"
    helper = tmp_path / "milo-client-set-timezone"
    helper.write_text(text.replace(ZONEINFO_LINE, f'ZONEINFO_DIR="{zoneinfo}"\n'), encoding="utf-8")

    stubs = tmp_path / "bin"
    stubs.mkdir()
    timedatectl = stubs / "timedatectl"
    timedatectl.write_text(f"#!{sys.executable}\n{TIMEDATECTL_STUB}")
    timedatectl.chmod(0o755)
    return {"helper": helper, "stubs": stubs, "state": tmp_path / "tz", "log": tmp_path / "tz.log"}


def _run(world, stdin, current="Etc/UTC", args=(), timeout=30):
    world["state"].write_text(current)
    result = subprocess.run(
        ["bash", str(world["helper"]), *args],
        **({"input": stdin} if isinstance(stdin, str) else {"stdin": stdin}),
        env={"PATH": f"{world['stubs']}:/usr/bin:/bin",
             "TZ_STATE": str(world["state"]), "TZ_LOG": str(world["log"])},
        capture_output=True, text=True, timeout=timeout,
    )
    calls = world["log"].read_text().splitlines() if world["log"].exists() else []
    return result, calls, world["state"].read_text()


def test_a_new_zone_is_set_exactly_as_validated(world):
    result, calls, zone = _run(world, "Europe/Paris\n")

    assert (result.returncode, result.stdout.strip()) == (0, "set"), result.stderr
    assert calls == ["show --property=Timezone --value", "set-timezone Europe/Paris"]
    assert zone == "Europe/Paris"


def test_the_zone_already_in_force_writes_nothing(world):
    """Pushed at every admission: setting it again rewrites /etc/localtime at every reconnection."""
    result, calls, _ = _run(world, "Europe/Paris\n", current="Europe/Paris")

    assert (result.returncode, result.stdout.strip()) == (0, "unchanged"), result.stderr
    assert calls == ["show --property=Timezone --value"]


@pytest.mark.parametrize("zone", ["America/Argentina/Salta", "Etc/GMT+5", "Europe/Belfast"])
def test_every_shape_tzdata_ships_is_accepted(world, zone):
    """Three segments, a `+`, and a link inside the tree are all real zones."""
    result, calls, _ = _run(world, f"{zone}\n")

    assert result.returncode == 0, result.stderr
    assert calls[-1] == f"set-timezone {zone}"


@pytest.mark.parametrize("zone", [
    "",
    "../../../etc/passwd",
    "Europe/../Etc/UTC",
    "/Europe/Paris",
    "Europe/Paris/",
    "Europe//Paris",
    "-h",
    "--help",
    "Etc/--help",
    "Europe/Paris extra",
    "Europe/Paris;reboot",
    "Europe/Pa$(id)ris",
    "UTC",
    "localtime",
    "Europe/Missing",
    "Europe/notes",
    "Europe/Escape",
    "America/Argentina",
    f"Etc/{LONG_NAME}",
])
def test_a_zone_it_cannot_vouch_for_never_reaches_timedatectl(world, zone):
    """Refused with exit 2, which the route answers 400 — before timedated is even asked."""
    result, calls, state = _run(world, f"{zone}\n")

    assert result.returncode == 2, (zone, result.stdout, result.stderr)
    assert result.stderr.startswith("ERROR:")
    assert calls == []
    assert state == "Etc/UTC"


def test_only_the_first_line_is_the_zone(world):
    """A second line is not appended to the first: `read` stops at the newline."""
    result, calls, _ = _run(world, "Europe/Paris\n../../etc/passwd\n")

    assert result.returncode == 0, result.stderr
    assert calls[-1] == "set-timezone Europe/Paris"


def test_an_argument_is_refused_whatever_it_says(world):
    """sudo grants the helper with none; one given here is a caller that bypassed the route."""
    result, calls, _ = _run(world, "Europe/Paris\n", args=("Europe/Paris",))

    assert result.returncode == 1
    assert calls == []



def test_a_zone_that_is_no_regular_file_is_refused_without_reading_it(world):
    """A FIFO under the tree is opened by nothing: `head` would block on it, the sudo session with it."""
    result, calls, _ = _run(world, "Europe/Pipe\n", timeout=5)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert calls == []


def test_a_terminal_on_stdin_is_refused_rather_than_waited_on(world):
    """`sudo milo-client-set-timezone` typed by hand: `read` would hold the session open for good."""
    primary, secondary = pty.openpty()
    try:
        result, calls, _ = _run(world, secondary, timeout=5)
    finally:
        os.close(primary)
        os.close(secondary)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert calls == []
