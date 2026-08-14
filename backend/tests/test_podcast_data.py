"""Unit tests for PodcastDataService's read-modify-write atomicity.

The CRUD surface itself is exercised through the source and the routes; what
is only observable here is that two mutators running at once both survive. The
file is redirected to a tmp path so nothing touches /var/lib/milo.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.sources.podcast import data as podcast_data
from backend.sources.podcast.data import PodcastDataService


@pytest.fixture
def service(tmp_path):
    svc = PodcastDataService()
    svc._data_file = tmp_path / "podcast_data.json"
    return svc


async def test_concurrent_mutations_both_survive(service):
    """A subscription added while a progress tick is in flight must not vanish.

    `load_data` and `save_data` take `_file_lock` separately, so the second
    mutator's load lands in the window between the first's load and its save:
    it starts from a stale dict and its save writes the first one's whole
    update back out. Both callers report success and the loss is silent —
    the podcast source ticks progress every few seconds while the user is
    free to subscribe, so this is an ordinary pair, not a contrived one.
    """
    await service.initialize()

    await asyncio.gather(
        service.add_subscription("feed-1", "One", "https://img/1"),
        service.update_playback_progress("ep-1", position=42, duration=600),
    )

    data = await service.load_data()
    assert [s["uuid"] for s in data["subscriptions"]] == ["feed-1"]
    assert data["playback_progress"]["ep-1"]["position"] == 42


async def test_concurrent_progress_updates_both_survive(service):
    """Two episodes' progress written at once — neither entry may be dropped."""
    await service.initialize()

    await asyncio.gather(*[
        service.update_playback_progress(f"ep-{i}", position=i, duration=600)
        for i in range(6)
    ])

    progress = (await service.load_data())["playback_progress"]
    assert sorted(progress) == [f"ep-{i}" for i in range(6)]


async def test_unchanged_mutation_does_not_rewrite(service, monkeypatch):
    """Completing an episode that has no progress entry must not write.

    `_mutate` writes only when its callback reports a change. The content would
    be identical either way, so the file cannot show this — only the write can:
    a mutator that found nothing to do still re-stamps the file, and on this
    appliance every write is an fsync to the SD card. It is also what the
    pre-`_mutate` code did, and keeping it is what makes this a pure
    refactor of the locking.
    """
    await service.initialize()
    await service.update_playback_progress("ep-1", position=42, duration=600)

    saves = AsyncMock()
    monkeypatch.setattr(podcast_data, "save_versioned_json", saves)

    assert await service.mark_episode_completed("absent-episode") is True

    saves.assert_not_awaited()
