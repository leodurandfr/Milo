# backend/tests/test_equalizer_service.py
"""
Unit tests for EqualizerService
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.services.equalizer_service import EqualizerService


class TestEqualizerService:
    """Tests for the equalizer service"""

    @pytest.fixture
    def mock_settings_service(self):
        """Mock of SettingsService"""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def service(self, mock_settings_service):
        """Fixture to create an EqualizerService"""
        return EqualizerService(settings_service=mock_settings_service)

    @pytest.fixture
    def service_no_settings(self):
        """Fixture to create a service without SettingsService"""
        return EqualizerService(settings_service=None)

    def test_initialization(self, service):
        """Service initialization test"""
        assert service.settings_service is not None
        assert len(service.BANDS) == 10
        assert service.BANDS[0]["id"] == "00"
        assert service.BANDS[0]["freq"] == "31 Hz"
        assert service.BANDS[9]["id"] == "09"
        assert service.BANDS[9]["freq"] == "16 kHz"

    def test_bands_configuration(self, service):
        """Bands configuration test"""
        # Check that all bands have id and freq
        for band in service.BANDS:
            assert "id" in band
            assert "freq" in band

    def test_format_frequency_display_khz(self, service):
        """kHz frequency formatting test"""
        assert service._format_frequency_display("1 kHz") == "1K"
        assert service._format_frequency_display("2 kHz") == "2K"
        assert service._format_frequency_display("16 kHz") == "16K"

    def test_format_frequency_display_hz(self, service):
        """Hz frequency formatting test"""
        assert service._format_frequency_display("31 Hz") == "31"
        assert service._format_frequency_display("125 Hz") == "125"
        assert service._format_frequency_display("500 Hz") == "500"

    def test_format_frequency_display_edge_cases(self, service):
        """Formatting test with spaces and edge cases"""
        assert service._format_frequency_display("  1 kHz  ") == "1K"
        assert service._format_frequency_display("  125 Hz  ") == "125"
        assert service._format_frequency_display("unknown") == "unknown"

    def test_parse_amixer_output_valid(self, service):
        """Valid amixer output parsing test"""
        # Simulated amixer output
        amixer_output = """Simple mixer control '00. 31 Hz',0
  Capabilities: pvolume pvolume-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 100
  Mono:
  Front Left: Playback 66 [66%] [0.00dB]
  Front Right: Playback 66 [66%] [0.00dB]
Simple mixer control '01. 63 Hz',0
  Capabilities: pvolume pvolume-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 100
  Mono:
  Front Left: Playback 75 [75%] [0.00dB]
  Front Right: Playback 75 [75%] [0.00dB]
"""

        bands = service._parse_amixer_output(amixer_output)

        assert len(bands) == 2
        assert bands[0]["id"] == "00"
        assert bands[0]["freq"] == "31 Hz"
        assert bands[0]["value"] == 66
        assert bands[0]["display_name"] == "31"

        assert bands[1]["id"] == "01"
        assert bands[1]["freq"] == "63 Hz"
        assert bands[1]["value"] == 75
        assert bands[1]["display_name"] == "63"

    def test_parse_amixer_output_empty(self, service):
        """Empty output parsing test"""
        bands = service._parse_amixer_output("")
        assert bands == []

    def test_parse_amixer_output_malformed(self, service):
        """Malformed output parsing test"""
        malformed_output = "Invalid output\nNo valid data"
        bands = service._parse_amixer_output(malformed_output)
        assert bands == []

    @pytest.mark.asyncio
    async def test_get_all_bands_success(self, service):
        """Successful retrieval of all bands test"""
        amixer_output = b"""Simple mixer control '00. 31 Hz',0
  Front Left: Playback 66 [66%] [0.00dB]
Simple mixer control '01. 63 Hz',0
  Front Left: Playback 70 [70%] [0.00dB]
"""

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(amixer_output, b''))
            mock_exec.return_value = mock_process

            bands = await service.get_all_bands()

            assert len(bands) == 2
            assert bands[0]["value"] == 66
            assert bands[1]["value"] == 70

    @pytest.mark.asyncio
    async def test_get_all_bands_amixer_error(self, service):
        """Bands retrieval test with amixer error"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'amixer error'))
            mock_exec.return_value = mock_process

            bands = await service.get_all_bands()

            assert bands == []

    @pytest.mark.asyncio
    async def test_get_all_bands_exception(self, service):
        """Bands retrieval test with exception"""
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Process failed")):
            bands = await service.get_all_bands()

            assert bands == []

    @pytest.mark.asyncio
    async def test_set_band_value_success(self, service):
        """Successful band modification test"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'success', b''))
            mock_exec.return_value = mock_process

            result = await service.set_band_value("00", 75)

            assert result is True
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "amixer" in args
            assert "75%" in args

    @pytest.mark.asyncio
    async def test_set_band_value_invalid_value_too_low(self, service):
        """Modification test with value too low"""
        result = await service.set_band_value("00", -10)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_invalid_value_too_high(self, service):
        """Modification test with value too high"""
        result = await service.set_band_value("00", 150)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_invalid_band_id(self, service):
        """Modification test with invalid band_id"""
        result = await service.set_band_value("99", 50)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_amixer_error(self, service):
        """Modification test with amixer error"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'amixer error'))
            mock_exec.return_value = mock_process

            result = await service.set_band_value("00", 75)

            assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_exception(self, service):
        """Modification test with exception"""
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Process failed")):
            result = await service.set_band_value("00", 75)

            assert result is False

    @pytest.mark.asyncio
    async def test_reset_all_bands_success(self, service):
        """Successful reset of all bands test"""
        with patch.object(service, 'set_band_value', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            result = await service.reset_all_bands(66)

            assert result is True
            assert mock_set.call_count == 10  # 10 bands
            # Check that all bands were called with 66
            for call in mock_set.call_args_list:
                assert call[0][1] == 66

    @pytest.mark.asyncio
    async def test_reset_all_bands_default_value(self, service):
        """Reset test with default value (66)"""
        with patch.object(service, 'set_band_value', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            result = await service.reset_all_bands()

            assert result is True
            # Check the default value
            for call in mock_set.call_args_list:
                assert call[0][1] == 66

    @pytest.mark.asyncio
    async def test_reset_all_bands_partial_failure(self, service):
        """Reset test with partial failure"""
        call_count = 0

        async def mock_set_band(band_id, value):
            nonlocal call_count
            call_count += 1
            # First 5 succeed, next 5 fail
            return call_count <= 5

        with patch.object(service, 'set_band_value', side_effect=mock_set_band):
            result = await service.reset_all_bands(66)

            assert result is False  # Not all successful

    @pytest.mark.asyncio
    async def test_reset_all_bands_exception(self, service):
        """Reset test with exception"""
        with patch.object(service, 'set_band_value', side_effect=Exception("Error")):
            result = await service.reset_all_bands(66)

            assert result is False

    @pytest.mark.asyncio
    async def test_is_available_true(self, service):
        """Availability test when equalizer is available"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'', b''))
            mock_exec.return_value = mock_process

            result = await service.is_available()

            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_false(self, service):
        """Availability test when equalizer is not available"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'error'))
            mock_exec.return_value = mock_process

            result = await service.is_available()

            assert result is False

    @pytest.mark.asyncio
    async def test_is_available_exception(self, service):
        """Availability test with exception"""
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Error")):
            result = await service.is_available()

            assert result is False

    @pytest.mark.asyncio
    async def test_save_current_bands_success(self, service, mock_settings_service):
        """Successful bands save test"""
        mock_bands = [
            {"id": "00", "freq": "31 Hz", "value": 66, "display_name": "31"},
            {"id": "01", "freq": "63 Hz", "value": 75, "display_name": "63"}
        ]

        with patch.object(service, 'get_all_bands', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_bands

            result = await service.save_current_bands()

            assert result is True
            mock_settings_service.set_setting.assert_called_once()
            args = mock_settings_service.set_setting.call_args[0]
            assert args[0] == 'equalizer.saved_bands'
            # Check that only id and value are saved
            saved_data = args[1]
            assert len(saved_data) == 2
            assert saved_data[0] == {"id": "00", "value": 66}
            assert saved_data[1] == {"id": "01", "value": 75}

    @pytest.mark.asyncio
    async def test_save_current_bands_no_bands(self, service, mock_settings_service):
        """Save test without available bands"""
        with patch.object(service, 'get_all_bands', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            result = await service.save_current_bands()

            assert result is False
            mock_settings_service.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_current_bands_no_settings_service(self, service_no_settings):
        """Save test without SettingsService"""
        result = await service_no_settings.save_current_bands()

        assert result is False

    @pytest.mark.asyncio
    async def test_save_current_bands_exception(self, service):
        """Save test with exception"""
        with patch.object(service, 'get_all_bands', side_effect=Exception("Error")):
            result = await service.save_current_bands()

            assert result is False

    @pytest.mark.asyncio
    async def test_restore_saved_bands_success(self, service, mock_settings_service):
        """Successful bands restore test"""
        saved_bands = [
            {"id": "00", "value": 66},
            {"id": "01", "value": 75}
        ]
        mock_settings_service.get_setting = AsyncMock(return_value=saved_bands)

        with patch.object(service, 'set_band_value', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            result = await service.restore_saved_bands()

            assert result is True
            assert mock_set.call_count == 2
            mock_set.assert_any_call("00", 66)
            mock_set.assert_any_call("01", 75)

    @pytest.mark.asyncio
    async def test_restore_saved_bands_no_saved_data(self, service, mock_settings_service):
        """Restore test without saved data"""
        mock_settings_service.get_setting = AsyncMock(return_value=None)

        result = await service.restore_saved_bands()

        # Not an error, just nothing to restore
        assert result is True

    @pytest.mark.asyncio
    async def test_restore_saved_bands_partial_failure(self, service, mock_settings_service):
        """Restore test with partial failure"""
        saved_bands = [
            {"id": "00", "value": 66},
            {"id": "01", "value": 75}
        ]
        mock_settings_service.get_setting = AsyncMock(return_value=saved_bands)

        call_count = 0

        async def mock_set_band(band_id, value):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # First succeeds, second fails

        with patch.object(service, 'set_band_value', side_effect=mock_set_band):
            result = await service.restore_saved_bands()

            assert result is False

    @pytest.mark.asyncio
    async def test_restore_saved_bands_no_settings_service(self, service_no_settings):
        """Restore test without SettingsService"""
        result = await service_no_settings.restore_saved_bands()

        assert result is False

    @pytest.mark.asyncio
    async def test_restore_saved_bands_exception(self, service, mock_settings_service):
        """Restore test with exception"""
        mock_settings_service.get_setting = AsyncMock(side_effect=Exception("Error"))

        result = await service.restore_saved_bands()

        assert result is False

    @pytest.mark.asyncio
    async def test_get_equalizer_status_available(self, service):
        """Status retrieval test when available"""
        mock_bands = [
            {"id": "00", "freq": "31 Hz", "value": 66, "display_name": "31"}
        ]

        with patch.object(service, 'is_available', new_callable=AsyncMock) as mock_avail:
            with patch.object(service, 'get_all_bands', new_callable=AsyncMock) as mock_get:
                mock_avail.return_value = True
                mock_get.return_value = mock_bands

                status = await service.get_equalizer_status()

                assert status["available"] is True
                assert len(status["bands"]) == 1
                assert status["band_count"] == 1

    @pytest.mark.asyncio
    async def test_get_equalizer_status_not_available(self, service):
        """Status retrieval test when not available"""
        with patch.object(service, 'is_available', new_callable=AsyncMock) as mock_avail:
            mock_avail.return_value = False

            status = await service.get_equalizer_status()

            assert status["available"] is False
            assert status["bands"] == []
            assert "message" in status

    @pytest.mark.asyncio
    async def test_get_equalizer_status_exception(self, service):
        """Status retrieval test with exception"""
        with patch.object(service, 'is_available', side_effect=Exception("Error")):
            status = await service.get_equalizer_status()

            assert status["available"] is False
            assert status["bands"] == []
            assert "error" in status
