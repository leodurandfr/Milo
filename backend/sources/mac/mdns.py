# backend/sources/mac/mdns.py
"""Bonjour naming for ROC senders — parsing of `avahi-browse -p` output.

ROC hands us the sender's IP and nothing else, so a name has to be looked up —
and every hostname path leads to the wrong one. The reverse (PTR) answer comes
from whoever owns the address's zone, i.e. the router's DHCP domain, and what
macOS announces over DHCP is a rotating *private* hostname shaped like a UUID:
'a8fca8ba-7a2f-4862-8934-70b031dd2eab'. That is not a lookup failure to retry —
the Mac publishes that name over mDNS too, so a forward '<label>.local' query
confirms it rather than correcting it.

The name a user recognises is the Bonjour *service instance* name, "Mac mini de
Léo" — the only one that can carry an accent, since a hostname cannot. This
module reads it out of the browse dump, matched by advertised address.
"""
import ipaddress
import re
from collections import Counter
from typing import Iterable, Optional

# `avahi-browse -p` line: =;iface;proto;name;type;domain;hostname;address;port;txt…
_ADDRESS_FIELD = 7
_NAME_FIELD = 3

# Avahi escapes a label's spaces and non-ASCII bytes as decimal '\ddd', and '.'
# and '\' as themselves behind a backslash.
_ESCAPE_RE = re.compile(rb'\\(\d{3}|.)', re.DOTALL)

# _raop advertises '<deviceid>@<instance name>'; the prefix is the protocol's,
# not part of the name.
_RAOP_PREFIX_RE = re.compile(r'^[0-9A-Fa-f]{12}@')

# The private hostname macOS rotates: a bare UUID, never a name to display.
_PRIVATE_HOSTNAME_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def is_private_hostname(label: str) -> bool:
    """True for the UUID-shaped hostname macOS publishes instead of its name."""
    return bool(label) and bool(_PRIVATE_HOSTNAME_RE.match(label))


def decode_avahi_label(raw: str) -> str:
    """Decode one escaped `avahi-browse -p` field back to text."""
    def _byte(match: re.Match) -> bytes:
        token = match.group(1)
        if len(token) == 3 and token.isdigit() and int(token) <= 255:
            return bytes([int(token)])
        return token

    return _ESCAPE_RE.sub(_byte, raw.encode()).decode('utf-8', 'replace')


def _normalize_address(address: Optional[str]) -> Optional[str]:
    """Canonical form of an IP, or None when it is not one."""
    if not address:
        return None
    try:
        return str(ipaddress.ip_address(address.strip('[]').split('%', 1)[0]))
    except ValueError:
        return None


def service_name_for_addresses(
    browse_output: str, addresses: Iterable[Optional[str]]
) -> Optional[str]:
    """Bonjour instance name advertised at any of `addresses`, or None.

    A Mac advertises a dozen services under one instance name, so the answer is
    a majority vote: a lone service published under its own name (a shared
    printer, a per-app instance) cannot outvote the device's own.
    """
    wanted = {a for a in map(_normalize_address, addresses) if a}
    if not wanted:
        return None

    names: Counter = Counter()
    for line in browse_output.splitlines():
        if not line.startswith('='):
            continue
        fields = line.split(';')
        if len(fields) <= _ADDRESS_FIELD:
            continue
        if _normalize_address(fields[_ADDRESS_FIELD]) not in wanted:
            continue
        name = _RAOP_PREFIX_RE.sub('', decode_avahi_label(fields[_NAME_FIELD])).strip()
        if name:
            names[name] += 1

    return names.most_common(1)[0][0] if names else None
