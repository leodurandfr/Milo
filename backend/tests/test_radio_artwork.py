"""Tests for RadioArtworkResolver (WI-8): iTunes Search cover art.

The HTTP boundary is thin; the pure selection helpers (plausibility gate,
artwork pick, upscale, query cleanup) are tested directly, and resolve()'s
caching contract is tested with a stubbed _lookup so no network is touched.
"""
import pytest

from backend.sources.radio.artwork import RadioArtworkResolver


def _song(artist, track, art="https://x/100x100bb.jpg"):
    return {"artistName": artist, "trackName": track, "artworkUrl100": art}


class TestPickArtwork:
    """Plausibility gate + first-plausible selection."""

    def test_exact_match_is_picked_and_upscaled(self):
        data = {"results": [_song("Pete Candoli", "Blues in the Night")]}
        url = RadioArtworkResolver._pick_artwork(data, "Pete Candoli", "Blues in the Night")
        assert url == "https://x/600x600bb.jpg"

    def test_extra_credits_still_match(self):
        # iTunes often returns "A & B feat. C" for a "A" query.
        data = {"results": [_song("Elisabet Raspall & Chris Cheek", "Contradiccions")]}
        url = RadioArtworkResolver._pick_artwork(data, "Elisabet Raspall", "Contradiccions")
        assert url is not None

    def test_wrong_artist_is_rejected(self):
        # "Nature Boy / Buddy Greco" must NOT match "Oh Boy / Buddy Holly".
        data = {"results": [_song("Buddy Holly", "Oh Boy")]}
        assert RadioArtworkResolver._pick_artwork(data, "Buddy Greco", "Nature Boy") is None

    def test_wrong_title_is_rejected(self):
        data = {"results": [_song("Miles Davis", "So What")]}
        assert RadioArtworkResolver._pick_artwork(data, "Miles Davis", "Blue in Green") is None

    def test_skips_implausible_then_takes_plausible(self):
        data = {"results": [
            _song("Wrong Artist", "Wrong Song"),
            _song("Miles Davis", "So What", art="https://y/100x100bb.jpg"),
        ]}
        url = RadioArtworkResolver._pick_artwork(data, "Miles Davis", "So What")
        assert url == "https://y/600x600bb.jpg"

    def test_no_results(self):
        assert RadioArtworkResolver._pick_artwork({"results": []}, "A", "B") is None
        assert RadioArtworkResolver._pick_artwork({}, "A", "B") is None

    def test_empty_artist_query_matches_on_title_only(self):
        # Title-only in-band ("Morning Jazz" with no artist split).
        data = {"results": [_song("Some Artist", "Morning Jazz")]}
        assert RadioArtworkResolver._pick_artwork(data, "", "Morning Jazz") is not None


class TestUpscale:
    def test_upscales_bb_variant(self):
        assert RadioArtworkResolver._upscale("http://x/a/100x100bb.jpg").endswith("600x600bb.jpg")

    def test_upscales_plain_variant(self):
        assert "600x600" in RadioArtworkResolver._upscale("http://x/a/100x100.jpg")


class TestQueryCleanup:
    def test_strips_parentheticals_for_query(self):
        assert RadioArtworkResolver._clean_query_field("So What (Vinyl)") == "So What"
        assert RadioArtworkResolver._clean_query_field(
            "Entre Nous (with Jill Corey & The Chorus)"
        ) == "Entre Nous"

    def test_cache_key_is_normalized(self):
        assert (
            RadioArtworkResolver._cache_key(" Miles Davis ", "So What")
            == RadioArtworkResolver._cache_key("miles davis", "so what")
        )


class TestResolveCaching:
    @pytest.mark.asyncio
    async def test_hits_lookup_once_per_key(self, monkeypatch):
        resolver = RadioArtworkResolver()
        calls = []

        async def fake_lookup(artist, title):
            calls.append((artist, title))
            return "https://caa/front.jpg"

        monkeypatch.setattr(resolver, "_lookup", fake_lookup)

        first = await resolver.resolve("Miles Davis", "So What")
        second = await resolver.resolve("miles davis", "so what")  # same key
        assert first == second == "https://caa/front.jpg"
        assert len(calls) == 1  # second served from cache

    @pytest.mark.asyncio
    async def test_misses_are_cached(self, monkeypatch):
        resolver = RadioArtworkResolver()
        calls = []

        async def fake_lookup(artist, title):
            calls.append(1)
            return None

        monkeypatch.setattr(resolver, "_lookup", fake_lookup)

        assert await resolver.resolve("X", "Y") is None
        assert await resolver.resolve("X", "Y") is None
        assert len(calls) == 1  # a miss is not re-queried

    @pytest.mark.asyncio
    async def test_empty_title_short_circuits(self, monkeypatch):
        resolver = RadioArtworkResolver()

        async def fail_lookup(artist, title):  # pragma: no cover
            raise AssertionError("should not be called")

        monkeypatch.setattr(resolver, "_lookup", fail_lookup)
        assert await resolver.resolve("Artist", "   ") is None
