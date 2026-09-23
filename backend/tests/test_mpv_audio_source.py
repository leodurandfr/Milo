# backend/tests/test_mpv_audio_source.py
"""
Unit tests for MpvAudioSource's shared plumbing that no single source owns:
the attach every mpv source shares, and the auto-stop delay's reload. The
session-driven path (phases from mpv's events, the idle timeout) is covered by
tests/test_mpv_sessions.py, tests/test_cd_sessions.py and the golden scenarios.
"""
import logging

import pytest
from unittest.mock import AsyncMock, Mock, patch

from backend.shared.mpv import MpvController
from backend.sources.podcast.source import PodcastSource
from backend.sources.radio.source import RadioSource


@pytest.fixture
def radio_source():
    source = RadioSource({"mpv_socket": "/tmp/test-radio-ipc.sock"})
    source.auto_stop_enabled = True
    source.auto_stop_delay = 999.0
    return source


@pytest.fixture
def podcast_source():
    source = PodcastSource({"mpv_socket": "/tmp/test-podcast-ipc.sock"})
    source.auto_stop_enabled = True
    source.auto_stop_delay = 999.0
    return source


class TestAttach:
    """`_attach_mpv` is the four sources' only way onto the IPC socket."""

    @pytest.mark.asyncio
    async def test_a_failed_start_writes_one_line_and_not_two(
        self, podcast_source, caplog
    ):
        """The source used to add a line of its own to a failure already logged.

        connect() reports what it waited for and how long; "Failed to connect to
        MPV IPC" next to it says only that it happened, in the second of the two
        spellings the four sources had drifted into. Whoever reads a boot's
        errors.log gets one record per failure, or the count means nothing.
        """
        podcast_source._service_manager = Mock(start=AsyncMock(return_value=True))

        with patch.object(MpvController, "connect", AsyncMock(return_value=False)), \
             caplog.at_level(logging.DEBUG):
            assert await podcast_source._do_start() is False

        from_the_source = [r for r in caplog.records if r.name.startswith("source.")]
        assert from_the_source == []

    @pytest.mark.asyncio
    async def test_the_attach_answers_what_the_link_answered(self, radio_source):
        """No interpretation between connect()'s verdict and the caller's.

        The source is left holding the controller either way: a failed start is
        stopped by the state machine.
        """
        with patch.object(MpvController, "connect", AsyncMock(return_value=True)):
            assert await radio_source._attach_mpv() is True
        assert radio_source._mpv.ipc_socket_path == radio_source._mpv_socket


class TestReloadAutoStop:
    """reload_auto_stop_config refreshes the delay on mpv sources."""

    @pytest.mark.asyncio
    async def test_reload_disables_when_zero(self, radio_source):
        radio_source._settings_service = Mock()
        radio_source._settings_service.get_setting = AsyncMock(return_value=0)

        result = await radio_source.reload_auto_stop_config()

        assert result is True
        assert radio_source.auto_stop_enabled is False

    @pytest.mark.asyncio
    async def test_reload_updates_delay(self, podcast_source):
        podcast_source._settings_service = Mock()
        podcast_source._settings_service.get_setting = AsyncMock(return_value=45.0)

        result = await podcast_source.reload_auto_stop_config()

        assert result is True
        assert podcast_source.auto_stop_enabled is True
        assert podcast_source.auto_stop_delay == 45.0
