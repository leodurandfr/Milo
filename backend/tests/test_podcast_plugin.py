# backend/tests/test_podcast_plugin.py
"""
Unit tests for the PodcastPlugin
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.plugins.podcast.plugin import PodcastPlugin
from backend.domain.audio_state import PluginState, AudioSource


class TestPodcastPlugin:
    """Tests for the Podcast plugin"""

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.update_plugin_state = AsyncMock()
        sm.system_state = Mock()
        sm.system_state.active_source = AudioSource.PODCAST
        sm.system_state.plugin_state = PluginState.READY
        sm.system_state.metadata = {}
        return sm

    @pytest.fixture
    def mock_settings_service(self):
        """Settings service mock"""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def plugin_config(self):
        """Plugin configuration"""
        return {
            'service_name': 'milo-podcast.service',
            'ipc_socket': '/run/milo/podcast-ipc.sock',
            'taddy_user_id': '3671',
            'taddy_api_key': 'test_api_key'
        }

    @pytest.fixture
    def plugin(self, plugin_config, mock_state_machine, mock_settings_service):
        """Fixture to create a Podcast plugin"""
        with patch('backend.infrastructure.plugins.podcast.plugin.PodcastDataService') as mock_pds, \
             patch('backend.infrastructure.plugins.podcast.plugin.MpvController') as mock_mpv, \
             patch('backend.infrastructure.plugins.podcast.plugin.TaddyAPI') as mock_taddy:

            # Mock PodcastDataService
            pds_instance = Mock()
            pds_instance.get_playback_progress = AsyncMock(return_value=None)
            pds_instance.update_playback_progress = AsyncMock()
            pds_instance.clear_playback_progress = AsyncMock()
            pds_instance.cache_episode = AsyncMock()
            pds_instance.set_setting = AsyncMock()
            mock_pds.return_value = pds_instance

            # Mock MpvController
            mpv_instance = Mock()
            mpv_instance.connect = AsyncMock(return_value=True)
            mpv_instance.disconnect = AsyncMock()
            mpv_instance.is_connected = True
            mpv_instance.stop = AsyncMock(return_value=True)
            mpv_instance.load_stream = AsyncMock(return_value=True)
            mpv_instance.is_playing = AsyncMock(return_value=False)
            mpv_instance.pause = AsyncMock()
            mpv_instance.resume = AsyncMock()
            mpv_instance.seek = AsyncMock()
            mpv_instance.get_property = AsyncMock(return_value=None)
            mpv_instance.set_property = AsyncMock()
            mock_mpv.return_value = mpv_instance

            # Mock TaddyAPI
            taddy_instance = Mock()
            taddy_instance.get_episode = AsyncMock(return_value={
                'uuid': 'episode-123',
                'name': 'Test Episode',
                'description': 'A test episode',
                'audio_url': 'http://example.com/episode.mp3',
                'image_url': 'http://example.com/image.jpg',
                'duration': 3600,
                'podcast': {
                    'uuid': 'podcast-456',
                    'name': 'Test Podcast'
                }
            })
            taddy_instance.close = AsyncMock()
            mock_taddy.return_value = taddy_instance

            plugin = PodcastPlugin(
                config=plugin_config,
                state_machine=mock_state_machine,
                settings_service=mock_settings_service
            )

            # Override the created instances with our mocks
            plugin.mpv = mpv_instance
            plugin.taddy_api = taddy_instance
            plugin.podcast_data_service = pds_instance

            # Mock service_manager
            plugin.service_manager = Mock()
            plugin.service_manager.is_active = AsyncMock(return_value=True)

            # Mock control_service
            plugin.control_service = AsyncMock(return_value=True)

            # Mock notify_state_change
            plugin.notify_state_change = AsyncMock()

            # Mock _monitor_playback to avoid infinite loops
            async def mock_monitor():
                pass
            plugin._monitor_playback = mock_monitor

            # Mock _periodic_progress_save
            async def mock_progress_save():
                pass
            plugin._periodic_progress_save = mock_progress_save

            return plugin

    # ===================
    # INITIALIZATION TESTS
    # ===================

    def test_initialization_config(self, plugin_config, mock_state_machine, mock_settings_service):
        """Test basic plugin initialization"""
        with patch('backend.infrastructure.plugins.podcast.plugin.PodcastDataService'), \
             patch('backend.infrastructure.plugins.podcast.plugin.MpvController'), \
             patch('backend.infrastructure.plugins.podcast.plugin.TaddyAPI'):

            plugin = PodcastPlugin(
                config=plugin_config,
                state_machine=mock_state_machine,
                settings_service=mock_settings_service
            )

            assert plugin.name == "podcast"
            assert plugin.service_name == "milo-podcast.service"
            assert plugin.current_episode is None
            assert plugin._is_playing is False
            assert plugin._playback_speed == 1.0

    @pytest.mark.asyncio
    async def test_do_initialize_success(self, plugin):
        """Test successful initialization"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'milo-podcast.service enabled', b''))
            mock_process.wait = AsyncMock()
            mock_exec.return_value = mock_process

            result = await plugin._do_initialize()

            assert result is True

    @pytest.mark.asyncio
    async def test_do_initialize_service_not_found(self, plugin):
        """Test initialization with service not found"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'not found'))
            mock_exec.return_value = mock_process

            result = await plugin._do_initialize()

            assert result is False

    # ===================
    # START TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_do_start_success(self, plugin):
        """Test successful startup"""
        result = await plugin._do_start()

        assert result is True
        plugin.control_service.assert_called_with(plugin.service_name, "start")
        plugin.mpv.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_start_service_failure(self, plugin):
        """Test startup with service failure"""
        plugin.control_service = AsyncMock(return_value=False)

        result = await plugin._do_start()

        assert result is False

    @pytest.mark.asyncio
    async def test_do_start_mpv_connection_failure(self, plugin):
        """Test startup with mpv connection failure"""
        plugin.mpv.connect = AsyncMock(return_value=False)

        result = await plugin._do_start()

        assert result is False

    # ===================
    # STOP TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_stop_success(self, plugin):
        """Test successful stop"""
        plugin.current_episode = {"uuid": "test", "name": "Test"}
        plugin._is_playing = True

        result = await plugin.stop()

        assert result is True
        assert plugin.current_episode is None
        assert plugin._is_playing is False
        plugin.notify_state_change.assert_called_with(PluginState.INACTIVE)

    # ===================
    # PLAYBACK TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_play_episode_success(self, plugin):
        """Test episode playback"""
        result = await plugin.play_episode("episode-123")

        assert result is True
        plugin.taddy_api.get_episode.assert_called_once_with("episode-123")
        plugin.mpv.load_stream.assert_called_once()
        assert plugin.current_episode is not None
        assert plugin._is_playing is True

    @pytest.mark.asyncio
    async def test_play_episode_not_found(self, plugin):
        """Test playback with episode not found"""
        plugin.taddy_api.get_episode = AsyncMock(return_value=None)

        result = await plugin.play_episode("unknown")

        assert result is False

    @pytest.mark.asyncio
    async def test_play_episode_no_audio_url(self, plugin):
        """Test playback without audio URL"""
        plugin.taddy_api.get_episode = AsyncMock(return_value={
            'uuid': 'test',
            'name': 'Test',
            'audio_url': None
        })

        result = await plugin.play_episode("test")

        assert result is False

    @pytest.mark.asyncio
    async def test_play_episode_load_stream_failure(self, plugin):
        """Test playback with stream loading failure"""
        plugin.mpv.load_stream = AsyncMock(return_value=False)

        result = await plugin.play_episode("episode-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_play_episode_with_resume(self, plugin):
        """Test playback with resume position"""
        plugin.podcast_data_service.get_playback_progress = AsyncMock(return_value={
            'position': 300,
            'duration': 3600
        })

        # Mock duration available for seek
        plugin.mpv.get_property = AsyncMock(return_value=3600)

        result = await plugin.play_episode("episode-123")

        assert result is True
        plugin.mpv.seek.assert_called_once_with(300)

    # ===================
    # PAUSE/RESUME TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_pause(self, plugin):
        """Test pause"""
        plugin.current_episode = {"uuid": "test", "podcast": {"uuid": "podcast-1", "name": "Test"}}
        plugin._is_playing = True
        plugin._current_position = 100  # > 0 for save_progress to be called

        result = await plugin.pause()

        assert result is True
        assert plugin._is_playing is False
        plugin.mpv.pause.assert_called_once()
        plugin.podcast_data_service.update_playback_progress.assert_called()

    @pytest.mark.asyncio
    async def test_resume(self, plugin):
        """Test resume"""
        plugin.current_episode = {"uuid": "test"}
        plugin._is_playing = False

        result = await plugin.resume()

        assert result is True
        assert plugin._is_playing is True
        plugin.mpv.resume.assert_called_once()

    # ===================
    # SEEK TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_seek(self, plugin):
        """Test seek"""
        plugin.current_episode = {"uuid": "test"}

        result = await plugin.seek(300)

        assert result is True
        assert plugin._current_position == 300
        plugin.mpv.seek.assert_called_once_with(300)

    # ===================
    # SPEED TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_set_speed_valid(self, plugin):
        """Test valid speed change"""
        plugin.current_episode = {"uuid": "test"}

        result = await plugin.set_speed(1.5)

        assert result is True
        assert plugin._playback_speed == 1.5
        plugin.mpv.set_property.assert_called_with("speed", 1.5)

    @pytest.mark.asyncio
    async def test_set_speed_invalid_rounds_to_nearest(self, plugin):
        """Test invalid speed change - rounds to nearest valid"""
        plugin.current_episode = {"uuid": "test"}

        result = await plugin.set_speed(1.3)  # Not in valid speeds

        assert result is True
        # Should round to nearest valid (1.25)
        assert plugin._playback_speed == 1.25

    # ===================
    # COMMAND HANDLING TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_handle_command_play_episode(self, plugin):
        """Test play_episode command"""
        result = await plugin.handle_command("play_episode", {"episode_uuid": "episode-123"})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_handle_command_play_episode_no_uuid(self, plugin):
        """Test play_episode command without uuid"""
        result = await plugin.handle_command("play_episode", {})

        assert result["success"] is False
        assert "episode_uuid required" in result["error"]

    @pytest.mark.asyncio
    async def test_handle_command_pause(self, plugin):
        """Test pause command"""
        plugin.current_episode = {"uuid": "test"}
        plugin._is_playing = True

        result = await plugin.handle_command("pause", {})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_handle_command_resume(self, plugin):
        """Test resume command"""
        plugin.current_episode = {"uuid": "test"}
        plugin._is_playing = False

        result = await plugin.handle_command("resume", {})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_handle_command_seek(self, plugin):
        """Test seek command"""
        plugin.current_episode = {"uuid": "test"}

        result = await plugin.handle_command("seek", {"position": 300})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_handle_command_seek_no_position(self, plugin):
        """Test seek command without position"""
        result = await plugin.handle_command("seek", {})

        assert result["success"] is False
        assert "position required" in result["error"]

    @pytest.mark.asyncio
    async def test_handle_command_stop(self, plugin):
        """Test stop command"""
        plugin.current_episode = {"uuid": "test"}
        plugin._is_playing = True

        result = await plugin.handle_command("stop", {})

        assert result["success"] is True
        assert plugin.current_episode is None
        assert plugin._is_playing is False

    @pytest.mark.asyncio
    async def test_handle_command_set_speed(self, plugin):
        """Test set_speed command"""
        plugin.current_episode = {"uuid": "test"}

        result = await plugin.handle_command("set_speed", {"speed": 1.5})

        assert result["success"] is True
        assert result["speed"] == 1.5

    @pytest.mark.asyncio
    async def test_handle_command_set_speed_no_speed(self, plugin):
        """Test set_speed command without speed"""
        result = await plugin.handle_command("set_speed", {})

        assert result["success"] is False
        assert "speed required" in result["error"]

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self, plugin):
        """Test unknown command"""
        result = await plugin.handle_command("unknown_command", {})

        assert result["success"] is False
        assert "Unknown command" in result["error"]

    # ===================
    # STATUS TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_get_status(self, plugin):
        """Test get_status"""
        plugin.current_episode = {"uuid": "test", "name": "Test Episode"}
        plugin._is_playing = True
        plugin._current_position = 100
        plugin._current_duration = 3600

        status = await plugin.get_status()

        assert status["is_playing"] is True
        assert status["position"] == 100
        assert status["duration"] == 3600
        assert status["current_episode"]["name"] == "Test Episode"

    # ===================
    # RELOAD CREDENTIALS TEST
    # ===================

    @pytest.mark.asyncio
    async def test_reload_credentials(self, plugin):
        """Test credentials reload"""
        # Keep a reference to the old taddy_api to verify it was closed
        old_taddy_api = plugin.taddy_api

        with patch('backend.infrastructure.plugins.podcast.plugin.TaddyAPI') as mock_taddy:
            new_taddy = Mock()
            mock_taddy.return_value = new_taddy

            result = await plugin.reload_credentials("new_user", "new_key")

            assert result is True
            old_taddy_api.close.assert_called_once()
            # Verify that the new TaddyAPI was created
            mock_taddy.assert_called_once_with(
                user_id="new_user",
                api_key="new_key",
                cache_duration_minutes=60
            )

    # ===================
    # METADATA BUILD TEST
    # ===================

    def test_build_metadata(self, plugin):
        """Test metadata building"""
        plugin.current_episode = {
            'uuid': 'episode-123',
            'name': 'Test Episode',
            'description': 'Description',
            'image_url': 'http://example.com/img.jpg',
            'podcast': {
                'uuid': 'podcast-456',
                'name': 'Test Podcast'
            }
        }
        plugin._is_playing = True
        plugin._is_buffering = False
        plugin._current_position = 100
        plugin._current_duration = 3600
        plugin._playback_speed = 1.5

        metadata = plugin._build_metadata()

        assert metadata["episode_uuid"] == "episode-123"
        assert metadata["episode_name"] == "Test Episode"
        assert metadata["is_playing"] is True
        assert metadata["is_buffering"] is False
        assert metadata["position"] == 100
        assert metadata["duration"] == 3600
        assert metadata["playback_speed"] == 1.5
        assert metadata["podcast_name"] == "Test Podcast"

    def test_build_metadata_no_episode(self, plugin):
        """Test metadata building without episode"""
        plugin.current_episode = None

        metadata = plugin._build_metadata()

        assert metadata == {}
