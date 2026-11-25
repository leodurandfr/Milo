"""Structural guardrail: the server waits on a satellite's snapclient or
CamillaDSP install longer than the satellite may take to run it.

After a satellite answers `started`, the server follows the install through
`/<program>/update/status` and gives up at `PROGRAM_UPDATE_CEILING_S` with
`did_not_return` — the row then says the speaker is not responding while the
install goes on and lands. A fixed 180 s did exactly that to an install the
satellite allowed to run past 300 s.

The satellite's own steps are bounded in three files of its tree: the download
by `DOWNLOAD_TIMEOUT_S` (services/release_download.py), the wait for apt before
snapclient is stopped by `APT_LOCK_WAIT_S` (services/snapclient.py), and
snapclient's `apt-get update` by `APT_UPDATE_TIMEOUT_S` (the install wrapper).
What follows — dpkg on a local package, the service's restart, a rollback — has
no timer of its own and is counted as a budget. Each extractor asserts its own output first.
"""
import ast
import re
from pathlib import Path

from backend.core.updates.satellite import (
    PROGRAM_SILENCE_S,
    PROGRAM_UPDATE_CEILING_S,
    PROGRAM_UPDATE_POLL_S,
)

REPO = Path(__file__).resolve().parents[3]
CLIENT = REPO / "milo-client"
DOWNLOAD = CLIENT / "app" / "services" / "release_download.py"
SNAPCLIENT = CLIENT / "app" / "services" / "snapclient.py"
WRAPPER = CLIENT / "rootfs" / "usr" / "local" / "bin" / "milo-client-install-snapclient"

# dpkg's run on the local .deb, the dependency it may fetch, the version reads
# (5 s each) and settling waits, the unit's restart — or CamillaDSP's rollback.
# A budget, not a measurement: the manual checklist is where it is checked.
INSTALL_BUDGET_S = 240
# Two polls: the final state can land just after one, and the waiter counts its
# sleeps, not the time its requests take.
MARGIN_S = 2 * PROGRAM_UPDATE_POLL_S


def _python_int(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [
        node.value.value for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int)
    ]
    assert len(values) == 1, f"{path.name}: expected one module-level `{name} = <int>`, found {values}"
    return values[0]


def _shell_int(path: Path, name: str) -> int:
    found = re.findall(rf"^{name}=(\d+)\s*$", path.read_text(encoding="utf-8"), re.M)
    assert len(found) == 1, f"{path.name}: expected one `{name}=<int>`, found {found}"
    return int(found[0])


def test_the_extractors_read_plausible_values():
    assert _python_int(DOWNLOAD, "DOWNLOAD_TIMEOUT_S") > 0
    assert _python_int(SNAPCLIENT, "APT_LOCK_WAIT_S") > 0
    assert _shell_int(WRAPPER, "APT_UPDATE_TIMEOUT_S") > 0


def test_the_wrapper_bounds_apt_update_with_its_own_value():
    """A bound declared and never applied would hold the inequality below for
    an `apt-get update` that still runs for as long as a mirror stalls."""
    text = WRAPPER.read_text(encoding="utf-8")
    assert re.search(r'^timeout "\$APT_UPDATE_TIMEOUT_S" apt-get update\b', text, re.M), (
        f"{WRAPPER.name} no longer runs `apt-get update` under APT_UPDATE_TIMEOUT_S"
    )
    assert not re.search(r"^apt-get update\b", text, re.M), f"{WRAPPER.name} runs an unbounded apt-get update"


def test_the_snapclient_update_waits_for_apt_with_its_own_value():
    """A wait declared and never applied would hold the inequality below for a
    wait bounded by some other value, or by none."""
    text = SNAPCLIENT.read_text(encoding="utf-8")
    assert re.search(r"\bwait_until_free\(\s*APT_LOCK_WAIT_S\b", text), (
        f"{SNAPCLIENT.name} no longer waits for apt under APT_LOCK_WAIT_S"
    )
    assert len(re.findall(r"\bwait_until_free\(", text)) == 1, (
        f"{SNAPCLIENT.name} waits for apt more than once: count every wait below"
    )


def test_the_server_outwaits_the_slowest_install_a_satellite_can_run():
    bound = (
        _python_int(DOWNLOAD, "DOWNLOAD_TIMEOUT_S")
        + _python_int(SNAPCLIENT, "APT_LOCK_WAIT_S")
        + _shell_int(WRAPPER, "APT_UPDATE_TIMEOUT_S")
        + INSTALL_BUDGET_S
    )
    assert PROGRAM_UPDATE_CEILING_S > bound + MARGIN_S, (
        f"PROGRAM_UPDATE_CEILING_S ({PROGRAM_UPDATE_CEILING_S} s) must exceed the slowest "
        f"install a satellite may run ({bound} s) plus {MARGIN_S} s, or the row reports a "
        "speaker not responding while its install goes on and lands"
    )


def test_a_silent_satellite_is_given_up_on_before_the_ceiling():
    """The satellite's API answers through these installs (neither restarts its
    app), so a silence is a unit gone: waited out to the ceiling, an unplugged
    speaker kept its row spinning for a quarter of an hour."""
    assert PROGRAM_UPDATE_POLL_S < PROGRAM_SILENCE_S < PROGRAM_UPDATE_CEILING_S
