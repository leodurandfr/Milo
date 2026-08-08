"""Unit tests for the Music Library network-share service (NetworkShareService).

Covers the boot remount of configured SMB/NFS shares plus its bounded catch-up
retry for a NAS that was still offline when the backend booted, the mount-health
gate the full-scan/purge route depends on, and the liveness probe that keeps
``mounted`` honest for a share whose far side died. The config store and the
storage layer are driven through their own public API; nothing privileged is
touched (milo-mount is behind StorageManager, which has its own tests) and the
filesystem is stubbed at ``os.statvfs``, the one call the probe makes.

Moved here from test_music_library_source.py when the share block left the audio
source — the service is what owns this behaviour now.
"""
import errno
import threading
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.sources.music_library.shares import NetworkShareService

STATVFS = "backend.sources.music_library.shares.os.statvfs"


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


@pytest.fixture
def probing(shares):
    """A share service with one mounted NAS, ready to be probed."""
    shares._data.list_shares = AsyncMock(
        return_value=[{"id": "nas", "name": "NAS-Leo", "host": "10.0.0.2"}]
    )
    shares._storage.get_mounted_share_ids = Mock(return_value={"nas"})
    shares._storage.request_scan = AsyncMock()
    yield shares
    shares._probe_pool.shutdown(wait=False, cancel_futures=True)


async def _sweeps(service, count):
    for _ in range(count):
        await service._probe_shares()


class TestShareLiveness:
    """The probe that answers what /proc/mounts cannot: a NAS that loses power
    leaves its CIFS mount in the table, so every track behind it stays on offer
    and its stream comes back as HTTP 200 + a JSON error body that mpv skips in
    silence. The verdict folds into `mounted`, which is what hides the space and
    what stops a queue reading from it — hence the hysteresis."""

    @pytest.mark.asyncio
    async def test_two_failed_probes_do_not_hide_a_share(self, probing):
        # Flipping `mounted` arms _stop_if_storage_gone, which cuts the music
        # that is playing. A busy NAS blocking one call must not do that.
        with patch(STATVFS, side_effect=OSError(errno.EHOSTDOWN, "host is down")):
            await _sweeps(probing, 2)

        assert (await probing.list())[0]["mounted"] is True
        probing._on_storages_changed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_third_failure_hides_it_and_pushes_exactly_once(self, probing):
        with patch(STATVFS, side_effect=OSError(errno.EHOSTDOWN, "host is down")):
            await _sweeps(probing, 3)
            assert (await probing.list())[0]["mounted"] is False
            probing._on_storages_changed.assert_awaited_once()
            probing._on_catalog_changed.assert_called_once()
            # The push is on the transition, not on the state: a NAS that stays
            # dead must not re-broadcast the whole storage list every 30 s.
            await _sweeps(probing, 3)

        probing._on_storages_changed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_error_that_is_not_a_dead_link_never_hides_a_share(self, probing):
        # Fail open: only a certain negative may cost the user a library and a
        # stopped track. EACCES says this call failed, not that the NAS is gone.
        with patch(STATVFS, side_effect=OSError(errno.EACCES, "permission denied")):
            await _sweeps(probing, 5)

        assert (await probing.list())[0]["mounted"] is True
        probing._on_storages_changed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_inconclusive_probe_does_not_reset_the_strikes(self, probing):
        # Two certain negatives, one inconclusive, one more certain negative: the
        # count survives the gap, because the gap is not evidence of health.
        with patch(STATVFS, side_effect=OSError(errno.ETIMEDOUT, "timed out")):
            await _sweeps(probing, 2)
        with patch(STATVFS, side_effect=OSError(errno.EACCES, "denied")):
            await _sweeps(probing, 1)
        with patch(STATVFS, side_effect=OSError(errno.ETIMEDOUT, "timed out")):
            await _sweeps(probing, 1)

        assert (await probing.list())[0]["mounted"] is False

    @pytest.mark.asyncio
    async def test_a_share_that_answers_again_returns_with_a_quick_scan(self, probing):
        with patch(STATVFS, side_effect=OSError(errno.ENOTCONN, "not connected")):
            await _sweeps(probing, 3)
        assert (await probing.list())[0]["mounted"] is False

        with patch(STATVFS, return_value=Mock()):
            await _sweeps(probing, 1)

        assert (await probing.list())[0]["mounted"] is True
        # Quick, never full: a full scan purges what Navidrome cannot see, and
        # what just came back is a whole library's worth of tracks.
        probing._storage.request_scan.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_a_parked_probe_is_not_resubmitted_and_still_counts(self, probing):
        # The timeout is a deadline on our wait, not a cancellation: the worker
        # stays inside statvfs. A dead NAS must cost one thread, not one per
        # tick — and being still parked is itself the answer.
        release = threading.Event()
        calls = []

        def blocking(path):
            calls.append(path)
            release.wait(timeout=10)
            return Mock()

        try:
            with patch(STATVFS, side_effect=blocking), patch(
                "backend.sources.music_library.shares._LIVENESS_TIMEOUT_S", 0.05
            ):
                await _sweeps(probing, 3)

                assert calls == ["/media/milo/nas"]
                assert (await probing.list())[0]["mounted"] is False
        finally:
            release.set()

    @pytest.mark.asyncio
    async def test_only_configured_shares_are_probed(self, probing):
        # get_mounted_share_ids() reports every mount under the root, USB keys
        # included — and a key has no far side to ask about.
        probing._storage.get_mounted_share_ids = Mock(return_value={"nas", "MUSIC"})

        with patch(STATVFS, return_value=Mock()) as statvfs:
            await _sweeps(probing, 1)

        assert [call.args[0] for call in statvfs.call_args_list] == ["/media/milo/nas"]

    @pytest.mark.asyncio
    async def test_a_successful_mount_clears_a_stale_dead_verdict(self, probing):
        # Fixing a dead share's host and saving it remounts: mount.cifs talked to
        # the far side, so the verdict is stale evidence and must not outlive the
        # mount — otherwise the row stays grey until the next probe catches up.
        with patch(STATVFS, side_effect=OSError(errno.ESTALE, "stale handle")):
            await _sweeps(probing, 3)
        assert (await probing.list())[0]["mounted"] is False

        probing._storage.mount_share = AsyncMock(return_value="/media/milo/nas")
        await probing._mount_share({"id": "nas"})

        assert (await probing.list())[0]["mounted"] is True

    @pytest.mark.asyncio
    async def test_a_dead_nas_defers_the_purge(self, probing):
        # The verdict has to reach the full-scan gate, not just the UI: purging
        # while a space is unreadable drops an index that is still perfectly
        # valid.
        with patch(STATVFS, side_effect=OSError(errno.EIO, "io error")):
            await _sweeps(probing, 3)

        assert await probing.offline_names() == ["NAS-Leo"]
