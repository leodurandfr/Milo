"""A fresh card becomes the speaker of a real Milō only (milo-mdns-probe, milo-first-boot).

`milo-first-boot` turns a fresh card into a satellite — destructively, for good
— when its probe says a Milō server is on the network. The probe used to ask
whether anything answered to milo.local: a NAS or a Mac named milo did, and so
did a packet merely containing the name. It now looks for the service type a
server announces, `_milo._tcp`, from `rootfs/etc/avahi/services/milo.service`,
which a satellite never publishes.

What breaks when these fail: a card plugged in beside a NAS named milo becomes
its satellite and must be reflashed; or a real server is not found and every
new speaker stays a server, wizard and all; or a satellite announces itself
and turns the next fresh card into a satellite of a satellite.

The probe is read as the module it is, and fed the packets the network sends:
an avahi answer, a NAS's answer, another card's query. Nothing here opens a
socket — multicast is not something CI can carry.
"""
import os
import re
import shlex
import struct
import subprocess
import types
import xml.etree.ElementTree as ET

import pytest

from backend.tests.architecture.test_helper_deployment import REPO_ROOT, _shell_function, _uncommented

PROBE = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-mdns-probe"
SERVICE_FILE = REPO_ROOT / "rootfs" / "etc" / "avahi" / "services" / "milo.service"
CLIENT_TREE = REPO_ROOT / "milo-client" / "rootfs"
FIRST_BOOT = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-first-boot"

TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33
RESPONSE = 0x8400  # QR + AA, as avahi answers


def _load_probe():
    """The script as a module, compiled in memory: an import would leave a
    __pycache__ beside it, in the tree a deploy copies to /usr/local/bin."""
    module = types.ModuleType("milo_mdns_probe")
    exec(compile(PROBE.read_text(encoding="utf-8"), str(PROBE), "exec"), module.__dict__)
    return module


probe = _load_probe()


# --------------------------------------------------------------------------- #
# Packets, written the way the network writes them
# --------------------------------------------------------------------------- #

def _labels(*labels: str) -> bytes:
    return b"".join(struct.pack("!B", len(l.encode())) + l.encode() for l in labels)


def _pointer(offset: int) -> bytes:
    return struct.pack("!H", 0xC000 | offset)


def _record(owner: bytes, rtype: int, rdata: bytes) -> bytes:
    return owner + struct.pack("!2HIH", rtype, 0x8001, 4500, len(rdata)) + rdata


def _packet(flags: int, answers: list[bytes], additional: list[bytes] = (), questions: list[bytes] = ()) -> bytes:
    header = struct.pack("!6H", 0, flags, len(questions), len(answers), 0, len(additional))
    return header + b"".join(questions) + b"".join(answers) + b"".join(additional)


def _avahi_answer(service_labels=("_milo", "_tcp")) -> bytes:
    """A Milō server's avahi answering the PTR query: the PTR owned by the
    service type, its instance compressed against it, then SRV, TXT and A in
    the additional section — compressed the way avahi compresses."""
    owner = _labels(*service_labels, "local") + b"\x00"  # at offset 12
    instance = _labels("Milo Audio System on milo") + _pointer(12)
    ptr = _record(owner, TYPE_PTR, instance)
    instance_at = 12 + len(owner) + 10
    srv = _record(_pointer(instance_at), TYPE_SRV,
                  struct.pack("!3H", 0, 0, 80) + _labels("milo", "local") + b"\x00")
    txt = _record(_pointer(instance_at), TYPE_TXT, b"\x06path=/")
    a = _record(_labels("milo", "local") + b"\x00", TYPE_A, bytes([192, 168, 1, 39]))
    return _packet(RESPONSE, [ptr], [srv, txt, a])


# --------------------------------------------------------------------------- #
# What counts as a server
# --------------------------------------------------------------------------- #

def test_a_milo_servers_answer_is_a_server():
    """The answer every real server sends: missed, no fresh card ever becomes a speaker."""
    assert probe.announces_milo(_avahi_answer()) is True


def test_a_host_named_milo_is_not_a_server():
    """A NAS or a Mac named milo answers milo.local, and nothing else: the
    answer the old probe converted a card on."""
    nas = _packet(RESPONSE, [_record(_labels("milo", "local") + b"\x00", TYPE_A, bytes([192, 168, 1, 20]))])

    assert probe.announces_milo(nas) is False


def test_another_cards_query_is_not_a_server():
    """Two fresh cards booting together hear each other's question — and each
    hears its own, looped back by the multicast socket. A question names the
    service type as plainly as an answer does."""
    assert probe.announces_milo(probe.build_query()) is False


def test_a_query_carrying_a_known_answer_is_not_a_server():
    """An mDNS query may list the answers its sender already holds (RFC 6762
    §7.1), the Milō PTR included: it is still a question, from anyone."""
    known = _avahi_answer()
    query = bytearray(known)
    query[2:4] = b"\x00\x00"

    assert probe.announces_milo(bytes(query)) is False


def test_only_the_services_pointer_record_counts():
    """The announcement is the PTR record the service type owns; another
    record under that name (a TXT, a negative answer) points at no server."""
    owner = _labels("_milo", "_tcp", "local") + b"\x00"
    not_a_pointer = _packet(RESPONSE, [_record(owner, TYPE_TXT, b"\x06path=/")])

    assert probe.announces_milo(not_a_pointer) is False


def test_another_service_type_is_not_a_server():
    """Avahi on any host announces services: only the Milō one counts."""
    assert probe.announces_milo(_avahi_answer(("_milo-other", "_tcp"))) is False
    assert probe.announces_milo(_avahi_answer(("_http", "_tcp"))) is False


def test_names_are_compared_without_case():
    """DNS names are case-insensitive (RFC 1035 §2.3.3); a responder may echo another case."""
    assert probe.announces_milo(_avahi_answer(("_MILO", "_TCP"))) is True


@pytest.mark.parametrize("packet", [
    _avahi_answer()[:40],                                   # cut inside the PTR record
    _packet(RESPONSE, [_record(_pointer(12), TYPE_PTR, b"")]),  # a name pointing at itself
    b"\x00\x00\x84\x00",                                    # shorter than a header
], ids=["truncated", "pointer loop", "no header"])
def test_a_packet_that_does_not_parse_is_no_server_and_no_crash(packet):
    """The probe runs as root before the network is settled; a packet it
    cannot read is anybody's, and an exception would end it with no answer."""
    assert probe.announces_milo(packet) is False


def test_the_query_asks_for_the_service_the_server_announces():
    """The question the probe sends, read back by its own parser: a PTR for
    the service type, nothing else."""
    query = probe.build_query()
    name, offset = probe.read_name(query, 12)
    qtype, qclass = struct.unpack("!2H", query[offset:offset + 4])

    assert (name, qtype, qclass) == (probe.SERVICE, TYPE_PTR, 1)


# --------------------------------------------------------------------------- #
# The announcement and the probe name the same service
# --------------------------------------------------------------------------- #

def _announced_types(path) -> set[str]:
    root = ET.parse(path).getroot()
    return {f"{node.text.strip()}.local" for node in root.iter("type")}


def test_the_probe_looks_for_the_service_a_server_announces():
    """Two spellings in one tree with nothing between them: renamed on one
    side, every fresh card stays a server, beside a server it cannot see."""
    announced = _announced_types(SERVICE_FILE)
    assert len(announced) >= 2, f"the service file announces {announced}; the read is broken"

    assert probe.SERVICE in announced


def test_no_satellite_announces_itself_as_a_server():
    """A satellite announcing `_milo._tcp` would make the next fresh card its
    satellite. Nothing its own tree installs names the service, and first-boot
    removes the server's file from a converted card (below)."""
    files = [p for p in CLIENT_TREE.rglob("*") if p.is_file()]
    assert len(files) >= 10, f"only {len(files)} files under {CLIENT_TREE}; the read is broken"
    service_type = probe.SERVICE.removesuffix(".local")

    announcing = [str(p.relative_to(REPO_ROOT)) for p in files if service_type in p.read_text(errors="ignore")]

    assert not announcing, f"satellite files name {service_type}: {announcing}"


def test_first_boot_asks_the_probe_for_a_server_not_for_a_name():
    """The probe's arguments are a timeout and an interface: given a hostname
    first, it refuses with its usage (exit 2), which first-boot reads as
    "nothing found" — every fresh card would stay a server."""
    code = _uncommented(FIRST_BOOT.read_text())
    calls = re.findall(r"milo-mdns-probe((?: [^\s;]+)*)", code)
    assert calls, "milo-first-boot no longer calls milo-mdns-probe; the read is broken"

    for call in calls:
        args = shlex.split(call)
        assert len(args) <= 2, f"milo-mdns-probe takes [timeout] [interface], called with {args}"
        assert float(args[0]) > 0, f"milo-mdns-probe's first argument is a timeout, called with {args}"

    refused = subprocess.run(
        ["python3", str(PROBE), "milo", "10", "192.0.2.1"], capture_output=True, text=True, timeout=10,
    )
    assert refused.returncode == 2 and "Usage" in refused.stderr


# --------------------------------------------------------------------------- #
# first-boot puts the announcement where the role wants it
# --------------------------------------------------------------------------- #

def _run_apply_avahi_service(tmp_path, role: str, target):
    text = FIRST_BOOT.read_text()
    log = re.search(r"^log\(\) \{.*\}$", text, re.MULTILINE)
    body = _shell_function(text, "apply_avahi_service")
    assert log and body, "could not read milo-first-boot's log and apply_avahi_service; the read is broken"
    script = "\n".join([
        "set -euo pipefail",
        f"MILO_APP_DIR={shlex.quote(str(REPO_ROOT))}",
        f"AVAHI_SERVICE_FILE={shlex.quote(str(target))}",
        log.group(0),
        f"apply_avahi_service() {{\n{body}}}",
        f"apply_avahi_service {role}",
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                          env={**os.environ})


def test_a_server_announces_the_checkouts_service(tmp_path):
    """Whatever an image or an earlier release left in its place, without
    `_milo._tcp`: a server boots into the checkout's, so a fresh card beside
    it finds it."""
    target = tmp_path / "etc" / "avahi" / "services" / "milo.service"
    target.parent.mkdir(parents=True)
    target.write_text("<service-group><service><type>_http._tcp</type></service></service-group>\n")

    result = _run_apply_avahi_service(tmp_path, "server", target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_bytes() == SERVICE_FILE.read_bytes()


def test_a_satellite_announces_no_server(tmp_path):
    target = tmp_path / "milo.service"
    target.write_bytes(SERVICE_FILE.read_bytes())

    result = _run_apply_avahi_service(tmp_path, "client", target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not target.exists()


def test_every_role_change_moves_the_announcement():
    """apply_avahi_role is what a conversion and a revert run, and what every
    detection boot runs: the announcement has to go with it."""
    body = _uncommented(_shell_function(FIRST_BOOT.read_text(), "apply_avahi_role"))
    assert "local role=" in body, "could not read apply_avahi_role; the read is broken"

    assert re.search(r'^\s*apply_avahi_service "\$role"\s*$', body, re.MULTILINE), (
        "apply_avahi_role no longer installs or removes the role's announcement"
    )
