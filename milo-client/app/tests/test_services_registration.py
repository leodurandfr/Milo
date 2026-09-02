"""
Tests for the satellite's boot-time registration with the main Milo.

What breaks when these fail: a literal `MILO_PRINCIPAL_IP` in the unit's env
file becomes inert again. That spelling exists for a LAN where mDNS does not
work, and a satellite set that way never appears as a pending speaker on the
server — with no error anywhere, because registration retries in silence
forever.

The mocked boundary is `socket.getaddrinfo`, i.e. mDNS resolution, which cannot
run in CI.
"""
import os
from unittest.mock import patch

import pytest

from services.registration import MILO_PRINCIPAL_PORT, _resolve_milo_principal


def _addrinfo(ip: str) -> list:
    """One getaddrinfo result tuple, in the shape the resolver indexes."""
    return [(2, 1, 6, "", (ip, MILO_PRINCIPAL_PORT))]


def test_literal_server_ip_is_used_without_resolving():
    """A literal IP in the env file reaches the app: no mDNS lookup is attempted.

    The defect this covers is not a wrong address but a lookup that should never
    happen — on such a LAN `milo.local` does not resolve at all.
    """
    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "192.168.1.10"}, clear=True), \
         patch("services.registration.socket.getaddrinfo") as getaddrinfo:
        assert _resolve_milo_principal() == "192.168.1.10"
        getaddrinfo.assert_not_called()


def test_hostname_from_env_is_the_name_resolved():
    """The conversion stores the string "milo.local", not an IP.

    `milo-first-boot` writes the hostname on purpose, so the satellite survives
    the server moving between ethernet and WiFi — the resolver must accept it.
    """
    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "milo.local"}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               return_value=_addrinfo("192.168.1.42")) as getaddrinfo:
        assert _resolve_milo_principal() == "192.168.1.42"
        assert getaddrinfo.call_args.args[0] == "milo.local"


def test_no_env_entry_falls_back_to_mdns():
    """A pi-gen-flashed satellite has no /var/lib/milo-client/env entry."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               return_value=_addrinfo("192.168.1.42")) as getaddrinfo:
        assert _resolve_milo_principal() == "192.168.1.42"
        assert getaddrinfo.call_args.args[0] == "milo.local"


def test_unresolvable_target_names_itself_in_the_error():
    """The retry loop logs this message; it must say which address failed.

    A bare "Cannot resolve milo.local" on a unit installed with --server sends
    the operator hunting for an mDNS fault that is not the cause.
    """
    import socket as socket_module

    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "milo-server.lan"}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               side_effect=socket_module.gaierror):
        with pytest.raises(RuntimeError, match="milo-server.lan"):
            _resolve_milo_principal()
