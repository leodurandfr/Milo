# backend/tests/test_music_library_libraries.py
"""`NavidromeLibraryService.reconcile` — keeping a library is not creating one.

What breaks when these fail: either a storage space that is present gets no
Navidrome library, and the frontend leaves it out of the filter entirely (a
library id is what makes it browsable), or an absent one is asked for on a loop
Navidrome can only refuse.

Measured on the unit 2026-08-25: two iPods last seen 18 and 21 days earlier were
still in the desired set, as designed -- an unplugged key keeps its library so
the 18-minute pass that indexed it is not thrown away. But neither had a library
yet, so every pass tried to CREATE one, Navidrome answered 400 `pathInvalid`
because the directory is not there, `missing` was never empty, and the retry
loop sat on its 60-second plateau writing ~1.4 MB a day of ERROR into the log an
operator reads. The distinction these tests pin is the one that was absent:
desired says what is kept, mounted says what can be created.
"""
import pytest
from unittest.mock import AsyncMock, Mock

from backend.sources.music_library.libraries import NavidromeLibraryService

NAS = "/media/milo/nas-leo"
IPOD = "/media/milo/IPOD_CLAIRE"


@pytest.fixture
def admin():
    """Navidrome's admin API, as the reconciler's collaborator."""
    client = Mock()
    client.list_libraries = AsyncMock(return_value=[])
    client.create_library = AsyncMock(return_value={"id": 7})
    client.delete_library = AsyncMock(return_value=True)
    client.rename_library = AsyncMock(return_value=True)
    client.grant_all_libraries = AsyncMock(return_value=True)
    return client


@pytest.fixture
def service(admin):
    svc = NavidromeLibraryService()
    svc._get_admin = AsyncMock(return_value=admin)
    svc._schedule_retry = Mock()
    return svc


class TestOnlyAMountedSpaceIsCreated:

    async def test_a_mounted_space_gets_its_library(self, service, admin):
        admin.list_libraries = AsyncMock(side_effect=[
            [], [{"id": 7, "path": NAS, "name": "NAS-Leo"}],
        ])

        assert await service.reconcile({NAS: "NAS-Leo"}, {NAS}) is True
        admin.create_library.assert_awaited_once_with("NAS-Leo", NAS)
        assert service.library_id(NAS) == 7

    async def test_an_absent_space_with_no_library_is_never_asked_for(self, service, admin):
        """Navidrome answers 400 pathInvalid — the request cannot ever succeed."""
        await service.reconcile({IPOD: "iPod de Claire"}, set())

        admin.create_library.assert_not_awaited()

    async def test_and_that_reconcile_is_a_success_so_the_retry_loop_ends(
        self, service, admin
    ):
        """Counting an absent space as missing is what looped for 18 days."""
        assert await service.reconcile({IPOD: "iPod de Claire"}, set()) is True
        service._schedule_retry.assert_not_called()

    async def test_a_mounted_space_still_missing_afterwards_is_a_failure(
        self, service, admin
    ):
        """The real error case must stay a failure, and stay retried."""
        admin.create_library = AsyncMock(return_value=None)

        assert await service.reconcile({NAS: "NAS-Leo"}, {NAS}) is False
        service._schedule_retry.assert_called_once()

    async def test_the_absent_one_does_not_hide_a_mounted_one_that_failed(
        self, service, admin
    ):
        """Both together: the iPod is skipped, the NAS still has to land."""
        admin.create_library = AsyncMock(return_value=None)

        assert await service.reconcile(
            {NAS: "NAS-Leo", IPOD: "iPod de Claire"}, {NAS}
        ) is False
        admin.create_library.assert_awaited_once_with("NAS-Leo", NAS)


class TestAnAbsentSpaceKeepsWhatItHas:
    """The behaviour the fix must not break: unplugging must cost no re-index."""

    async def test_an_absent_space_that_already_has_a_library_keeps_it(
        self, service, admin
    ):
        admin.list_libraries = AsyncMock(
            return_value=[{"id": 3, "path": IPOD, "name": "iPod de Claire"}]
        )

        assert await service.reconcile({IPOD: "iPod de Claire"}, set()) is True
        admin.delete_library.assert_not_awaited()
        assert service.library_id(IPOD) == 3

    async def test_an_absent_space_is_still_renamed(self, service, admin):
        """A rename is a write on an existing library, not a create."""
        admin.list_libraries = AsyncMock(side_effect=[
            [{"id": 3, "path": IPOD, "name": "old name"}],
            [{"id": 3, "path": IPOD, "name": "iPod de Claire"}],
        ])

        assert await service.reconcile({IPOD: "iPod de Claire"}, set()) is True
        admin.rename_library.assert_awaited_once_with(3, "iPod de Claire", IPOD)

    async def test_a_library_no_storage_space_claims_is_still_dropped(
        self, service, admin
    ):
        gone = "/media/milo/forgotten-key"
        admin.list_libraries = AsyncMock(side_effect=[
            [{"id": 3, "path": NAS, "name": "NAS-Leo"},
             {"id": 9, "path": gone, "name": "Forgotten"}],
            [{"id": 3, "path": NAS, "name": "NAS-Leo"}],
        ])

        assert await service.reconcile({NAS: "NAS-Leo"}, {NAS}) is True
        admin.delete_library.assert_awaited_once_with(9)
