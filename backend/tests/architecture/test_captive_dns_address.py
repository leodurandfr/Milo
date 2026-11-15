"""The captive DNS answer is the address the recovery AP pins.

`milo-captive.conf` makes NetworkManager's dnsmasq answer every name with one
address, so a phone joining the open AP opens the wizard by itself. dnsmasq reads
it as a static file and cannot import `RECOVERY_AP_ADDRESS`; this test is the
link. Drifted, every lookup on the AP answers an address nobody holds: the phone
reports "no internet" and never shows the setup page, and a headless unit set up
over the AP is left with nothing to type into.
"""
import ipaddress
import re
from pathlib import Path

from backend.config.constants import RECOVERY_AP_ADDRESS, RECOVERY_AP_PREFIX, RECOVERY_AP_SUBNET

REPO = Path(__file__).resolve().parents[3]
CAPTIVE_CONF = REPO / "rootfs/etc/NetworkManager/dnsmasq-shared.d/milo-captive.conf"

# dnsmasq's `address=/<domain>/<ip>`; `#` matches every domain.
_ADDRESS_LINE = re.compile(r"^address=/(?P<domain>[^/]*)/(?P<ip>\S+)$")


def _captive_answers() -> dict[str, str]:
    answers = {}
    for line in CAPTIVE_CONF.read_text().splitlines():
        match = _ADDRESS_LINE.match(line.strip())
        if match:
            answers[match["domain"]] = match["ip"]
    return answers


def test_every_name_on_the_access_point_resolves_to_the_address_it_pins():
    answers = _captive_answers()
    assert "#" in answers, f"no catch-all `address=/#/…` line parsed from {CAPTIVE_CONF}: {answers}"
    assert answers == {"#": RECOVERY_AP_ADDRESS}


def test_the_pinned_address_lies_in_the_subnet_the_guard_watches():
    """The AP profile pins `RECOVERY_AP_ADDRESS/RECOVERY_AP_PREFIX`, and the
    request guard knows its clients by `RECOVERY_AP_SUBNET`: an address outside
    it hands the clients a subnet the guard never refuses."""
    interface = ipaddress.ip_interface(f"{RECOVERY_AP_ADDRESS}/{RECOVERY_AP_PREFIX}")
    assert interface.network == ipaddress.ip_network(RECOVERY_AP_SUBNET)
