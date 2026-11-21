"""Structural guardrail: the system files that keep a recovery-AP client out.

The recovery AP is open (brcmfmac offers no OWE or SAE in AP mode), so anyone in
radio range can join it. The backend's request gate refuses such a client its
writes; three files outside the backend carry the rest, and each is read only by
a daemon CI never runs:

  * the polkit rule, which grants NetworkManager to the backend's user and to no
    one else — though every daemon Milō runs, and every SSH login, is that user
    too: bound to the backend's unit, it refused the backend itself (measured);
  * the sshd drop-in, which refuses a connection from the AP's subnet — a client
    of this unit's recovery AP, or of the speaker's hotspot an adoption takes
    this unit onto;
  * the nginx site, whose `X-Forwarded-For` is how the gate learns who is asking —
    a proxied path without it hands the backend nginx's loopback address.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing
on an empty surface.
"""
import re
from pathlib import Path

from backend.config.constants import RECOVERY_AP_SUBNET
from backend.tests.architecture.test_proxy_timeouts import _locations
from backend.tests.architecture.test_rootfs_deployment import INSPECT_IMAGE_SH, _stage_text

REPO_ROOT = Path(__file__).resolve().parents[3]

POLKIT_RULE = REPO_ROOT / "rootfs" / "etc" / "polkit-1" / "rules.d" / "50-milo-networkmanager.rules"
SSHD_DROPIN = REPO_ROOT / "rootfs" / "etc" / "ssh" / "sshd_config.d" / "milo-recovery-ap.conf"
BACKEND_UNIT = REPO_ROOT / "system" / "milo-backend.service"

ADD_RULE_RE = re.compile(r"polkit\.addRule\(\s*function\s*\(\s*action\s*,\s*subject\s*\)\s*\{(.*?)\n\}\);", re.DOTALL)
IF_RE = re.compile(r"\bif\s*\((.*?)\)\s*\{\s*return\s+polkit\.Result\.(\w+)\s*;\s*\}", re.DOTALL)
CONDITION_RE = re.compile(r"""subject\.(\w+)\s*===?\s*["']([^"']+)["']""")


def _code(text: str, comment: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(comment))


def _polkit_grants() -> list[tuple[str, dict[str, str]]]:
    """Each branch of the rule that answers YES: its condition, and the subject fields it pins."""
    rules = ADD_RULE_RE.findall(_code(POLKIT_RULE.read_text(), "//"))
    assert rules, f"no polkit.addRule(function(action, subject) {{…}}) read from {POLKIT_RULE.name}"
    grants = []
    for body in rules:
        branches = IF_RE.findall(body)
        assert branches, f"no `if (…) {{ return polkit.Result.… }}` read from a rule in {POLKIT_RULE.name}"
        grants += [(cond, dict(CONDITION_RE.findall(cond))) for cond, result in branches if result == "YES"]
    return grants


def test_networkmanager_is_granted_to_the_backend_user_only():
    """Every YES the rule answers names NetworkManager and the backend's user.

    Fails when a grant reaches another user, another action family, or rides a
    disjunction. Fails too when a `subject.system_unit` condition comes back:
    bound to the backend's unit, the rule refused the backend itself on
    NetworkManager 1.52.1 and polkit 126 (2026-11-16: "Not authorized to
    recheck connectivity" at every start, and no recovery AP), which CI cannot
    see. Narrowing the grant to the backend is still wanted — the user also
    covers go-librespot, shairport-sync, the kiosk's Chromium and an SSH
    session — but only with a condition measured on a unit, and this test
    rewritten with it.
    """
    grants = _polkit_grants()
    assert grants, f"{POLKIT_RULE.name} grants nothing at all: the backend loses NetworkManager"
    unit_user = re.search(r"^User=(\S+)$", BACKEND_UNIT.read_text(), re.MULTILINE)
    assert unit_user, f"no User= read from {BACKEND_UNIT.name}"
    for cond, pinned in grants:
        assert 'action.id.indexOf("org.freedesktop.NetworkManager.") === 0' in cond, cond
        assert "system_unit" not in pinned, f"bound to a unit, which refused the backend itself: {cond}"
        assert pinned.get("user") == unit_user.group(1), f"not bound to the unit's user: {cond}"
        # An `||` lets one side of the condition grant alone.
        assert "||" not in cond, f"a disjunction in the grant: {cond}"


def _sshd_matches() -> list[tuple[str, list[str]]]:
    """(Match line, the directives under it), in file order."""
    blocks: list[tuple[str, list[str]]] = []
    for line in _code(SSHD_DROPIN.read_text(), "#").splitlines():
        words = line.split()
        if not words:
            continue
        if words[0].lower() == "match":
            blocks.append((" ".join(words[1:]), []))
        else:
            assert blocks, f"global directive outside a Match in {SSHD_DROPIN.name}: {line!r}"
            blocks[-1][1].append(" ".join(words))
    assert blocks, f"no Match block read from {SSHD_DROPIN.name}"
    return blocks


def test_sshd_refuses_every_client_of_the_recovery_ap_subnet():
    """The drop-in refuses a connection *from* the AP's subnet, whatever it dialed.

    Matched on the address dialed (`LocalAddress 10.42.0.1`), it let through
    every client of a speaker's open hotspot while an adoption held this unit on
    it — there this unit is a 10.42.0.x client itself — and any client of either
    AP that dialed this unit's LAN address over wlan0. The HTTP guard refused
    them in the same case. Fails when the criterion is the dialed address
    again, when the subnet drifts from `RECOVERY_AP_SUBNET` (the AP moves, the
    drop-in matches nothing, and a stranger on the open AP reaches a login
    prompt), when the match narrows to some of its clients, or when the block
    stops refusing.

    The match is IPv4 only, which is why both links carry no IPv6: the AP's
    profile is pinned by test_network_service.py, the adoption's by
    test_wifi_adoption.py.
    """
    matches = _sshd_matches()
    criteria = [m.split() for m, _ in matches]
    assert all(c[0].lower() != "localaddress" for c in criteria), matches
    assert ["address", RECOVERY_AP_SUBNET] in [[c[0].lower(), *c[1:]] for c in criteria], matches
    refused = next(body for m, body in matches if m.split()[0].lower() == "address")
    assert [d.lower() for d in refused] == ["refuseconnection yes"], refused


def test_the_sshd_drop_in_is_flashed_and_inspected():
    """A freshly flashed card carries the drop-in, and the inspection compares it.

    A Milō update installs every rootfs file, but the stage copies them one by
    one: a file it omits is missing from every new unit until its first update,
    which is exactly the unit the recovery AP serves.
    """
    rel = SSHD_DROPIN.relative_to(REPO_ROOT).as_posix()
    stage, inspect = _code(_stage_text(), "#"), _code(INSPECT_IMAGE_SH.read_text(), "#")
    assert re.search(rf"cp\s+\S*/{re.escape(rel)}\s", stage), f"pi-gen/stage-milo never copies {rel}"
    assert f"cmp -s '$REPO/{rel}'" in inspect, f"pi-gen/inspect-image.sh never compares {rel}"


def test_every_proxied_location_names_the_client():
    """Every location nginx proxies to the backend forwards `X-Forwarded-For`.

    uvicorn takes the client from it (`forwarded_allow_ips="127.0.0.1"`), and the
    recovery-AP gate reads that client. Fails when a location — `/ws` was the
    one — proxies without it: every caller there reads as 127.0.0.1, the kiosk's
    own address.
    """
    proxied = [loc for loc in _locations() if loc.proxies]
    assert any(loc.path == "/ws" for loc in proxied), [loc.path for loc in proxied]
    header = "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"
    missing = [f"{loc.modifier} {loc.path}".strip() for loc in proxied if header not in loc.headers]
    assert not missing, f"proxied without {header}: {missing}"
