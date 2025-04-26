<template>
  <div class="home-view">
    <div class="status-panel">
      <h1>oakOS</h1>

      <div class="current-state">
        <h2>Source actuelle: {{ audioStore.stateLabel }}</h2>
      </div>

      <div class="volume-control">
        <h3>Volume: {{ audioStore.volume }}%</h3>
        <input type="range" min="0" max="100" v-model.number="audioStore.volume" 
               @change="updateVolume" class="volume-slider">
      </div>

      <div class="error-message" v-if="audioStore.error">
        {{ audioStore.error }}
      </div>
    </div>

    <div class="source-buttons">
      <button @click="changeSource('librespot')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'librespot'" 
        class="source-button spotify">
        Spotify
      </button>

      <button @click="changeSource('bluetooth')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'bluetooth'"
        class="source-button bluetooth">
        Bluetooth
      </button>

      <button @click="changeSource('snapclient')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'snapclient'" 
        class="source-button macos">
        MacOS
      </button>

      <button @click="changeSource('webradio')"
        :disabled="audioStore.isTransitioning || audioStore.currentState === 'webradio'" 
        class="source-button webradio">
        Web Radio
      </button>
    </div>

    <div v-if="audioStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>
    <template v-else-if="audioStore.currentState !== 'none' && audioStore.pluginState !== 'inactive'">
      <LibrespotDisplay v-if="audioStore.currentState === 'librespot'" />
      <SnapclientComponent v-else-if="audioStore.currentState === 'snapclient'" />
      <div v-else class="no-source-error">
        <h2>Source non disponible</h2>
        <p>La source audio "{{ audioStore.currentState }}" n'est pas disponible ou n'est pas encore implémentée.</p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useLibrespotStore } from '@/stores/librespot';
import useWebSocket from '@/services/websocket';

import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import SnapclientComponent from '@/components/sources/snapclient/SnapclientComponent.vue';

const audioStore = useAudioStore();
const librespotStore = useLibrespotStore();
const { on } = useWebSocket();

async function changeSource(source) {
  if (audioStore.currentState === 'librespot' && source !== 'librespot') {
    librespotStore.clearState();
  }
  
  await audioStore.changeSource(source);
}

async function updateVolume() {
  await audioStore.setVolume(audioStore.volume);
}

onMounted(async () => {
  await audioStore.fetchState();

  // Écouter les nouveaux événements unifiés
  on('transition_completed', (data) => {
    audioStore.handleWebSocketUpdate('transition_completed', data);
  });

  on('transition_started', (data) => {
    audioStore.handleWebSocketUpdate('transition_started', data);
  });

  on('plugin_state_changed', (data) => {
    audioStore.handleWebSocketUpdate('plugin_state_changed', data);
  });

  on('volume_changed', (data) => {
    audioStore.handleWebSocketUpdate('volume_changed', data);
  });

  on('transition_error', (data) => {
    audioStore.handleWebSocketUpdate('transition_error', data);
  });
});
</script>

<style scoped>
/* Styles inchangés */
</style>