"""Tests for the shared ArtworkResolver: iTunes Search cover art.

Used by radio (in-band ICY, artist/title only) and Bluetooth (AVRCP, which also
knows the album). The HTTP boundary is thin; the pure selection helpers
(plausibility gate, artwork pick, upscale, query cleanup) are tested directly,
and the ordering + caching contracts are tested with the network stubbed out.
"""
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
