"""Tests for the shared ArtworkResolver: cover art from text, via iTunes.

Used by radio (in-band ICY, artist/title only), DLNA (controllers that publish
no albumArtURI) and Bluetooth (AVRCP, which carries no image at all and also
knows the album). It is the *only* cover source those sources have, so what this
returns is what the player paints.

The shape under test is "find the artist, then read their catalogue": `/search`
is asked exactly one question — which artist is this — and everything after is
`/lookup`, which is exact. The HTTP boundary is stood in for by
`_SessionRecorder`; the pure helpers are exercised directly.
"""
import asyncio
import logging
import time

import aiohttp
import pytest

from backend.shared.artwork_resolver import (
    ArtworkResolver,
    _LookupUnavailable,
    _artist_candidates,
    _entries,
    _same_name,
    _tokens,
)


def _song(track, art="https://x/100x100bb.jpg"):
    return {"wrapperType": "track", "trackName": track, "artworkUrl100": art}


def _album(collection, art="https://x/100x100bb.jpg"):
    return {"wrapperType": "collection", "collectionName": collection,
            "artworkUrl100": art}


def _artist_row(name, artist_id):
    return {"wrapperType": "artist", "artistName": name, "artistId": artist_id}


def _itunes(results):
    return {"resultCount": len(results), "results": results}


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

    `_request` builds its own session per call, so covering it means replacing
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


@pytest.fixture
def http(monkeypatch):
    """Install a recorder over aiohttp and hand it back to the test."""
    def install(replies):
        session = _SessionRecorder(replies)
        monkeypatch.setattr(
            "backend.shared.artwork_resolver.aiohttp.ClientSession", session
        )
        return session
    return install


class _Settings:
    """Stands in for SettingsService — the appliance's own declared country."""

    def __init__(self, country):
        self._country = country

    async def get_setting(self, key):
        assert key == "wifi.country", key
        return self._country


# === Naming helpers ==========================================================

class TestArtistCandidates:
    """Which names an artist is looked up under, and in which order."""

    def test_a_plain_name_is_asked_once(self):
        assert _artist_candidates("Nils Frahm") == ["Nils Frahm"]

    def test_the_whole_string_is_tried_before_the_lead_credit(self):
        """A comma is not always a credit separator. `Tyler, The Creator` is one
        artist, and splitting on it resolves a different one — measured, an id
        whose catalogue holds none of the tracks asked for. The whole string
        matching exactly is the only thing that tells the two cases apart.
        """
        assert _artist_candidates("Tyler, The Creator") == [
            "Tyler, The Creator", "Tyler",
        ]

    def test_a_joint_credit_falls_back_to_the_principal(self):
        """And the other case: `Jeune Mort, ISHA` names nobody, so the record is
        found under the lead credit."""
        assert _artist_candidates("Jeune Mort, ISHA") == [
            "Jeune Mort, ISHA", "Jeune Mort",
        ]


class TestAccents:
    """The tokeniser cuts on anything outside [a-z0-9], so an accented word is
    not merely unmatched, it is shredded into pieces that match nothing."""

    def test_an_accented_word_stays_one_token(self):
        assert _tokens("Le réveil") == ["le", "reveil"]
        assert _tokens("Björk") == ["bjork"]
        assert _tokens("Édith Piaf") == ["edith", "piaf"]

    def test_a_sender_that_drops_the_accent_still_matches_the_catalogue(self):
        """Measured on a live AVRCP sender: it published `Roce` for a catalogue
        that spells the artist `Rocé`."""
        assert _same_name("Roce", "Rocé")

    def test_punctuation_is_not_a_difference(self):
        assert _same_name("Untitled (Black Is)", "UNTITLED (Black Is)")

    def test_two_different_names_are_not_the_same_name(self):
        assert not _same_name("Buddy Greco", "Buddy Holly")

    def test_nothing_is_not_a_match(self):
        """`_artist_id` accepts an id on this, so an empty name matching would
        hand a whole catalogue to whoever came back first."""
        assert not _same_name("", "")


# === The gate ================================================================

class TestEntries:
    """What is kept out of an iTunes result set, and what is dropped.

    A catalogue is up to 200 rows of thirty-odd fields and it is held for the
    life of the process, so only the two values that are read survive."""

    def test_a_row_becomes_its_name_and_its_cover(self):
        assert _entries(_itunes([_song("So What")]), "trackName") == [
            ("So What", "https://x/100x100bb.jpg"),
        ]

    def test_the_artist_row_is_not_a_record(self):
        """A lookup answers with the artist first and their catalogue after. It
        names no record in either matched field, and left in it makes the
        catalogue's own length lie about how much came back."""
        data = _itunes([_artist_row("Nils Frahm", 42), _album("Spaces")])
        assert _entries(data, "collectionName") == [("Spaces", "https://x/100x100bb.jpg")]

    def test_the_field_that_is_read_is_the_one_the_pair_names(self):
        """iTunes names the matched field differently for a record and a track.
        Read with the wrong key an album row yields an empty name, and every
        album would be refused."""
        data = _itunes([_album("Kind of Blue")])
        assert _entries(data, "collectionName") == [("Kind of Blue", "https://x/100x100bb.jpg")]
        assert _entries(data, "trackName") == [("", "https://x/100x100bb.jpg")]

    def test_a_row_with_no_cover_keeps_its_place_but_carries_nothing(self):
        data = _itunes([{"wrapperType": "track", "trackName": "So What"}])
        assert _entries(data, "trackName") == [("So What", "")]


class TestPickArtwork:
    """Judging names inside one artist's catalogue.

    The artist is not in question here — the entries came back from a `/lookup`
    on an id whose name matched exactly — so only the record's name is judged.
    """

    def test_an_exact_name_is_picked_and_upscaled(self):
        url = ArtworkResolver._pick_artwork(
            [("So What", "https://x/100x100bb.jpg")], "So What")
        assert url == "https://x/600x600bb.jpg"

    def test_a_featuring_credit_the_catalogue_keeps_still_matches(self):
        """The sender's title is the short one and the catalogue's the long one,
        routinely: `Wild Wild West` against `Wild Wild West (feat. Dru Hill &
        Kool Moe Dee)`, `Un parmi des millions` against `… (feat. Rocé &
        Kohndo)`. Both were live tracks on this unit."""
        entries = [("Wild Wild West (feat. Dru Hill & Kool Moe Dee)", "https://x/100x100bb.jpg")]
        assert ArtworkResolver._pick_artwork(entries, "Wild Wild West")

    def test_a_different_record_is_refused(self):
        """A wrong cover is worse than none, and an artist's catalogue is full
        of other records."""
        assert ArtworkResolver._pick_artwork(
            [("Kind of Blue", "https://x/100x100bb.jpg")], "Bitches Brew"
        ) is None

    def test_the_exact_name_wins_over_a_merely_plausible_one(self):
        """A record and its deluxe reissue both pass the gate; the plain one is
        the answer, wherever it sits in the catalogue."""
        entries = [
            ("Spaces (Deluxe Edition)", "https://deluxe/100x100bb.jpg"),
            ("Spaces", "https://plain/100x100bb.jpg"),
        ]
        assert ArtworkResolver._pick_artwork(entries, "Spaces") == "https://plain/600x600bb.jpg"

    def test_a_plausible_one_is_taken_when_nothing_matches_exactly(self):
        entries = [("Spaces (Deluxe Edition)", "https://x/100x100bb.jpg")]
        assert ArtworkResolver._pick_artwork(entries, "Spaces")

    def test_an_entry_with_no_artwork_is_skipped(self):
        entries = [("So What", ""), ("So What", "https://real/100x100bb.jpg")]
        assert ArtworkResolver._pick_artwork(entries, "So What") == "https://real/600x600bb.jpg"

    def test_an_empty_catalogue_answers_nothing(self):
        assert ArtworkResolver._pick_artwork([], "So What") is None

    def test_an_empty_name_matches_nothing(self):
        """Otherwise the first record in the catalogue would be everyone's
        cover."""
        assert ArtworkResolver._pick_artwork(
            [("So What", "https://x/100x100bb.jpg")], "") is None


class TestUpscale:
    def test_upscales_bb_variant(self):
        assert ArtworkResolver._upscale(
            "http://x/a/100x100bb.jpg").endswith("600x600bb.jpg")

    def test_upscales_plain_variant(self):
        assert "600x600" in ArtworkResolver._upscale("http://x/a/100x100.jpg")


class TestQueryCleanup:
    def test_strips_parentheticals_for_query(self):
        assert ArtworkResolver._clean_query_field("So What (Vinyl)") == "So What"

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


# === Finding the artist ======================================================

class TestTheArtistLookup:
    """The one fuzzy question this module asks — and the only `/search` call.

    Everything downstream trusts the artist completely, so a near-miss here
    would hand a whole catalogue to the wrong person. That is the failure
    `music_library/artist_images.py` exists to undo for artist photos.
    """

    async def test_the_search_asks_for_an_artist_and_nothing_else(
        self, http, no_throttle
    ):
        session = http([(200, _itunes([_artist_row("Nils Frahm", 42)]))])
        resolver = ArtworkResolver()

        assert await resolver._artist_id("Nils Frahm") == 42

        url, params = session.requests[0]
        assert url == "https://itunes.apple.com/search"
        assert params == {"term": "Nils Frahm", "entity": "musicArtist", "limit": "5"}

    async def test_only_an_identical_name_is_accepted(self, http, no_throttle):
        """`/search` ranks fuzzily and answers neighbours: asked for `Tyler` it
        returns artists who merely contain it. Accepting one would look like a
        hit and then fail on every track of the record."""
        http([(200, _itunes([
            _artist_row("Tyler Childers", 1), _artist_row("Tyler, The Creator", 2),
        ]))])
        resolver = ArtworkResolver()

        assert await resolver._artist_id("Tyler, The Creator") == 2

    async def test_accents_are_not_a_difference(self, http, no_throttle):
        http([(200, _itunes([_artist_row("Björk", 7)]))])
        resolver = ArtworkResolver()

        assert await resolver._artist_id("Bjork") == 7

    async def test_the_lead_credit_is_tried_when_the_whole_string_names_nobody(
        self, http, no_throttle
    ):
        """`Jeune Mort, ISHA` is a joint publication and no artist is called
        that; `Jeune Mort` is. Measured on the unit — it is what put a cover on
        `Paire blanche`."""
        session = http([
            (200, _itunes([])),
            (200, _itunes([_artist_row("Jeune Mort", 99)])),
        ])
        resolver = ArtworkResolver()

        assert await resolver._artist_id("Jeune Mort, ISHA") == 99
        assert [p["term"] for _, p in session.requests] == [
            "Jeune Mort, ISHA", "Jeune Mort",
        ]

    async def test_the_whole_string_is_not_split_when_it_matches(
        self, http, no_throttle
    ):
        """The trap the fallback must not spring: `Tyler, The Creator` is one
        name, and one request is all it takes."""
        session = http([(200, _itunes([_artist_row("Tyler, The Creator", 2)]))])
        resolver = ArtworkResolver()

        await resolver._artist_id("Tyler, The Creator")

        assert len(session.requests) == 1

    async def test_an_artist_itunes_does_not_carry_answers_none(
        self, http, no_throttle
    ):
        http([(200, _itunes([])), (200, _itunes([]))])
        resolver = ArtworkResolver()

        assert await resolver._artist_id("Numéro d'écrou, X") is None

    async def test_an_artist_is_asked_about_once(self, http, no_throttle):
        """Including when the answer was "nobody": a station or a record playing
        an artist iTunes does not carry must not re-ask on every track."""
        session = http([(200, _itunes([])), (200, _itunes([]))])
        resolver = ArtworkResolver()

        assert await resolver._artist_id("Jeune Mort, ISHA") is None
        assert await resolver._artist_id("Jeune Mort, ISHA") is None

        assert len(session.requests) == 2, "the miss was not remembered"


# === Reading the catalogue ===================================================

class TestTheCatalogueLookup:
    async def test_the_lookup_asks_for_one_half_of_one_artist(
        self, http, no_throttle
    ):
        from backend.shared.artwork_resolver import _ALBUM_ENTITY

        session = http([(200, _itunes([_album("Spaces")]))])
        resolver = ArtworkResolver()

        await resolver._from_catalogue(42, _ALBUM_ENTITY, "Spaces")

        url, params = session.requests[0]
        assert url == "https://itunes.apple.com/lookup"
        assert params == {"id": "42", "entity": "album", "limit": "200"}

    async def test_a_catalogue_is_read_once_per_artist(self, http, no_throttle):
        """This is what makes the whole shape cheaper than a query per track: a
        record played track by track pays for its artist once."""
        from backend.shared.artwork_resolver import _ALBUM_ENTITY

        session = http([(200, _itunes([_album("Spaces"), _album("Solo")]))])
        resolver = ArtworkResolver()

        assert await resolver._from_catalogue(42, _ALBUM_ENTITY, "Spaces")
        assert await resolver._from_catalogue(42, _ALBUM_ENTITY, "Solo")

        assert len(session.requests) == 1


# === The order of the two halves ============================================

class TestLookupOrder:
    @staticmethod
    def _record(resolver, monkeypatch, artist_id=42, album_hit=None, song_hit=None):
        tried = []

        async def _artist_id_(artist):
            return artist_id

        async def _from_catalogue(aid, entity, name):
            tried.append((entity[0], name))
            return album_hit if entity[0] == "album" else song_hit

        monkeypatch.setattr(resolver, "_artist_id", _artist_id_)
        monkeypatch.setattr(resolver, "_from_catalogue", _from_catalogue)
        return tried

    async def test_a_known_album_is_read_first_and_alone(self, monkeypatch):
        """An album has one cover; the same track can sit on a dozen
        compilations with a dozen different ones. Reading the album keeps every
        track of one record on one cover."""
        resolver = ArtworkResolver()
        tried = self._record(resolver, monkeypatch, album_hit="https://x/600x600bb.jpg")

        url = await resolver._lookup("Nils Frahm", "Says", "Spaces")

        assert url == "https://x/600x600bb.jpg"
        assert tried == [("album", "Spaces")]

    async def test_the_songs_are_the_fallback(self, monkeypatch):
        """Singles and album tracks iTunes files apart have no album entry."""
        resolver = ArtworkResolver()
        tried = self._record(resolver, monkeypatch, song_hit="https://y/600x600bb.jpg")

        url = await resolver._lookup("Hotel Blue", "Homme à rêve", "Medina")

        assert url == "https://y/600x600bb.jpg"
        assert [entity for entity, _ in tried] == ["album", "song"]

    async def test_no_album_goes_straight_to_the_songs(self, monkeypatch):
        """Radio's in-band ICY carries no album."""
        resolver = ArtworkResolver()
        tried = self._record(resolver, monkeypatch, song_hit="https://y/600x600bb.jpg")

        await resolver._lookup("Miles Davis", "So What", "")

        assert tried == [("song", "So What")]

    async def test_an_unknown_artist_reads_no_catalogue(self, monkeypatch):
        """There is nothing to read, and asking anyway would be a lookup on
        `None`."""
        resolver = ArtworkResolver()
        tried = self._record(resolver, monkeypatch, artist_id=None)

        assert await resolver._lookup(
            "Numéro d'écrou", "CRYPTO", "Numéro d'écrou"
        ) is None
        assert tried == []


# === The request itself ======================================================

class TestTheRequest:
    async def test_the_call_is_bounded_in_time(self, http, no_throttle):
        """It runs inline on the metadata publish path. Unbounded, a hung iTunes
        holds the resolver lock and every later track waits behind it."""
        session = http([(200, _itunes([]))])
        resolver = ArtworkResolver()

        await resolver._request("https://itunes.apple.com/search", {"term": "A"})

        assert session.kwargs["timeout"].total == 8

    async def test_a_non_200_is_not_an_answer_about_the_track(
        self, http, no_throttle, caplog
    ):
        """iTunes rate-limits, and a rate limit is not "this track has no cover".

        Answered as one it was cached as one, and a track stayed coverless until
        the backend restarted. `resolve` turns it back into None for its caller
        — nothing escapes this module, because a cover is a decoration and must
        never take down the metadata publish of a track that is playing.
        """
        http([(403, None)])
        resolver = ArtworkResolver()

        with caplog.at_level(logging.INFO):
            with pytest.raises(_LookupUnavailable):
                await resolver._request(
                    "https://itunes.apple.com/search", {"term": "A"}
                )

        assert "HTTP 403" in caplog.text

    @pytest.mark.parametrize("failure", [
        aiohttp.ClientError("connection reset"),
        asyncio.TimeoutError(),
    ])
    async def test_an_unreachable_itunes_is_not_an_answer_either(
        self, http, no_throttle, failure, caplog
    ):
        """The unit runs with no internet often enough — Bluetooth and a local
        library need none. Recording that as "no cover for this track" makes the
        whole session coverless once the link comes back.
        """
        http([failure])
        resolver = ArtworkResolver()

        with caplog.at_level(logging.INFO):
            with pytest.raises(_LookupUnavailable):
                await resolver._request(
                    "https://itunes.apple.com/search", {"term": "A"}
                )

        assert "Artwork lookup failed" in caplog.text

    async def test_a_body_that_is_not_json_is_not_an_answer(self, http, no_throttle):
        """iTunes serves `text/javascript`, so the parse is deliberately lenient
        (`content_type=None`). A captive portal answering HTML is what this
        catches — `ValueError` is in the except tuple for exactly that.
        """
        http([(200, ValueError("not json"))])
        resolver = ArtworkResolver()

        with pytest.raises(_LookupUnavailable):
            await resolver._request("https://itunes.apple.com/search", {"term": "A"})

    async def test_a_body_that_is_not_a_result_set_is_not_an_answer(
        self, http, no_throttle
    ):
        """A proxy answering valid JSON that is not iTunes. Reported as
        unavailable rather than as an empty catalogue, for the same reason a 500
        is — and guarded because `.get` on a list would escape as a 500."""
        http([(200, ["nope"])])
        resolver = ArtworkResolver()

        with pytest.raises(_LookupUnavailable):
            await resolver._request("https://itunes.apple.com/search", {"term": "A"})


class TestTheStorefront:
    """Which iTunes store is read.

    iTunes indexes per store and answers `resultCount: 0` for a record simply
    not distributed in the one it was asked about — indistinguishable from "no
    such artist". Measured on the unit: `Koma — Un parmi des millions` answers 0
    on the API's default store and 1 on FR, and it survives every other fix in
    this module.
    """

    async def test_the_appliances_country_is_the_store_that_is_read(
        self, http, no_throttle
    ):
        session = http([(200, _itunes([]))])
        resolver = ArtworkResolver(_Settings("FR"))

        await resolver._request("https://itunes.apple.com/search", {"term": "Koma"})

        assert session.requests[0][1]["country"] == "FR"

    async def test_no_declared_country_leaves_the_api_its_own_default(
        self, http, no_throttle
    ):
        """`wifi.country` is empty until the owner sets one, and a country
        invented here would be worse than the API's."""
        session = http([(200, _itunes([]))])
        resolver = ArtworkResolver(_Settings(""))

        await resolver._request("https://itunes.apple.com/search", {"term": "A"})

        assert "country" not in session.requests[0][1]


class TestTheThrottle:
    async def test_the_calls_are_spaced_by_the_politeness_interval(
        self, monkeypatch, http
    ):
        """An album lookup that misses is followed immediately by a song lookup;
        a station changing tracks fast queues more. The spacing is what keeps
        that from bursting a public API the appliance has no account with."""
        monkeypatch.setattr("backend.shared.artwork_resolver._ITUNES_MIN_INTERVAL", 5.0)
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        monkeypatch.setattr("backend.shared.artwork_resolver.asyncio.sleep", _sleep)
        http([(200, _itunes([]))])
        resolver = ArtworkResolver()
        resolver._last_call = time.monotonic()

        await resolver._request("https://itunes.apple.com/search", {"term": "A"})

        assert len(slept) == 1 and 0 < slept[0] <= 5.0

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


# === resolve(): caching, and what is never cached ============================

class TestResolveCaching:
    async def test_a_key_is_looked_up_once(self, monkeypatch):
        resolver = ArtworkResolver()
        calls = []

        async def _lookup(artist, title, album):
            calls.append((artist, title, album))
            return "https://caa/front.jpg"

        monkeypatch.setattr(resolver, "_lookup", _lookup)

        assert await resolver.resolve("Miles Davis", "So What", "Kind of Blue")
        assert await resolver.resolve("Miles Davis", "So What", "Kind of Blue")
        assert len(calls) == 1

    async def test_misses_are_remembered(self, monkeypatch):
        """Deliberate: an unmatched title must not be re-queried on every poll
        of a station that keeps announcing it."""
        resolver = ArtworkResolver()
        calls = []

        async def _lookup(artist, title, album):
            calls.append(title)
            return None

        monkeypatch.setattr(resolver, "_lookup", _lookup)

        assert await resolver.resolve("A", "B", "") is None
        assert await resolver.resolve("A", "B", "") is None
        assert len(calls) == 1

    async def test_an_empty_query_asks_nothing(self, monkeypatch):
        resolver = ArtworkResolver()

        async def _lookup(*a):
            raise AssertionError("iTunes was asked about a track with no name")

        monkeypatch.setattr(resolver, "_lookup", _lookup)

        assert await resolver.resolve("Miles Davis", "", "") is None


class TestAFailedLookupIsNotAMiss:
    async def test_resolve_answers_none_without_recording_it(self, monkeypatch):
        """The caller sees the same None either way — a cover is a decoration
        and nothing may escape this module onto the metadata publish path. What
        differs is the memory: a miss is kept for the process, a failure is not,
        so the next track change asks again instead of inheriting a verdict from
        a blip.
        """
        resolver = ArtworkResolver()
        calls = []

        async def _lookup(artist, title, album):
            calls.append(title)
            if len(calls) == 1:
                raise _LookupUnavailable("iTunes unreachable")
            return "https://is1/600x600bb.jpg"

        monkeypatch.setattr(resolver, "_lookup", _lookup)

        assert await resolver.resolve(
            "Koma", "Un parmi des millions", "Le réveil"
        ) is None
        assert not resolver._cache, "the failure was recorded as a verdict"

        assert await resolver.resolve(
            "Koma", "Un parmi des millions", "Le réveil"
        ) == "https://is1/600x600bb.jpg"
        assert len(calls) == 2, "the second attempt was served from the cache"


class TestCacheEviction:
    """The bounded LRU — this resolver serves a radio station that never stops."""

    async def test_the_cache_is_capped_and_evicts_the_least_recently_used(
        self, monkeypatch
    ):
        monkeypatch.setattr("backend.shared.artwork_resolver._CACHE_MAX", 3)
        resolver = ArtworkResolver()

        async def _lookup(artist, title, album):
            return f"https://x/{title}.jpg"

        monkeypatch.setattr(resolver, "_lookup", _lookup)

        for title in ("a", "b", "c", "d"):
            await resolver.resolve("Artist", title, "")

        assert len(resolver._cache) == 3
        assert ArtworkResolver._cache_key("Artist", "a", "") not in resolver._cache

    async def test_a_cache_hit_is_promoted_so_a_played_track_survives(
        self, monkeypatch
    ):
        monkeypatch.setattr("backend.shared.artwork_resolver._CACHE_MAX", 2)
        resolver = ArtworkResolver()

        async def _lookup(artist, title, album):
            return f"https://x/{title}.jpg"

        monkeypatch.setattr(resolver, "_lookup", _lookup)

        await resolver.resolve("Artist", "a", "")
        await resolver.resolve("Artist", "b", "")
        await resolver.resolve("Artist", "a", "")   # promotes a
        await resolver.resolve("Artist", "c", "")   # evicts b

        assert ArtworkResolver._cache_key("Artist", "a", "") in resolver._cache
        assert ArtworkResolver._cache_key("Artist", "b", "") not in resolver._cache


class TestNoArtist:
    """Radio's in-band feed announces a bare title about half the time.

    `_parse_inband_track` splits an artist off only on `Artist - Title` or an
    unambiguous `Title by Artist`, and leaves it empty otherwise — deliberately,
    because a wrong artist is worse than none. With nothing to index by there is
    no catalogue to read, so the term search answers instead. It is not a
    fallback for the catalogue path; it is the answer to a different question.
    """

    async def test_a_bare_title_is_still_looked_up(self, http, no_throttle):
        session = http([(200, _itunes([_song("Blues in the Night")]))])
        resolver = ArtworkResolver()

        url = await resolver.resolve("", "Blues in the Night")

        assert url == "https://x/600x600bb.jpg"
        assert session.requests[0][1] == {
            "term": "Blues in the Night", "media": "music", "limit": "5",
        }

    async def test_no_artist_is_never_looked_up_as_an_artist(self, http, no_throttle):
        """An empty name matches nobody, so the artist search would be a request
        that cannot succeed — and the miss it answers is cached for the process."""
        session = http([(200, _itunes([_song("Blues in the Night")]))])
        resolver = ArtworkResolver()

        await resolver.resolve("", "Blues in the Night")

        assert all(p.get("entity") != "musicArtist" for _, p in session.requests)

    async def test_a_bare_title_with_no_title_asks_nothing(self, http, no_throttle):
        resolver = ArtworkResolver()

        assert await resolver.resolve("", "", "Some Album") is None


class TestTheArtistCachesAreBounded:
    """`_cache` beside them is capped for a reason: this resolver serves a radio
    station that never stops, on a unit that never reboots."""

    async def test_the_remembered_artists_are_capped(self, http, no_throttle, monkeypatch):
        monkeypatch.setattr("backend.shared.artwork_resolver._ARTISTS_MAX", 2)
        http([(200, _itunes([_artist_row(f"A{i}", i)])) for i in range(4)])
        resolver = ArtworkResolver()

        for i in range(4):
            await resolver._artist_id(f"A{i}")

        assert len(resolver._artist_ids) == 2
        assert "a0" not in resolver._artist_ids

    async def test_their_catalogues_are_capped_too(self, http, no_throttle, monkeypatch):
        from backend.shared.artwork_resolver import _ALBUM_ENTITY

        monkeypatch.setattr("backend.shared.artwork_resolver._ARTISTS_MAX", 2)
        http([(200, _itunes([_album("X")])) for _ in range(4)])
        resolver = ArtworkResolver()

        for aid in range(4):
            await resolver._from_catalogue(aid, _ALBUM_ENTITY, "X")

        assert len(resolver._catalogues) == 2
        assert (0, "album") not in resolver._catalogues

    async def test_a_catalogue_that_is_read_again_is_promoted(
        self, http, no_throttle, monkeypatch
    ):
        """Otherwise the record actually playing is the one evicted."""
        from backend.shared.artwork_resolver import _ALBUM_ENTITY

        monkeypatch.setattr("backend.shared.artwork_resolver._ARTISTS_MAX", 2)
        http([(200, _itunes([_album("X")])) for _ in range(3)])
        resolver = ArtworkResolver()

        await resolver._from_catalogue(1, _ALBUM_ENTITY, "X")
        await resolver._from_catalogue(2, _ALBUM_ENTITY, "X")
        await resolver._from_catalogue(1, _ALBUM_ENTITY, "X")   # promotes 1
        await resolver._from_catalogue(3, _ALBUM_ENTITY, "X")   # evicts 2

        assert (1, "album") in resolver._catalogues
        assert (2, "album") not in resolver._catalogues


class TestEndToEnd:
    """The whole shape over one stubbed network, because the cost claim in the
    module docstring is only true if the two caches actually hold."""

    async def test_a_record_played_track_by_track_pays_for_its_artist_once(
        self, http, no_throttle
    ):
        session = http([
            (200, _itunes([])),                                   # nobody is called that
            (200, _itunes([_artist_row("Jeune Mort", 99)])),      # the lead credit is
            (200, _itunes([_artist_row("Jeune Mort", 99),         # their albums
                           _album("NO COLORS", art="https://cover/100x100bb.jpg")])),
        ])
        resolver = ArtworkResolver()

        first = await resolver.resolve("Jeune Mort, ISHA", "Paire blanche", "NO COLORS")
        paid_for_the_first = len(session.requests)
        second = await resolver.resolve("Jeune Mort, ISHA", "Marénoire", "NO COLORS")

        assert first == second == "https://cover/600x600bb.jpg"
        assert paid_for_the_first == 3, "the artist and their albums, once"
        assert len(session.requests) == 3, "the second track paid for the artist again"
