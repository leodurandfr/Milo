"""Tests for LyricsService: LRC parsing, match normalization, and the cache contract.

The pure helpers (LRC parser, match-key cleanup, record normalization) are tested
directly. get_lyrics()'s caching contract is tested with a stubbed _lookup and the
class-level CACHE_DIR redirected to tmp_path, so no network and no /var/lib/milo
write is ever touched. The thin HTTP boundary (_get/_search) is exercised with a
minimal fake session, like test_music_library_navidrome.py does.

Two of the rules here were paid for in the field, on a track whose lyrics LRCLIB
had all along (Moussa - Laguna, 62 synced lines):

- an answer that is not 200 is NOT "this track has no lyrics". lrclib.net serves
  `503 ServerOverloaded` in bursts, and reading one as a miss wrote a permanent
  negative to disk — the track then showed "no lyrics found" forever.
- the album is not part of the query. LRCLIB matched `album_name` exactly, and
  its own record for that track carries a mojibake album (`La nuit je r^ve`, an
  ASCII caret), so the correct string 404s and every lookup fell through to the
  fuzzy search — the very call that was 503ing.
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest

from backend.core.lyrics.service import (
    _NEGATIVE_TTL,
    LyricsService,
    LyricsUnavailable,
    _clean,
    _from_record,
    _is_fresh,
    _is_well_formed,
    _parse_lrc,
)


@pytest.fixture
def service(tmp_path, monkeypatch):
    # CACHE_DIR is a class attribute read by __init__ (mkdir) and _cache_file —
    # patch it before constructing so nothing writes to /var/lib/milo.
    monkeypatch.setattr(LyricsService, "CACHE_DIR", tmp_path)
    return LyricsService()


def _found(plain="la la"):
    """The wire payload — what get_lyrics returns and the route spreads."""
    return {"found": True, "synced": [{"t": 0, "line": plain}], "plain": plain}


def _miss():
    return {"found": False, "synced": None, "plain": None}


def _record(payload=None, age_s=0):
    """A cached record: a payload plus the timestamp the expiry rule reads."""
    return {**(payload or _found()), "checked_at": time.time() - age_s}


@pytest.fixture
def no_ttl(monkeypatch):
    """Shorten the negative shelf life to nothing, for the end-to-end re-ask.

    Shortening the constant rather than freezing the clock: the module imports
    `time`, so patching `time.time` through it reaches the stdlib module and
    stops the clock for everything in the process, not just the service. The
    rule itself is pinned against the real constant in TestFreshness, with
    explicit record ages.
    """
    monkeypatch.setattr("backend.core.lyrics.service._NEGATIVE_TTL", 0)


class TestParseLrc:
    def test_basic_parse(self):
        assert _parse_lrc("[00:01.00]first\n[00:02.00]second") == [
            {"t": 1000, "line": "first"},
            {"t": 2000, "line": "second"},
        ]

    def test_centiseconds_are_padded_to_ms(self):
        # 2-digit fraction is centiseconds: .34 → 340ms, not 34ms.
        assert _parse_lrc("[00:12.34]x")[0]["t"] == 12340

    def test_milliseconds_are_kept(self):
        assert _parse_lrc("[00:12.345]x")[0]["t"] == 12345

    def test_missing_fraction_is_zero(self):
        assert _parse_lrc("[00:12]x")[0]["t"] == 12000

    def test_colon_fraction_separator(self):
        # The stamp regex accepts [.:] as the fraction separator.
        assert _parse_lrc("[00:12:34]x")[0]["t"] == 12340

    def test_minutes_are_accumulated(self):
        assert _parse_lrc("[02:03.50]x")[0]["t"] == 123500

    def test_multiple_stamps_on_one_line_repeat_the_text(self):
        # A repeated chorus line is stamped several times in one LRC row.
        assert _parse_lrc("[00:10.00][00:20.00]chorus") == [
            {"t": 10000, "line": "chorus"},
            {"t": 20000, "line": "chorus"},
        ]

    def test_output_is_sorted(self):
        parsed = _parse_lrc("[00:30.00]late\n[00:10.00]early")
        assert [line["t"] for line in parsed] == [10000, 30000]

    def test_empty_stamped_line_is_kept_as_empty_text(self):
        # Instrumental gaps are stamped with no text; the UI renders them as "♪".
        assert _parse_lrc("[00:05.00]") == [{"t": 5000, "line": ""}]

    def test_garbage_returns_none(self):
        assert _parse_lrc("not an lrc file") is None

    def test_empty_returns_none(self):
        assert _parse_lrc("") is None


class TestClean:
    def test_strips_parentheticals(self):
        assert _clean("Song (feat. X)") == "Song"

    def test_strips_brackets(self):
        assert _clean("Song [Live]") == "Song"

    def test_strips_trailing_suffix(self):
        assert _clean("Song - Remastered 2011") == "Song"

    def test_trims_whitespace(self):
        assert _clean("  Song  ") == "Song"

    def test_tolerates_empty(self):
        assert _clean("") == ""
        assert _clean(None) == ""


class TestFromRecord:
    def test_instrumental_is_empty(self):
        record = {"instrumental": True, "plainLyrics": "ignored"}
        assert _from_record(record) == {"found": False, "synced": None, "plain": None}

    def test_none_record_is_empty(self):
        assert _from_record(None)["found"] is False

    def test_record_without_lyrics_is_empty(self):
        assert _from_record({"trackName": "x"})["found"] is False

    def test_plain_is_derived_from_synced_when_absent(self):
        result = _from_record({"syncedLyrics": "[00:01.00]a\n[00:02.00]b"})
        assert result["found"] is True
        assert result["plain"] == "a\nb"

    def test_plain_only_has_no_synced(self):
        result = _from_record({"plainLyrics": "just words"})
        assert result == {"found": True, "synced": None, "plain": "just words"}

    def test_explicit_plain_wins_over_derived(self):
        result = _from_record(
            {"syncedLyrics": "[00:01.00]a", "plainLyrics": "canonical"}
        )
        assert result["plain"] == "canonical"


class TestCacheKey:
    def test_case_and_whitespace_are_normalized(self):
        assert LyricsService._cache_key(" Miles Davis ", "So What", None) == (
            LyricsService._cache_key("miles davis", "so what", None)
        )

    def test_parentheticals_do_not_change_the_key(self):
        assert LyricsService._cache_key("A", "Song (feat. X)", None) == (
            LyricsService._cache_key("A", "Song", None)
        )

    def test_a_different_track_is_a_different_key(self):
        assert LyricsService._cache_key("A", "One", None) != (
            LyricsService._cache_key("A", "Two", None)
        )
        assert LyricsService._cache_key("A", "One", None) != (
            LyricsService._cache_key("B", "One", None)
        )

    def test_two_recordings_of_one_song_are_two_entries(self):
        """`_clean` strips "(Live at Wembley)" before matching, so artist+title
        alone collapses a live take onto the studio one — and the cache would
        then answer with the other recording's LRC before the duration, the
        thing that tells them apart, ever reached LRCLIB."""
        studio = LyricsService._cache_key("A", "Song", 240_000)
        live = LyricsService._cache_key("A", "Song (Live at Wembley)", 315_000)
        assert studio != live

    def test_the_key_rounds_the_duration_exactly_as_the_query_does(self):
        """Keyed on milliseconds, two rips of one track 3 ms apart would be two
        entries for one LRCLIB answer. The query sends seconds; so does the key,
        so the two cannot disagree."""
        assert LyricsService._cache_key("A", "B", 240_100) == (
            LyricsService._cache_key("A", "B", 239_900)
        )

    def test_no_duration_is_its_own_key(self):
        """Radio's Shazam feed carries none, and LRCLIB is asked without it."""
        assert LyricsService._cache_key("A", "B", None) == LyricsService._cache_key("A", "B", 0)
        assert LyricsService._cache_key("A", "B", None) != LyricsService._cache_key("A", "B", 240_000)


class TestFreshness:
    """The rule that separates the two kinds of cached answer."""

    def test_a_positive_never_expires(self):
        assert _is_fresh(_record(_found(), age_s=10 * _NEGATIVE_TTL)) is True

    def test_a_negative_inside_the_ttl_is_served(self):
        assert _is_fresh(_record(_miss(), age_s=60)) is True

    def test_a_negative_past_the_ttl_is_not(self):
        """LRCLIB is crowd-sourced and grows: "no lyrics" is an answer with a
        shelf life, and a new release is exactly the case that outlives it."""
        assert _is_fresh(_record(_miss(), age_s=_NEGATIVE_TTL + 1)) is False

    @pytest.mark.parametrize("record", [
        None, "not a dict", {}, {"found": True, "synced": None, "plain": None},
    ])
    def test_a_record_missing_a_field_is_not_well_formed(self, record):
        """A cache file from an older shape must read as a miss, not raise and
        not be served — that is what buys this cache its no-migration rule."""
        assert _is_well_formed(record) is False

    @pytest.mark.parametrize("checked_at", ["2026-09-11T17:00:00", None, [], {}])
    def test_a_timestamp_of_the_wrong_type_is_not_well_formed(self, checked_at):
        """Presence is not enough: `_is_fresh` subtracts from this field and
        runs outside the read's try/except, so a string here raised a TypeError
        out of the service and 500'd that one track until the file was deleted
        by hand — the one outcome a disposable cache must never produce."""
        assert _is_well_formed({**_miss(), "checked_at": checked_at}) is False

    def test_a_found_flag_of_the_wrong_type_is_not_well_formed(self):
        assert _is_well_formed({**_miss(), "found": "yes", "checked_at": 0.0}) is False


class TestGetLyricsCaching:
    """get_lyrics()'s cache contract, with the network boundary stubbed out."""

    def _stub_lookup(self, service, monkeypatch, result):
        calls = []

        async def fake_lookup(artist, title, duration_ms):
            calls.append((artist, title, duration_ms))
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(service, "_lookup", fake_lookup)
        return calls

    async def test_blank_identity_short_circuits(self, service, monkeypatch):
        async def fail_lookup(*a, **kw):  # pragma: no cover - must not run
            raise AssertionError("_lookup should not be called")

        monkeypatch.setattr(service, "_lookup", fail_lookup)
        assert await service.get_lyrics("", "Title") == {
            "found": False, "synced": None, "plain": None
        }
        assert (await service.get_lyrics("Artist", "   "))["found"] is False

    async def test_memory_cache_serves_second_call(self, service, monkeypatch):
        calls = self._stub_lookup(service, monkeypatch, _found())
        first = await service.get_lyrics("Miles Davis", "So What")
        second = await service.get_lyrics("miles davis", "so what")  # same key
        assert first == second
        assert len(calls) == 1

    async def test_disk_cache_survives_a_fresh_instance(self, service, monkeypatch, tmp_path):
        self._stub_lookup(service, monkeypatch, _found())
        await service.get_lyrics("A", "B")

        fresh = LyricsService()  # CACHE_DIR still patched to tmp_path

        async def fail_lookup(*a, **kw):  # pragma: no cover - must not run
            raise AssertionError("disk cache should have served this")

        monkeypatch.setattr(fresh, "_lookup", fail_lookup)
        assert (await fresh.get_lyrics("A", "B"))["found"] is True

    async def test_negative_result_is_cached(self, service, monkeypatch):
        # A genuine "LRCLIB answered, no match" is cached so it isn't re-queried.
        calls = self._stub_lookup(service, monkeypatch, _miss())
        assert (await service.get_lyrics("A", "B"))["found"] is False
        assert (await service.get_lyrics("A", "B"))["found"] is False
        assert len(calls) == 1

    async def test_an_expired_negative_is_asked_again(
        self, service, monkeypatch, no_ttl
    ):
        """LRCLIB gains entries daily. Cached with no expiry, the answer for a
        track released this week is frozen at the one moment it was most likely
        to be missing, and nothing short of deleting the file ever revisits it.
        """
        calls = self._stub_lookup(service, monkeypatch, _miss())
        await service.get_lyrics("A", "B")
        await service.get_lyrics("A", "B")

        assert len(calls) == 2

    async def test_a_positive_is_never_asked_again(
        self, service, monkeypatch, no_ttl
    ):
        """The control on the rule above: a track's lyrics do not change, so no
        shelf life reaches them — here, not even one of zero seconds."""
        calls = self._stub_lookup(service, monkeypatch, _found())
        await service.get_lyrics("A", "B")
        assert (await service.get_lyrics("A", "B"))["found"] is True

        assert len(calls) == 1

    async def test_the_returned_payload_carries_no_cache_bookkeeping(
        self, service, monkeypatch
    ):
        """The route spreads this dict straight onto the wire (`{"status": ...,
        **result}`), so an internal field here becomes a public API field."""
        self._stub_lookup(service, monkeypatch, _found())
        fresh = await service.get_lyrics("A", "B")
        cached = await service.get_lyrics("A", "B")

        assert set(fresh) == {"found", "synced", "plain"}
        assert set(cached) == {"found", "synced", "plain"}

    async def test_transient_failure_raises_and_is_not_cached(
        self, service, monkeypatch, tmp_path
    ):
        """Regression: an unreachable LRCLIB must not poison the cache.

        It once returned the same empty dict as a genuine 404, so one outage
        persisted found=false to disk forever — and, once the route reported it
        as a success, into the frontend's per-session cache too.
        """
        calls = self._stub_lookup(service, monkeypatch, LyricsUnavailable("down"))
        with pytest.raises(LyricsUnavailable):
            await service.get_lyrics("A", "B")
        assert list(tmp_path.glob("*.json")) == []  # nothing persisted

        with pytest.raises(LyricsUnavailable):
            await service.get_lyrics("A", "B")
        assert len(calls) == 2  # retried rather than served from cache

    async def test_successful_lookup_round_trips_through_disk(
        self, service, monkeypatch, tmp_path
    ):
        payload = _found("hello")
        self._stub_lookup(service, monkeypatch, payload)
        await service.get_lyrics("A", "B")

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        written = json.loads(files[0].read_text(encoding="utf-8"))
        assert {k: written[k] for k in payload} == payload
        assert isinstance(written["checked_at"], float)

    async def test_lookup_receives_the_untrimmed_display_values(self, service, monkeypatch):
        # The match key is normalized, but the query keeps the real tags.
        calls = self._stub_lookup(service, monkeypatch, _found())
        await service.get_lyrics("  Artist ", " Title ", duration_ms=180000)
        assert calls == [("Artist", "Title", 180000)]

    async def test_memory_cache_evicts_oldest(self, service):
        from backend.core.lyrics.service import _MEM_CACHE_MAX

        for i in range(_MEM_CACHE_MAX + 10):
            service._store_mem(f"key-{i}", _record())
        assert len(service._mem) == _MEM_CACHE_MAX
        assert "key-0" not in service._mem
        assert f"key-{_MEM_CACHE_MAX + 9}" in service._mem


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
        return self._body


def _session(status=200, body=None):
    session = MagicMock()
    session.get = MagicMock(return_value=_Resp(status, body))
    return session


class TestGetEndpoint:
    async def test_404_is_a_miss(self, service):
        """The one non-200 that is an answer: LRCLIB has no such track, so the
        fuzzy search is worth trying."""
        assert await service._get(_session(404), {}) is None

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_any_other_non_200_is_an_outage_not_a_miss(self, service, status):
        """Measured: lrclib.net serves 503 ServerOverloaded in bursts. Read as a
        miss, one burst writes a permanent "this track has no lyrics" to disk —
        which is exactly what happened to a track LRCLIB had all along."""
        with pytest.raises(LyricsUnavailable):
            await service._get(_session(status), {})

    async def test_200_returns_the_record(self, service):
        record = {"plainLyrics": "x"}
        assert await service._get(_session(200, record), {}) == record


class TestSearchSelection:
    async def test_prefers_a_synced_result_over_an_earlier_plain_one(self, service):
        body = [
            {"plainLyrics": "plain only"},
            {"syncedLyrics": "[00:01.00]synced"},
        ]
        picked = await service._search(_session(200, body), "A", "B")
        assert picked["syncedLyrics"] == "[00:01.00]synced"

    async def test_falls_back_to_the_first_plain_result(self, service):
        body = [{"trackName": "no lyrics"}, {"plainLyrics": "words"}]
        picked = await service._search(_session(200, body), "A", "B")
        assert picked["plainLyrics"] == "words"

    async def test_empty_list_is_a_miss(self, service):
        """LRCLIB searched and found nothing. That IS an answer, and the only
        one on this endpoint that may be cached."""
        assert await service._search(_session(200, []), "A", "B") is None

    async def test_results_without_any_lyrics_are_a_miss(self, service):
        assert await service._search(_session(200, [{"trackName": "x"}]), "A", "B") is None

    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_a_non_200_is_an_outage_not_a_miss(self, service, status):
        """The fallback carries the whole lookup whenever the exact match 404s,
        so a 503 here is what poisons the cache."""
        with pytest.raises(LyricsUnavailable):
            await service._search(_session(status), "A", "B")

    async def test_a_non_list_body_is_an_outage_not_a_miss(self, service):
        """An error object where the result list belongs is LRCLIB failing to
        answer, not a track without lyrics."""
        with pytest.raises(LyricsUnavailable):
            await service._search(_session(200, {"error": "x"}), "A", "B")


class _SessionRecorder:
    """A full `aiohttp.ClientSession` stand-in, including its async context manager.

    `_lookup` builds the session itself (`async with aiohttp.ClientSession(...)`),
    so exercising it means standing in for the constructor as well as the calls.
    Both the timeout and the headers are captured, because both are contractual:
    LRCLIB asks callers to identify themselves, and the timeout is what keeps a
    lyrics fetch from outliving the track.
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
    """Collapse the 0.3 s politeness spacing.

    Reduced, never removed: `_lookup` calls it twice on the fallback path and a
    test that asserts on the second request would otherwise pay 0.6 s.
    """
    monkeypatch.setattr("backend.core.lyrics.service._MIN_INTERVAL", 0)


class TestLookup:
    """`_lookup` — the two-stage LRCLIB resolution, all eighteen lines at zero.

    This is what separates the three answers the Lyrics view can show: real
    lyrics, a genuine "this track has none" (cached forever), and "LRCLIB could
    not be reached" (cached nowhere, retried on the next open). Collapsing the
    third into the second freezes an outage into a permanent negative for every
    track played during it.
    """

    async def test_the_exact_lookup_carries_artist_track_and_duration(
        self, service, monkeypatch, no_throttle
    ):
        """LRCLIB's `/get` matches on artist + track + duration — and no album.

        The duration is seconds, rounded from the metadata's milliseconds; sent
        as milliseconds it matches nothing and every track falls through to the
        fuzzy search, which is both slower and less accurate.
        """
        session = _SessionRecorder([(200, {"plainLyrics": "words"})])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        await service._lookup("Miles Davis", "So What", 545_000)

        url, params = session.requests[0]
        assert url.endswith("/get")
        assert params == {
            "artist_name": "Miles Davis",
            "track_name": "So What",
            "duration": "545",
        }

    async def test_the_album_is_never_sent(self, service, monkeypatch, no_throttle):
        """LRCLIB filters `album_name` by exact string match, with none of the
        ±2 s tolerance it gives duration, against free text its contributors
        typed. Measured: its record for Moussa - Laguna spells the album
        `La nuit je r^ve` (an ASCII caret), so the correct string 404s forever
        and the whole lookup falls onto the fuzzy search. Sources that carry an
        album were strictly worse off than radio, which carries none.
        """
        session = _SessionRecorder([(200, {"plainLyrics": "words"})])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        await service._lookup("Moussa", "Laguna", 181_000)

        assert "album_name" not in session.requests[0][1]

    async def test_a_track_with_no_duration_asks_without_it(
        self, service, monkeypatch, no_throttle
    ):
        """Radio's Shazam feed has no duration. Sent as a zero, LRCLIB matches
        on it and answers nothing."""
        session = _SessionRecorder([(200, {"plainLyrics": "words"})])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        await service._lookup("Artist", "Title", None)

        assert session.requests[0][1] == {"artist_name": "Artist", "track_name": "Title"}

    async def test_a_missed_exact_match_falls_back_to_the_fuzzy_search(
        self, service, monkeypatch, no_throttle
    ):
        """The exact match misses on an absent duration or on tag noise —
        "(feat. X)", "- Remastered 2011". Without the fallback those tracks show
        no lyrics at all, and they are a large share of a real library.
        """
        session = _SessionRecorder([
            (404, None),
            (200, [{"syncedLyrics": "[00:01.00]found by search"}]),
        ])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        result = await service._lookup("Artist", "Title (feat. X)", None)

        assert [url for url, _ in session.requests] == [
            "https://lrclib.net/api/get", "https://lrclib.net/api/search",
        ]
        assert result["found"] is True

    async def test_a_refused_exact_match_still_reaches_the_search(
        self, service, monkeypatch, no_throttle
    ):
        """A 503 on `/get` must take the same road as a 404, not end the lookup.

        The two are separate endpoints and a burst hits them independently —
        measured on this unit: `/get` answering 503 while `/search` answered 200
        for the same track, in the same window. Raising on the first refusal
        traded a poisoned cache for an empty Lyrics view on tracks the search
        would have resolved, which is a smaller bug but still a bug.
        """
        session = _SessionRecorder([
            (503, {"name": "ServerOverloaded"}),
            (200, [{"syncedLyrics": "[00:01.00]found by search"}]),
        ])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        result = await service._lookup("Artist", "Title", None)

        assert [url for url, _ in session.requests] == [
            "https://lrclib.net/api/get", "https://lrclib.net/api/search",
        ]
        assert result["found"] is True

    async def test_only_both_calls_refusing_is_an_outage(
        self, service, monkeypatch, no_throttle
    ):
        """The other half of the rule above: when neither endpoint answered,
        there is no answer to cache and the next open must retry."""
        session = _SessionRecorder([(503, None), (503, None)])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        with pytest.raises(LyricsUnavailable):
            await service._lookup("Artist", "Title", None)

        assert len(session.requests) == 2

    async def test_a_refused_exact_match_then_an_empty_search_is_a_negative(
        self, service, monkeypatch, no_throttle
    ):
        """The search answered — it searched and found nothing. That is a real
        result even though the exact match refused, and it is cached."""
        session = _SessionRecorder([(503, None), (200, [])])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        assert await service._lookup("Artist", "Title", None) == _miss()

    async def test_a_hit_on_the_exact_match_does_not_search(
        self, service, monkeypatch, no_throttle
    ):
        """The control. A search issued anyway doubles every lookup against a
        free public API this appliance is asked to be polite to."""
        session = _SessionRecorder([(200, {"plainLyrics": "words"})])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        await service._lookup("Artist", "Title", None)

        assert len(session.requests) == 1

    async def test_a_genuine_no_match_answers_a_negative_rather_than_None(
        self, service, monkeypatch, no_throttle
    ):
        """LRCLIB answered; the track has no lyrics. That IS a result, and it is
        cached — otherwise every instrumental is re-queried on every play."""
        session = _SessionRecorder([(404, None), (200, [])])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        result = await service._lookup("Artist", "Title", None)

        assert result == {"found": False, "synced": None, "plain": None}

    @pytest.mark.parametrize("failure", [
        aiohttp.ClientError("connection reset"),
        asyncio.TimeoutError(),
    ])
    async def test_an_unreachable_lrclib_raises_and_caches_nothing(
        self, service, monkeypatch, no_throttle, failure, caplog
    ):
        """`LyricsUnavailable` is the one answer `get_lyrics` does not store.

        Answered as a negative instead, a thirty-second outage is frozen into
        "no lyrics" for every track played during it — on disk, permanently,
        with no way for the user to clear it.
        """
        session = _SessionRecorder([failure])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(LyricsUnavailable):
                await service._lookup("Artist", "Title", None)

        assert "Lyrics lookup failed" in caplog.text

    async def test_lrclib_is_told_who_is_calling(
        self, service, monkeypatch, no_throttle
    ):
        """LRCLIB asks callers to identify themselves with a User-Agent. Dropped,
        the appliance is an anonymous client against a free service."""
        session = _SessionRecorder([(200, {"plainLyrics": "x"})])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        await service._lookup("Artist", "Title", None)

        assert "Milo" in session.kwargs["headers"]["User-Agent"]

    async def test_the_lookup_is_bounded_in_time(
        self, service, monkeypatch, no_throttle
    ):
        """Unbounded, a lyrics fetch outlives the track it was for and the view
        renders words for something that stopped playing minutes ago."""
        session = _SessionRecorder([(200, {"plainLyrics": "x"})])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)

        await service._lookup("Artist", "Title", None)

        assert session.kwargs["timeout"].total == 8

    async def test_the_calls_are_spaced_by_the_politeness_interval(
        self, service, monkeypatch
    ):
        """Two requests per lookup against a free API, and the Lyrics view can be
        opened on track after track. The throttle is what keeps a rapid skip from
        bursting LRCLIB."""
        monkeypatch.setattr("backend.core.lyrics.service._MIN_INTERVAL", 5.0)
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        monkeypatch.setattr("backend.core.lyrics.service.asyncio.sleep", _sleep)
        session = _SessionRecorder([(404, None), (200, [])])
        monkeypatch.setattr("backend.core.lyrics.service.aiohttp.ClientSession", session)
        service._last_call = time.monotonic()

        await service._lookup("Artist", "Title", None)

        assert len(slept) == 2
        assert all(0 < d <= 5.0 for d in slept)

    async def test_a_first_call_after_a_long_idle_is_not_delayed(
        self, service, monkeypatch
    ):
        """The control. A throttle that always slept would add its interval to
        the very first lookup, which is the one the user is watching for."""
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        monkeypatch.setattr("backend.core.lyrics.service.asyncio.sleep", _sleep)
        service._last_call = time.monotonic() - 3600

        await service._throttle()

        assert slept == []


class TestDiskCache:
    """The disposable derived cache under /var/lib/milo/lyrics/.

    No schema_version and no fail-loud protocol by design — every failure arm
    here has to degrade to "fetch again", never to an exception that reaches the
    route.
    """

    async def test_a_result_survives_a_restart_through_the_disk_cache(self, service):
        """The mem cache is 256 entries and dies with the process; the disk cache
        is what makes the second play of a track instant."""
        key = service._cache_key("Artist", "Title", None)
        record = _record(_found("words"))
        await service._write_disk(key, record)

        assert await service._read_disk(key) == record

    async def test_the_write_is_atomic(self, service, tmp_path):
        """A partially written cache file is read back as a corrupt one on the
        next boot. The temp name carries the pid so two processes cannot collide
        on it, and the rename is what makes the visible file always complete.
        """
        key = service._cache_key("Artist", "Title", None)
        await service._write_disk(key, _record())

        assert (tmp_path / f"{key}.json").is_file()
        assert not list(tmp_path.glob("*.tmp"))

    async def test_a_corrupt_cache_file_reads_as_a_miss(self, service, tmp_path, caplog):
        """Truncated by a power cut mid-write. Raising here would 500 the lyrics
        route for that track forever, with no way to clear it from the UI."""
        key = service._cache_key("Artist", "Title", None)
        (tmp_path / f"{key}.json").write_text("{not json")

        with caplog.at_level(logging.WARNING):
            assert await service._read_disk(key) is None

        assert "Lyrics cache read failed" in caplog.text

    async def test_an_absent_cache_file_is_a_miss_without_a_warning(self, service):
        """The common case — every first play. A warning here would fill
        errors.log with one line per new track."""
        assert await service._read_disk(service._cache_key("A", "B", None)) is None

    async def test_an_expired_negative_on_disk_reads_as_a_miss(self, service):
        """What makes the TTL survive a reboot. Held only in memory it would
        reset on every restart — and the appliance restarts far less often than
        a week."""
        key = service._cache_key("Artist", "Title", None)
        await service._write_disk(key, _record(_miss(), age_s=_NEGATIVE_TTL + 1))

        assert await service._read_disk(key) is None

    async def test_an_expired_positive_on_disk_is_still_served(self, service):
        """The control: the expiry is the negative's alone."""
        key = service._cache_key("Artist", "Title", None)
        record = _record(_found(), age_s=10 * _NEGATIVE_TTL)
        await service._write_disk(key, record)

        assert await service._read_disk(key) == record

    async def test_a_file_from_an_older_shape_reads_as_a_miss(self, service, tmp_path):
        """The no-migration rule, made real: this cache carries no
        schema_version, so a record written before the expiry field existed must
        cost one refetch — not a KeyError inside the route."""
        key = service._cache_key("Artist", "Title", None)
        (tmp_path / f"{key}.json").write_text('{"found": false, "synced": null, "plain": null}')

        assert await service._read_disk(key) is None

    async def test_a_write_that_fails_is_survivable_and_leaves_no_temp_file(
        self, service, tmp_path, monkeypatch, caplog
    ):
        """A full disk must cost the cache entry, not the lyrics.

        The temp file is unlinked on the way out: left behind, a directory that
        cannot be written to accumulates one orphan per lookup.
        """
        key = service._cache_key("Artist", "Title", None)
        real_replace = os.replace

        def _boom(src, dst):
            raise OSError("no space left on device")

        monkeypatch.setattr("backend.core.lyrics.service.os.replace", _boom)

        with caplog.at_level(logging.WARNING):
            await service._write_disk(key, _record())

        monkeypatch.setattr("backend.core.lyrics.service.os.replace", real_replace)
        assert "Lyrics cache write failed" in caplog.text
        assert not list(tmp_path.glob("*.tmp"))

    def test_an_unwritable_cache_directory_does_not_stop_construction(
        self, monkeypatch, tmp_path, caplog
    ):
        """The service is built at boot by `_create_service`; raising here would
        take the whole backend down for a feature that is a dock app.
        """
        monkeypatch.setattr(LyricsService, "CACHE_DIR", tmp_path / "nope")
        real_mkdir = Path.mkdir

        def _boom(self, *a, **kw):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "mkdir", _boom)

        with caplog.at_level(logging.WARNING):
            svc = LyricsService()

        monkeypatch.setattr(Path, "mkdir", real_mkdir)
        assert svc is not None
        assert "Could not create lyrics cache dir" in caplog.text


class TestConcurrentLookups:
    """Two viewers, one track: the double-check inside the lock."""

    async def test_two_viewers_on_one_track_query_lrclib_once(self, service):
        """The re-check INSIDE the lock, which the one before it cannot cover.

        Both callers pass the pre-lock cache read while it is still empty — that
        is the whole condition the lock exists for — and the second then waits.
        Without the second look at the cache it re-queries a track the first has
        already resolved, doubling every request under exactly the concurrency
        the lock was added to serialise.

        Driven with two real tasks: two sequential calls are answered by the
        pre-lock read and never reach the second one.
        """
        calls = {"n": 0}
        released = asyncio.Event()

        async def _lookup(*args):
            calls["n"] += 1
            await released.wait()
            return _found("first")

        service._lookup = _lookup

        first = asyncio.create_task(service.get_lyrics("Artist", "Title"))
        for _ in range(20):
            await asyncio.sleep(0)
            if calls["n"] == 1:
                break
        assert calls["n"] == 1, "the first caller never reached the lookup"

        second = asyncio.create_task(service.get_lyrics("Artist", "Title"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert not second.done(), "the second caller did not queue on the lock"

        released.set()
        results = await asyncio.gather(first, second)

        assert results == [_found("first"), _found("first")]
        assert calls["n"] == 1
