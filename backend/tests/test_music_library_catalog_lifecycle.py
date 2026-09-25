"""Navidrome runs exactly while the dock enables Music Library.

What breaks when these fail: `milo-navidrome.service` has no [Install], and
`PartOf=milo-backend` propagates stop and restart but never start, so the
backend is the only thing that brings the catalog up. A boot that skips it
leaves every library empty and the refresh button answering 503 with no
message (measured 2026-09-25, after a `stop` + `start` of the backend). A boot
that starts it regardless runs a catalog the dock switched off.

Assertions are on what systemd was told, and on what the shares layer — whose
reconciler gives every storage space its library id — was asked to do.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.config.constants import NAVIDROME_SERVICE
from backend.sources.music_library.source import MusicLibrarySource
from backend.tests.golden.harness import settle


def _library(enabled_apps, calls=None):
    calls = calls if calls is not None else []
    src = MusicLibrarySource({"mpv_socket": "/tmp/test-music-library-ipc.sock"})
    src._settings_service = Mock(get_setting=AsyncMock(return_value=list(enabled_apps)))
    src._service_manager = Mock(
        start=AsyncMock(side_effect=lambda unit: calls.append(("start", unit)) or True),
        stop=AsyncMock(side_effect=lambda unit: calls.append(("stop", unit)) or True),
    )
    src._shares = Mock(initialize=AsyncMock(), catalog_started=Mock())
    return src


def _hang(event):
    async def systemctl(unit):
        await event.wait()
        return True
    return systemctl


class TestAtBoot:
    @pytest.mark.asyncio
    async def test_a_dock_that_enables_the_library_starts_the_catalog(self):
        src = _library(["radio", "music_library"])

        assert await src.initialize() is True
        await settle()

        src._service_manager.start.assert_awaited_once_with(NAVIDROME_SERVICE)
        src._service_manager.stop.assert_not_called()
        src._shares.initialize.assert_awaited_once()
        src._shares.catalog_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_dock_without_the_library_never_starts_the_catalog(self):
        src = _library(["radio", "spotify"])

        await src.initialize()
        await settle()

        src._service_manager.start.assert_not_called()
        src._service_manager.stop.assert_awaited_once_with(NAVIDROME_SERVICE)

    @pytest.mark.asyncio
    async def test_the_storage_layer_does_not_wait_for_the_catalog(self):
        """`systemctl start` waits out the unit's ExecStartPre and its config
        oneshot — up to the 12.5 s control budget on a busy SD card. The USB
        watcher and the share remounts must not sit behind it."""
        src = _library(["music_library"])
        src._service_manager.start = AsyncMock(side_effect=_hang(asyncio.Event()))

        try:
            assert await asyncio.wait_for(src.initialize(), timeout=5) is True
            src._shares.initialize.assert_awaited_once()
        finally:
            await src.shutdown()

    @pytest.mark.asyncio
    async def test_a_catalog_that_will_not_start_does_not_fail_the_backend(self):
        """Fail open: an empty library is recoverable, a backend that refuses
        to boot is not."""
        src = _library(["music_library"])
        src._service_manager.start = AsyncMock(return_value=False)

        assert await src.initialize() is True
        await settle()

        src._shares.catalog_started.assert_not_called()

    @pytest.mark.asyncio
    async def test_leaving_the_source_does_not_cancel_a_catalog_start(self):
        """Opening then leaving Music Library while the boot start is still in
        systemctl: a stop of the source drains the source's own task set, and a
        catalog start living there lost its library reconcile in silence."""
        src = _library(["music_library"])
        release = asyncio.Event()
        src._service_manager.start = AsyncMock(side_effect=_hang(release))
        await src.initialize()
        await settle()

        await src.stop()
        release.set()
        await settle()

        src._shares.catalog_started.assert_called_once()


class TestFromTheDock:
    @pytest.mark.asyncio
    async def test_enabling_starts_the_catalog_and_asks_for_its_libraries(self):
        """Without the reconcile every storage space keeps a null library id,
        which the library view drops, until the reconciler's next retry."""
        src = _library([])

        src.set_catalog_running(True)
        await settle()

        src._service_manager.start.assert_awaited_once_with(NAVIDROME_SERVICE)
        src._shares.catalog_started.assert_called_once()

    @pytest.mark.asyncio
    async def test_disabling_stops_the_catalog(self):
        src = _library([])

        src.set_catalog_running(False)
        await settle()

        src._service_manager.stop.assert_awaited_once_with(NAVIDROME_SERVICE)
        src._shares.catalog_started.assert_not_called()

    @pytest.mark.asyncio
    async def test_toggles_are_applied_in_the_order_they_were_made(self):
        """Off then on while the stop is still in systemctl: applied out of
        order, the catalog ends up stopped under a dock that says it is on."""
        calls = []
        src = _library([], calls)
        release = asyncio.Event()

        async def slow_stop(unit):
            await release.wait()
            calls.append(("stop", unit))
            return True

        src._service_manager.stop = AsyncMock(side_effect=slow_stop)

        src.set_catalog_running(False)
        src.set_catalog_running(True)
        await settle()
        release.set()
        await settle()

        assert calls == [("stop", NAVIDROME_SERVICE), ("start", NAVIDROME_SERVICE)]

    @pytest.mark.asyncio
    async def test_a_refused_start_reconciles_nothing(self):
        src = _library([])
        src._service_manager.start = AsyncMock(return_value=False)

        src.set_catalog_running(True)
        await settle()

        src._shares.catalog_started.assert_not_called()
