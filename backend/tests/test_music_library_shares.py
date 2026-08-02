"""Unit tests for the Music Library network-share service (NetworkShareService).

Covers the boot remount of configured SMB/NFS shares plus its bounded catch-up
retry for a NAS that was still offline when the backend booted, and the
mount-health gate the full-scan/purge route depends on. The config store and the
storage layer are driven through their own public API; nothing privileged is
touched (milo-mount is behind StorageManager, which has its own tests).

Moved here from test_music_library_source.py when the share block left the audio
source — the service is what owns this behaviour now.
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.sources.music_library.shares import NetworkShareService


@pytest.fixture
def shares():
    """A share service whose collaborators are stubbed at their own boundary."""
    service = NetworkShareService(AsyncMock(return_value=None), Mock(), AsyncMock())
    service._data.initialize = AsyncMock()
    service._data.get_known_usb = AsyncMock(return_value={})
    service._storage.initialize = AsyncMock(return_value=True)
    service._storage.get_usb_mounts = Mock(return_value=[])
    return service


class TestOfflineNames:
    """The mount-health gate for the full-scan/purge route: purging while a share
    is offline would wrongly drop its still-valid tracks, so the route refuses
    when offline_names() is non-empty."""

    @pytest.mark.asyncio
    async def test_lists_only_unmounted_shares(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[
            {"id": "a", "name": "NAS-Leo", "host": "10.0.0.2"},
            {"id": "b", "name": "Studio", "host": "10.0.0.3"},
        ])
        shares._storage.get_mounted_share_ids = Mock(return_value={"a"})

        assert await shares.offline_names() == ["Studio"]

    @pytest.mark.asyncio
    async def test_empty_when_all_mounted(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[
            {"id": "a", "name": "NAS-Leo", "host": "10.0.0.2"},
        ])
        shares._storage.get_mounted_share_ids = Mock(return_value={"a"})

        assert await shares.offline_names() == []

    @pytest.mark.asyncio
    async def test_names_come_from_the_storage_list_not_a_second_rule(self, shares):
        # offline_names() projects storages(), so a share reads the same here as
        # in the settings row and the storage filter. It used to apply its own
        # name→host→id fallback, which is how the "cleanup deferred" warning came
        # to name a share differently from the row the user was looking at.
        shares._data.list_shares = AsyncMock(return_value=[
            {"id": "a", "name": "NAS-Leo", "host": "10.0.0.2"},
            {"id": "b", "host": "10.0.0.3"},   # unnamed → its id, as in storages()
        ])
        shares._storage.get_mounted_share_ids = Mock(return_value=set())

        assert await shares.offline_names() == ["NAS-Leo", "b"]

    @pytest.mark.asyncio
    async def test_unplugged_usb_key_defers_the_purge_too(self, shares):
        # A full scan purges what Navidrome cannot see. Now that an unplugged key
        # keeps its library, it has to gate the scan exactly like an asleep NAS —
        # otherwise a refresh silently destroys the index the key kept.
        shares._data.list_shares = AsyncMock(return_value=[])
        shares._storage.get_mounted_share_ids = Mock(return_value=set())
        shares._data.get_known_usb = AsyncMock(return_value={
            "U-1": {"name": "iPod", "label": "MUSIC",
                    "mountpoint": "/media/milo/MUSIC"},
        })

        assert await shares.offline_names() == ["iPod"]


class TestBootRemountRetry:
    """Boot remount of configured network shares + the bounded catch-up retry
    for a NAS that was still offline when the backend booted (reboot race)."""

    @pytest.mark.asyncio
    async def test_all_mounted_spawns_no_retry(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[
            {"id": "a"}, {"id": "b"},
        ])
        shares._storage.mount_share = AsyncMock(return_value="/media/milo/x")

        await shares._mount_configured()

        assert shares._storage.mount_share.await_count == 2
        assert shares._retry_task is None

    @pytest.mark.asyncio
    async def test_offline_share_spawns_retry(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[{"id": "a"}])
        shares._storage.mount_share = AsyncMock(return_value=None)  # offline

        with patch(
            "backend.sources.music_library.shares._SHARE_REMOUNT_RETRY_DELAYS_S", ()
        ):
            await shares._mount_configured()
            assert shares._retry_task is not None
            await shares._retry_task  # exhausted schedule → gives up cleanly

    @pytest.mark.asyncio
    async def test_retry_remounts_when_nas_comes_up(self, shares):
        share = {"id": "a"}
        # Offline on the first two attempts, then reachable.
        shares._storage.mount_share = AsyncMock(
            side_effect=[None, None, "/media/milo/a"]
        )

        with patch(
            "backend.sources.music_library.shares.asyncio.sleep", AsyncMock()
        ), patch(
            "backend.sources.music_library.shares._SHARE_REMOUNT_RETRY_DELAYS_S",
            (1, 1, 1),
        ):
            await shares._retry_offline([share])

        # Two retry rounds, the second one connects → stops early (3rd delay unused).
        assert shares._storage.mount_share.await_count == 3

    @pytest.mark.asyncio
    async def test_try_mount_is_fail_open(self, shares):
        shares._storage.mount_share = AsyncMock(side_effect=OSError("boom"))
        assert await shares._try_mount({"id": "a"}) is False
