"""Tests for GET /api/lyrics — the wire shape the frontend Lyrics app reads.

create_lyrics_router() takes the service directly, so the route mounts on a bare
FastAPI app with a stub service; no app wiring or network involved.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.lyrics import create_lyrics_router
from backend.core.lyrics import LyricsUnavailable


@pytest.fixture
def lyrics_service():
    service = AsyncMock()
    service.get_lyrics = AsyncMock(
        return_value={"found": True, "synced": [{"t": 0, "line": "a"}], "plain": "a"}
    )
    return service


@pytest.fixture
def api(lyrics_service):
    app = FastAPI()
    app.include_router(create_lyrics_router(lyrics_service))
    return TestClient(app)


class TestLyricsRoute:
    def test_success_envelope(self, api):
        r = api.get("/api/lyrics", params={"artist": "A", "title": "B"})
        assert r.status_code == 200
        assert r.json() == {
            "status": "success",
            "found": True,
            "synced": [{"t": 0, "line": "a"}],
            "plain": "a",
        }

    def test_miss_is_a_200_not_an_error(self, api, lyrics_service):
        # A track with no lyrics is a normal empty state, not a failure.
        lyrics_service.get_lyrics.return_value = {
            "found": False, "synced": None, "plain": None
        }
        r = api.get("/api/lyrics", params={"artist": "A", "title": "B"})
        assert r.status_code == 200
        assert r.json() == {
            "status": "success", "found": False, "synced": None, "plain": None
        }

    def test_duration_is_forwarded_as_milliseconds(self, api, lyrics_service):
        api.get(
            "/api/lyrics",
            params={"artist": "A", "title": "B", "album": "C", "duration": 180000},
        )
        lyrics_service.get_lyrics.assert_awaited_once_with(
            artist="A", title="B", album="C", duration_ms=180000
        )

    def test_optional_params_default_to_none(self, api, lyrics_service):
        api.get("/api/lyrics", params={"artist": "A", "title": "B"})
        lyrics_service.get_lyrics.assert_awaited_once_with(
            artist="A", title="B", album=None, duration_ms=None
        )

    @pytest.mark.parametrize("params", [{}, {"artist": "A"}, {"title": "B"}])
    def test_missing_required_params_are_422(self, api, params):
        assert api.get("/api/lyrics", params=params).status_code == 422

    def test_unreachable_lrclib_is_a_200_with_status_error(self, api, lyrics_service):
        # Regression: an outage used to be indistinguishable from a genuine miss
        # (200 + status=success + found=false), so the frontend cached it for the
        # session and never retried. status=error keeps the same empty state on
        # screen while telling the client not to cache it.
        lyrics_service.get_lyrics.side_effect = LyricsUnavailable("A - B")
        r = api.get("/api/lyrics", params={"artist": "A", "title": "B"})
        assert r.status_code == 200
        assert r.json() == {
            "status": "error", "found": False, "synced": None, "plain": None
        }

    def test_service_failure_is_a_500(self, api, lyrics_service):
        # api_error_handler turns an unexpected service error into a logged 500.
        lyrics_service.get_lyrics.side_effect = RuntimeError("boom")
        r = api.get("/api/lyrics", params={"artist": "A", "title": "B"})
        assert r.status_code == 500
