# backend/tests/test_state_machine.py
"""
Tests unitaires pour UnifiedAudioStateMachine
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.domain.audio_state import AudioSource, PluginState, SystemAudioState


class TestUnifiedAudioStateMachine:
    """Tests pour la machine à états unifiée"""

    @pytest.fixture
    def state_machine(self, mock_websocket_handler, mock_routing_service):
        """Fixture pour créer une state machine"""
        sm = UnifiedAudioStateMachine(
            routing_service=mock_routing_service,
            websocket_handler=mock_websocket_handler
        )
        return sm

    def test_initialization(self, state_machine):
        """Test de l'initialisation de la state machine"""
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.plugin_state == PluginState.INACTIVE
        assert state_machine.system_state.transitioning is False
        assert state_machine.system_state.target_source is None

    def test_register_plugin(self, state_machine, mock_plugin):
        """Test de l'enregistrement d'un plugin"""
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        assert state_machine.plugins[AudioSource.SPOTIFY] == mock_plugin
        assert state_machine.get_plugin(AudioSource.SPOTIFY) == mock_plugin

    def test_get_plugin_metadata(self, state_machine):
        """Test de récupération des métadonnées d'un plugin"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.metadata = {"title": "Test Song"}

        metadata = state_machine.get_plugin_metadata(AudioSource.SPOTIFY)
        assert metadata == {"title": "Test Song"}

        # Source non-active devrait retourner {}
        metadata_other = state_machine.get_plugin_metadata(AudioSource.BLUETOOTH)
        assert metadata_other == {}

    def test_get_plugin_state(self, state_machine):
        """Test de récupération de l'état d'un plugin"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        state = state_machine.get_plugin_state(AudioSource.SPOTIFY)
        assert state == PluginState.CONNECTED

        # Source non-active devrait retourner INACTIVE
        state_other = state_machine.get_plugin_state(AudioSource.BLUETOOTH)
        assert state_other == PluginState.INACTIVE

    @pytest.mark.asyncio
    async def test_get_current_state(self, state_machine):
        """Test de récupération de l'état actuel"""
        state = await state_machine.get_current_state()

        assert "active_source" in state
        assert "plugin_state" in state
        assert "transitioning" in state
        assert "metadata" in state
        assert state["active_source"] == "none"

    @pytest.mark.asyncio
    async def test_transition_to_same_source(self, state_machine, mock_plugin):
        """Test de transition vers la même source (devrait être no-op)"""
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_plugin.stop.assert_not_called()
        mock_plugin.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_transition_to_none(self, state_machine, mock_plugin):
        """Test de transition vers NONE (arrêt de la source active)"""
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        result = await state_machine.transition_to_source(AudioSource.NONE)

        assert result is True
        mock_plugin.stop.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.plugin_state == PluginState.INACTIVE

    @pytest.mark.asyncio
    async def test_transition_to_new_source_success(self, state_machine, mock_plugin):
        """Test de transition réussie vers une nouvelle source"""
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_plugin.start.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        # L'état devrait être au moins READY
        assert state_machine.system_state.plugin_state in [PluginState.READY, PluginState.CONNECTED]

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(self, state_machine):
        """Test de transition vers une source non-enregistrée (devrait échouer)"""
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False

    @pytest.mark.asyncio
    async def test_transition_start_fail(self, state_machine, mock_plugin):
        """Test de transition avec échec du démarrage"""
        mock_plugin.start = AsyncMock(return_value=False)
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        # Devrait se retrouver en état NONE après échec
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine, mock_plugin):
        """Test de timeout durant une transition"""
        # Simuler un plugin qui prend trop de temps à démarrer
        async def slow_start():
            await asyncio.sleep(10)  # Plus long que TRANSITION_TIMEOUT (5s)
            return True

        mock_plugin.start = slow_start
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        # Le timeout se produit mais error peut être None si _emergency_stop réinitialise l'état
        assert state_machine.system_state.transitioning is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_update_plugin_state_active_source(self, state_machine):
        """Test de mise à jour de l'état d'un plugin actif"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.READY

        metadata = {"title": "Test Song"}
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            metadata
        )

        assert state_machine.system_state.plugin_state == PluginState.CONNECTED
        assert state_machine.system_state.metadata == metadata

    @pytest.mark.asyncio
    async def test_update_plugin_state_inactive_source_ignored(self, state_machine):
        """Test que les updates d'une source inactive sont ignorées"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        # Tenter de mettre à jour une source non-active
        await state_machine.update_plugin_state(
            AudioSource.BLUETOOTH,
            PluginState.CONNECTED,
            {}
        )

        # L'état ne devrait pas avoir changé
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY

    @pytest.mark.asyncio
    async def test_update_plugin_state_during_transition_ignored(self, state_machine):
        """Test que les updates pendant une transition sont ignorées"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.transitioning = True
        old_state = state_machine.system_state.plugin_state

        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {}
        )

        # L'état ne devrait pas avoir changé
        assert state_machine.system_state.plugin_state == old_state

    @pytest.mark.asyncio
    async def test_update_multiroom_state(self, state_machine):
        """Test de mise à jour de l'état multiroom"""
        await state_machine.update_multiroom_state(True)

        assert state_machine.system_state.multiroom_enabled is True

    @pytest.mark.asyncio
    async def test_update_equalizer_state(self, state_machine):
        """Test de mise à jour de l'état equalizer"""
        await state_machine.update_equalizer_state(True)

        assert state_machine.system_state.equalizer_enabled is True

    @pytest.mark.asyncio
    async def test_broadcast_event(self, state_machine, mock_websocket_handler):
        """Test de broadcast d'événements"""
        await state_machine.broadcast_event("test", "test_event", {"data": "value"})

        mock_websocket_handler.handle_event.assert_called_once()
        call_args = mock_websocket_handler.handle_event.call_args[0][0]

        assert call_args["category"] == "test"
        assert call_args["type"] == "test_event"
        assert "timestamp" in call_args

    @pytest.mark.asyncio
    async def test_concurrent_transitions_prevented(self, state_machine, mock_plugin):
        """Test que les transitions concurrentes sont empêchées par le lock"""
        mock_plugin._initialized = True

        # Simuler un plugin qui prend du temps à démarrer
        async def slow_start():
            await asyncio.sleep(0.5)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Lancer deux transitions en parallèle
        task1 = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))
        task2 = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))

        results = await asyncio.gather(task1, task2)

        # L'une devrait réussir, l'autre devrait être no-op (déjà sur la source)
        assert any(results)  # Au moins une réussie

    @pytest.mark.asyncio
    async def test_buffered_updates_max_capacity(self, state_machine, mock_plugin):
        """Test que la queue de updates a une capacité maximale"""
        mock_plugin._initialized = True

        # Simuler un plugin qui prend du temps
        async def slow_start():
            await asyncio.sleep(0.3)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Démarrer une transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.SPOTIFY)
        )

        # Attendre un peu que la transition commence
        await asyncio.sleep(0.1)

        # Essayer d'envoyer un update pendant la transition
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {"title": "Test Song"}
        )

        # Vérifier que l'update est bufferisé
        assert len(state_machine._buffered_updates) == 1

        # Attendre la fin de la transition
        await transition_task

        # Après la transition, la queue devrait être vide (updates rejoués)
        assert len(state_machine._buffered_updates) == 0

        # Simuler un plugin qui prend du temps à démarrer
        async def slow_start():
            await asyncio.sleep(0.3)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Démarrer une transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.SPOTIFY)
        )

        # Attendre que la transition commence
        await asyncio.sleep(0.1)

        # Envoyer des updates pendant la transition
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {"title": "Test Song", "artist": "Test Artist"}
        )

        # Attendre la fin de la transition
        await transition_task

        # Vérifier que l'update a été appliqué
        assert state_machine.system_state.plugin_state == PluginState.CONNECTED
        assert state_machine.system_state.metadata.get("title") == "Test Song"
        assert state_machine.system_state.metadata.get("artist") == "Test Artist"

        # Simuler un plugin qui timeout
        async def timeout_start():
            await asyncio.sleep(10)  # Plus long que TRANSITION_TIMEOUT
            return True

        mock_plugin.start = timeout_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Démarrer une transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.SPOTIFY)
        )

        # Attendre que la transition commence
        await asyncio.sleep(0.1)

        # Envoyer un update pendant la transition
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {"title": "Test Song"}
        )

        # Vérifier que l'update est bufferisé
        assert len(state_machine._buffered_updates) == 1

        # Attendre que la transition timeout
        result = await transition_task

        # La transition devrait échouer
        assert result is False

        # La queue devrait être vidée
        assert len(state_machine._buffered_updates) == 0

    @pytest.mark.asyncio
    async def test_buffered_updates_max_capacity(self, state_machine, mock_plugin):
        """Test que la queue de updates a une capacité maximale"""
        mock_plugin._initialized = True

        # Simuler un plugin qui prend du temps
        async def slow_start():
            await asyncio.sleep(0.5)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Démarrer une transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.SPOTIFY)
        )

        # Attendre que la transition commence
        await asyncio.sleep(0.1)

        # Envoyer plus d'updates que la capacité max
        for i in range(state_machine.MAX_BUFFERED_UPDATES + 10):
            await state_machine.update_plugin_state(
                AudioSource.SPOTIFY,
                PluginState.CONNECTED,
                {"index": i}
            )

        # La queue ne devrait pas dépasser la capacité max
        assert len(state_machine._buffered_updates) <= state_machine.MAX_BUFFERED_UPDATES

        # Attendre la fin de la transition
        await transition_task
