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

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
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
