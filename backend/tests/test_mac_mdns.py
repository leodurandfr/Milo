# backend/tests/test_mac_mdns.py
"""Bonjour naming for ROC senders (sources/mac/mdns.py).

When these fail, the Mac card reads "Audio reçu de a8fca8ba 7a2f 4862 8934
70b031dd2eab" instead of "Mac mini de Léo": the rotating private hostname macOS
publishes is a name the Mac genuinely answers to over mDNS, so no hostname
lookup can tell the two apart — only the Bonjour instance name can.

Samples are verbatim `avahi-browse -a -r -p -t` lines captured off a live LAN.
Consumers: MacSource._resolve_hostname → WS `client_names` → AudioSourceStatus.
"""
from backend.sources.mac.mdns import (
    decode_avahi_label,
    is_private_hostname,
    service_name_for_addresses,
)

MAC_MINI = r"Mac\032mini\032de\032L\195\169o"

BROWSE_OUTPUT = "\n".join([
    rf"+;eth0;IPv4;{MAC_MINI};_companion-link._tcp;local",
    rf"=;eth0;IPv4;{MAC_MINI};_companion-link._tcp;local;Mac-mini-de-Leo.local;192.168.1.21;49153;"
    r'"rpVr=680.1.1" "rpMac=0"',
    rf"=;eth0;IPv6;{MAC_MINI};SSH Remote Terminal;local;Mac-mini-de-Leo.local;192.168.1.21;22;",
    rf"=;eth0;IPv4;2EF6B1224052\064{MAC_MINI};AirTunes Remote Audio;local;"
    r"Mac-mini-de-Leo.local;192.168.1.21;7000;" r'"am=Mac16,11"',
    r"=;eth0;IPv4;NAS\032Leo;SFTP File Transfer;local;NAS-Leo.local;192.168.1.30;22;",
])


class TestLabelDecoding:
    """The escaped form avahi prints is not a name anyone can read."""

    def test_decodes_spaces_and_utf8(self):
        """`\\032` is a space and `\\195\\169` the two bytes of 'é'."""
        assert decode_avahi_label(MAC_MINI) == "Mac mini de Léo"

    def test_decodes_backslash_escaped_punctuation(self):
        """A dot inside a label is escaped, not a label separator."""
        assert decode_avahi_label(r"Bureau\032n\194\1762") == "Bureau n°2"
        assert decode_avahi_label(r"Studio\.1") == "Studio.1"

    def test_plain_label_is_unchanged(self):
        assert decode_avahi_label("Mac-mini-de-Leo") == "Mac-mini-de-Leo"


class TestServiceNameLookup:
    """Which name the browse dump yields for a given address."""

    def test_returns_the_accented_display_name(self):
        """The whole point: a hostname cannot carry the accent, this can."""
        assert service_name_for_addresses(BROWSE_OUTPUT, ["192.168.1.21"]) == "Mac mini de Léo"

    def test_raop_device_id_prefix_is_stripped(self):
        """_raop advertises '<deviceid>@<name>' — the prefix is the protocol's."""
        raop_only = BROWSE_OUTPUT.splitlines()[3]
        assert service_name_for_addresses(raop_only, ["192.168.1.21"]) == "Mac mini de Léo"

    def test_matches_on_the_advertised_address_not_the_roc_one(self):
        """The bug's own shape: ROC streams from .173, Bonjour lives on .21."""
        assert service_name_for_addresses(
            BROWSE_OUTPUT, ["192.168.1.173", "192.168.1.21"]
        ) == "Mac mini de Léo"

    def test_another_host_is_not_borrowed(self):
        assert service_name_for_addresses(BROWSE_OUTPUT, ["192.168.1.30"]) == "NAS Leo"
        assert service_name_for_addresses(BROWSE_OUTPUT, ["192.168.1.99"]) is None

    def test_ipv6_is_matched_in_canonical_form(self):
        """avahi prints a compressed address; the sender's may be spelled out."""
        line = rf"=;eth0;IPv6;{MAC_MINI};SSH;local;Mac-mini-de-Leo.local;fe80::1c2;22;"
        assert service_name_for_addresses(line, ["fe80:0:0:0:0:0:0:01c2%eth0"]) == "Mac mini de Léo"

    def test_no_usable_address_is_not_a_match(self):
        assert service_name_for_addresses(BROWSE_OUTPUT, [None, ""]) is None

    def test_truncated_line_is_skipped(self):
        """A browse line with no address field must not name anything."""
        assert service_name_for_addresses(
            rf"=;eth0;IPv4;{MAC_MINI};_companion-link._tcp;local", ["192.168.1.21"]
        ) is None


class TestPrivateHostname:
    """The name that must never reach the UI."""

    def test_uuid_hostname_is_private(self):
        assert is_private_hostname("a8fca8ba-7a2f-4862-8934-70b031dd2eab")
        assert is_private_hostname("A8FCA8BA-7A2F-4862-8934-70B031DD2EAB")

    def test_real_names_are_not(self):
        for name in ("Mac-mini-de-Leo", "mac-mini-de-leo", "MacBook-Pro", "192.168.1.173", ""):
            assert not is_private_hostname(name)
