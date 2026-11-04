"""Structural guardrail: the splash always hands the screen over.

Three units decide whether a Milō ever shows a UI, and CI can watch none of them
run: `plymouth-start` paints, `milo-readiness` drives the bar and quits Plymouth,
`milo-kiosk` takes the screen. Plymouth's own quit units are **masked** on
purpose (`provisioning/display.sh`, `pi-gen/…/03-configure/00-run.sh`), so
`milo-wait-ready.sh` is the only thing that ever releases DRM master. If it does
not, the appliance sits on a frozen splash — and the splash is unobservable, so
nobody can even photograph the failure.

Two ways that happened, both measured before this file existed:

  * **An unbounded wait.** `responds()` shelled `curl` with no time limit, while
    the script's whole contract is a 45 s fail-open deadline that is only read
    *between* iterations. Against a socket that completes the TCP handshake and
    never answers, the script ran 100 s — past its own deadline and past the
    unit's `TimeoutStartSec=60`, where systemd SIGTERMs it before it can reach
    `plymouth quit`.
  * **A hard dependency.** `milo-kiosk` carried `Requires=milo-readiness`, and
    `milo-readiness` carried `Requires=milo-backend`. `Requires=` propagates an
    explicit stop, and nothing propagates back (both units are
    `WantedBy=multi-user.target`, already active). One `systemctl stop
    milo-backend` left this appliance's screen dead for 23 minutes, through
    three later `systemctl start milo-backend`.

Nothing else catches this class. These are a bash script and two `.ini` files
read by pid 1 on a machine no test runner touches: there is no import to fail and
no route to 404.

The satellite's snapclient launcher is pinned here too, for the same reason: the
image masks `NetworkManager-wait-online`, so its own wait for NetworkManager's
startup is the only thing that keeps a wired satellite's stream off its WiFi at
boot. That one is run, not read: under `sh`, with NetworkManager, the resolver
and snapclient faked on PATH.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

WAIT_READY = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-wait-ready.sh"
READINESS_UNIT = REPO_ROOT / "system" / "milo-readiness.service"
KIOSK_UNIT = REPO_ROOT / "system" / "milo-kiosk.service"

ROOTFS_TREES = (REPO_ROOT / "rootfs", REPO_ROOT / "milo-client" / "rootfs")

# A command word at the start of a statement — line start, or after `|`, `&&`,
# `||`, `;`, `&`, `(` or a function body's `{` (never `${`). Anchoring this way is
# what keeps the word inside a comment (`# … plymouth quit …`) from reading as an
# invocation, while `progress() { timeout 1 plymouth … }` still reads as one.
STATEMENT_BREAK = re.compile(r"\|\||&&|[|;&)]|(?<!\$)[{(]")

# An optional `timeout <spec>` wrapper and any leading `VAR=value` assignments sit
# between the statement's start and the command word.
PREFIX = r"(?:\w+=\S*\s+)*(?:timeout\s+(?:-\S+\s+)*\S+\s+)?"


def _invocations(text: str, command: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for stmt in STATEMENT_BREAK.split(line):
            stmt = stmt.strip()
            if re.match(rf"^{PREFIX}{re.escape(command)}\b", stmt):
                out.append(stmt)
    return out


def _bounded(statement: str) -> bool:
    """Is this statement's wall time bounded, either by `timeout` or by a flag?"""
    return "timeout " in statement or "--max-time" in statement


def _directives(unit: Path) -> list[tuple[str, str]]:
    out = []
    for line in unit.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            out.append((key.strip(), value.strip()))
    return out


def _values(unit: Path, key: str) -> list[str]:
    return [v for k, v in _directives(unit) if k == key]


def _shell_files(tree: Path) -> list[Path]:
    return sorted(
        p for p in tree.rglob("*")
        if p.is_file() and p.read_text(errors="ignore").startswith("#!")
    )


# --------------------------------------------------------------------------- #
# Non-triviality: a broken read must fail here, not pass everything below.
# --------------------------------------------------------------------------- #

def test_extractors_see_a_real_surface():
    """An empty read of the script or the units makes every rule below vacuous."""
    assert WAIT_READY.is_file(), f"{WAIT_READY} is missing"
    text = WAIT_READY.read_text(encoding="utf-8")

    plymouth = _invocations(text, "plymouth")
    assert len(plymouth) >= 2, f"only {plymouth} plymouth invocation(s) extracted"

    curls = [
        stmt
        for tree in ROOTFS_TREES
        for path in _shell_files(tree)
        for stmt in _invocations(path.read_text(errors="ignore"), "curl")
    ]
    assert curls, "no curl invocation extracted from either rootfs tree"

    for unit in (READINESS_UNIT, KIOSK_UNIT):
        assert len(_directives(unit)) >= 5, f"{unit.name} parsed as {_directives(unit)}"

    # ...and the bound test must discriminate, not rubber-stamp.
    assert _bounded("timeout 1 plymouth quit")
    assert _bounded("curl -sf --max-time 3 http://x/")
    assert not _bounded("curl -sf http://x/")


# --------------------------------------------------------------------------- #
# WI-1: every wait is bounded, and the handover happens even when it is not.
# --------------------------------------------------------------------------- #

def test_every_curl_in_a_rootfs_tree_is_time_bounded():
    """An unbounded poll suspends the deadline that is supposed to bound it.

    `milo-wait-ready.sh` is the case that shipped, but the rule is the tree's:
    every one of these scripts runs from a systemd unit with a start timeout, and
    a `curl` with no `--max-time` waits for as long as the peer keeps the socket
    open — which is forever, for a peer that accepted and then wedged.
    """
    unbounded = [
        f"{path.relative_to(REPO_ROOT)}: {stmt}"
        for tree in ROOTFS_TREES
        for path in _shell_files(tree)
        for stmt in _invocations(path.read_text(errors="ignore"), "curl")
        if not _bounded(stmt)
    ]
    assert not unbounded, (
        "these curl calls have no time bound; add --max-time:\n" + "\n".join(unbounded)
    )


def test_every_plymouth_call_in_the_readiness_script_is_time_bounded():
    """plymouthd is a socket peer like any other, and it holds the screen.

    A wedged daemon blocks the tick that would otherwise re-check DEADLINE, and
    blocks the final `quit` — which is the one call whose whole job is to make
    sure the splash goes away.
    """
    text = WAIT_READY.read_text(encoding="utf-8")
    unbounded = [s for s in _invocations(text, "plymouth") if not _bounded(s)]
    assert not unbounded, (
        "these plymouth calls have no time bound; wrap them in `timeout`:\n"
        + "\n".join(unbounded)
    )


def test_the_readiness_unit_quits_plymouth_even_when_the_script_did_not():
    """The only fallback for a killed or failed readiness run.

    `ExecStopPost=` runs when the unit stops, including after a failed or
    timed-out start. Without it, a SIGTERM at `TimeoutStartSec` leaves plymouthd
    holding DRM master and nothing else will ever ask it to let go —
    plymouth-quit.service and plymouth-quit-wait.service are both masked.
    """
    post = _values(READINESS_UNIT, "ExecStopPost")
    assert post, "milo-readiness.service declares no ExecStopPost"
    quits = [v for v in post if "plymouth" in v and "quit" in v]
    assert quits, f"no ExecStopPost quits plymouth: {post}"
    for line in quits:
        assert line.startswith("-"), (
            f"{line!r} must be prefixed with `-`, or a failed handover fails the unit"
        )
        assert _bounded(line), f"{line!r} is unbounded — a second way to hang the boot"


def test_the_readiness_deadline_runs_on_the_monotonic_clock():
    """The 45 s fail-open must mean 45 s, whatever the wall clock does meanwhile.

    bash's `$SECONDS` follows the wall clock, and a Pi has no RTC: timesyncd's
    first sync steps it forward by however long the unit was off. Measured
    2026-09-27, a 46 min step 14 s into the wait expired the deadline at once, and
    the kiosk loaded a page whose every request answered 502 — a UI in English,
    with no favorites, until someone reloaded it.
    """
    code = "\n".join(
        line.split("#", 1)[0] for line in WAIT_READY.read_text(encoding="utf-8").splitlines()
    )
    assert "DEADLINE" in code, "the readiness script no longer declares a deadline"
    assert not re.search(r"\bSECONDS\b", code), (
        "milo-wait-ready.sh reads $SECONDS, which a clock step moves; "
        "time the deadline from /proc/uptime"
    )
    assert "/proc/uptime" in code, "the readiness deadline reads no monotonic clock"


# --------------------------------------------------------------------------- #
# WI-2: the screen does not follow the services it merely waits on.
# --------------------------------------------------------------------------- #

def test_readiness_does_not_require_the_services_it_polls():
    """`Requires=` here contradicts the script's own fail-open contract.

    It also propagates an explicit stop down to `milo-kiosk`, which nothing
    propagates back — measured: 23 minutes of dead screen after one
    `systemctl stop milo-backend`.
    """
    required = " ".join(_values(READINESS_UNIT, "Requires"))
    wanted = " ".join(_values(READINESS_UNIT, "Wants"))
    for polled in ("milo-backend.service", "nginx.service"):
        assert polled not in required, (
            f"milo-readiness.service Requires={polled}; use Wants= — the script "
            "polls it and fails open, and Requires= takes the kiosk down with it"
        )
        assert polled in wanted, f"milo-readiness.service no longer pulls in {polled}"


def test_the_kiosk_is_ordered_after_readiness_without_requiring_it():
    """A readiness that failed must still end in a UI, not in a frozen splash."""
    after = " ".join(_values(KIOSK_UNIT, "After"))
    required = " ".join(_values(KIOSK_UNIT, "Requires"))
    wanted = " ".join(_values(KIOSK_UNIT, "Wants"))

    assert "milo-readiness.service" in after, (
        "milo-kiosk.service must stay ordered after the handover"
    )
    assert "milo-readiness.service" not in required, (
        "milo-kiosk.service Requires=milo-readiness.service; a readiness that "
        "fails or is stopped then means no UI at all"
    )
    assert "milo-readiness.service" in wanted, (
        "milo-kiosk.service no longer pulls in milo-readiness.service at all"
    )
    # seatd is a genuine requirement and must not be relaxed along with it.
    assert "seatd.service" in required, (
        "milo-kiosk.service must keep Requires=seatd.service — no seat, no compositor"
    )


@pytest.mark.parametrize("unit", [READINESS_UNIT, KIOSK_UNIT])
def test_the_boot_chain_units_are_still_installed(unit):
    """Relaxing a dependency must not have removed what starts the unit at all."""
    assert "multi-user.target" in " ".join(_values(unit, "WantedBy")) or \
           "graphical.target" in " ".join(_values(unit, "WantedBy")), (
        f"{unit.name} has no [Install] target left — nothing would start it at boot"
    )


# =============================================================================
# The kiosk follows the screen
# =============================================================================

APPLY_HARDWARE = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-apply-hardware"


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("verb", ["enable", "disable"])
def test_the_kiosk_is_switched_with_the_screen(verb):
    """`milo-apply-hardware` owns both directions of `milo-kiosk.service`.

    A unit configured with no screen kept the kiosk enabled, so cage came up
    against a DRM device with no connected output, failed, and systemd restarted
    it every 3 seconds forever — with `milo-readiness` reading 339 MB of
    Chromium off the SD card at each boot to warm a browser nothing would
    display. Nothing said a word: `milo-kiosk` has no ConditionPath of its own
    and the backend never looked. Disabling is the signal, not the whole cure:
    a restart starts a disabled unit and `milo-readiness` runs either way, so
    the update rollback and the prewarm read `is-enabled` themselves
    (`test_rollback_leaves_a_disabled_kiosk_stopped`,
    `test_the_prewarm_only_runs_for_an_enabled_kiosk`).

    Both verbs, because only pruning is what makes a role half-applied: a
    screen plugged in later must re-enable the kiosk through the same apply.
    This is the one place that already knows the screen changed and that runs
    on both paths which can change it (the wizard, and the Hardware page).
    """
    script = _uncommented(APPLY_HARDWARE)

    # Non-triviality first: a rename would otherwise let both assertions pass
    # against a file that no longer touches the kiosk at all.
    assert "SCREEN_TYPE" in script, "milo-apply-hardware no longer reads the screen type"
    assert "milo-kiosk.service" in script, "milo-apply-hardware no longer touches the kiosk"

    assert re.search(rf"systemctl {verb} milo-kiosk\.service", script), (
        f"milo-apply-hardware never runs `systemctl {verb} milo-kiosk.service`"
    )


@pytest.mark.parametrize("kiosk", ["enabled", "disabled"])
def test_the_prewarm_only_runs_for_an_enabled_kiosk(tmp_path, kiosk):
    """The 339 MB Chromium read is for the kiosk, and a disabled kiosk is a
    unit with no screen (`milo-apply-hardware` disables it there).

    `milo-readiness` still runs on such a unit, on purpose, so the script
    itself has to ask: unguarded, it read the whole browser off the SD card at
    every boot, alongside the backend's own imports, for a window nothing would
    open. The splash is handed over either way — it does not depend on the
    kiosk. The real script runs under `bash` with `systemctl`, `timeout` and
    `curl` faked on PATH; the fake `timeout` records what it was asked to bound
    and runs only `systemctl`, so neither the read nor plymouth happens here.
    """
    calls = tmp_path / "calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rc = 0 if kiosk == "enabled" else 1
    fakes = {
        "systemctl": f'echo "systemctl $*" >> "{calls}"; echo {kiosk}; exit {rc}',
        "timeout": f'echo "timeout $*" >> "{calls}"; shift; '
        f'if [ "$1" = systemctl ]; then exec "$@"; fi; exit 0',
        "curl": "exit 0",
    }
    for name, body in fakes.items():
        fake = bin_dir / name
        fake.write_text(f"#!/bin/sh\n{body}\n")
        fake.chmod(0o755)

    result = subprocess.run(
        ["bash", str(WAIT_READY)],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    log = calls.read_text().splitlines() if calls.exists() else []

    assert result.returncode == 0, f"milo-wait-ready.sh failed: {result.stderr}"
    assert any(
        c.startswith("systemctl is-enabled") and c.endswith(" milo-kiosk.service") for c in log
    ), f"milo-wait-ready.sh never asks whether the kiosk is enabled (calls: {log})"
    prewarmed = any(c.startswith("timeout ") and "cat /usr/lib/chromium" in c for c in log)
    assert prewarmed == (kiosk == "enabled"), (
        f"the Chromium prewarm ran={prewarmed} for a {kiosk} kiosk (calls: {log})"
    )
    assert any(c.startswith("timeout ") and "plymouth quit" in c for c in log), (
        f"the splash is no longer handed over for a {kiosk} kiosk (calls: {log})"
    )


def test_the_apply_helper_can_defer_its_reboot():
    """`--no-reboot` is what lets both backend callers act on the result first.

    Without it the helper takes the box down itself. The setup wizard then has
    to write `setup_completed` first — and a power cut in that window leaves a
    unit that believes it is configured with no dtoverlay in config.txt: no
    sound, no wizard, and no line anywhere saying so. The Hardware page could
    no longer restore the previous hardware.json on a refused apply. The
    reboot arm stays for an operator running the helper by hand.
    """
    script = _uncommented(APPLY_HARDWARE)

    assert "--no-reboot" in script, "milo-apply-hardware always reboots"
    assert "/sbin/reboot" in script, (
        "milo-apply-hardware no longer reboots at all — run by hand it would "
        "leave the new config.txt unapplied until the next boot"
    )


# =============================================================================
# The satellite's snapclient starts on the cable
# =============================================================================

SAT_LAUNCHER = (
    REPO_ROOT / "milo-client" / "rootfs" / "usr" / "local" / "bin" / "milo-client-snapclient-launcher"
)
SAT_ETHERNET_DROPIN = (
    REPO_ROOT / "milo-client" / "rootfs" / "etc" / "NetworkManager" / "conf.d" / "90-milo-ethernet-ipv4.conf"
)


def _run_sat_launcher(tmp_path: Path, nm_online_rc: int) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the real launcher under `sh`, with the outside world faked.

    NetworkManager (`nm-online`), the resolver (`getent`) and `snapclient` are
    stand-ins on PATH that append each call to one log, in order; the fake
    `nm-online` logs again when it returns, so a launcher that does not wait for
    it shows `getent` in between. The hardware file and the snapclient binary are
    absolute paths in the script, so the copy run here points them at the
    stand-ins — and the substitution must hit exactly once, or the copy is no
    longer the launcher.
    """
    calls = tmp_path / "calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fakes = {
        "nm-online": f'echo "nm-online $*" >> "{calls}"; sleep 0.2; echo "nm-online returned" >> "{calls}"; '
        f"exit {nm_online_rc}",
        "getent": f'echo "getent $*" >> "{calls}"; echo "192.0.2.10 milo.local"',
        "snapclient": f'echo "snapclient $*" >> "{calls}"',
    }
    for name, body in fakes.items():
        fake = bin_dir / name
        fake.write_text(f"#!/bin/sh\n{body}\n")
        fake.chmod(0o755)

    hardware = tmp_path / "hardware.json"
    hardware.write_text(json.dumps({"audio": {"id": "hifiberry-dac"}}))
    script = SAT_LAUNCHER.read_text(encoding="utf-8")
    for real, stand_in in (
        ("/var/lib/milo-client/hardware.json", hardware),
        ("/usr/bin/snapclient", bin_dir / "snapclient"),
    ):
        assert script.count(real) == 1, f"the launcher no longer names {real} exactly once"
        script = script.replace(real, str(stand_in))
    launcher = tmp_path / "milo-client-snapclient-launcher"
    launcher.write_text(script)

    result = subprocess.run(
        ["sh", str(launcher)],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MILO_PRINCIPAL_IP": "milo.local"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize("nm_online_rc", [0, 1], ids=["startup-complete", "startup-never-completes"])
def test_the_satellite_snapclient_waits_for_networkmanager_startup(tmp_path, nm_online_rc):
    """`network-online.target` orders nothing on the image, so the launcher waits.

    The image masks `NetworkManager-wait-online` on both roles, so the unit's
    `After=network-online.target` is met as soon as NetworkManager is up. On a
    wired satellite that also holds the server's WiFi, wlan0 leases first:
    measured on Bureau (boot of 2026-10-17), snapclient connected from the WiFi
    address and kept that connection for 5 h 20, and nothing re-resolves it,
    because the server's address never moved.

    One sample of eth0's state cannot tell a missing cable from one still
    negotiating: on the server's boot of 2026-10-29 eth0 read "unavailable" (20)
    for 3 s with its cable in, and Bureau's snapclient started the very second
    its carrier came up. So the launcher waits, before it resolves anything, for
    what wait-online waited for: NetworkManager's startup, which completes once
    eth0 has its IPv4 or gave up (measured 2 ms after eth0 activated). The wait
    outlasts the IPv4 wait the drop-in sets, or it gives up while NetworkManager
    is still holding the cable. And it fails open: a NetworkManager that never
    reports startup must not keep the room silent.
    """
    result, calls = _run_sat_launcher(tmp_path, nm_online_rc)
    assert result.returncode == 0, f"the launcher failed: {result.stderr}"
    assert calls and calls[-1].startswith("snapclient "), (
        f"the launcher never execed snapclient (calls: {calls}, stderr: {result.stderr})"
    )
    if nm_online_rc:
        assert "nm-online" in result.stderr, (
            "the launcher started without waiting and said nothing: the journal is the only trace of it"
        )

    asked = [c for c in calls if c.startswith("nm-online ")]
    assert asked, "the launcher never waits for NetworkManager's startup before it dials"
    args = asked[0].split()[1:]
    assert "-s" in args or "--wait-for-startup" in args, (
        f"nm-online is not asked for startup ({args}): without -s it returns as soon as "
        "wlan0 alone is connected"
    )
    resolved = next((i for i, c in enumerate(calls) if c.startswith("getent ")), None)
    assert resolved is not None, f"the launcher no longer resolves the server (calls: {calls})"
    assert "nm-online returned" in calls[:resolved], (
        f"the server is resolved before NetworkManager's startup is over (calls: {calls})"
    )

    required = re.search(r"^ipv4\.required-timeout=(\d+)\s*$", SAT_ETHERNET_DROPIN.read_text(), re.MULTILINE)
    assert required, "90-milo-ethernet-ipv4.conf sets no ipv4.required-timeout; the extractor is broken"
    timeout = re.search(r"(?:-t|--timeout)[ =](\d+)", " ".join(args))
    assert timeout, f"nm-online runs on its default timeout ({args}); bound it explicitly"
    assert int(timeout.group(1)) > int(required.group(1)) / 1000, (
        f"the wait ({timeout.group(1)} s) gives up before NetworkManager stops waiting for "
        f"eth0's IPv4 ({required.group(1)} ms)"
    )
