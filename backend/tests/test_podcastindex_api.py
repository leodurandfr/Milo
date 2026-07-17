# backend/tests/test_podcastindex_api.py
"""
Unit tests for PodcastIndexAPI (sources/podcast/podcastindex_api.py).

Payloads mirror the Podcast Index OpenAPI schemas
(https://github.com/Podcastindex-org/docs-api — feed_search / item_podcast /
item_podcast_byid), including the shape quirks the normalization must absorb:
- episode `explicit` is 0/1 while feed `explicit` is a boolean
- /episodes/byfeedid items omit `feedTitle` (only /episodes/byid has it)
- `categories` is a {id: name} object, not a list
- IDs are ints on the wire but opaque strings in Milō's normalized schema
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.sources.podcast.podcastindex_api import (
    PodcastIndexAPI,
    map_milo_language_to_itunes_country,
)


# Realistic /search/byterm feed (subset of documented fields Milō maps)
SAMPLE_FEED = {
    "id": 920666,
    "podcastGuid": "9b024349-ccf0-5f69-a609-6b82873eab3c",
    "title": "Radiolab",
    "url": "http://feeds.wnyc.org/radiolab",
    "link": "https://www.wnycstudios.org/shows/radiolab",
    "description": "<p>Radiolab is on a curiosity bender.</p>",
    "author": "WNYC Studios",
    "ownerName": "WNYC",
    "image": "https://media.wnyc.org/i/1400/1400/l/80/1/Radiolab.png",
    "artwork": "https://media.wnyc.org/artwork/Radiolab-600.png",
    "lastUpdateTime": 1613394044,
    "newestItemPubdate": 1613300000,
    "itunesId": 152249110,
    "language": "en",
    "explicit": False,
    "episodeCount": 530,
    "categories": {"113": "Science", "77": "Society", "78": "Culture"},
}

# Realistic /episodes/byid episode (has feedTitle)
SAMPLE_EPISODE_BYID = {
    "id": 16795089,
    "title": "The Vanishing of Harry Pace",
    "link": "https://www.wnycstudios.org/story/vanishing-harry-pace",
    "description": "Episode <b>one</b> of a series &amp; more.",
    "guid": "prx_96_a92dc325",
    "datePublished": 1623717600,
    "enclosureUrl": "https://dts.podtrac.com/redirect.mp3/episode1.mp3",
    "enclosureType": "audio/mpeg",
    "enclosureLength": 55099977,
    "duration": 3266,
    "explicit": 1,
    "episode": 1,
    "episodeType": "full",
    "season": 2,
    "image": "https://media.wnyc.org/i/1400/1400/l/80/episode1.png",
    "feedItunesId": 152249110,
    "feedImage": "https://media.wnyc.org/i/1400/1400/l/80/1/Radiolab.png",
    "feedId": 920666,
    "feedTitle": "Radiolab",
    "feedLanguage": "en",
}

# Realistic /episodes/byfeedid item — NO feedTitle, minimal optionals
SAMPLE_EPISODE_BYFEEDID = {
    "id": 16795090,
    "title": "Mixtape",
    "link": "",
    "description": "",
    "guid": "prx_96_b0000001",
    "datePublished": 1623900000,
    "enclosureUrl": "https://dts.podtrac.com/redirect.mp3/episode2.mp3",
    "enclosureType": "audio/mpeg",
    "enclosureLength": 44099977,
    "duration": None,
    "explicit": 0,
    "episode": None,
    "episodeType": None,
    "season": None,
    "image": "",
    "feedItunesId": 152249110,
    "feedImage": "https://media.wnyc.org/i/1400/1400/l/80/1/Radiolab.png",
    "feedId": 920666,
    "feedLanguage": "en",
}


@pytest.fixture
def api():
    return PodcastIndexAPI(api_key="test-key", api_secret="test-secret")


class TestNormalizePodcastSeries:
    def test_maps_pi_fields_to_milo_keys(self, api):
        out = api._normalize_podcast_series(SAMPLE_FEED)

        assert out["uuid"] == "920666"  # feedId stringified (opaque for routes/frontend)
        assert out["itunes_id"] == 152249110
        assert out["name"] == "Radiolab"
        assert out["description"] == "Radiolab is on a curiosity bender."  # HTML stripped
        assert out["image_url"] == "https://media.wnyc.org/artwork/Radiolab-600.png"
        assert out["publisher"] == "WNYC Studios"
        assert out["author"] == "WNYC Studios"
        assert out["total_episodes"] == 530
        assert out["genres"] == ["Science", "Society", "Culture"]
        assert out["language"] == "en"
        assert out["is_explicit"] is False
        assert out["children_hash"] == "1613394044"  # lastUpdateTime as new-episodes token
        assert out["website_url"] == "https://www.wnycstudios.org/shows/radiolab"
        assert out["rss_url"] == "http://feeds.wnyc.org/radiolab"

    def test_image_falls_back_to_feed_image_without_artwork(self, api):
        feed = {**SAMPLE_FEED, "artwork": ""}
        out = api._normalize_podcast_series(feed)
        assert out["image_url"] == SAMPLE_FEED["image"]

    def test_children_hash_falls_back_to_newest_item_pubdate(self, api):
        feed = {**SAMPLE_FEED, "lastUpdateTime": None}
        out = api._normalize_podcast_series(feed)
        assert out["children_hash"] == "1613300000"

    def test_publisher_falls_back_to_owner_name(self, api):
        feed = {**SAMPLE_FEED, "author": ""}
        out = api._normalize_podcast_series(feed)
        assert out["publisher"] == "WNYC"

    def test_null_categories_yield_empty_genres(self, api):
        feed = {**SAMPLE_FEED, "categories": None}
        out = api._normalize_podcast_series(feed)
        assert out["genres"] == []


class TestNormalizeEpisode:
    def test_maps_pi_fields_to_milo_keys(self, api):
        out = api._normalize_episode(SAMPLE_EPISODE_BYID)

        assert out["uuid"] == "16795089"  # episode id stringified
        assert out["guid"] == "prx_96_a92dc325"
        assert out["name"] == "The Vanishing of Harry Pace"
        assert out["description"] == "Episode one of a series & more."  # tags + entities
        assert out["date_published"] == 1623717600
        assert out["duration"] == 3266
        assert out["audio_url"] == "https://dts.podtrac.com/redirect.mp3/episode1.mp3"
        assert out["image_url"] == "https://media.wnyc.org/i/1400/1400/l/80/episode1.png"
        assert out["episode_type"] == "full"
        assert out["season_number"] == 2
        assert out["episode_number"] == 1
        assert out["is_explicit"] is True  # 0/1 int coerced to bool
        assert out["file_length"] == 55099977
        assert out["file_type"] == "audio/mpeg"
        assert out["podcast"] == {
            "uuid": "920666",
            "name": "Radiolab",
            "image_url": "https://media.wnyc.org/i/1400/1400/l/80/1/Radiolab.png",
        }

    def test_byfeedid_item_uses_caller_fallbacks(self, api):
        """/episodes/byfeedid items have no feedTitle: podcast context comes
        from the caller (series fetch or subscription metadata)."""
        out = api._normalize_episode(
            SAMPLE_EPISODE_BYFEEDID,
            podcast_name="Radiolab",
            podcast_image="https://fallback.png",
            podcast_uuid="920666",
        )

        assert out["podcast"]["name"] == "Radiolab"
        assert out["podcast"]["uuid"] == "920666"
        # feedImage still wins over the caller fallback for the podcast block
        assert out["podcast"]["image_url"] == SAMPLE_EPISODE_BYFEEDID["feedImage"]
        # Episode has no own image: falls back to feedImage
        assert out["image_url"] == SAMPLE_EPISODE_BYFEEDID["feedImage"]
        assert out["duration"] == 0  # null duration coerced
        assert out["is_explicit"] is False
        assert out["episode_type"] == "full"  # null episodeType defaulted

    def test_duration_over_24h_treated_as_milliseconds(self, api):
        episode = {**SAMPLE_EPISODE_BYID, "duration": 3_266_000}
        out = api._normalize_episode(episode)
        assert out["duration"] == 3266


class TestSearchPodcasts:
    @pytest.mark.asyncio
    async def test_fetches_once_and_paginates_client_side(self, api):
        """PI has no page/offset: one max=100 fetch (cached), sliced locally."""
        feeds = [{**SAMPLE_FEED, "id": i} for i in range(60)]
        envelope = {"status": "true", "feeds": feeds, "count": 60}

        with patch.object(api, "_make_request", new=AsyncMock(return_value=envelope)) as mock:
            page1 = await api.search_podcasts("radiolab", page=1, limit=25)
            page3 = await api.search_podcasts("radiolab", page=3, limit=25)

        mock.assert_awaited_once_with(
            "/search/byterm", {"q": "radiolab", "max": api.SEARCH_FETCH_MAX}
        )
        assert len(page1["podcasts"]) == 25
        assert page1["podcasts"][0]["uuid"] == "0"
        assert len(page3["podcasts"]) == 10  # 60 feeds -> 25+25+10
        assert page1["pagination"]["podcasts"] == {"total": 60, "pages": 3}

    @pytest.mark.asyncio
    async def test_network_error_sentinel_propagates(self, api):
        with patch.object(
            api, "_make_request", new=AsyncMock(return_value={"_network_error": True})
        ):
            result = await api.search_podcasts("radiolab")

        assert result["network_error"] is True
        assert result["podcasts"] == []


class TestGetPodcastSeries:
    @pytest.mark.asyncio
    async def test_combines_feed_and_episodes(self, api):
        items = [
            {**SAMPLE_EPISODE_BYFEEDID, "id": 100 + i, "datePublished": 1000 - i}
            for i in range(3)
        ]

        async def fake_request(path, params=None):
            if path == "/podcasts/byfeedid":
                return {"status": "true", "feed": SAMPLE_FEED}
            return {"status": "true", "items": items, "count": len(items)}

        with patch.object(api, "_make_request", new=AsyncMock(side_effect=fake_request)):
            series = await api.get_podcast_series("920666", episodes_page=1, episodes_limit=2)

        assert series["uuid"] == "920666"
        assert len(series["episodes"]) == 2
        assert series["episodes"][0]["uuid"] == "100"
        # Episodes inherit the podcast context (byfeedid items lack feedTitle)
        assert series["episodes"][0]["podcast"]["name"] == "Radiolab"
        # total_episodes is the fetched (browsable) count, not the feed's
        # episodeCount metadata — so the frontend "load more" cutoff can never
        # over-promise episodes beyond what was actually fetched (3 here).
        assert series["total_episodes"] == 3

    @pytest.mark.asyncio
    async def test_oldest_sort_reverses_items(self, api):
        items = [{**SAMPLE_EPISODE_BYFEEDID, "id": 100 + i} for i in range(3)]

        async def fake_request(path, params=None):
            if path == "/podcasts/byfeedid":
                return {"status": "true", "feed": SAMPLE_FEED}
            return {"status": "true", "items": items}

        with patch.object(api, "_make_request", new=AsyncMock(side_effect=fake_request)):
            series = await api.get_podcast_series("920666", sort_order="OLDEST")

        assert [e["uuid"] for e in series["episodes"]] == ["102", "101", "100"]

    @pytest.mark.asyncio
    async def test_missing_feed_returns_none(self, api):
        async def fake_request(path, params=None):
            if path == "/podcasts/byfeedid":
                return {"status": "true", "feed": []}  # PI: empty array when unknown
            return {"status": "true", "items": []}

        with patch.object(api, "_make_request", new=AsyncMock(side_effect=fake_request)):
            assert await api.get_podcast_series("999999999") is None


class TestGetEpisode:
    @pytest.mark.asyncio
    async def test_returns_normalized_episode(self, api):
        envelope = {"status": "true", "episode": SAMPLE_EPISODE_BYID}
        with patch.object(api, "_make_request", new=AsyncMock(return_value=envelope)):
            episode = await api.get_episode("16795089")

        assert episode["uuid"] == "16795089"
        assert episode["audio_url"] == SAMPLE_EPISODE_BYID["enclosureUrl"]

    @pytest.mark.asyncio
    async def test_caller_mutation_does_not_poison_cache(self, api):
        """Routes enrich the returned dict in place (playback_progress); the
        cached object must stay clean so a later cache hit isn't leaked stale
        per-request state."""
        envelope = {"status": "true", "episode": SAMPLE_EPISODE_BYID}
        with patch.object(api, "_make_request", new=AsyncMock(return_value=envelope)) as mock:
            first = await api.get_episode("16795089")
            first["playback_progress"] = {"position": 42}  # route-style enrichment
            second = await api.get_episode("16795089")      # served from cache

        mock.assert_awaited_once()  # second call hit the cache
        assert "playback_progress" not in second
        assert first is not second


class TestLookupByItunesId:
    @pytest.mark.asyncio
    async def test_resolves_feed_id_as_string(self, api):
        envelope = {"status": "true", "feed": SAMPLE_FEED}
        with patch.object(api, "_make_request", new=AsyncMock(return_value=envelope)) as mock:
            assert await api.lookup_by_itunes_id("152249110") == "920666"

        mock.assert_awaited_once_with("/podcasts/byitunesid", {"id": 152249110})

    @pytest.mark.asyncio
    async def test_invalid_itunes_id_returns_none_without_request(self, api):
        with patch.object(api, "_make_request", new=AsyncMock()) as mock:
            assert await api.lookup_by_itunes_id("not-a-number") is None

        mock.assert_not_awaited()


class TestGetLatestEpisodes:
    @pytest.mark.asyncio
    async def test_merges_sorts_and_applies_feed_meta(self, api):
        async def fake_request(path, params=None):
            fid = params["id"]
            return {
                "status": "true",
                "items": [
                    {**SAMPLE_EPISODE_BYFEEDID, "id": int(fid) * 10,
                     "feedId": int(fid), "datePublished": int(fid) * 1000}
                ],
            }

        feed_meta = {
            "1": {"name": "Feed One", "image_url": "https://one.png"},
            "2": {"name": "Feed Two", "image_url": "https://two.png"},
        }
        with patch.object(api, "_make_request", new=AsyncMock(side_effect=fake_request)):
            result = await api.get_latest_episodes(["1", "2"], feed_meta=feed_meta)

        assert [e["uuid"] for e in result["results"]] == ["20", "10"]  # newest first
        assert result["results"][1]["podcast"]["name"] == "Feed One"

    @pytest.mark.asyncio
    async def test_empty_feed_list_short_circuits(self, api):
        assert await api.get_latest_episodes([]) == {"results": [], "total": 0}


def test_map_milo_language_to_itunes_country():
    assert map_milo_language_to_itunes_country("french") == "fr"
    assert map_milo_language_to_itunes_country("unknown") == "us"
