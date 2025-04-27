<template>
  <div class="home-view">
    <div class="status-panel">
      <h1>oakOS</h1>

      <div class="current-state">
        <h2>Source actuelle: {{ audioStore.stateLabel }}</h2>
      </div>

      <div class="error-message" v-if="audioStore.error">
        {{ audioStore.error }}
      </div>
    </div>

    <div class="source-buttons">
      <button @click="changeSource('librespot')"
        :disabled="audioStore.isTransitioning || audioStore.currentSource === 'librespot'" 
        class="source-button spotify">
        Spotify
      </button>

      <button @click="changeSource('bluetooth')"
        :disabled="audioStore.isTransitioning || audioStore.currentSource === 'bluetooth'"
        class="source-button bluetooth">
        Bluetooth
      </button>

      <button @click="changeSource('snapclient')"
        :disabled="audioStore.isTransitioning || audioStore.currentSource === 'snapclient'" 
        class="source-button macos">
        MacOS
      </button>

      <button @click="changeSource('webradio')"
        :disabled="audioStore.isTransitioning || audioStore.currentSource === 'webradio'" 
        class="source-button webradio">
        Web Radio
      </button>
    </div>

    <div v-if="audioStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>
    <template v-else-if="audioStore.currentSource !== 'none' && audioStore.pluginState !== 'inactive'">
      <LibrespotDisplay v-if="audioStore.currentSource === 'librespot'" />
      <SnapclientComponent v-else-if="audioStore.currentSource === 'snapclient'" />
      <div v-else class="no-source-error">
        <h2>Source non disponible</h2>
        <p>La source audio "{{ audioStore.currentSource }}" n'est pas disponible ou n'est pas encore implémentée.</p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { useAudioStore } from '@/stores/audioStore';
import useWebSocket from '@/services/websocket';

import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import SnapclientComponent from '@/components/sources/snapclient/SnapclientComponent.vue';

const audioStore = useAudioStore();
const { on } = useWebSocket();

async function changeSource(source) {
  await audioStore.changeSource(source);
}

onMounted(async () => {
  // Récupérer l'état initial
  await audioStore.fetchSystemState();

  // S'abonner aux mises à jour d'état
  on('state_update', (data) => {
    audioStore.handleWebSocketUpdate(data);
  });
});
</script>

<style scoped>
/* Styles inchangés */
</style>