# backend/tests/test_podcast_routes.py
"""`sources/podcast/routes.py` — the podcast browser's whole REST surface.

The file had no test file at all and sat at 29.5%: fifteen routes, and not one
of their bodies had ever run. Every one of them has a consumer in
`frontend/src/components/podcasts/` or `frontend/src/stores/podcastStore.js`
(none is in the Milo-Mac manifest), so what these routes decide is what the
podcast screens show.

Three things the handlers do that the services underneath cannot:

* **the language → iTunes-country translation.** Discovery and search each read
  `settings['language']` and turn it into a country code before calling the
  catalogue. Lose that and a French unit browses the US charts, with no error
  anywhere.
* **the subscribed flag.** The catalogue does not know what this appliance is
  subscribed to; the route joins the two. It is what draws the filled/hollow
  subscribe button on every card, and the join is not the same in all three
  places — measured below rather than assumed.
* **the composite `/play`.** CLAUDE.md sanctions exactly two multi-command
  routes and this is one of them: play, then resume-seek, in one request.

The doubles here answer with the shapes `podcastindex_api.py` really returns
(`{"results": [...]}` for the iTunes charts, `{"podcasts": [...],
"pagination": ...}` for search, `"api_error": True` only on an upstream
failure) — a double that can represent a state the producer cannot is the 17th
blind spot.
"""
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.podcast.routes import setup_podcast_routes
from backend.sources.podcast.source import VALID_PLAYBACK_SPEEDS


@pytest.fixture
def settings(monkeypatch):
    """Stand in for the global `get_service("settings_service")` the discovery
    and search handlers import inside their own body."""
    svc = Mock()
    svc.load_settings = AsyncMock(return_value={"language": "french"})
    monkeypatch.setattr("backend.dependencies.get_service", lambda name: svc)
    return svc


@pytest.fixture
def source():
    src = Mock()
    src.podcast_api = Mock()
    src.podcast_data = Mock()
    src.podcast_api.get_itunes_top_podcasts = AsyncMock(return_value={"results": []})
    src.podcast_api.get_itunes_top_podcasts_by_genre = AsyncMock(
        return_value={"results": []}
    )
    src.podcast_api.search_podcasts = AsyncMock(
        return_value={"podcasts": [], "pagination": {"podcasts": {"total": 0, "pages": 0}}}
    )
    src.podcast_api.lookup_by_itunes_id = AsyncMock(return_value=None)
    src.podcast_api.get_podcast_series = AsyncMock(return_value=None)
    src.podcast_api.get_episode = AsyncMock(return_value=None)
    src.podcast_api.get_latest_episodes = AsyncMock(return_value={"results": []})
    src.podcast_data.get_subscriptions = AsyncMock(return_value=[])
    src.podcast_data.get_subscription_uuids = AsyncMock(return_value=[])
    src.podcast_data.is_subscribed = AsyncMock(return_value=False)
    src.podcast_data.get_playback_progress = AsyncMock(return_value=None)
    src.podcast_data.add_subscription = AsyncMock(return_value=True)
    src.podcast_data.remove_subscription = AsyncMock(return_value=True)
    src.podcast_data.get_in_progress_episodes = AsyncMock(return_value=[])
    src.podcast_data.mark_episode_completed = AsyncMock(return_value=True)
    src.podcast_data.get_podcast_settings = AsyncMock(return_value={})
    src.command = AsyncMock(return_value={"success": True})
    return src


@pytest.fixture
def client(source):
    app = FastAPI()
    app.include_router(setup_podcast_routes(lambda: source), prefix="/api")
    return TestClient(app)


class TestTopCharts:
    """`GET /discover/top-charts` — the podcast home screen (`HomeView.vue`)."""

    def test_the_units_language_decides_which_country_chart_is_fetched(
        self, client, source, settings
    ):
        """`french` must reach the catalogue as `fr`. The iTunes RSS charts are
        per-store: asking for the wrong one returns a full, plausible, entirely
        foreign chart — there is nothing to notice."""
        settings.load_settings.return_value = {"language": "french"}

        resp = client.get("/api/podcast/discover/top-charts")

        assert resp.status_code == 200
        assert source.podcast_api.get_itunes_top_podcasts.await_args.kwargs[
            "country_code"
        ] == "fr"

    def test_an_unmapped_language_falls_back_to_the_us_store(
        self, client, source, settings
    ):
        """The fallback is what keeps discovery working on a language Milō
        translates but Apple has no store for."""
        settings.load_settings.return_value = {"language": "esperanto"}

        client.get("/api/podcast/discover/top-charts")

        assert source.podcast_api.get_itunes_top_podcasts.await_args.kwargs[
            "country_code"
        ] == "us"

    def test_the_chart_is_returned_and_carries_the_resolved_store(
        self, client, source, settings
    ):
        """Non-triviality: this route can answer with entries. `country` and
        `language` are what `HomeView.vue` shows next to the chart so the owner
        can tell which store they are looking at."""
        source.podcast_api.get_itunes_top_podcasts.return_value = {
            "results": [{"uuid": "feed-1", "name": "One"}]
        }

        body = client.get("/api/podcast/discover/top-charts").json()

        assert [p["name"] for p in body["results"]] == ["One"]
        assert body["country"] == "fr"
        assert body["language"] == "french"

    def test_a_subscribed_chart_entry_comes_back_flagged(
        self, client, source, settings
    ):
        """The subscribe button on a chart card is drawn from this flag alone.
        Without the join every chart entry offers "subscribe" for a podcast the
        owner already follows."""
        source.podcast_api.get_itunes_top_podcasts.return_value = {
            "results": [{"uuid": "feed-1"}, {"uuid": "feed-2"}]
        }
        source.podcast_data.get_subscription_uuids.return_value = ["feed-2"]

        results = client.get("/api/podcast/discover/top-charts").json()["results"]

        assert [p["is_subscribed"] for p in results] == [False, True]

    def test_an_unresolved_itunes_entry_is_flagged_unsubscribed_not_crashed(
        self, client, source, settings
    ):
        """Chart entries carry `uuid=None` until `lookup/itunes` resolves them.
        `get_subscription_uuids` drops falsy uuids, so None can never match a
        stored one — the entry stays unsubscribed-looking, which is the
        documented behaviour and not an accident of the data."""
        source.podcast_api.get_itunes_top_podcasts.return_value = {
            "results": [{"uuid": None, "itunes_id": "42"}]
        }
        source.podcast_data.get_subscription_uuids.return_value = ["feed-2"]

        results = client.get("/api/podcast/discover/top-charts").json()["results"]

        assert results[0]["is_subscribed"] is False

    def test_the_limit_reaches_the_catalogue(self, client, source, settings):
        """`HomeView.vue` asks for a screenful; a limit that stops travelling
        makes every home screen fetch the default 25."""
        client.get("/api/podcast/discover/top-charts?limit=60")

        assert source.podcast_api.get_itunes_top_podcasts.await_args.kwargs["limit"] == 60

    def test_a_catalogue_failure_is_a_500_and_is_logged(
        self, client, source, settings, caplog
    ):
        """`api_error_handler` is the only thing between the iTunes client and
        the browser here."""
        source.podcast_api.get_itunes_top_podcasts.side_effect = RuntimeError("apple down")

        with caplog.at_level("ERROR", logger="backend.sources.podcast.routes"):
            resp = client.get("/api/podcast/discover/top-charts")

        assert resp.status_code == 500
        assert "apple down" in caplog.text


class TestByGenre:
    """`GET /discover/by-genre` — `GenreView.vue`."""

    def test_the_genre_and_the_resolved_store_both_reach_the_catalogue(
        self, client, source, settings
    ):
        client.get("/api/podcast/discover/by-genre?genre=PODCASTSERIES_TECHNOLOGY")

        kwargs = source.podcast_api.get_itunes_top_podcasts_by_genre.await_args.kwargs
        assert kwargs["genre"] == "PODCASTSERIES_TECHNOLOGY"
        assert kwargs["country_code"] == "fr"

    def test_the_podcasts_are_returned_under_their_own_key(
        self, client, source, settings
    ):
        """Non-triviality, and the key rename is the route's own work: the
        catalogue answers `results`, `GenreView.vue` reads `podcasts`."""
        source.podcast_api.get_itunes_top_podcasts_by_genre.return_value = {
            "results": [{"uuid": "feed-1", "name": "One"}]
        }

        body = client.get("/api/podcast/discover/by-genre?genre=G").json()

        assert [p["name"] for p in body["podcasts"]] == ["One"]
        assert body["country"] == "fr"

    def test_an_upstream_failure_is_carried_out_as_api_error(
        self, client, source, settings
    ):
        """An empty list and a catalogue that answered 503 look identical to the
        screen unless this flag survives — that distinction is the whole reason
        `UPSTREAM_ERROR_KEY` exists in the client below."""
        source.podcast_api.get_itunes_top_podcasts_by_genre.return_value = {
            "results": [],
            "api_error": True,
        }

        assert client.get("/api/podcast/discover/by-genre?genre=G").json()["api_error"] is True

    def test_a_healthy_empty_genre_carries_no_api_error_key(
        self, client, source, settings
    ):
        """The other half of the pair: a genre Apple has nothing for must not
        raise the "catalogue unavailable" state."""
        source.podcast_api.get_itunes_top_podcasts_by_genre.return_value = {"results": []}

        assert "api_error" not in client.get("/api/podcast/discover/by-genre?genre=G").json()

    def test_genre_results_carry_no_subscribed_flag_at_all(
        self, client, source, settings
    ):
        """Measured asymmetry, pinned so a reader does not have to rediscover
        it: `/top-charts` and `/search` join against the subscriptions,
        `/by-genre` does not — it never reads them. A podcast the owner follows
        therefore shows a hollow subscribe button in the genre list and a filled
        one everywhere else.

        Kept as a constat rather than fixed: the enrichment on `/top-charts`
        cannot match an unresolved iTunes entry either (uuid is None until the
        podcast is opened), so adding the same join here would flag almost
        nothing while adding a subscriptions read to every genre page."""
        source.podcast_api.get_itunes_top_podcasts_by_genre.return_value = {
            "results": [{"uuid": "feed-2"}]
        }
        source.podcast_data.get_subscription_uuids.return_value = ["feed-2"]

        body = client.get("/api/podcast/discover/by-genre?genre=G").json()

        assert "is_subscribed" not in body["podcasts"][0]
        source.podcast_data.get_subscription_uuids.assert_not_awaited()
        source.podcast_data.get_subscriptions.assert_not_awaited()

    def test_the_genre_is_required(self, client, settings):
        assert client.get("/api/podcast/discover/by-genre").status_code == 422


class TestItunesLookup:
    """`GET /lookup/itunes/{id}` — how a chart entry becomes openable.

    Chart and search hits carry only an Apple id; `PodcastSource.vue` calls this
    to turn it into the Podcast Index feedId every other route takes.
    """

    def test_a_resolved_feed_comes_back_with_both_ids(self, client, source):
        source.podcast_api.lookup_by_itunes_id.return_value = "feed-9"

        body = client.get("/api/podcast/lookup/itunes/12345").json()

        assert body == {"uuid": "feed-9", "itunes_id": "12345"}

    def test_a_podcast_podcast_index_does_not_know_is_a_404(self, client, source, caplog):
        """A 404 here is what stops `PodcastSource.vue` opening a details page
        for a feed no other route can serve."""
        source.podcast_api.lookup_by_itunes_id.return_value = None

        with caplog.at_level("ERROR", logger="backend.sources.podcast.routes"):
            resp = client.get("/api/podcast/lookup/itunes/12345")

        assert resp.status_code == 404
        assert "12345" in caplog.text


class TestSearch:
    """`GET /search` — `podcastStore.searchPodcasts`."""

    def test_an_empty_term_answers_without_touching_the_catalogue(self, client, source):
        """The search box calls this per keystroke. Reaching Apple on an empty
        term is a request per cleared field, and the shape returned has to be
        the one the store already renders — hence a literal empty envelope
        rather than a bare `{}`."""
        resp = client.get("/api/podcast/search?term=")

        assert resp.json() == {
            "podcasts": [],
            "pagination": {"podcasts": {"total": 0, "pages": 0}},
        }
        source.podcast_api.search_podcasts.assert_not_awaited()

    def test_the_term_page_limit_and_store_all_reach_the_catalogue(
        self, client, source, settings
    ):
        client.get("/api/podcast/search?term=underscore&page=3&limit=10")

        kwargs = source.podcast_api.search_podcasts.await_args.kwargs
        assert kwargs["term"] == "underscore"
        assert kwargs["page"] == 3
        assert kwargs["limit"] == 10
        assert kwargs["country"] == "fr"

    def test_a_hit_already_subscribed_by_feed_id_is_flagged(
        self, client, source, settings
    ):
        source.podcast_api.search_podcasts.return_value = {
            "podcasts": [{"uuid": "feed-1"}, {"uuid": "feed-2"}],
            "pagination": {"podcasts": {"total": 2, "pages": 1}},
        }
        # `add_subscription` always writes `itunes_id`, None included, so a
        # stored record missing the key is a state the producer cannot make.
        source.podcast_data.get_subscriptions.return_value = [
            {"uuid": "feed-2", "itunes_id": None}
        ]

        podcasts = client.get("/api/podcast/search?term=x").json()["podcasts"]

        assert [p["is_subscribed"] for p in podcasts] == [False, True]

    def test_a_hit_with_no_feed_id_is_flagged_by_its_apple_id(
        self, client, source, settings
    ):
        """This is the half `/top-charts` does not have. Search hits are
        iTunes-sourced and carry `uuid=None` until opened, so the feedId join
        can never fire for them; the Apple id captured at subscribe time is the
        only thing that can. Drop it and every already-followed podcast shows
        "subscribe" in search results."""
        source.podcast_api.search_podcasts.return_value = {
            "podcasts": [{"uuid": None, "itunes_id": "111"},
                         {"uuid": None, "itunes_id": "222"}],
            "pagination": {"podcasts": {"total": 2, "pages": 1}},
        }
        source.podcast_data.get_subscriptions.return_value = [
            {"uuid": "feed-2", "itunes_id": "222"}
        ]

        podcasts = client.get("/api/podcast/search?term=x").json()["podcasts"]

        assert [p["is_subscribed"] for p in podcasts] == [False, True]

    def test_a_subscription_without_an_apple_id_matches_nothing_by_apple_id(
        self, client, source, settings
    ):
        """Both lookup sets drop their falsy keys, so a subscription stored
        before `itunes_id` existed cannot make a `None`-id hit match."""
        source.podcast_api.search_podcasts.return_value = {
            "podcasts": [{"uuid": None, "itunes_id": None}],
            "pagination": {"podcasts": {"total": 1, "pages": 1}},
        }
        source.podcast_data.get_subscriptions.return_value = [
            {"uuid": "feed-2", "itunes_id": None}
        ]

        podcasts = client.get("/api/podcast/search?term=x").json()["podcasts"]

        assert podcasts[0]["is_subscribed"] is False

    def test_the_subscriptions_are_read_once_for_both_lookup_sets(
        self, client, source, settings
    ):
        """Both sets are derived from a single fetch — the comment above them
        says so, and a second read here is a second load of
        `podcast_data.json` on every keystroke."""
        client.get("/api/podcast/search?term=x")

        assert source.podcast_data.get_subscriptions.await_count == 1

    def test_the_pagination_block_is_carried_through(self, client, source, settings):
        source.podcast_api.search_podcasts.return_value = {
            "podcasts": [],
            "pagination": {"podcasts": {"total": 57, "pages": 3}},
        }

        body = client.get("/api/podcast/search?term=x").json()

        assert body["pagination"]["podcasts"] == {"total": 57, "pages": 3}

    def test_a_catalogue_answer_missing_its_pagination_still_renders(
        self, client, source, settings
    ):
        """`podcastStore` reads `pagination.podcasts.total` unconditionally to
        decide whether to offer a next page."""
        source.podcast_api.search_podcasts.return_value = {"podcasts": []}

        body = client.get("/api/podcast/search?term=x").json()

        assert body["pagination"]["podcasts"] == {"total": 0, "pages": 0}

    def test_an_upstream_failure_is_carried_out_as_api_error(
        self, client, source, settings
    ):
        source.podcast_api.search_podcasts.return_value = {
            "podcasts": [],
            "pagination": {"podcasts": {"total": 0, "pages": 0}},
            "api_error": True,
        }

        assert client.get("/api/podcast/search?term=x").json()["api_error"] is True

    def test_a_page_past_the_allowed_window_is_refused_before_the_catalogue(
        self, client, source, settings
    ):
        assert client.get("/api/podcast/search?term=x&page=99").status_code == 422
        source.podcast_api.search_podcasts.assert_not_awaited()


class TestSeries:
    """`GET /series/{uuid}` — `PodcastDetails.vue`."""

    def test_the_paging_and_sort_order_reach_the_catalogue(self, client, source):
        source.podcast_api.get_podcast_series.return_value = {"episodes": []}

        client.get("/api/podcast/series/feed-1?page=2&limit=10&sort_order=OLDEST")

        kwargs = source.podcast_api.get_podcast_series.await_args.kwargs
        assert kwargs["feed_id"] == "feed-1"
        assert kwargs["episodes_page"] == 2
        assert kwargs["episodes_limit"] == 10
        assert kwargs["sort_order"] == "OLDEST"

    def test_a_series_the_catalogue_does_not_have_is_a_404(self, client, source, caplog):
        source.podcast_api.get_podcast_series.return_value = None

        with caplog.at_level("ERROR", logger="backend.sources.podcast.routes"):
            resp = client.get("/api/podcast/series/nope")

        assert resp.status_code == 404
        assert "nope" in caplog.text

    def test_the_details_page_learns_whether_this_podcast_is_followed(
        self, client, source
    ):
        """The subscribe/unsubscribe button on the details page is this flag."""
        source.podcast_api.get_podcast_series.return_value = {"episodes": []}
        source.podcast_data.is_subscribed.return_value = True

        body = client.get("/api/podcast/series/feed-1").json()

        assert body["is_subscribed"] is True
        assert source.podcast_data.is_subscribed.await_args.args[0] == "feed-1"

    def test_each_episode_carries_the_progress_stored_for_it(self, client, source):
        """The resume bar under an episode row. The progress store is keyed by
        episode uuid, so a lookup on the wrong key silently shows every episode
        as unplayed."""
        source.podcast_api.get_podcast_series.return_value = {
            "episodes": [{"uuid": "ep-1"}, {"uuid": "ep-2"}]
        }
        stored = {"ep-2": {"position": 120, "duration": 3600}}
        source.podcast_data.get_playback_progress.side_effect = (
            lambda uuid: stored.get(uuid)
        )

        episodes = client.get("/api/podcast/series/feed-1").json()["episodes"]

        assert "playback_progress" not in episodes[0]
        assert episodes[1]["playback_progress"]["position"] == 120

    def test_an_episode_with_no_progress_gets_no_empty_block(self, client, source):
        """`PodcastDetails.vue` renders the resume bar on the key's presence."""
        source.podcast_api.get_podcast_series.return_value = {"episodes": [{"uuid": "ep-1"}]}
        source.podcast_data.get_playback_progress.return_value = None

        episodes = client.get("/api/podcast/series/feed-1").json()["episodes"]

        assert "playback_progress" not in episodes[0]

    def test_a_series_with_no_episode_list_still_answers(self, client, source):
        source.podcast_api.get_podcast_series.return_value = {"name": "One"}

        assert client.get("/api/podcast/series/feed-1").status_code == 200


class TestEpisode:
    """`GET /episode/{uuid}` — `EpisodeDetails.vue`."""

    def test_an_episode_carries_its_stored_progress(self, client, source):
        source.podcast_api.get_episode.return_value = {"uuid": "ep-1", "name": "Ep"}
        source.podcast_data.get_playback_progress.return_value = {"position": 42}

        body = client.get("/api/podcast/episode/ep-1").json()

        assert body["playback_progress"] == {"position": 42}
        assert source.podcast_data.get_playback_progress.await_args.args[0] == "ep-1"

    def test_an_unplayed_episode_gets_no_progress_block(self, client, source):
        source.podcast_api.get_episode.return_value = {"uuid": "ep-1"}
        source.podcast_data.get_playback_progress.return_value = None

        assert "playback_progress" not in client.get("/api/podcast/episode/ep-1").json()

    def test_an_episode_the_catalogue_does_not_have_is_a_404(self, client, source, caplog):
        source.podcast_api.get_episode.return_value = None

        with caplog.at_level("ERROR", logger="backend.sources.podcast.routes"):
            resp = client.get("/api/podcast/episode/nope")

        assert resp.status_code == 404
        assert "nope" in caplog.text


class TestPlay:
    """`POST /play` — one of the two composites CLAUDE.md sanctions.

    The rule it earns its exemption under is "composes more than one command in
    one request": play, then the resume seek, because two round-trips make the
    episode audibly start at 0:00 first.
    """

    def test_the_episode_reaches_the_source_as_a_play_command(self, client, source):
        resp = client.post("/api/podcast/play", json={"episode_uuid": "ep-1"})

        assert resp.status_code == 200
        assert source.command.await_args_list[0].args == (
            "play_episode", {"episode_uuid": "ep-1"}
        )

    def test_a_request_without_a_position_sends_exactly_one_command(
        self, client, source
    ):
        """This is what the Vue store actually sends — it never carries a
        position. Counting rather than asserting presence: a stray second
        command is invisible to a membership check (12th blind spot)."""
        client.post("/api/podcast/play", json={"episode_uuid": "ep-1"})

        assert source.command.await_count == 1

    def test_a_carried_position_is_seeked_after_the_play(self, client, source):
        """Order is the point: seeking before the stream is loaded seeks
        nothing. Asserting the sequence, not the membership."""
        client.post("/api/podcast/play", json={"episode_uuid": "ep-1", "position": 90})

        assert [c.args[0] for c in source.command.await_args_list] == [
            "play_episode", "seek"
        ]
        assert source.command.await_args_list[1].args[1] == {"position": 90}

    def test_a_zero_position_is_not_a_resume(self, client, source):
        """An episode at 0 is a fresh start; a seek to 0 on a stream that has
        not buffered yet is a round-trip that can only go wrong."""
        client.post("/api/podcast/play", json={"episode_uuid": "ep-1", "position": 0})

        assert source.command.await_count == 1

    def test_a_play_that_failed_is_never_followed_by_a_seek(self, client, source):
        """`run_source_command` raises on failure, so the seek is unreachable —
        pinned because the alternative (an error dict) would have seeked into a
        stream that never loaded."""
        source.command.return_value = {"success": False, "error": "Episode not found"}

        resp = client.post(
            "/api/podcast/play", json={"episode_uuid": "ep-1", "position": 90}
        )

        assert resp.status_code == 400
        assert source.command.await_count == 1

    def test_a_refused_seek_fails_the_request_although_playback_started(
        self, client, source
    ):
        """Measured behaviour, worth knowing before changing it: the composite
        has no partial-success answer. The episode is playing from 0:00 and the
        store raises "Failed to play episode"."""
        source.command.side_effect = [
            {"success": True},
            {"success": False, "error": "seek refused"},
        ]

        resp = client.post(
            "/api/podcast/play", json={"episode_uuid": "ep-1", "position": 90}
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "seek refused"

    def test_the_episode_uuid_is_required(self, client, source):
        assert client.post("/api/podcast/play", json={}).status_code == 422
        source.command.assert_not_awaited()


class TestPlaybackSpeeds:
    def test_the_canonical_speed_list_is_served_from_the_source_module(self, client):
        """The frontend is forbidden from hardcoding backend-derived values, so
        `podcastStore` fetches this list. Derived from the constant rather than
        restated, or the test only pins a literal someone typed twice."""
        body = client.get("/api/podcast/playback-speeds").json()

        assert body["speeds"] == VALID_PLAYBACK_SPEEDS
        assert body["status"] == "success"


class TestSubscriptions:
    """`/subscriptions` — `SubscriptionsView.vue` and `PodcastDetails.vue`."""

    def test_the_list_is_returned_with_its_own_count(self, client, source):
        source.podcast_data.get_subscriptions.return_value = [{"uuid": "a"}, {"uuid": "b"}]

        body = client.get("/api/podcast/subscriptions").json()

        assert [s["uuid"] for s in body["subscriptions"]] == ["a", "b"]
        assert body["total"] == 2

    def test_every_field_of_a_subscribe_reaches_the_store(self, client, source):
        """`itunes_id` is the one that matters later: it is what lets a search
        hit — which carries no feedId — come back flagged as subscribed."""
        client.post("/api/podcast/subscriptions", json={
            "uuid": "feed-1",
            "name": "One",
            "image_url": "http://img/1.jpg",
            "children_hash": "h1",
            "itunes_id": 42,
        })

        assert source.podcast_data.add_subscription.await_args.kwargs == {
            "podcast_uuid": "feed-1",
            "name": "One",
            "image_url": "http://img/1.jpg",
            "children_hash": "h1",
            "itunes_id": 42,
        }

    def test_a_subscribe_without_an_apple_id_is_accepted(self, client, source):
        """Podcasts opened from a feedId have no Apple id to carry."""
        resp = client.post("/api/podcast/subscriptions", json={
            "uuid": "feed-1", "name": "One", "image_url": "http://img/1.jpg",
        })

        assert resp.status_code == 200
        assert source.podcast_data.add_subscription.await_args.kwargs["itunes_id"] is None

    def test_a_subscribe_missing_its_name_is_refused_before_the_store(
        self, client, source
    ):
        """The stored name is what `SubscriptionsView.vue` lists; a nameless
        row is a card with nothing on it."""
        resp = client.post("/api/podcast/subscriptions", json={"uuid": "feed-1"})

        assert resp.status_code == 422
        source.podcast_data.add_subscription.assert_not_awaited()

    def test_unsubscribing_names_the_podcast_being_dropped(self, client, source):
        resp = client.delete("/api/podcast/subscriptions/feed-1")

        assert resp.json() == {"status": "success"}
        assert source.podcast_data.remove_subscription.await_args.args[0] == "feed-1"

    def test_a_store_failure_while_unsubscribing_is_a_500(self, client, source, caplog):
        source.podcast_data.remove_subscription.side_effect = OSError("disk full")

        with caplog.at_level("ERROR", logger="backend.sources.podcast.routes"):
            resp = client.delete("/api/podcast/subscriptions/feed-1")

        assert resp.status_code == 500
        assert "disk full" in caplog.text


class TestLatestEpisodes:
    """`GET /subscriptions/latest-episodes` — the "new episodes" list."""

    def test_no_subscriptions_answers_without_calling_the_catalogue(
        self, client, source
    ):
        """There is no batch endpoint: the client fans out one HTTP call per
        feed. Calling it with an empty feed list is a fan-out over nothing, and
        the guard is what makes a fresh install's home screen instant."""
        source.podcast_data.get_subscriptions.return_value = []

        body = client.get("/api/podcast/subscriptions/latest-episodes").json()

        assert body == {"results": [], "total": 0}
        source.podcast_api.get_latest_episodes.assert_not_awaited()

    def test_the_stored_name_and_image_travel_with_each_feed_id(self, client, source):
        """`/episodes/byfeedid` items may omit `feedTitle`, so the stored
        metadata is the only fallback for the podcast name and artwork under an
        episode row."""
        source.podcast_data.get_subscriptions.return_value = [
            {"uuid": "feed-1", "name": "One", "image_url": "http://img/1.jpg"},
        ]

        client.get("/api/podcast/subscriptions/latest-episodes")

        kwargs = source.podcast_api.get_latest_episodes.await_args.kwargs
        assert kwargs["feed_ids"] == ["feed-1"]
        assert kwargs["feed_meta"] == {
            "feed-1": {"name": "One", "image_url": "http://img/1.jpg"}
        }

    def test_a_subscription_with_no_feed_id_is_not_fanned_out_to(self, client, source):
        """A subscription stored from a chart entry before it was resolved has
        `uuid=None`; asking the catalogue for feed `None` is one wasted HTTP
        call per page load, forever."""
        source.podcast_data.get_subscriptions.return_value = [
            {"uuid": None, "name": "Unresolved"},
            {"uuid": "feed-1", "name": "One"},
        ]

        client.get("/api/podcast/subscriptions/latest-episodes")

        assert source.podcast_api.get_latest_episodes.await_args.kwargs["feed_ids"] == [
            "feed-1"
        ]

    def test_the_paging_reaches_the_catalogue(self, client, source):
        source.podcast_data.get_subscriptions.return_value = [{"uuid": "feed-1"}]

        client.get("/api/podcast/subscriptions/latest-episodes?page=2&limit=20")

        kwargs = source.podcast_api.get_latest_episodes.await_args.kwargs
        assert kwargs["page"] == 2
        assert kwargs["limit"] == 20

    def test_each_episode_carries_the_progress_stored_for_it(self, client, source):
        source.podcast_data.get_subscriptions.return_value = [{"uuid": "feed-1"}]
        source.podcast_api.get_latest_episodes.return_value = {
            "results": [{"uuid": "ep-1"}, {"uuid": "ep-2"}]
        }
        stored = {"ep-1": {"position": 30, "duration": 600}}
        source.podcast_data.get_playback_progress.side_effect = (
            lambda uuid: stored.get(uuid)
        )

        results = client.get("/api/podcast/subscriptions/latest-episodes").json()["results"]

        assert results[0]["playback_progress"]["position"] == 30
        assert "playback_progress" not in results[1]


class TestQueue:
    """`/queue` — `QueueView.vue`, the "continue listening" list."""

    def test_the_in_progress_episodes_are_returned_with_their_count(
        self, client, source
    ):
        source.podcast_data.get_in_progress_episodes.return_value = [
            {"episode_uuid": "ep-1"}, {"episode_uuid": "ep-2"},
        ]

        body = client.get("/api/podcast/queue").json()

        assert [e["episode_uuid"] for e in body["episodes"]] == ["ep-1", "ep-2"]
        assert body["total"] == 2

    def test_marking_complete_names_the_episode_being_dropped(self, client, source):
        """The swipe-to-finish gesture on a queue row. The uuid is what decides
        which episode leaves the list."""
        resp = client.post("/api/podcast/queue/ep-1/complete")

        assert resp.json() == {"status": "success"}
        assert source.podcast_data.mark_episode_completed.await_args.args[0] == "ep-1"


class TestSettings:
    def test_the_stored_podcast_settings_are_returned_under_settings(
        self, client, source
    ):
        """`podcastStore` reads `settings.playback_speed` to restore the speed
        control's position on load."""
        source.podcast_data.get_podcast_settings.return_value = {"playback_speed": 1.5}

        assert client.get("/api/podcast/settings").json() == {
            "settings": {"playback_speed": 1.5}
        }


class TestTheSourceDependency:
    """Every route hangs off `Depends(get_source)`, so an unconfigured or
    absent source is the one failure they all share."""

    def test_an_absent_source_is_a_503_and_not_a_crash(self):
        app = FastAPI()
        app.include_router(setup_podcast_routes(lambda: None), prefix="/api")

        resp = TestClient(app).get("/api/podcast/subscriptions")

        assert resp.status_code == 503
        assert "Podcast" in resp.json()["detail"]
