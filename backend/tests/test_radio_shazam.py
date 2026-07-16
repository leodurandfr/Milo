# backend/tests/test_radio_shazam.py
"""
Unit tests for ShazamRecognitionService hardening (WI-2).

Covers the confidence gate (drop 0-match results) and result parsing, which
are the correctness-critical, pure pieces. The recognition-loop stale-clear
counter is time-driven and exercised via a compressed loop test.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.sources.radio.shazam import ShazamRecognitionService


@pytest.fixture
def service():
    settings = Mock()
    settings.get_setting = AsyncMock(return_value={"shazam_enabled": True})
    return ShazamRecognitionService(settings_service=settings)


class TestConfidenceGate:
    """Zero-match results are low-confidence and must be dropped (WI-2)."""

    def test_zero_matches_dropped(self, service):
        result = {
            "track": {"title": "Guess", "subtitle": "Someone"},
            "matches": [],
        }
        assert service._parse_result(result) is None

    def test_missing_matches_key_dropped(self, service):
        result = {"track": {"title": "Guess", "subtitle": "Someone"}}
        assert service._parse_result(result) is None

    def test_one_match_accepted(self, service):
        result = {
            "track": {"title": "So What", "subtitle": "Miles Davis"},
            "matches": [{"id": "abc"}],
        }
        track = service._parse_result(result)
        assert track == {"title": "So What", "artist": "Miles Davis", "artwork": None}

    def test_no_track_block(self, service):
        assert service._parse_result({"matches": [{"id": "x"}]}) is None
        assert service._parse_result({}) is None


class TestArtwork:
    def test_artwork_upgraded_resolution(self, service):
        result = {
            "track": {
                "title": "T",
                "subtitle": "A",
                "images": {"coverart": "https://x/image/400x400cc.jpg"},
            },
            "matches": [{"id": "1"}],
        }
        track = service._parse_result(result)
        assert track["artwork"] == "https://x/image/1280x1280cc.jpg"


class TestRunningFlag:
    def test_is_running_default_false(self, service):
        assert service.is_running is False


class TestStaleClear:
    """After STALE_CLEAR_ROUNDS unrecognized rounds, a pinned track clears."""

    @pytest.mark.asyncio
    async def test_stale_track_cleared_and_broadcast(self, monkeypatch):
        import backend.sources.radio.shazam as shazam_mod

        monkeypatch.setattr(shazam_mod, "INITIAL_DELAY_SECONDS", 0)
        monkeypatch.setattr(shazam_mod, "RECOGNITION_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(shazam_mod, "MAX_RETRIES", 0)

        callback = AsyncMock()
        settings = Mock()
        settings.get_setting = AsyncMock(return_value={"shazam_enabled": True})
        svc = ShazamRecognitionService(settings_service=settings, on_track_changed=callback)

        # Pin a stale track, then make every recognition attempt fail.
        svc._current_track = {"title": "Stale", "artist": "Old", "artwork": None}
        svc._running = True
        svc._stream_url = "http://x"

        calls = {"n": 0}

        async def failing_recognize():
            calls["n"] += 1
            # Stop the loop once the stale clear has had a chance to fire.
            if calls["n"] >= shazam_mod.STALE_CLEAR_ROUNDS + 1:
                svc._running = False
            return False

        svc._try_recognize = failing_recognize

        await asyncio.wait_for(svc._recognition_loop(), timeout=2.0)

        assert svc._current_track is None
        callback.assert_awaited_with(None)
