"""Unit tests for the Music Library network-share service (NetworkShareService).

Covers the boot remount of configured SMB/NFS shares plus its bounded catch-up
retry for a NAS that was still offline when the backend booted, and the liveness
probe that keeps ``mounted`` honest for a share whose far side died. The config store and the
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

from backend.sources.music_library.models import ShareRequest
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
    async def test_a_remount_drops_the_probe_parked_on_the_old_mount(self, probing):
        # A parked probe reads as a negative on every tick, so the one left
        # inside statvfs on the dead mount goes on answering for a mount that no
        # longer exists — three ticks after the repair, it hides the share the
        # remount just brought back, 90 s later and for no reason the user can see.
        release = threading.Event()

        def blocking(path):
            release.wait(timeout=10)
            return Mock()

        try:
            with patch(
                "backend.sources.music_library.shares._LIVENESS_TIMEOUT_S", 0.05
            ):
                with patch(STATVFS, side_effect=blocking):
                    await _sweeps(probing, 3)
                assert (await probing.list())[0]["mounted"] is False

                probing._storage.mount_share = AsyncMock(
                    return_value="/media/milo/nas"
                )
                await probing._mount_share({"id": "nas"})

                # The old worker is still parked; the repaired share must survive
                # the same three sweeps that hid it the first time.
                with patch(STATVFS, return_value=Mock()):
                    await _sweeps(probing, 3)

            assert (await probing.list())[0]["mounted"] is True
        finally:
            release.set()

    @pytest.mark.asyncio
    async def test_a_dead_nas_is_unmounted_in_the_storage_list(self, probing):
        # The verdict has to reach storages(), not just list(): that is where the
        # browse scope is built from, and a space whose far side is dead answers a
        # stream request with a 200 carrying a JSON error body that mpv skips in
        # silence. Nothing catches it at play time, so it has to be gone from the
        # catalog the moment the probe gives up.
        with patch(STATVFS, side_effect=OSError(errno.EIO, "io error")):
            await _sweeps(probing, 3)

        entries = await probing.storages()
        assert [e["name"] for e in entries if not e["mounted"]] == ["NAS-Leo"]


class TestUnmountRunsNoScan:
    """Removing or editing a share unmounts it and asks for no scan at all.

    A scan here would walk a path that no longer exists. What actually takes the
    removed share's tracks out of the catalog is the reconcile that retires its
    Navidrome library; an edit keeps the library and lets the next ordinary scan
    mark whatever the old path held. This used to run a *full* scan, which is
    global — one removal while a USB key was unplugged threw away the key's whole
    index, the 18-minute pass a replug exists to skip."""

    @pytest.fixture
    def removable(self, shares):
        """One mounted share, ready to be removed; collaborators stubbed.

        The config store is stubbed as a real (tiny) store rather than a constant
        list: the point of the removal path is the *order* of its steps, and a
        list_shares() that keeps answering after remove_share() would let a
        reconcile running too early pass.
        """
        configured = [{"id": "nas", "name": "NAS-Leo", "host": "10.0.0.2"}]
        shares._data.list_shares = AsyncMock(side_effect=lambda: list(configured))
        shares._data.get_share = AsyncMock(return_value={"id": "nas"})

        async def _remove(share_id):
            configured[:] = [c for c in configured if c["id"] != share_id]
            return True

        shares._data.remove_share = AsyncMock(side_effect=_remove)
        shares._storage.get_mounted_share_ids = Mock(return_value={"nas"})
        shares._storage.unmount_share = AsyncMock()
        shares._storage.request_scan = AsyncMock()
        shares._storage.forget_share_credentials = AsyncMock()
        shares._libraries.reconcile = AsyncMock(return_value=True)
        return shares

    @pytest.mark.asyncio
    async def test_removing_a_share_unmounts_it_and_scans_nothing(self, removable):
        assert await removable.remove("nas") is True
        removable._storage.unmount_share.assert_awaited_once_with("nas")
        removable._storage.request_scan.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_removing_a_share_retires_its_library(self, removable):
        """The reconcile is what deletes the tracks, so it has to run *after* the
        config entry is gone — reconciling against a set that still lists the
        share would keep its library, and with it every track it indexed."""
        await removable.remove("nas")

        removable._libraries.reconcile.assert_awaited_once()
        assert "/media/milo/nas" not in removable._libraries.reconcile.await_args[0][0]

    @pytest.mark.asyncio
    async def test_editing_a_share_scans_nothing_on_the_way_out(self, removable):
        # update() unmounts before it remounts; only the remount asks for a scan,
        # and it is the ordinary one that marks what the old path no longer holds.
        removable._data.update_share = AsyncMock(return_value={"id": "nas"})
        removable._storage.mount_share = AsyncMock(return_value="/media/milo/nas")

        await removable.update("nas", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS-Leo",
        ))

        removable._storage.unmount_share.assert_awaited_once_with("nas")
        removable._storage.request_scan.assert_not_awaited()


class TestShareCredentialsMoveWithThePassword:
    """The three cases the comment in update() states, none of which had run.

    milo-mount attaches ``<id>.cred`` whenever the file exists, so what the
    service does (or fails to do) with it *is* what the next mount authenticates
    with. ``has_credentials`` is not decoration either: the edit screen reads it
    to show "a password is saved" and to leave the field empty-means-keep.
    """

    @pytest.fixture
    def editable(self, shares):
        shares._data.get_share = AsyncMock(
            return_value={"id": "nas", "has_credentials": True}
        )
        shares._data.update_share = AsyncMock(
            side_effect=lambda sid, up: {"id": sid, **up}
        )
        shares._storage.unmount_share = AsyncMock()
        shares._storage.mount_share = AsyncMock(return_value="/media/milo/nas")
        shares._storage.forget_share_credentials = AsyncMock()
        shares._storage.get_mounted_share_ids = Mock(return_value={"nas"})
        shares._libraries.reconcile = AsyncMock(return_value=True)
        return shares

    @pytest.mark.asyncio
    async def test_switching_to_nfs_drops_the_stored_password(self, editable):
        """Clearing the flag alone would leave the file, and a later switch back
        to CIFS would remount with the old password while the screen says none is
        saved."""
        await editable.update("nas", ShareRequest(
            type="nfs", host="10.0.0.2", path="export/music", name="NAS",
        ))

        editable._storage.forget_share_credentials.assert_awaited_once_with("nas")
        assert editable._data.update_share.await_args[0][1]["has_credentials"] is False

    @pytest.mark.asyncio
    async def test_switching_to_nfs_a_share_that_had_none_forgets_nothing(
        self, editable
    ):
        """No cred file was ever written, so there is nothing to drop — and a
        privileged helper is not spawned to find that out."""
        editable._data.get_share = AsyncMock(
            return_value={"id": "nas", "has_credentials": False}
        )

        await editable.update("nas", ShareRequest(
            type="nfs", host="10.0.0.2", path="export/music", name="NAS",
        ))

        editable._storage.forget_share_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_new_password_carries_its_username_and_domain(self, editable):
        """They move as a unit: milo-mount rewrites the whole cred file from what
        it is handed, so a password stored beside a stale username authenticates
        as the wrong account."""
        await editable.update("nas", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS",
            username="claire", password="s3cret", domain="WORKGROUP",
        ))

        stored = editable._data.update_share.await_args[0][1]
        assert stored["has_credentials"] is True
        assert (stored["username"], stored["domain"]) == ("claire", "WORKGROUP")
        assert editable._storage.mount_share.await_args.kwargs["credentials"] == {
            "username": "claire", "password": "s3cret", "domain": "WORKGROUP",
        }

    @pytest.mark.asyncio
    async def test_an_edit_without_a_password_hands_the_mount_nothing(self, editable):
        """The idempotent PUT: no credentials on stdin is what makes milo-mount
        keep the persisted file, so passing an empty dict — or the username on
        its own — would rewrite it without a password and lock the share out."""
        await editable.update("nas", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS",
            username="claire",
        ))

        assert editable._storage.mount_share.await_args.kwargs["credentials"] is None
        editable._storage.forget_share_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_password_with_no_username_is_a_guest_login_password(
        self, editable
    ):
        """Only the keys that were given: milo-mount writes the cred file from
        these lines verbatim, and an empty ``username=`` is not the same thing as
        no username at all."""
        await editable.update("nas", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS",
            password="s3cret",
        ))

        assert editable._storage.mount_share.await_args.kwargs["credentials"] == {
            "password": "s3cret",
        }
