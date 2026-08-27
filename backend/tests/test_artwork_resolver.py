"""Tests for the shared ArtworkResolver: iTunes Search cover art.

Used by radio (in-band ICY, artist/title only) and Bluetooth (AVRCP, which also
knows the album). The HTTP boundary is thin; the pure selection helpers
(plausibility gate, artwork pick, upscale, query cleanup) are tested directly,
and the ordering + caching contracts are tested with the network stubbed out.
"""
import asyncio
import logging
import time

import aiohttp
import pytest

from backend.shared.artwork_resolver import ArtworkResolver


def _song(artist, track, art="https://x/100x100bb.jpg"):
    return {"artistName": artist, "trackName": track, "artworkUrl100": art}


def _album(artist, collection, art="https://x/100x100bb.jpg"):
    return {"artistName": artist, "collectionName": collection, "artworkUrl100": art}


class TestPickArtwork:
    """Plausibility gate + first-plausible selection."""

    def test_exact_match_is_picked_and_upscaled(self):
        data = {"results": [_song("Pete Candoli", "Blues in the Night")]}
        url = ArtworkResolver._pick_artwork(
            data, "Pete Candoli", "Blues in the Night", "trackName"
        )
        assert url == "https://x/600x600bb.jpg"

    def test_extra_credits_still_match(self):
        # iTunes often returns "A & B feat. C" for a "A" query.
        data = {"results": [_song("Elisabet Raspall & Chris Cheek", "Contradiccions")]}
        url = ArtworkResolver._pick_artwork(
            data, "Elisabet Raspall", "Contradiccions", "trackName"
        )
        assert url is not None

    def test_wrong_artist_is_rejected(self):
        # "Nature Boy / Buddy Greco" must NOT match "Oh Boy / Buddy Holly".
        data = {"results": [_song("Buddy Holly", "Oh Boy")]}
        assert ArtworkResolver._pick_artwork(
            data, "Buddy Greco", "Nature Boy", "trackName"
        ) is None

    def test_wrong_title_is_rejected(self):
        data = {"results": [_song("Miles Davis", "So What")]}
        assert ArtworkResolver._pick_artwork(
            data, "Miles Davis", "Blue in Green", "trackName"
        ) is None

    def test_skips_implausible_then_takes_plausible(self):
        data = {"results": [
            _song("Wrong Artist", "Wrong Song"),
            _song("Miles Davis", "So What", art="https://y/100x100bb.jpg"),
        ]}
        url = ArtworkResolver._pick_artwork(data, "Miles Davis", "So What", "trackName")
        assert url == "https://y/600x600bb.jpg"

    def test_no_results(self):
        assert ArtworkResolver._pick_artwork({"results": []}, "A", "B", "trackName") is None
        assert ArtworkResolver._pick_artwork({}, "A", "B", "trackName") is None

    def test_empty_artist_query_matches_on_title_only(self):
        # Title-only in-band ("Morning Jazz" with no artist split).
        data = {"results": [_song("Some Artist", "Morning Jazz")]}
        assert ArtworkResolver._pick_artwork(
            data, "", "Morning Jazz", "trackName"
        ) is not None

    def test_the_album_search_is_gated_on_its_own_field(self):
        """An album result carries `collectionName`, not `trackName`. Reading
        the wrong key would make every candidate implausible and silently
        return no cover at all."""
        data = {"results": [_album("Thee Sacred Souls", "Got a Story to Tell")]}

        assert ArtworkResolver._pick_artwork(
            data, "Thee Sacred Souls", "Got a Story to Tell", "collectionName"
        ) is not None
        assert ArtworkResolver._pick_artwork(
            data, "Thee Sacred Souls", "Got a Story to Tell", "trackName"
        ) is None


class TestUpscale:
    def test_upscales_bb_variant(self):
        assert ArtworkResolver._upscale("http://x/a/100x100bb.jpg").endswith("600x600bb.jpg")

    def test_upscales_plain_variant(self):
        assert "600x600" in ArtworkResolver._upscale("http://x/a/100x100.jpg")


class TestQueryCleanup:
    def test_strips_parentheticals_for_query(self):
        assert ArtworkResolver._clean_query_field("So What (Vinyl)") == "So What"
        assert ArtworkResolver._clean_query_field(
            "Entre Nous (with Jill Corey & The Chorus)"
        ) == "Entre Nous"

    def test_cache_key_is_normalized(self):
        assert (
            ArtworkResolver._cache_key(" Miles Davis ", "So What", "")
            == ArtworkResolver._cache_key("miles davis", "so what", "")
        )

    def test_the_album_is_part_of_the_cache_key(self):
        """Two records can share a track title. Keying on artist+title alone
        would serve the first one's cover for the second."""
        assert (
            ArtworkResolver._cache_key("Miles Davis", "So What", "Kind of Blue")
            != ArtworkResolver._cache_key("Miles Davis", "So What", "Live in Europe")
        )


class TestSearchOrder:
    """Which query is tried, and in what order."""

    @staticmethod
    def _record(resolver, monkeypatch, album_hit=None, song_hit=None):
        tried = []

        async def fake_search(entity, q_artist, q_name):
            tried.append((entity[0], q_name))
            return album_hit if entity[0] == "album" else song_hit

        monkeypatch.setattr(resolver, "_search", fake_search)
        return tried

    @pytest.mark.asyncio
    async def test_a_known_album_is_searched_first_and_alone(self, monkeypatch):
        """An album has one cover; the same track can sit on a dozen
        compilations with a dozen different ones. Searching the album keeps
        every track of one record on one cover, and costs one query, not two."""
        resolver = ArtworkResolver()
        tried = self._record(monkeypatch=monkeypatch, resolver=resolver,
                             album_hit="https://x/600x600bb.jpg")

        url = await resolver._lookup("Thee Sacred Souls", "Live For You", "Got a Story to Tell")

        assert url == "https://x/600x600bb.jpg"
        assert tried == [("album", "Got a Story to Tell")]

    @pytest.mark.asyncio
    async def test_the_track_search_is_the_fallback(self, monkeypatch):
        """Singles and unreleased tracks have no album entry to match."""
        resolver = ArtworkResolver()
        tried = self._record(monkeypatch=monkeypatch, resolver=resolver,
                             album_hit=None, song_hit="https://y/600x600bb.jpg")

        url = await resolver._lookup("Artist", "A Track", "An Album")

        assert url == "https://y/600x600bb.jpg"
        assert [entity for entity, _ in tried] == ["album", "song"]

    @pytest.mark.asyncio
    async def test_no_album_goes_straight_to_the_track_search(self, monkeypatch):
        """Radio's in-band ICY carries no album. Its behaviour must be exactly
        what it was before this resolver was shared with Bluetooth."""
        resolver = ArtworkResolver()
        tried = self._record(monkeypatch=monkeypatch, resolver=resolver,
                             song_hit="https://y/600x600bb.jpg")

        url = await resolver._lookup("Miles Davis", "So What", "")

        assert url == "https://y/600x600bb.jpg"
        assert tried == [("song", "So What")]


class TestResolveCaching:
    @pytest.mark.asyncio
    async def test_hits_lookup_once_per_key(self, monkeypatch):
        resolver = ArtworkResolver()
        calls = []

        async def fake_lookup(artist, title, album):
            calls.append((artist, title, album))
            return "https://caa/front.jpg"

        monkeypatch.setattr(resolver, "_lookup", fake_lookup)

        first = await resolver.resolve("Miles Davis", "So What")
        second = await resolver.resolve("miles davis", "so what")  # same key
        assert first == second == "https://caa/front.jpg"
        assert len(calls) == 1  # second served from cache

    @pytest.mark.asyncio
    async def test_misses_are_cached(self, monkeypatch):
        resolver = ArtworkResolver()
        calls = []

        async def fake_lookup(artist, title, album):
            calls.append(1)
            return None

        monkeypatch.setattr(resolver, "_lookup", fake_lookup)

        assert await resolver.resolve("X", "Y") is None
        assert await resolver.resolve("X", "Y") is None
        assert len(calls) == 1  # a miss is not re-queried

    @pytest.mark.asyncio
    async def test_empty_query_short_circuits(self, monkeypatch):
        resolver = ArtworkResolver()

        async def fail_lookup(artist, title, album):  # pragma: no cover
            raise AssertionError("should not be called")

        monkeypatch.setattr(resolver, "_lookup", fail_lookup)
        assert await resolver.resolve("Artist", "   ") is None


class _Resp:
    """Minimal aiohttp response stand-in (async context manager)."""

    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, **kwargs):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _SessionRecorder:
    """Stands in for `aiohttp.ClientSession` and for its constructor.

    `_search` builds its own session per query, so covering it means replacing
    the class, not an injected object. The captured kwargs are asserted because
    the timeout is contractual: this call sits on the metadata path of a track
    that is already playing.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, params=None):
        self.requests.append((url, dict(params or {})))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Resp(*reply)


@pytest.fixture
def no_throttle(monkeypatch):
    """Collapse the 0.5 s politeness spacing; never assert on it."""
    monkeypatch.setattr("backend.shared.artwork_resolver._ITUNES_MIN_INTERVAL", 0)


def _itunes(results):
    return {"resultCount": len(results), "results": results}


class TestSearchRequest:
    """`_search` — the one iTunes query, nineteen lines that had never run.

    This is the only cover source Bluetooth has: AVRCP carries no image over the
    link, so what this returns *is* the artwork on the card. Radio's ICY feed is
    the same shape without an album.
    """

    @pytest.mark.asyncio
    async def test_the_query_pairs_the_two_fields_and_names_its_entity(
        self, monkeypatch, no_throttle
    ):
        """iTunes names the matched field differently per entity, so the entity
        and the result key travel together as one pair. Sent apart, an album
        search would be scored against `trackName`, which an album result does
        not carry, and every album query would be rejected as implausible.
        """
        from backend.shared.artwork_resolver import _ALBUM_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, _itunes([]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        await resolver._search(_ALBUM_ENTITY, "Miles Davis", "Kind of Blue")

        url, params = session.requests[0]
        assert url == "https://itunes.apple.com/search"
        assert params == {
            "term": "Miles Davis Kind of Blue", "entity": "album", "limit": "5",
        }

    @pytest.mark.asyncio
    async def test_a_query_with_no_artist_still_carries_the_name(
        self, monkeypatch, no_throttle
    ):
        """A radio station announcing only a title is common. A term with a
        leading space matches worse, and an empty one matches everything."""
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, _itunes([]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        await resolver._search(_SONG_ENTITY, "", "So What")

        assert session.requests[0][1]["term"] == "So What"

    @pytest.mark.asyncio
    async def test_a_plausible_hit_is_returned_upscaled(self, monkeypatch, no_throttle):
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, _itunes([{
            "artistName": "Miles Davis",
            "trackName": "So What",
            "artworkUrl100": "https://is1/100x100bb.jpg",
        }]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        url = await resolver._search(_SONG_ENTITY, "Miles Davis", "So What")

        assert url == "https://is1/600x600bb.jpg"

    @pytest.mark.asyncio
    async def test_an_album_hit_is_scored_against_the_album_field(
        self, monkeypatch, no_throttle
    ):
        """The other half of the entity pair, and the half a params-only
        assertion cannot see. An album result carries `collectionName` and no
        `trackName`; scored against the wrong key it reads as an empty field,
        the plausibility gate rejects it, and the album search — the first and
        better one — never returns anything for anybody.
        """
        from backend.shared.artwork_resolver import _ALBUM_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, _itunes([{
            "artistName": "Miles Davis",
            "collectionName": "Kind of Blue",
            "artworkUrl100": "https://is1/100x100bb.jpg",
        }]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        url = await resolver._search(_ALBUM_ENTITY, "Miles Davis", "Kind of Blue")

        assert url == "https://is1/600x600bb.jpg"

    @pytest.mark.asyncio
    async def test_an_implausible_hit_is_refused(self, monkeypatch, no_throttle):
        """"A wrong cover is worse than none" — the module's own words.

        iTunes always answers *something* for a fuzzy term, so without the gate
        every unrecognised radio track would get a confidently wrong sleeve.
        """
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, _itunes([{
            "artistName": "Buddy Holly",
            "trackName": "Peggy Sue",
            "artworkUrl100": "https://is1/100x100bb.jpg",
        }]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        assert await resolver._search(_SONG_ENTITY, "Buddy Greco", "The Lady Is a Tramp") is None

    @pytest.mark.asyncio
    async def test_a_non_200_answers_nothing(self, monkeypatch, no_throttle, caplog):
        """iTunes rate-limits. Raising would take down the metadata publish of
        the track that is playing; this is a decoration, not the audio."""
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(403, None)])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        with caplog.at_level(logging.INFO):
            assert await resolver._search(_SONG_ENTITY, "A", "B") is None

        assert "iTunes search HTTP 403" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [
        aiohttp.ClientError("connection reset"),
        asyncio.TimeoutError(),
    ])
    async def test_an_unreachable_itunes_answers_nothing(
        self, monkeypatch, no_throttle, failure, caplog
    ):
        """The unit runs with no internet often enough — Bluetooth and a local
        library need none. A raise here would surface as a metadata error on a
        track that is playing perfectly.
        """
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([failure])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        with caplog.at_level(logging.INFO):
            assert await resolver._search(_SONG_ENTITY, "A", "B") is None

        assert "Artwork lookup failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_body_that_is_not_json_answers_nothing(
        self, monkeypatch, no_throttle
    ):
        """iTunes serves `text/javascript`, so the parse is deliberately lenient
        (`content_type=None`). A captive portal answering HTML is what this
        catches — `ValueError` is in the except tuple for exactly that.
        """
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, ValueError("not json"))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        assert await resolver._search(_SONG_ENTITY, "A", "B") is None

    @pytest.mark.asyncio
    async def test_the_search_is_bounded_in_time(self, monkeypatch, no_throttle):
        """It runs inline on the metadata publish path. Unbounded, a hung iTunes
        holds the resolver lock and every later track waits behind it."""
        from backend.shared.artwork_resolver import _SONG_ENTITY

        resolver = ArtworkResolver()
        session = _SessionRecorder([(200, _itunes([]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        await resolver._search(_SONG_ENTITY, "A", "B")

        assert session.kwargs["timeout"].total == 8

    @pytest.mark.asyncio
    async def test_the_calls_are_spaced_by_the_politeness_interval(self, monkeypatch):
        """An album search that misses is followed immediately by a track search;
        a station changing tracks fast queues more. The spacing is what keeps
        that from bursting a public API the appliance has no account with."""
        from backend.shared.artwork_resolver import _SONG_ENTITY

        monkeypatch.setattr(
            "backend.shared.artwork_resolver._ITUNES_MIN_INTERVAL", 5.0
        )
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        monkeypatch.setattr("backend.shared.artwork_resolver.asyncio.sleep", _sleep)
        resolver = ArtworkResolver()
        resolver._last_call = time.monotonic()
        session = _SessionRecorder([(200, _itunes([]))])
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )

        await resolver._search(_SONG_ENTITY, "A", "B")

        assert len(slept) == 1 and 0 < slept[0] <= 5.0

    @pytest.mark.asyncio
    async def test_a_first_call_after_a_long_idle_is_not_delayed(self, monkeypatch):
        """The control. A throttle that always slept would add half a second to
        the cover of every track a listener actually waits for."""
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        monkeypatch.setattr("backend.shared.artwork_resolver.asyncio.sleep", _sleep)
        resolver = ArtworkResolver()
        resolver._last_call = time.monotonic() - 3600

        await resolver._throttle()

        assert slept == []


class TestCacheEviction:
    """The bounded LRU — this resolver serves a radio station that never stops."""

    @pytest.mark.asyncio
    async def test_the_cache_is_capped_and_evicts_the_least_recently_used(
        self, monkeypatch
    ):
        """Unbounded, a station running for weeks accumulates one entry per
        track, forever, in a process that is never restarted."""
        monkeypatch.setattr("backend.shared.artwork_resolver._CACHE_MAX", 3)
        resolver = ArtworkResolver()

        for i in range(3):
            resolver._store(f"k{i}", f"url{i}")
        resolver._store("k0", "url0")  # re-touch the oldest
        resolver._store("k3", "url3")

        assert set(resolver._cache) == {"k0", "k2", "k3"}

    @pytest.mark.asyncio
    async def test_a_cache_hit_is_promoted_so_a_played_track_survives(
        self, monkeypatch
    ):
        """`resolve` moves the key to the end on a hit. Without it the LRU is a
        FIFO, and the cover of the track on screen is the next one evicted."""
        monkeypatch.setattr("backend.shared.artwork_resolver._CACHE_MAX", 2)
        resolver = ArtworkResolver()

        async def _lookup(artist, title, album):
            return f"url:{title}"

        resolver._lookup = _lookup

        await resolver.resolve("A", "first")
        await resolver.resolve("A", "second")
        await resolver.resolve("A", "first")   # hit -> promoted
        await resolver.resolve("A", "third")

        assert resolver._cache_key("A", "second", "") not in resolver._cache
        assert resolver._cache_key("A", "first", "") in resolver._cache

    @pytest.mark.asyncio
    async def test_a_title_that_cleans_down_to_nothing_never_searches(
        self, monkeypatch
    ):
        """A title that is entirely a parenthetical — "(Live)" alone — cleans to
        an empty string. Searched, the term is the artist alone and iTunes
        answers that artist's most popular record, which is a wrong cover."""
        resolver = ArtworkResolver()
        tried = []

        async def _search(entity, q_artist, q_name):
            tried.append(entity[0])
            return None

        resolver._search = _search

        assert await resolver._lookup("Miles Davis", "(Live)", "") is None
        assert tried == []

    @pytest.mark.asyncio
    async def test_two_tracks_of_one_album_query_itunes_once(self):
        """The re-check INSIDE the lock, which the one before it cannot cover.

        Both callers pass the pre-lock read while the cache is still empty —
        exactly what the lock is there to serialise — and the second then waits.
        Without the second look it re-queries iTunes for a cover the first has
        already resolved, which is how two tracks of the same album, published
        milliseconds apart by a gapless queue, cost two searches instead of one.

        Two real tasks: two sequential calls are answered by the pre-lock read
        and never reach the second check.
        """
        resolver = ArtworkResolver()
        calls = {"n": 0}
        released = asyncio.Event()

        async def _lookup(artist, title, album):
            calls["n"] += 1
            await released.wait()
            return "https://is1/600x600bb.jpg"

        resolver._lookup = _lookup

        first = asyncio.create_task(resolver.resolve("Miles Davis", "So What", "Kind of Blue"))
        for _ in range(20):
            await asyncio.sleep(0)
            if calls["n"] == 1:
                break
        assert calls["n"] == 1, "the first caller never reached the lookup"

        second = asyncio.create_task(resolver.resolve("Miles Davis", "So What", "Kind of Blue"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert not second.done(), "the second caller did not queue on the lock"

        released.set()
        results = await asyncio.gather(first, second)

        assert results == ["https://is1/600x600bb.jpg"] * 2
        assert calls["n"] == 1
