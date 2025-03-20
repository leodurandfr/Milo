<template>
  <div class="home-view">
    <div class="status-panel">
      <h1>oakOS</h1>

      <div class="current-state">
        <h2>Source actuelle: {{ audioStore.stateLabel }}</h2>
        <span v-if="audioStore.isTransitioning" class="transitioning-badge">Transition en cours...</span>
      </div>

      <div class="volume-control">
        <h3>Volume: {{ audioStore.volume }}%</h3>
        <input type="range" min="0" max="100" v-model.number="audioStore.volume" class="volume-slider">
      </div>

      <div class="error-message" v-if="audioStore.error">
        {{ audioStore.error }}
      </div>
    </div>

    <div class="source-buttons">
      <button @click="changeSource('librespot')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'librespot'" class="source-button spotify">
        Spotify
      </button>

      <button @click="changeSource('bluetooth')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'bluetooth'"
        class="source-button bluetooth">
        Bluetooth
      </button>

      <button @click="changeSource('macos')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'macos'" class="source-button macos">
        MacOS
      </button>

      <button @click="changeSource('webradio')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'webradio'" class="source-button webradio">
        Web Radio
      </button>
    </div>

    <!-- Composant modulaire d'affichage de la source active -->
    <SourceDisplay v-if="audioStore.currentState !== 'none'" />
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { useAudioStore } from '@/stores/index';
import useWebSocket from '@/services/websocket';
import SourceDisplay from '@/components/SourceDisplay.vue';

const audioStore = useAudioStore();
const { on } = useWebSocket();

// Changer la source audio
async function changeSource(source) {
  await audioStore.changeSource(source);
}

// Initialiser les données au montage du composant
onMounted(async () => {
  // Récupérer l'état initial
  await audioStore.fetchState();

  // S'abonner aux événements WebSocket
  on('audio_state_changed', (data) => {
    audioStore.handleWebSocketUpdate('audio_state_changed', data);
  });

  on('volume_changed', (data) => {
    audioStore.handleWebSocketUpdate('volume_changed', data);
  });

  on('audio_error', (data) => {
    audioStore.handleWebSocketUpdate('audio_error', data);
  });

  on('audio_metadata_updated', (data) => {
    audioStore.handleWebSocketUpdate('audio_metadata_updated', data);
  });

  on('audio_status_updated', (data) => {
    audioStore.handleWebSocketUpdate('audio_status_updated', data);
  });
  
  on('audio_seek', (data) => {
    audioStore.handleWebSocketUpdate('audio_seek', data);
  });
  
  // Événements spécifiques à Snapclient
  on('snapclient_connection_request', (data) => {
    audioStore.handleWebSocketUpdate('snapclient_connection_request', data);
  });
  
  on('snapclient_connection_rejected', (data) => {
    audioStore.handleWebSocketUpdate('snapclient_connection_rejected', data);
  });
});
</script>

<style scoped>
.home-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 20px;
}

.status-panel {
  margin-bottom: 2rem;
}

h1 {
  font-size: 2.5rem;
  margin-bottom: 1rem;
}

.current-state {
  display: flex;
  align-items: center;
  margin-bottom: 1.5rem;
}

.transitioning-badge {
  background-color: #ff9800;
  color: white;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  margin-left: 1rem;
  font-size: 0.8rem;
}

.volume-control {
  margin-bottom: 1.5rem;
}

.volume-slider {
  width: 100%;
  max-width: 400px;
}

.error-message {
  background-color: #f44336;
  color: white;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  margin-bottom: 1rem;
}

.source-buttons {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 1rem;
}

.source-button {
  background-color: #1976d2;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 1rem;
  font-size: 1.2rem;
  cursor: pointer;
  transition: background-color 0.3s;
}

.source-button:hover:not(:disabled) {
  background-color: #1565c0;
}

.source-button:disabled {
  background-color: #ccc;
  cursor: not-allowed;
}

/* Couleurs spécifiques à chaque source */
.spotify {
  background-color: #1DB954;
}

.bluetooth {
  background-color: #0082FC;
}

.macos {
  background-color: #7D7D7D;
}

.webradio {
  background-color: #FF5722;
}
</style>