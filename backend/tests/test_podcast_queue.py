# backend/tests/test_podcast_queue.py
"""`podcast/data.py` — the in-progress queue, unsubscribe, and the settings.

`test_podcast_data.py` covers the boot path, the schema-mismatch banner and the
`itunes_id` capture. What it never ran is `get_in_progress_episodes` — 11 lines,
zero of them — which *is* the "continue listening" screen, plus the unsubscribe
mutation and the whole settings pair.

Everything here drives the real service against a file in `tmp_path`: the store
writes `/var/lib/milo/podcast_data.json` on the live appliance, and this is the
file whose sibling (`radio_data.json`) an escaped evisceration flattened on
2026-08-25.
"""
import json

import pytest

from backend.sources.podcast.data import PodcastDataService


@pytest.fixture
def store(tmp_path):
    svc = PodcastDataService()
    svc._data_file = tmp_path / "podcast_data.json"
    return svc


def _write(store, **sections):
    body = {"schema_version": PodcastDataService.SCHEMA_VERSION,
            "subscriptions": [], "playback_progress": {},
            "settings": {"playback_speed": 1.0}}
    body.update(sections)
    store._data_file.write_text(json.dumps(body))


def _read(store):
    return json.loads(store._data_file.read_text())


def _row(position, duration, **over):
    """One `playback_progress` row, in the shape `update_playback_progress`
    writes — every key present, because that writer never omits one."""
    row = {"position": position, "duration": duration, "last_played": 1_700_000_000,
           "completed": False, "podcast_uuid": "feed-1", "episode_name": "Ep",
           "podcast_name": "Show", "image_url": "http://img/e.jpg"}
    row.update(over)
    return row


class TestTheContinueListeningQueue:
    """`get_in_progress_episodes` — the queue view's only source of rows."""

    async def test_a_part_played_episode_is_in_the_queue(self, store):
        """Non-triviality: every exclusion below rests on this row being one
        the queue can hold."""
        _write(store, playback_progress={"ep-1": _row(640, 3600)})

        queue = await store.get_in_progress_episodes()

        assert [e["episode_uuid"] for e in queue] == ["ep-1"]

    async def test_the_stored_metadata_travels_with_the_row(self, store):
        """The queue card shows the episode name, the show name and the
        artwork; the catalogue is not consulted here, so anything the row does
        not carry is a blank card."""
        _write(store, playback_progress={"ep-1": _row(640, 3600)})

        entry = (await store.get_in_progress_episodes())[0]

        assert entry["episode_name"] == "Ep"
        assert entry["podcast_name"] == "Show"
        assert entry["image_url"] == "http://img/e.jpg"
        assert (entry["position"], entry["duration"]) == (640, 3600)

    async def test_an_episode_never_started_is_not_in_the_queue(self, store):
        """A row at position 0 exists as soon as a progress tick lands; putting
        it in the queue offers "continue" on an episode with nothing to
        continue."""
        _write(store, playback_progress={"ep-1": _row(0, 3600)})

        assert await store.get_in_progress_episodes() == []

    async def test_an_episode_with_no_known_duration_is_not_in_the_queue(self, store):
        """Podcast Index serves `duration: null` often enough to matter.

        Measured constat: the `duration > 0` clause is **inert**, shadowed by
        the `position < duration - 30` test below it — with duration 0 and any
        position past 0 that comparison is already false, and a negative
        duration is not a shape the writer can produce. Removing the clause
        changes no answer. Left in place (it says what the row must be, and
        deleting it buys nothing — family B1-10 / B7-15); the assertion is on
        the behaviour, which holds either way."""
        _write(store, playback_progress={"ep-1": _row(640, 0)})

        assert await store.get_in_progress_episodes() == []

    async def test_an_episode_marked_completed_is_out_of_the_queue(self, store):
        """The swipe-to-finish gesture writes exactly this flag, and this is
        what makes the card disappear."""
        _write(store, playback_progress={"ep-1": _row(640, 3600, completed=True)})

        assert await store.get_in_progress_episodes() == []

    async def test_an_episode_inside_the_last_thirty_seconds_is_out(self, store):
        """The end-of-episode grace. Without it the last credits leave every
        finished episode sitting in the queue, and the owner clears them by
        hand forever."""
        _write(store, playback_progress={"ep-1": _row(3580, 3600)})

        assert await store.get_in_progress_episodes() == []

    async def test_the_boundary_of_the_grace_window_is_still_in_the_queue(self, store):
        """One second before the window: the pair that makes the comparison a
        real boundary rather than a direction."""
        _write(store, playback_progress={"ep-1": _row(3569, 3600)})

        assert len(await store.get_in_progress_episodes()) == 1

    async def test_the_most_recently_played_episode_is_first(self, store):
        """The queue is a resume list, so the top row must be the one the owner
        just left. The rows are inserted oldest-first so insertion order alone
        cannot produce this answer — only the sort can."""
        _write(store, playback_progress={
            "oldest": _row(10, 3600, last_played=1_700_000_000),
            "middle": _row(10, 3600, last_played=1_700_000_500),
            "newest": _row(10, 3600, last_played=1_700_001_000),
        })

        queue = await store.get_in_progress_episodes()

        assert [e["episode_uuid"] for e in queue] == ["newest", "middle", "oldest"]

    async def test_a_row_with_no_timestamp_sorts_last_rather_than_crashing(self, store):
        """A row written before `last_played` existed must not take the whole
        queue down with a TypeError."""
        row = _row(10, 3600)
        del row["last_played"]
        _write(store, playback_progress={"undated": row,
                                         "dated": _row(10, 3600)})

        queue = await store.get_in_progress_episodes()

        assert [e["episode_uuid"] for e in queue] == ["dated", "undated"]

    async def test_an_empty_store_answers_an_empty_queue(self, store):
        _write(store)

        assert await store.get_in_progress_episodes() == []


class TestMarkingAnEpisodeFinished:
    async def test_the_flag_is_persisted_and_the_row_stays(self, store):
        """The row is kept, not deleted: `playback_progress` is also what makes
        an episode show "already listened" in the series list."""
        _write(store, playback_progress={"ep-1": _row(640, 3600)})

        await store.mark_episode_completed("ep-1")

        assert _read(store)["playback_progress"]["ep-1"]["completed"] is True

    async def test_the_episode_leaves_the_queue(self, store):
        _write(store, playback_progress={"ep-1": _row(640, 3600)})

        await store.mark_episode_completed("ep-1")

        assert await store.get_in_progress_episodes() == []

    async def test_an_episode_with_no_row_writes_nothing(self, store):
        """Constat, kept from the Lot A sweep and now pinned: this answers
        `True` either way — its `apply` returns `(False, True)`, so "nothing
        changed" and "done" are the same answer, and both callers drop the
        return value. What is observable is the file, so that is what is
        asserted."""
        _write(store, playback_progress={})
        before = store._data_file.read_text()

        assert await store.mark_episode_completed("never-played") is True
        assert store._data_file.read_text() == before


class TestUnsubscribing:
    async def test_the_podcast_is_dropped_and_the_others_are_kept(self, store):
        _write(store, subscriptions=[
            {"uuid": "feed-1", "name": "One", "itunes_id": None},
            {"uuid": "feed-2", "name": "Two", "itunes_id": None},
        ])

        await store.remove_subscription("feed-1")

        assert [s["uuid"] for s in _read(store)["subscriptions"]] == ["feed-2"]

    async def test_removing_one_that_is_not_there_rewrites_nothing(self, store):
        """`_mutate` only writes when `apply` reports a change; without that,
        every stray DELETE is a full rewrite of `podcast_data.json`."""
        _write(store, subscriptions=[{"uuid": "feed-1", "name": "One",
                                      "itunes_id": None}])
        before = store._data_file.read_text()

        await store.remove_subscription("feed-9")

        assert store._data_file.read_text() == before

    async def test_the_removal_is_announced_so_the_ui_updates(self, store):
        """`SubscriptionsView.vue` reacts to the WS event, not to the response;
        without the broadcast the card stays on screen until a manual reload."""
        events = []
        store._state_machine = type("SM", (), {
            "broadcast": staticmethod(lambda e: _record(events, e))
        })()
        _write(store, subscriptions=[{"uuid": "feed-1", "name": "One",
                                     "itunes_id": None}])

        await store.remove_subscription("feed-1")

        assert [type(e).__name__ for e in events] == ["PodcastFavoriteRemoved"]
        assert events[0].uuid == "feed-1"

    async def test_a_removal_that_changed_nothing_is_not_announced(self, store):
        events = []
        store._state_machine = type("SM", (), {
            "broadcast": staticmethod(lambda e: _record(events, e))
        })()
        _write(store, subscriptions=[])

        await store.remove_subscription("feed-9")

        assert events == []

    async def test_a_store_with_no_state_machine_still_removes(self, store):
        """The data service is constructed without a state machine in the route
        tests and on a dev host; the broadcast has to be optional or every
        write raises."""
        _write(store, subscriptions=[{"uuid": "feed-1", "name": "One",
                                      "itunes_id": None}])

        await store.remove_subscription("feed-1")

        assert _read(store)["subscriptions"] == []


async def _record(events, event):
    events.append(event)


class TestSubscriptionLookups:
    async def test_the_uuids_of_the_stored_subscriptions_are_listed(self, store):
        _write(store, subscriptions=[
            {"uuid": "feed-1", "itunes_id": None},
            {"uuid": "feed-2", "itunes_id": "42"},
        ])

        assert await store.get_subscription_uuids() == ["feed-1", "feed-2"]

    async def test_a_subscribed_podcast_is_reported_subscribed(self, store):
        _write(store, subscriptions=[{"uuid": "feed-1", "itunes_id": None}])

        assert await store.is_subscribed("feed-1") is True

    async def test_a_podcast_that_is_not_stored_is_not_subscribed(self, store):
        """`PodcastDetails.vue` draws subscribe vs unsubscribe from this one
        boolean."""
        _write(store, subscriptions=[{"uuid": "feed-1", "itunes_id": None}])

        assert await store.is_subscribed("feed-9") is False


class TestThePodcastSettings:
    async def test_a_stored_setting_is_read_back(self, store):
        _write(store, settings={"playback_speed": 1.5})

        assert await store.get_setting("playback_speed") == 1.5

    async def test_a_setting_that_is_not_stored_falls_back(self, store):
        """`_do_start` reads the speed with a 1.0 default; a fresh install has
        no row and must boot at normal speed, not at None."""
        _write(store, settings={})

        assert await store.get_setting("playback_speed", 1.0) == 1.0

    async def test_writing_a_setting_persists_it(self, store):
        _write(store, settings={"playback_speed": 1.0})

        await store.set_setting("playback_speed", 2.0)

        assert _read(store)["settings"]["playback_speed"] == 2.0

    async def test_a_partial_update_leaves_the_other_settings_alone(self, store):
        _write(store, settings={"playback_speed": 1.0, "other": "kept"})

        await store.update_podcast_settings({"playback_speed": 1.25})

        assert _read(store)["settings"] == {"playback_speed": 1.25, "other": "kept"}

    async def test_a_key_the_defaults_do_not_declare_is_dropped_in_silence(
        self, store
    ):
        """Measured, and left as it is. `update_podcast_settings` only writes
        keys already present, so a setting the default structure does not
        declare is discarded — and the call still answers `True`, so the caller
        cannot tell.

        Latent on this appliance: `playback_speed` is the only setting anyone
        writes and `_get_default_structure` declares it, so the one live caller
        always finds its key. The value of pinning it is that the next setting
        added here has to be added to the defaults too, or it will look saved
        and not be. Same family as B1-9 (`_modify_config_content` rewriting
        only keys already in `[stream]`) and the `hardware.json` accessors."""
        _write(store, settings={"playback_speed": 1.0})

        assert await store.set_setting("brand_new_setting", 42) is True
        assert "brand_new_setting" not in _read(store)["settings"]
