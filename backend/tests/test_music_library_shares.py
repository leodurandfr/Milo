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
import asyncio
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

    @pytest.mark.asyncio
    async def test_a_share_config_that_cannot_be_read_mounts_nothing(self, shares):
        """The boot remount's own fail-open: an unreadable store leaves the
        mounts alone and schedules no retry, rather than propagating into the
        gather that would turn it into the reset banner.

        Note what this does NOT buy: initialize() reads the same store again
        through _sync_libraries, unguarded, so a read that keeps failing takes
        the source down one line later anyway (constat T16-5)."""
        shares._data.list_shares = AsyncMock(side_effect=OSError("boom"))
        shares._storage.mount_share = AsyncMock()

        await shares._mount_configured()

        shares._storage.mount_share.assert_not_awaited()
        assert shares._retry_task is None


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
    async def test_a_probe_that_never_answers_in_time_counts_as_a_strike(self, probing):
        """The branch that actually runs on the appliance, and the only one that
        was never driven here.

        Every other test in this class raises from ``statvfs``, which returns
        long before the deadline and therefore exercises the errno branch.
        Measured on the unit with the NAS's cable pulled, that is not the branch
        taken: ``statvfs``, ``stat`` and ``listdir`` all sat in the kernel for
        10.18 s before returning EHOSTDOWN, so the verdict came from the timeout
        instead. A regression in this path would hide a live share, and no test
        would have seen it.

        The second and third sweeps never submit a probe of their own — the
        first one is still parked in the syscall, and being parked *is* the
        negative, which is what lets a wedged mount reach three strikes at all.
        """
        release = threading.Event()
        with patch(STATVFS, side_effect=lambda _path: release.wait(30)), patch(
            "backend.sources.music_library.shares._LIVENESS_TIMEOUT_S", 0.01
        ):
            try:
                await _sweeps(probing, 3)
                assert (await probing.list())[0]["mounted"] is False
            finally:
                release.set()

    @pytest.mark.asyncio
    async def test_a_share_that_answered_starts_its_next_outage_from_zero(self, probing):
        """Strikes must not survive a recovery, or the second outage of a session
        is announced on its first failed probe instead of its third — and a NAS
        that merely flaps ends up hidden on one blocked call, which is the exact
        thing the hysteresis exists to prevent.

        The complement of the inconclusive case below: a probe that *answered* is
        evidence of health, so it clears the count, where a probe that could not
        say anything leaves it standing.
        """
        with patch(STATVFS, side_effect=OSError(errno.EHOSTDOWN, "host is down")):
            await _sweeps(probing, 2)
        with patch(STATVFS, return_value=Mock()):
            await _sweeps(probing, 1)
        with patch(STATVFS, side_effect=OSError(errno.EHOSTDOWN, "host is down")):
            await _sweeps(probing, 1)

        assert (await probing.list())[0]["mounted"] is True

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

    @pytest.mark.asyncio
    async def test_editing_an_offline_share_still_gives_it_its_library(
        self, removable
    ):
        """A remount that failed fired no hook, so this is the pass that carries
        the new name to Navidrome — without it an edit made while the NAS is
        asleep is invisible until the NAS comes back."""
        removable._data.update_share = AsyncMock(
            return_value={"id": "nas", "name": "NAS-Claire"}
        )
        removable._storage.mount_share = AsyncMock(return_value=None)

        updated = await removable.update("nas", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS-Claire",
        ))

        assert updated["mounted"] is False
        removable._libraries.reconcile.assert_awaited_once()


class TestBoot:
    """initialize() — the method that mounts the owner's NAS on every boot.

    Measured green by evisceration (2026-08-24): replaced by `return None`, the
    whole suite still passed. It loads the share config (the one fail-loud step
    of the source), brings the storage layer up, remounts every configured
    share, reconciles the Navidrome libraries, and starts the two watchers that
    keep `scanning` and `mounted` honest afterwards. Nothing observed any of it.
    """

    @pytest.fixture
    def bootable(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[{"id": "nas"}])
        shares._storage.mount_share = AsyncMock(return_value="/media/milo/nas")
        shares._storage.get_mounted_share_ids = Mock(return_value={"nas"})
        shares._libraries.reconcile = AsyncMock(return_value=True)
        yield shares
        shares._probe_pool.shutdown(wait=False, cancel_futures=True)

    @pytest.mark.asyncio
    async def test_boot_brings_the_layers_up_then_mounts(self, bootable):
        """The config load has to precede the mount: _mount_configured reads the
        share list out of the store, so a mount attempted first would find none
        and the NAS would stay absent until the user re-saved it by hand."""
        order = []
        bootable._data.initialize = AsyncMock(side_effect=lambda: order.append("data"))
        bootable._storage.initialize = AsyncMock(
            side_effect=lambda: order.append("storage")
        )
        bootable._storage.mount_share = AsyncMock(
            side_effect=lambda *a, **k: order.append("mount") or "/media/milo/nas"
        )

        await bootable.initialize()

        assert order == ["data", "storage", "mount"]

    @pytest.mark.asyncio
    async def test_boot_remounts_every_configured_share(self, bootable):
        bootable._data.list_shares = AsyncMock(
            return_value=[{"id": "nas"}, {"id": "backup"}]
        )

        await bootable.initialize()

        assert [c.args[0]["id"] for c in bootable._storage.mount_share.await_args_list] \
            == ["nas", "backup"]

    @pytest.mark.asyncio
    async def test_boot_reconciles_the_libraries_even_with_nothing_mounted(
        self, bootable
    ):
        """The pass that covers what no mount hook fired for — an offline NAS, or
        a boot with nothing plugged in. Without it a configured-but-absent share
        has no Navidrome library, so it is unbrowsable until the next mount
        change or the next reboot."""
        bootable._data.list_shares = AsyncMock(return_value=[])
        bootable._storage.get_mounted_share_ids = Mock(return_value=set())

        await bootable.initialize()

        bootable._libraries.reconcile.assert_awaited_once_with({}, set())

    @pytest.mark.asyncio
    async def test_boot_starts_both_watchers(self, bootable):
        """The scan watcher and the liveness probe are the only things that keep
        `scanning` and a dead NAS's `mounted` honest after boot; neither is
        started anywhere else, so a boot that skips them leaves both frozen at
        whatever the last mount happened to say."""
        await bootable.initialize()

        try:
            running = {t.get_name() for t in asyncio.all_tasks()}
            assert "music_library.shares.scan-watcher" in running
            assert "music_library.shares.share-liveness" in running
        finally:
            await bootable._bg.cancel_all()


class TestUnknownIdIsTheOnlyBarrier:
    """`get_share(...) is None` is what keeps a share write off another storage
    space's mountpoint, and its line had never run.

    StorageManager.unmount_share falls back to the deterministic
    ``/media/milo/<id>`` when the id isn't in its session map, so the id from the
    URL becomes a real path. milo-umount then confines it to a single component
    under the mount root — which every USB key's mountpoint also satisfies. So a
    single-segment id naming a plugged-in key (``DELETE /shares/IPOD``) reaches
    milo-umount as that key's live mountpoint, and this guard is the only thing
    that stops it. Anything with a slash never gets this far: the routes declare
    a plain ``{share_id}``, which Starlette will not match across ``/``.
    """

    @pytest.fixture
    def strict(self, shares):
        shares._data.get_share = AsyncMock(return_value=None)
        shares._data.update_share = AsyncMock()
        shares._data.remove_share = AsyncMock()
        shares._storage.unmount_share = AsyncMock()
        shares._storage.mount_share = AsyncMock()
        shares._storage.forget_share_credentials = AsyncMock()
        return shares

    @pytest.mark.asyncio
    async def test_removing_an_unknown_id_unmounts_nothing(self, strict):
        assert await strict.remove("IPOD_CLAIRE") is False

        strict._storage.unmount_share.assert_not_awaited()
        strict._data.remove_share.assert_not_awaited()
        strict._storage.forget_share_credentials.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updating_an_unknown_id_unmounts_nothing(self, strict):
        result = await strict.update("IPOD_CLAIRE", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="x",
        ))

        assert result is None
        strict._storage.unmount_share.assert_not_awaited()
        strict._data.update_share.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_share_that_disappears_between_the_read_and_the_write(
        self, strict
    ):
        """The config write is a second chance to say no. Unmounting on the way
        to a write that then found nothing would take down a share the caller no
        longer owns."""
        strict._data.get_share = AsyncMock(return_value={"id": "nas"})
        strict._data.update_share = AsyncMock(return_value=None)

        result = await strict.update("nas", ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="x",
        ))

        assert result is None
        strict._storage.unmount_share.assert_not_awaited()
        strict._storage.mount_share.assert_not_awaited()


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


class TestScanChannel:
    """The whole scan channel had never run: the state the settings screen reads,
    the two ways a scan is asked for, and the poll that ends it.

    Navidrome exposes no scan event, so this poll is the only thing that tells a
    client indexing started or stopped — including the hourly scan Navidrome runs
    on its own, which nothing else could know to look for.
    """

    @pytest.fixture
    def scanner(self, shares):
        shares._storage.request_scan = AsyncMock()
        return shares

    @pytest.mark.asyncio
    async def test_scan_state_is_a_copy(self, scanner):
        """It reaches a route and a broadcast; handing out the live dict would
        let a caller's own bookkeeping rewrite what the watcher compares
        against, and the next real change would then push nothing."""
        state = scanner.scan_state()
        state["scanning"] = "tampered"

        assert scanner.scan_state() == {"scanning": False}

    @pytest.mark.asyncio
    async def test_request_scan_goes_through_the_busy_handling(self, scanner):
        """Deferring behind a running scan lives in StorageManager; a caller with
        no storage event of its own gets it by using the same primitive rather
        than a second, thinner one."""
        await scanner.request_scan()

        scanner._storage.request_scan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_started_scan_is_announced_and_watched_closely(self, scanner):
        """A quick scan takes ~0.4 s against a 15 s idle poll, so without this
        push the refresh button would start one and no client would ever hear
        about it; the kick is what has the watcher confirm the end of it."""
        await scanner.note_scan_started()

        assert scanner.scan_state() == {"scanning": True}
        scanner._on_storages_changed.assert_awaited_once()
        assert scanner._scan_kick.is_set()

    @pytest.mark.asyncio
    async def test_a_scan_that_ends_drops_the_catalog_caches(self, scanner):
        """The falling edge is the catalog change: the album lists cached before
        it were built from the old index."""
        scanner._scan = {"scanning": True}
        client = AsyncMock()
        client.get_scan_status = AsyncMock(return_value={"scanning": False})
        scanner._navidrome_provider = AsyncMock(return_value=client)

        assert await scanner._poll_scan() is False

        scanner._on_catalog_changed.assert_called_once()
        scanner._on_storages_changed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_scan_still_running_pushes_its_growing_counts(self, scanner):
        """Each poll carries the storage spaces' track counts while indexing, so
        a freshly-plugged key's tab fills as it goes — but nothing is invalidated
        until it ends."""
        scanner._scan = {"scanning": True}
        client = AsyncMock()
        client.get_scan_status = AsyncMock(return_value={"scanning": True})
        scanner._navidrome_provider = AsyncMock(return_value=client)

        assert await scanner._poll_scan() is True

        scanner._on_storages_changed.assert_awaited_once()
        scanner._on_catalog_changed.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_idle_poll_pushes_nothing(self, scanner):
        """The idle cadence is one poll every 15 s forever; pushing on each would
        be the per-browser polling this watcher exists to replace."""
        client = AsyncMock()
        client.get_scan_status = AsyncMock(return_value={"scanning": False})
        scanner._navidrome_provider = AsyncMock(return_value=client)

        assert await scanner._poll_scan() is False

        scanner._on_storages_changed.assert_not_awaited()
        scanner._on_catalog_changed.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_navidrome_that_is_not_up_is_not_a_finished_scan(self, scanner):
        """Fail-open, and specifically not a falling edge: reading "no answer" as
        "the scan ended" would drop the catalog caches on every poll while
        Navidrome is restarting."""
        scanner._navidrome_provider = AsyncMock(return_value=None)

        assert await scanner._poll_scan() is False

        scanner._on_catalog_changed.assert_not_called()
        scanner._on_storages_changed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refused_status_read_is_not_a_finished_scan_either(self, scanner):
        scanner._scan = {"scanning": True}
        client = AsyncMock()
        client.get_scan_status = AsyncMock(return_value=None)
        scanner._navidrome_provider = AsyncMock(return_value=client)

        assert await scanner._poll_scan() is False

        scanner._on_catalog_changed.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failing_poll_does_not_kill_the_watcher(self, scanner):
        """It is spawned once at boot and never restarted, so one exception
        escaping the loop body would freeze `scanning` for the whole session."""
        scanner._navidrome_provider = AsyncMock(
            side_effect=[RuntimeError("navidrome hiccup"), asyncio.CancelledError]
        )
        scanner._scan_kick.set()  # so the first cadence does not really sleep

        with pytest.raises(asyncio.CancelledError):
            await scanner._watch_scan()

        assert scanner._navidrome_provider.await_count == 2


class TestShareCreation:
    """add() had never run — the path a share takes from the LAN request body to
    a read-only mount under /media/milo."""

    @pytest.fixture
    def creatable(self, shares):
        shares._data.add_share = AsyncMock(
            side_effect=lambda **kw: {"id": "nas-leo-ab12cd34", **kw}
        )
        shares._data.list_shares = AsyncMock(return_value=[])
        shares._storage.mount_share = AsyncMock(return_value="/media/milo/nas-leo-ab12cd34")
        shares._storage.get_mounted_share_ids = Mock(return_value=set())
        shares._libraries.reconcile = AsyncMock(return_value=True)
        return shares

    @pytest.mark.asyncio
    async def test_a_new_share_is_persisted_before_it_is_mounted(self, creatable):
        """The config is the source of truth a boot remount replays, so it has to
        land whether or not the NAS answers today — and the id it generates is
        what the mountpoint and the cred file are named after."""
        order = []
        creatable._data.add_share = AsyncMock(
            side_effect=lambda **kw: order.append("persist") or {"id": "nas-leo-ab12cd34", **kw}
        )
        creatable._storage.mount_share = AsyncMock(
            side_effect=lambda *a, **k: order.append("mount") or "/media/milo/x"
        )

        await creatable.add(ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS-Leo",
        ))

        assert order == ["persist", "mount"]

    @pytest.mark.asyncio
    async def test_a_password_is_stored_as_a_flag_and_never_returned(self, creatable):
        """It goes to milo-mount on stdin and to the root-only cred file, and
        nowhere else: the response feeds the settings screen."""
        created = await creatable.add(ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS-Leo",
            username="claire", password="s3cret",
        ))

        assert creatable._data.add_share.await_args.kwargs["has_credentials"] is True
        assert "password" not in created
        assert creatable._storage.mount_share.await_args.kwargs["credentials"] == {
            "username": "claire", "password": "s3cret",
        }

    @pytest.mark.asyncio
    async def test_a_share_whose_nas_is_down_is_kept_and_reported_unmounted(
        self, creatable
    ):
        """It reached no mount hook, so this is what gives it its Navidrome
        library — without it the share stays unbrowsable until an unrelated mount
        change or a reboot."""
        creatable._storage.mount_share = AsyncMock(return_value=None)

        created = await creatable.add(ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS-Leo",
        ))

        assert created["mounted"] is False
        creatable._libraries.reconcile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_mount_that_succeeded_does_not_reconcile_twice(self, creatable):
        """StorageManager's own hook already did it; a second pass here would
        rebuild the library set on every add for nothing."""
        await creatable.add(ShareRequest(
            type="cifs", host="10.0.0.2", path="music", name="NAS-Leo",
        ))

        creatable._libraries.reconcile.assert_not_awaited()


class TestUsbWrites:
    """The three USB writes and the read that separates their two failures — all
    at zero coverage, and the route branches 404 and 409 on them."""

    @pytest.fixture
    def keys(self, shares):
        shares._storage.get_usb_mounts = Mock(return_value=[
            {"uuid": "AAAA", "label": "IPOD", "mountpoint": "/media/milo/IPOD"},
        ])
        shares._data.get_known_usb = AsyncMock(return_value={})
        shares._data.list_shares = AsyncMock(return_value=[])
        shares._data.remember_usb = AsyncMock()
        shares._storage.get_mounted_share_ids = Mock(return_value=set())
        shares._libraries.reconcile = AsyncMock(return_value=True)
        return shares

    @pytest.mark.asyncio
    async def test_a_plugged_in_key_is_reported_mounted(self, keys):
        assert await keys.usb_is_mounted("AAAA") is True
        assert await keys.usb_is_mounted("BBBB") is False

    @pytest.mark.asyncio
    async def test_renaming_an_unknown_key_changes_nothing(self, keys):
        """The route turns this into a 404, so a rename that quietly did nothing
        would be reported as success."""
        keys._data.set_usb_name = AsyncMock(return_value=False)

        assert await keys.rename_usb("BBBB", "Claire") is False
        keys._libraries.reconcile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_rename_reaches_the_navidrome_library(self, keys):
        """The name is what both the storage filter and Navidrome's own UI show;
        persisting it without reconciling would leave the two disagreeing until
        the next mount change."""
        keys._data.set_usb_name = AsyncMock(return_value=True)
        keys._data.get_known_usb = AsyncMock(
            return_value={"AAAA": {"name": "Claire", "mountpoint": "/media/milo/IPOD"}}
        )

        assert await keys.rename_usb("AAAA", "Claire") is True
        assert keys._libraries.reconcile.await_args[0][0] == {"/media/milo/IPOD": "Claire"}

    @pytest.mark.asyncio
    async def test_a_key_still_plugged_in_is_not_forgotten(self, keys):
        """The next reconcile would put it straight back, so the only readable
        outcome is to refuse and let the user unplug it — the route's 409."""
        keys._data.forget_usb = AsyncMock(return_value=True)

        assert await keys.forget_usb("AAAA") is False
        keys._data.forget_usb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forgetting_an_unknown_key_retires_nothing(self, keys):
        keys._data.forget_usb = AsyncMock(return_value=False)

        assert await keys.forget_usb("BBBB") is False
        keys._libraries.reconcile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forgetting_an_unplugged_key_retires_its_library(self, keys):
        """This is what actually frees the index — the deliberate counterpart to
        keeping a key's catalog forever by default."""
        keys._storage.get_usb_mounts = Mock(return_value=[])
        keys._data.forget_usb = AsyncMock(return_value=True)

        assert await keys.forget_usb("AAAA") is True
        keys._libraries.reconcile.assert_awaited_once_with({}, set())
        keys._on_catalog_changed.assert_called()


class TestStoragesWithStats:
    """The shape `GET /storages` and the `storages_changed` broadcast both
    return, so a page load and a hotplug push agree field for field — and it had
    never been built once."""

    @pytest.mark.asyncio
    async def test_counts_are_matched_to_their_own_storage_space(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[
            {"id": "nas", "name": "NAS-Leo"},
        ])
        shares._data.get_known_usb = AsyncMock(return_value={
            "AAAA": {"name": "IPOD", "mountpoint": "/media/milo/IPOD"},
        })
        shares._storage.get_mounted_share_ids = Mock(return_value={"nas"})
        shares._libraries.stats = AsyncMock(return_value={
            "/media/milo/nas": {"track_count": 9, "album_count": 2, "missing_count": 1},
        })

        by_name = {e["name"]: e for e in await shares.storages_with_stats()}

        assert by_name["NAS-Leo"]["track_count"] == 9
        assert by_name["NAS-Leo"]["missing_count"] == 1
        # Navidrome has no library for the key yet; zeros, not the neighbour's
        # counts and not a missing key the frontend would render as undefined.
        assert by_name["IPOD"]["track_count"] == 0
        assert by_name["IPOD"]["album_count"] == 0


class TestAVerdictDiesWithItsMount:
    """A liveness verdict is about a mount, so it must not outlive it: kept, it
    would carry a dead NAS's three strikes into the next mount under the same
    id and hide a share that has just been repaired."""

    @pytest.mark.asyncio
    async def test_an_unmounted_share_loses_its_verdict_and_its_strikes(self, probing):
        probing._alive["nas"] = False
        probing._probe_failures["nas"] = 3
        probing._storage.get_mounted_share_ids = Mock(return_value=set())

        await probing._probe_shares()

        assert "nas" not in probing._alive
        assert "nas" not in probing._probe_failures

    @pytest.mark.asyncio
    async def test_a_failing_sweep_does_not_kill_the_liveness_watcher(self, probing):
        """Spawned once at boot and never restarted: one exception escaping the
        loop body freezes every share's `mounted` for the session, and a NAS that
        dies afterwards keeps offering tracks mpv skips in silence."""
        probing._storage.get_mounted_share_ids = Mock(
            side_effect=[RuntimeError("procfs hiccup"), asyncio.CancelledError]
        )

        with patch(
            "backend.sources.music_library.shares.asyncio.sleep", AsyncMock()
        ), pytest.raises(asyncio.CancelledError):
            await probing._watch_share_liveness()

        assert probing._storage.get_mounted_share_ids.call_count == 2


class TestTeardown:
    """cleanup() comes out red under evisceration with 0% of its lines: the only
    thing that fails is the AST guardrail in test_service_wiring, which reads
    `cancel_all` out of the source without running anything. These four drains
    are what a systemd stop actually depends on."""

    @pytest.mark.asyncio
    async def test_teardown_drains_every_layer_it_owns(self, shares):
        shares._storage.cleanup = AsyncMock()
        shares._libraries.cleanup = AsyncMock()
        shares._bg.spawn(asyncio.sleep(3600), label="scan-watcher")

        await shares.cleanup()

        assert not asyncio.all_tasks() - {asyncio.current_task()}
        shares._storage.cleanup.assert_awaited_once()
        shares._libraries.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_teardown_does_not_wait_on_a_probe_stuck_on_a_dead_mount(
        self, shares
    ):
        """A worker parked in statvfs returns when the kernel gives up on the
        link, which is minutes, and nothing at shutdown needs its answer."""
        started, release, finished = (threading.Event() for _ in range(3))

        def _parked():
            started.set()
            release.wait(5)
            finished.set()

        shares._probe_pool.submit(_parked)
        assert started.wait(2)
        shares._storage.cleanup = AsyncMock()
        shares._libraries.cleanup = AsyncMock()

        await shares.cleanup()

        # Waiting for it would hold the shutdown for as long as the kernel takes
        # to give up on the link. Asserted as state, not as elapsed time.
        assert not finished.is_set()
        release.set()


class TestPlaylistPlacement:
    """A playlist Milō creates is tied to the storage space it was created in,
    because Navidrome's own library ids are reassigned when a key comes back and
    anything persisted here is keyed by Milō's id instead."""

    @pytest.fixture
    def placed(self, shares):
        shares._data.list_shares = AsyncMock(return_value=[])
        shares._data.get_known_usb = AsyncMock(return_value={
            "AAAA": {"name": "IPOD", "mountpoint": "/media/milo/IPOD"},
        })
        shares._storage.get_mounted_share_ids = Mock(return_value=set())
        shares._libraries.library_id = Mock(
            side_effect=lambda mp: 7 if mp == "/media/milo/IPOD" else None
        )
        shares._data.set_playlist_storage = AsyncMock()
        return shares

    @pytest.mark.asyncio
    async def test_a_playlist_is_filed_under_milos_own_storage_id(self, placed):
        await placed.record_playlist_storage("pl-1", 7)

        placed._data.set_playlist_storage.assert_awaited_once_with("pl-1", "AAAA")

    @pytest.mark.asyncio
    async def test_a_playlist_in_a_library_milo_cannot_place_is_not_filed(
        self, placed
    ):
        """Recording it against the wrong space would survive the reassignment
        and put the playlist behind a filter it does not belong to."""
        await placed.record_playlist_storage("pl-1", 99)

        placed._data.set_playlist_storage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unmapped_library_resolves_to_no_storage(self, placed):
        assert await placed.storage_id_for_library(7) == "AAAA"
        assert await placed.storage_id_for_library(99) is None
