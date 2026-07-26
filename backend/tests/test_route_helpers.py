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

from backend.api.route_helpers import api_error_handler
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
