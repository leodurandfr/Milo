"""No tracked file names a real household's addresses.

Captures from a unit on the owner's LAN are the best fixtures this suite has,
and they carry the addresses that LAN was handed: a global IPv6 address (its
/64 is the ISP's name for the subscriber line, and the redaction of the
diagnostic report strips it for that reason) and the carrier-grade NAT address
of a tunnel. Both reached the tree verbatim, in a production docstring and in
test fixtures, and would have been published with it. So a capture keeps its
shape but not its addresses:

  * a global IPv6 address (2000::/3, the half `strip_global_ipv6` redacts) is
    written in the documentation range 2001:db8::/32 (RFC 3849), which reads as
    global to every rule here that looks at the text and to none that asks
    `ipaddress` whether it is routable;
  * a 100.64.0.0/10 address (RFC 6598, which reserves no documentation block)
    is written 100.64.0.x, the one stand-in this tree uses.

What this enforces is wider than those two values: no literal in 2000::/3
outside 2001:db8::/32, and none in 100.64.0.0/10 outside 100.64.0.0/24 — written
whole or as a prefix (`…:b5b0`, `…/64`, a capture cut with `…`). A public service
address the code genuinely needs (a resolver used as a connectivity target, say)
goes in `NOT_AN_ADDRESS` with a one-line reason, as the range bound there does.

What breaks when this fails: a fixture, a docstring or a doc carries an address
that is not a stand-in, most likely one someone was actually handed. Replace it by a stand-in from the rule above, then
check that the test around it still proves what it said (a global-only code path
stays global in 2001:db8::/32 only when it reads the text, not `is_global`).
"""
import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")
DOCUMENTATION_V6 = ipaddress.ip_network("2001:db8::/32")
CARRIER_GRADE_NAT = ipaddress.ip_network("100.64.0.0/10")
CGNAT_STAND_IN = ipaddress.ip_network("100.64.0.0/24")

# The bound of the global half itself, named by the comment over the redaction's
# regex ("2000::/3") — a range, not anyone's address.
NOT_AN_ADDRESS = {ipaddress.IPv6Address("2000::")}

_V6 = re.compile(r"(?<![\w:.])[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,7}(?![\w:])", re.IGNORECASE)
_V4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")


def _tracked_text_files():
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode().split("\0")
    for name in filter(None, names):
        path = REPO_ROOT / name
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue


def _parse_v6(text):
    """The IPv6 address a match names, a bare or cut prefix read as its network.

    A /64 written alone (`2001:db8:0:1`, `2001:db8:0:1/64`) or a capture cut
    after a colon does not parse as an address, and it is exactly the part that
    names the subscriber line — so a match that fails whole is read once more as
    a prefix: trailing colons dropped, `::` appended when it has fewer than
    eight groups and none elided.
    """
    try:
        return ipaddress.IPv6Address(text)
    except ValueError:
        pass
    candidate = text.rstrip(":")
    if "::" not in candidate and candidate.count(":") < 7:
        candidate += "::"
    try:
        return ipaddress.IPv6Address(candidate)
    except ValueError:
        return None


def _addresses_in(text):
    """Every IPv6 address in 2000::/3 and IPv4 address in 100.64.0.0/10 in a text."""
    for match in _V6.finditer(text):
        address = _parse_v6(match.group())
        if address is not None and address in GLOBAL_UNICAST and address not in NOT_AN_ADDRESS:
            yield address
    for match in _V4.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group())
        except ValueError:
            continue
        if address in CARRIER_GRADE_NAT:
            yield address


def _is_stand_in(address):
    return address in (DOCUMENTATION_V6 if address.version == 6 else CGNAT_STAND_IN)


def _addresses():
    """Every address `_addresses_in` finds, by tracked file."""
    return [
        (name, address)
        for name, text in _tracked_text_files()
        for address in _addresses_in(text)
    ]


# The shapes a /64 takes in prose and captures. Filled at run time, so the
# offending prefix below never sits in this file as a literal the scan would read.
_PREFIX_SHAPES = (
    "prefix {p} on eth0",
    "{p}/64",
    "addr {p}:… truncated",
    "host {p}::1: refused",
)
_OFFENDING_PREFIX = ":".join(("2a00", "0", "0", "1"))


@pytest.mark.parametrize("shape", _PREFIX_SHAPES)
def test_a_global_prefix_is_found_in_every_shape_it_is_written(shape):
    """A /64 written without its host part is reported, not dropped as unparseable."""
    text = shape.format(p=_OFFENDING_PREFIX)
    found = list(_addresses_in(text))
    assert found, text
    assert not any(_is_stand_in(a) for a in found), found


def test_every_global_and_cgnat_address_in_the_tree_is_a_stand_in():
    found = _addresses()
    # The extractor reads the tree: the stand-ins the fixtures use are there to
    # be found, in both families, or the parse is broken and the check is empty.
    assert sum(a in DOCUMENTATION_V6 for _, a in found) >= 10, found
    assert any(a.version == 4 for _, a in found), found

    offenders = sorted(
        f"{name}: {address}" for name, address in found if not _is_stand_in(address)
    )
    assert not offenders, (
        "a real address sits in the tree — use 2001:db8::/32 for a global IPv6 "
        "and 100.64.0.x for a carrier-grade NAT one:\n  " + "\n  ".join(offenders)
    )
