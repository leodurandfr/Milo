# backend/tests/test_route_helpers.py
"""
Unit tests for backend/api/route_helpers.py.

Covers the log level api_error_handler picks for a satellite failure. If this
fails, every EQ, volume or crossover write aimed at an offline satellite is
logged at ERROR, and WebSocketLogHandler (level=ERROR) turns each one into a
backend-error banner in the UI — for a speaker the frontend already renders as
offline from the registry.
"""
import logging

import pytest
from fastapi import HTTPException

from backend.api.route_helpers import api_error_handler, coerce_audio_source_or_none
from backend.core.models.audio_state import AudioSource
from backend.core.equalizer.client_proxy import SatelliteUnreachable


class TestApiErrorHandlerSatelliteLogLevel:
    """Tests for api_error_handler's SatelliteUnreachable branch."""

    @pytest.mark.asyncio
    async def test_unreachable_satellite_is_a_warning(self, caplog):
        """503 = the satellite did not answer: expected state, no error banner."""
        logger = logging.getLogger("test.route_helpers")

        with caplog.at_level(logging.DEBUG, logger="test.route_helpers"):
            with pytest.raises(HTTPException) as exc:
                async with api_error_handler("Error setting volume", logger):
                    raise SatelliteUnreachable("192.168.1.60", "Cannot reach client", status_code=503)

        assert exc.value.status_code == 503
        assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_satellite_that_answered_and_refused_is_an_error(self, caplog):
        """Any other status means the satellite replied or the proxy broke — a real failure."""
        logger = logging.getLogger("test.route_helpers")

        with caplog.at_level(logging.DEBUG, logger="test.route_helpers"):
            with pytest.raises(HTTPException):
                async with api_error_handler("Error setting volume", logger):
                    raise SatelliteUnreachable("192.168.1.60", "boom", status_code=500)

        assert any(r.levelno == logging.ERROR for r in caplog.records)


class TestCoerceAudioSourceOrNone:
    """`coerce_audio_source_or_none` — the defensive read of a stored source name.

    Consumers: every route that reports which source is active from state
    (`api/audio.py`, `api/routing.py`, the WS full-state payload). It is the
    trusted-input twin of `parse_audio_source`, which raises a 400 instead.

    What breaks when this fails: replaced by its neutral it answers None for
    every name, so a unit playing Spotify reports no active source at all —
    and the whole suite stayed green on that, which is why it is here.
    """

    def test_a_known_name_becomes_its_enum(self):
        # NONE's own value is the absence sentinel, handled by the test below.
        member = next(m for m in AudioSource if m.value != "none")
        assert coerce_audio_source_or_none(member.value) is member

    @pytest.mark.parametrize("empty", [None, "", "none"])
    def test_the_absence_sentinels_answer_none(self, empty):
        assert coerce_audio_source_or_none(empty) is None

    def test_a_name_no_enum_member_carries_answers_none_and_says_so(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert coerce_audio_source_or_none("gramophone") is None
        assert "gramophone" in caplog.text, (
            "a broken upstream must stay visible; silence is how it survives"
        )
