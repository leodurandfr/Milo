# backend/tests/test_equalizer_service.py
"""
Tests unitaires pour EqualizerService
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.services.equalizer_service import EqualizerService


class TestEqualizerService:
    """Tests pour le service equalizer"""

    @pytest.fixture
    def mock_settings_service(self):
        """Mock du SettingsService"""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def service(self, mock_settings_service):
        """Fixture pour créer un EqualizerService"""
        return EqualizerService(settings_service=mock_settings_service)

    @pytest.fixture
    def service_no_settings(self):
        """Fixture pour créer un service sans SettingsService"""
        return EqualizerService(settings_service=None)

    def test_initialization(self, service):
        """Test de l'initialisation du service"""
        assert service.settings_service is not None
        assert len(service.BANDS) == 10
        assert service.BANDS[0]["id"] == "00"
        assert service.BANDS[0]["freq"] == "31 Hz"
        assert service.BANDS[9]["id"] == "09"
        assert service.BANDS[9]["freq"] == "16 kHz"

    def test_bands_configuration(self, service):
        """Test de la configuration des bandes"""
        # Vérifier que toutes les bandes ont id et freq
        for band in service.BANDS:
            assert "id" in band
            assert "freq" in band

    def test_format_frequency_display_khz(self, service):
        """Test du formatage des fréquences kHz"""
        assert service._format_frequency_display("1 kHz") == "1K"
        assert service._format_frequency_display("2 kHz") == "2K"
        assert service._format_frequency_display("16 kHz") == "16K"

    def test_format_frequency_display_hz(self, service):
        """Test du formatage des fréquences Hz"""
        assert service._format_frequency_display("31 Hz") == "31"
        assert service._format_frequency_display("125 Hz") == "125"
        assert service._format_frequency_display("500 Hz") == "500"

    def test_format_frequency_display_edge_cases(self, service):
        """Test du formatage avec espaces et cas edge"""
        assert service._format_frequency_display("  1 kHz  ") == "1K"
        assert service._format_frequency_display("  125 Hz  ") == "125"
        assert service._format_frequency_display("unknown") == "unknown"

    def test_parse_amixer_output_valid(self, service):
        """Test du parsing d'une sortie amixer valide"""
        # Sortie simulée d'amixer
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
        """Test du parsing d'une sortie vide"""
        bands = service._parse_amixer_output("")
        assert bands == []

    def test_parse_amixer_output_malformed(self, service):
        """Test du parsing d'une sortie malformée"""
        malformed_output = "Invalid output\nNo valid data"
        bands = service._parse_amixer_output(malformed_output)
        assert bands == []

    @pytest.mark.asyncio
    async def test_get_all_bands_success(self, service):
        """Test de récupération de toutes les bandes avec succès"""
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
        """Test de récupération des bandes avec erreur amixer"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'amixer error'))
            mock_exec.return_value = mock_process

            bands = await service.get_all_bands()

            assert bands == []

    @pytest.mark.asyncio
    async def test_get_all_bands_exception(self, service):
        """Test de récupération des bandes avec exception"""
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Process failed")):
            bands = await service.get_all_bands()

            assert bands == []

    @pytest.mark.asyncio
    async def test_set_band_value_success(self, service):
        """Test de modification d'une bande avec succès"""
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
        """Test de modification avec valeur trop basse"""
        result = await service.set_band_value("00", -10)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_invalid_value_too_high(self, service):
        """Test de modification avec valeur trop haute"""
        result = await service.set_band_value("00", 150)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_invalid_band_id(self, service):
        """Test de modification avec band_id invalide"""
        result = await service.set_band_value("99", 50)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_amixer_error(self, service):
        """Test de modification avec erreur amixer"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'amixer error'))
            mock_exec.return_value = mock_process

            result = await service.set_band_value("00", 75)

            assert result is False

    @pytest.mark.asyncio
    async def test_set_band_value_exception(self, service):
        """Test de modification avec exception"""
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Process failed")):
            result = await service.set_band_value("00", 75)

            assert result is False

    @pytest.mark.asyncio
    async def test_reset_all_bands_success(self, service):
        """Test de reset de toutes les bandes avec succès"""
        with patch.object(service, 'set_band_value', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            result = await service.reset_all_bands(66)

            assert result is True
            assert mock_set.call_count == 10  # 10 bandes
            # Vérifier que toutes les bandes ont été appelées avec 66
            for call in mock_set.call_args_list:
                assert call[0][1] == 66

    @pytest.mark.asyncio
    async def test_reset_all_bands_default_value(self, service):
        """Test de reset avec valeur par défaut (66)"""
        with patch.object(service, 'set_band_value', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            result = await service.reset_all_bands()

            assert result is True
            # Vérifier la valeur par défaut
            for call in mock_set.call_args_list:
                assert call[0][1] == 66

    @pytest.mark.asyncio
    async def test_reset_all_bands_partial_failure(self, service):
        """Test de reset avec échec partiel"""
        call_count = 0

        async def mock_set_band(band_id, value):
            nonlocal call_count
            call_count += 1
            # Les 5 premières réussissent, les 5 suivantes échouent
            return call_count <= 5

        with patch.object(service, 'set_band_value', side_effect=mock_set_band):
            result = await service.reset_all_bands(66)

            assert result is False  # Pas toutes réussies

    @pytest.mark.asyncio
    async def test_reset_all_bands_exception(self, service):
        """Test de reset avec exception"""
        with patch.object(service, 'set_band_value', side_effect=Exception("Error")):
            result = await service.reset_all_bands(66)

            assert result is False

    @pytest.mark.asyncio
    async def test_is_available_true(self, service):
        """Test de disponibilité quand l'equalizer est disponible"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'', b''))
            mock_exec.return_value = mock_process

            result = await service.is_available()

            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_false(self, service):
        """Test de disponibilité quand l'equalizer n'est pas disponible"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'error'))
            mock_exec.return_value = mock_process

            result = await service.is_available()

            assert result is False

    @pytest.mark.asyncio
    async def test_is_available_exception(self, service):
        """Test de disponibilité avec exception"""
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Error")):
            result = await service.is_available()

            assert result is False

    @pytest.mark.asyncio
    async def test_save_current_bands_success(self, service, mock_settings_service):
        """Test de sauvegarde des bandes avec succès"""
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
            # Vérifier que seulement id et value sont sauvegardés
            saved_data = args[1]
            assert len(saved_data) == 2
            assert saved_data[0] == {"id": "00", "value": 66}
            assert saved_data[1] == {"id": "01", "value": 75}

    @pytest.mark.asyncio
    async def test_save_current_bands_no_bands(self, service, mock_settings_service):
        """Test de sauvegarde sans bandes disponibles"""
        with patch.object(service, 'get_all_bands', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            result = await service.save_current_bands()

            assert result is False
            mock_settings_service.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_current_bands_no_settings_service(self, service_no_settings):
        """Test de sauvegarde sans SettingsService"""
        result = await service_no_settings.save_current_bands()

        assert result is False

    @pytest.mark.asyncio
    async def test_save_current_bands_exception(self, service):
        """Test de sauvegarde avec exception"""
        with patch.object(service, 'get_all_bands', side_effect=Exception("Error")):
            result = await service.save_current_bands()

            assert result is False

    @pytest.mark.asyncio
    async def test_restore_saved_bands_success(self, service, mock_settings_service):
        """Test de restauration des bandes avec succès"""
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
        """Test de restauration sans données sauvegardées"""
        mock_settings_service.get_setting = AsyncMock(return_value=None)

        result = await service.restore_saved_bands()

        # Pas une erreur, juste rien à restaurer
        assert result is True

    @pytest.mark.asyncio
    async def test_restore_saved_bands_partial_failure(self, service, mock_settings_service):
        """Test de restauration avec échec partiel"""
        saved_bands = [
            {"id": "00", "value": 66},
            {"id": "01", "value": 75}
        ]
        mock_settings_service.get_setting = AsyncMock(return_value=saved_bands)

        call_count = 0

        async def mock_set_band(band_id, value):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # Première réussit, deuxième échoue

        with patch.object(service, 'set_band_value', side_effect=mock_set_band):
            result = await service.restore_saved_bands()

            assert result is False

    @pytest.mark.asyncio
    async def test_restore_saved_bands_no_settings_service(self, service_no_settings):
        """Test de restauration sans SettingsService"""
        result = await service_no_settings.restore_saved_bands()

        assert result is False

    @pytest.mark.asyncio
    async def test_restore_saved_bands_exception(self, service, mock_settings_service):
        """Test de restauration avec exception"""
        mock_settings_service.get_setting = AsyncMock(side_effect=Exception("Error"))

        result = await service.restore_saved_bands()

        assert result is False

    @pytest.mark.asyncio
    async def test_get_equalizer_status_available(self, service):
        """Test de récupération du status quand disponible"""
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
        """Test de récupération du status quand non disponible"""
        with patch.object(service, 'is_available', new_callable=AsyncMock) as mock_avail:
            mock_avail.return_value = False

            status = await service.get_equalizer_status()

            assert status["available"] is False
            assert status["bands"] == []
            assert "message" in status

    @pytest.mark.asyncio
    async def test_get_equalizer_status_exception(self, service):
        """Test de récupération du status avec exception"""
        with patch.object(service, 'is_available', side_effect=Exception("Error")):
            status = await service.get_equalizer_status()

            assert status["available"] is False
            assert status["bands"] == []
            assert "error" in status
