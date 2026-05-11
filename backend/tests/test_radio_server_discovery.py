# backend/tests/test_radio_server_discovery.py
"""
Unit tests for ServerDiscovery (sources/radio/server_discovery.py).

Tests cover:
- DNS resolution + reverse-lookup → friendly hostnames
- Lazy resolution on first get_server() call
- Server stickiness between rotate() calls
- rotate() cycles through all mirrors and re-resolves on wrap
- TTL expiry triggers re-resolution
- Resolution failure → fallback host, no exception
"""
import socket
from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.sources.radio.server_discovery import ServerDiscovery


THREE_IPS = ("all.api.radio-browser.info", [], ["1.1.1.1", "2.2.2.2", "3.3.3.3"])

REVERSE_MAP = {
    "1.1.1.1": ("de1.api.radio-browser.info", [], ["1.1.1.1"]),
    "2.2.2.2": ("fr1.api.radio-browser.info", [], ["2.2.2.2"]),
    "3.3.3.3": ("nl1.api.radio-browser.info", [], ["3.3.3.3"]),
}


def _fake_gethostbyaddr(ip):
    return REVERSE_MAP[ip]


@pytest.fixture
def patched_dns():
    """Patch socket lookups + disable shuffle for deterministic ordering."""
    with patch(
        "backend.sources.radio.server_discovery.socket.gethostbyname_ex",
        return_value=THREE_IPS,
    ), patch(
        "backend.sources.radio.server_discovery.socket.gethostbyaddr",
        side_effect=_fake_gethostbyaddr,
    ), patch(
        "backend.sources.radio.server_discovery.random.shuffle",
        side_effect=lambda lst: None,
    ):
        yield


@pytest.fixture
def discovery():
    return ServerDiscovery()


class TestBaseUrl:
    def test_base_url_format(self, discovery):
        assert discovery.base_url("de1.api.radio-browser.info") == (
            "https://de1.api.radio-browser.info/json"
        )


class TestResolution:
    @pytest.mark.asyncio
    async def test_first_get_server_triggers_resolution(self, patched_dns, discovery):
        assert discovery.server_count == 0
        server = await discovery.get_server()
        assert server == "de1.api.radio-browser.info"
        assert discovery.server_count == 3

    @pytest.mark.asyncio
    async def test_subsequent_calls_return_same_server(self, patched_dns, discovery):
        first = await discovery.get_server()
        second = await discovery.get_server()
        third = await discovery.get_server()
        assert first == second == third == "de1.api.radio-browser.info"

    @pytest.mark.asyncio
    async def test_reverse_lookup_failure_falls_back_to_ip(self, discovery):
        with patch(
            "backend.sources.radio.server_discovery.socket.gethostbyname_ex",
            return_value=THREE_IPS,
        ), patch(
            "backend.sources.radio.server_discovery.socket.gethostbyaddr",
            side_effect=socket.herror("no PTR record"),
        ), patch(
            "backend.sources.radio.server_discovery.random.shuffle",
            side_effect=lambda lst: None,
        ):
            server = await discovery.get_server()
            # All reverse lookups failed → raw IPs become the server names.
            assert server == "1.1.1.1"
            assert discovery.server_count == 3


class TestRotation:
    @pytest.mark.asyncio
    async def test_rotate_advances_through_all_servers(self, patched_dns, discovery):
        first = await discovery.get_server()
        second = await discovery.rotate()
        third = await discovery.rotate()
        assert first == "de1.api.radio-browser.info"
        assert second == "fr1.api.radio-browser.info"
        assert third == "nl1.api.radio-browser.info"

    @pytest.mark.asyncio
    async def test_rotate_wraps_and_re_resolves(self, patched_dns, discovery):
        await discovery.get_server()
        await discovery.rotate()
        await discovery.rotate()
        # Third rotate() pushes cursor to len(servers): triggers _refresh()
        # and resets cursor to 0.
        wrapped = await discovery.rotate()
        assert wrapped == "de1.api.radio-browser.info"
        assert discovery.server_count == 3
        # Subsequent rotate() must continue advancing from the reset cursor.
        assert await discovery.rotate() == "fr1.api.radio-browser.info"

    @pytest.mark.asyncio
    async def test_rotate_before_get_server_resolves_first(
        self, patched_dns, discovery
    ):
        # Calling rotate() with no servers yet should resolve, not crash.
        server = await discovery.rotate()
        assert server == "de1.api.radio-browser.info"
        assert discovery.server_count == 3


class TestTtlExpiry:
    @pytest.mark.asyncio
    async def test_ttl_expiry_triggers_re_resolve(self, patched_dns, discovery):
        await discovery.get_server()
        original_resolved_at = discovery._resolved_at

        # Force the cached resolution to look older than TTL.
        discovery._resolved_at = original_resolved_at - ServerDiscovery.TTL - timedelta(
            seconds=1
        )

        # Spy on _refresh so we can prove it was actually invoked, not just
        # infer it from a timestamp comparison.
        with patch.object(discovery, "_refresh", wraps=discovery._refresh) as spy:
            await discovery.get_server()
            spy.assert_called_once()

        assert discovery._resolved_at >= original_resolved_at

    @pytest.mark.asyncio
    async def test_get_server_within_ttl_does_not_re_resolve(
        self, patched_dns, discovery
    ):
        await discovery.get_server()

        with patch.object(discovery, "_refresh", wraps=discovery._refresh) as spy:
            await discovery.get_server()
            await discovery.get_server()
            spy.assert_not_called()


class TestResolutionFailure:
    @pytest.mark.asyncio
    async def test_dns_failure_returns_fallback(self, discovery):
        with patch(
            "backend.sources.radio.server_discovery.socket.gethostbyname_ex",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            server = await discovery.get_server()
            assert server == ServerDiscovery.FALLBACK_SERVER
            assert discovery.server_count == 0

    @pytest.mark.asyncio
    async def test_dns_failure_does_not_set_resolved_at(self, discovery):
        with patch(
            "backend.sources.radio.server_discovery.socket.gethostbyname_ex",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            await discovery.get_server()
            # _resolved_at stays None so the next call retries immediately
            # instead of being stuck on the fallback for a full TTL window.
            assert discovery._resolved_at is None

    @pytest.mark.asyncio
    async def test_rotate_with_failed_resolution_returns_fallback(self, discovery):
        with patch(
            "backend.sources.radio.server_discovery.socket.gethostbyname_ex",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            server = await discovery.rotate()
            assert server == ServerDiscovery.FALLBACK_SERVER
