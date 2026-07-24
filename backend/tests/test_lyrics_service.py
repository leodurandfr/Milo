"""Tests for LyricsService: LRC parsing, match normalization, and the cache contract.

The pure helpers (LRC parser, match-key cleanup, record normalization) are tested
directly. get_lyrics()'s caching contract is tested with a stubbed _lookup and the
class-level CACHE_DIR redirected to tmp_path, so no network and no /var/lib/milo
write is ever touched. The thin HTTP boundary (_get/_search) is exercised with a
minimal fake session, like test_music_library_navidrome.py does.
"""
import json
from unittest.mock import MagicMock

import pytest

from backend.core.lyrics.service import (
    LyricsService,
    LyricsUnavailable,
    _clean,
    _from_record,
    _parse_lrc,
)


@pytest.fixture
def service(tmp_path, monkeypatch):
    # CACHE_DIR is a class attribute read by __init__ (mkdir) and _cache_file —
    # patch it before constructing so nothing writes to /var/lib/milo.
    monkeypatch.setattr(LyricsService, "CACHE_DIR", tmp_path)
    return LyricsService()


def _found(plain="la la"):
    return {"found": True, "synced": [{"t": 0, "line": plain}], "plain": plain}


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

    def test_album_participates_in_the_key(self):
        assert LyricsService._cache_key("A", "B", "Album One") != (
            LyricsService._cache_key("A", "B", "Album Two")
        )

    def test_missing_album_matches_empty_album(self):
        assert LyricsService._cache_key("A", "B", None) == (
            LyricsService._cache_key("A", "B", "")
        )


class TestGetLyricsCaching:
    """get_lyrics()'s cache contract, with the network boundary stubbed out."""

    def _stub_lookup(self, service, monkeypatch, result):
        calls = []

        async def fake_lookup(artist, title, album, duration_ms):
            calls.append((artist, title, album, duration_ms))
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
        empty = {"found": False, "synced": None, "plain": None}
        calls = self._stub_lookup(service, monkeypatch, empty)
        assert (await service.get_lyrics("A", "B"))["found"] is False
        assert (await service.get_lyrics("A", "B"))["found"] is False
        assert len(calls) == 1

    async def test_transient_failure_raises_and_is_not_cached(
        self, service, monkeypatch, tmp_path
    ):
        """Regression: a network error (_lookup → None) must not poison the cache.

        It once returned the same empty dict as a genuine 404, so one outage
        persisted found=false to disk forever — and, once the route reported it
        as a success, into the frontend's per-session cache too.
        """
        calls = self._stub_lookup(service, monkeypatch, None)
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
        assert json.loads(files[0].read_text(encoding="utf-8")) == payload

    async def test_lookup_receives_the_untrimmed_display_values(self, service, monkeypatch):
        # The match key is normalized, but the query keeps the real tags.
        calls = self._stub_lookup(service, monkeypatch, _found())
        await service.get_lyrics("  Artist ", " Title ", album="Album", duration_ms=180000)
        assert calls == [("Artist", "Title", "Album", 180000)]

    async def test_memory_cache_evicts_oldest(self, service):
        from backend.core.lyrics.service import _MEM_CACHE_MAX

        for i in range(_MEM_CACHE_MAX + 10):
            service._store_mem(f"key-{i}", _found())
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
        assert await service._get(_session(404), {}) is None

    async def test_non_200_is_a_miss(self, service):
        assert await service._get(_session(500), {}) is None

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
        assert await service._search(_session(200, []), "A", "B") is None

    async def test_non_list_body_is_a_miss(self, service):
        assert await service._search(_session(200, {"error": "x"}), "A", "B") is None

    async def test_non_200_is_a_miss(self, service):
        assert await service._search(_session(500), "A", "B") is None

    async def test_results_without_any_lyrics_are_a_miss(self, service):
        assert await service._search(_session(200, [{"trackName": "x"}]), "A", "B") is None
