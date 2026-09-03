# backend/tests/test_music_library_discovery.py
"""Tests for mDNS SMB/NFS server discovery (the Phase 2 add-share convenience).

Covers the parseable-output parser, the browse/dedupe/sort aggregation, the
fail-open behaviour (missing binary, a browse that reports nothing), the rule
that servers printed before the deadline survive a process that never exits,
and the resilient /shares/discover route envelope.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library.discovery import (
    _parse_resolved,
    _unescape,
    discover_servers,
)
from backend.sources.music_library.routes import router, setup_music_library_routes

# Parseable avahi-browse output: one resolved SMB server answering on two
# interfaces (must dedupe), plus a non-resolved and an IPv6 line (both ignored).
SMB_OUTPUT = "\n".join([
    "+;eth0;IPv4;Synology\\032NAS;_smb._tcp;local",
    "=;eth0;IPv4;Synology\\032NAS;_smb._tcp;local;synology.local;192.168.1.20;445;\"model=x\"",
    "=;wlan0;IPv4;Synology\\032NAS;_smb._tcp;local;synology.local;192.168.1.20;445;\"model=x\"",
    "=;eth0;IPv6;Synology\\032NAS;_smb._tcp;local;synology.local;fe80::1;445;",
])
NFS_OUTPUT = "=;eth0;IPv4;bigstore;_nfs._tcp;local;bigstore.local;192.168.1.30;2049;"


def _fake_exec(outputs, *, raise_exc=None, hang_after_output=False):
    """Build a fake asyncio.create_subprocess_exec keyed on the service type
    (the last positional arg to avahi-browse).

    The fake streams `stdout.readline()`, because that is how the production
    code consumes avahi-browse. With `hang_after_output` the reader never sees
    EOF — the shape of a real browse whose last record never resolves.
    """
    async def fake(*args, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        service = args[-1]
        text = outputs.get(service, "")
        lines = [f"{line}\n".encode() for line in text.splitlines()]
        pending = iter(lines)

        async def readline():
            line = next(pending, None)
            if line is not None:
                return line
            if hang_after_output:
                await asyncio.sleep(3600)
            return b""

        proc = MagicMock()
        proc.returncode = None if hang_after_output else 0
        proc.stdout = MagicMock()
        proc.stdout.readline = readline
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        return proc
    return fake


class TestParseResolved:
    def test_parses_smb_line(self):
        line = "=;eth0;IPv4;Synology\\032NAS;_smb._tcp;local;synology.local;192.168.1.20;445;"
        assert _parse_resolved(line, "cifs") == {
            "name": "Synology NAS",
            "host": "192.168.1.20",  # IP, not the .local name (getaddrinfo can't resolve .local)
            "address": "192.168.1.20",
            "type": "cifs",
        }

    def test_ignores_non_resolved_line(self):
        assert _parse_resolved("+;eth0;IPv4;x;_smb._tcp;local", "cifs") is None

    def test_ignores_ipv6(self):
        assert _parse_resolved("=;eth0;IPv6;x;_smb._tcp;local;x.local;fe80::1;445;", "cifs") is None

    def test_falls_back_to_address_when_no_fqdn(self):
        line = "=;eth0;IPv4;x;_smb._tcp;local;;192.168.1.9;445;"
        assert _parse_resolved(line, "cifs")["host"] == "192.168.1.9"

    def test_ignores_row_without_address(self):
        assert _parse_resolved("=;eth0;IPv4;x;_smb._tcp;local;x.local;;445;", "cifs") is None

    def test_ignores_short_line(self):
        assert _parse_resolved("=;eth0;IPv4;x", "cifs") is None


class TestUnescape:
    def test_decimal_escapes(self):
        assert _unescape("My\\032NAS\\040v2\\041") == "My NAS(v2)"


class TestDiscoverServers:
    async def test_dedupes_and_sorts_by_name(self):
        fake = _fake_exec({"_smb._tcp": SMB_OUTPUT, "_nfs._tcp": NFS_OUTPUT})
        with patch("asyncio.create_subprocess_exec", fake):
            servers = await discover_servers()
        # bigstore (nfs) sorts before "Synology NAS" (cifs); the SMB dup collapses.
        assert [s["name"] for s in servers] == ["bigstore", "Synology NAS"]
        assert servers[0]["type"] == "nfs"
        assert servers[1]["host"] == "192.168.1.20"

    async def test_fail_open_when_binary_missing(self):
        fake = _fake_exec({}, raise_exc=FileNotFoundError())
        with patch("asyncio.create_subprocess_exec", fake):
            assert await discover_servers() == []

    async def test_fail_open_when_browse_reports_nothing(self):
        fake = _fake_exec({})
        with patch("asyncio.create_subprocess_exec", fake):
            assert await discover_servers() == []

    async def test_keeps_servers_printed_before_the_deadline(self):
        """The regression this file exists for: avahi-browse stays alive until
        every advertised record resolves, and one that never does held the whole
        run past the deadline — so a LAN whose servers had already been reported
        answered "no servers found"."""
        fake = _fake_exec({"_smb._tcp": SMB_OUTPUT}, hang_after_output=True)
        with patch("asyncio.create_subprocess_exec", fake), \
                patch("backend.sources.music_library.discovery.BROWSE_TIMEOUT_S", 0.05):
            servers = await discover_servers()
        assert [s["name"] for s in servers] == ["Synology NAS"]

    async def test_kills_a_browse_that_outlives_the_deadline(self):
        """A hung avahi-browse per wizard visit would pile up processes."""
        killed = []
        fake = _fake_exec({"_smb._tcp": SMB_OUTPUT}, hang_after_output=True)

        async def spy(*args, **kwargs):
            proc = await fake(*args, **kwargs)
            proc.kill = MagicMock(side_effect=lambda: killed.append(args[-1]))
            return proc

        with patch("asyncio.create_subprocess_exec", spy), \
                patch("backend.sources.music_library.discovery.BROWSE_TIMEOUT_S", 0.05):
            await discover_servers()
        assert sorted(killed) == ["_nfs._tcp", "_smb._tcp"]


class TestDiscoverRoute:
    @pytest.fixture
    def api(self):
        app = FastAPI()
        setup_music_library_routes(lambda: MagicMock())
        app.include_router(router, prefix="/api")
        return TestClient(app)

    def test_returns_servers_envelope(self, api):
        servers = [{"name": "NAS", "host": "nas.local", "address": "10.0.0.5", "type": "cifs"}]
        with patch(
            "backend.sources.music_library.routes.discover_servers",
            AsyncMock(return_value=servers),
        ):
            r = api.get("/api/music-library/shares/discover")
        assert r.status_code == 200
        assert r.json() == {"servers": servers}

    def test_resilient_empty_list(self, api):
        with patch(
            "backend.sources.music_library.routes.discover_servers",
            AsyncMock(return_value=[]),
        ):
            r = api.get("/api/music-library/shares/discover")
        assert r.status_code == 200
        assert r.json() == {"servers": []}
