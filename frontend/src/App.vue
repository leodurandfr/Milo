<!-- App.vue - Version avec i18n WebSocket -->
<template>
  <div class="app-container">
    <!-- DEBUG OVERLAY - Timer Screen -->
    <div v-if="showDebug" class="screen-debug-overlay">
      <div class="debug-title">Screen Timer Debug</div>
      <div class="debug-row">
        <span>Timer:</span>
        <span>{{ debugInfo.time_since_activity }}s / {{ debugInfo.timeout_seconds }}s</span>
      </div>
      <div class="debug-row">
        <span>Extinction:</span>
        <span :class="{ 'warning': debugInfo.will_turn_off_in < 3 }">
          {{ debugInfo.will_turn_off_in?.toFixed(1) }}s
        </span>
      </div>
      <div class="debug-row">
        <span>Plugin:</span>
        <span>{{ debugInfo.current_plugin_state }}</span>
      </div>
      <div class="debug-row">
        <span>Écran:</span>
        <span>{{ debugInfo.screen_on ? '✅' : '❌' }}</span>
      </div>

      <!-- Séparateur -->
      <div class="debug-separator"></div>

      <!-- Timer Spotify -->
      <div class="debug-subtitle">Spotify Disconnect</div>
      <div class="debug-row">
        <span>Timer actif:</span>
        <span>{{ spotifyDebugInfo.timer_active ? '✅' : '❌' }}</span>
      </div>
      <div v-if="spotifyDebugInfo.timer_active" class="debug-row">
        <span>Déconnexion dans:</span>
        <span :class="{ 'warning': spotifyDebugInfo.time_remaining < 3 }">
          {{ spotifyDebugInfo.time_remaining }}s / {{ spotifyDebugInfo.total_delay }}s
        </span>
      </div>
    </div>

    <router-view />
    <VolumeBar ref="volumeBar" />
    <BottomNavigation
      @open-snapcast="isSnapcastOpen = true"
      @open-equalizer="isEqualizerOpen = true"
      @open-settings="isSettingsOpen = true"

    />

    <Modal :is-open="isSnapcastOpen" height-mode="auto" @close="isSnapcastOpen = false">
      <SnapcastModal />
    </Modal>

    <Modal :is-open="isEqualizerOpen" height-mode="fixed" @close="isEqualizerOpen = false">
      <EqualizerModal />
    </Modal>

    <Modal :is-open="isSettingsOpen" height-mode="fixed" @close="isSettingsOpen = false">
      <SettingsModal />
    </Modal>

  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, provide } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';
import Modal from '@/components/ui/Modal.vue';
import SnapcastModal from '@/components/snapcast/SnapcastModal.vue';
import EqualizerModal from '@/components/equalizer/EqualizerModal.vue';
import SettingsModal from '@/components/settings/SettingsModal.vue';

import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';

const unifiedStore = useUnifiedAudioStore();
const { on, onReconnect } = useWebSocket();

const volumeBar = ref(null);
const isSnapcastOpen = ref(false);
const isEqualizerOpen = ref(false);
const isSettingsOpen = ref(false);

// DEBUG - Screen timer
const showDebug = ref(true); // Mettre à false pour désactiver
const debugInfo = ref({
  time_since_activity: 0,
  timeout_seconds: 10,
  screen_on: true,
  current_plugin_state: 'inactive',
  will_turn_off_in: 10
});

// DEBUG - Spotify disconnect timer
const spotifyDebugInfo = ref({
  timer_active: false,
  time_remaining: 0,
  total_delay: 10
});

// Provide pour les composants enfants
provide('openSnapcast', () => isSnapcastOpen.value = true);
provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('openSettings', () => isSettingsOpen.value = true);
provide('closeModals', () => {
  isSnapcastOpen.value = false;
  isEqualizerOpen.value = false;
  isSettingsOpen.value = false;

});

const cleanupFunctions = [];

onMounted(() => {
  // Configuration initiale
  unifiedStore.setVolumeBarRef(volumeBar);
  
  // Setup des listeners
  const visibilityCleanup = unifiedStore.setupVisibilityListener();
  cleanupFunctions.push(visibilityCleanup);
  
  // Événements WebSocket - Audio
  cleanupFunctions.push(
    on('volume', 'volume_changed', (event) => unifiedStore.handleVolumeEvent(event)),
    on('system', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_start', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_complete', (event) => unifiedStore.updateState(event)),
    on('system', 'error', (event) => unifiedStore.updateState(event)),
    on('plugin', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('plugin', 'metadata', (event) => unifiedStore.updateState(event))
  );

  // Événements WebSocket - i18n
  cleanupFunctions.push(
    on('settings', 'language_changed', (event) => {
      console.log('Received language_changed WebSocket event:', event);
      if (event.data?.language) {
        console.log(`Changing language to: ${event.data.language}`);
        i18n.handleLanguageChanged(event.data.language);
      } else {
        console.error('Language_changed event missing language data:', event);
      }
    })
  );

  // Événements WebSocket - Screen Debug
  cleanupFunctions.push(
    on('screen', 'debug_update', (event) => {
      if (event.data) {
        debugInfo.value = { ...event.data };
      }
    })
  );

  // Événements WebSocket - Spotify Disconnect Timer Debug
  cleanupFunctions.push(
    on('spotify', 'disconnect_timer_update', (event) => {
      if (event.data) {
        spotifyDebugInfo.value = { ...event.data };
      }
    })
  );

  // Reconnexion WebSocket
  cleanupFunctions.push(
    onReconnect(() => {
      console.log('WebSocket reconnected - full state sync incoming');
    })
  );

  // État initial
  unifiedStore.refreshState();
});

onUnmounted(() => {
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  height: 100%;
}

/* DEBUG OVERLAY - Simple CSS */
.screen-debug-overlay {
  position: fixed;
  top: 10px;
  right: 10px;
  background: rgba(0, 0, 0, 0.9);
  color: #fff;
  padding: 12px;
  border-radius: 8px;
  font-family: monospace;
  font-size: 12px;
  z-index: 9999;
  min-width: 250px;
}

.debug-title {
  font-weight: bold;
  margin-bottom: 8px;
  border-bottom: 1px solid #444;
  padding-bottom: 4px;
}

.debug-subtitle {
  font-weight: bold;
  margin-top: 4px;
  margin-bottom: 6px;
  font-size: 11px;
  color: #aaa;
}

.debug-separator {
  border-top: 1px solid #333;
  margin: 8px 0;
}

.debug-row {
  display: flex;
  justify-content: space-between;
  margin: 4px 0;
}

.debug-row span:first-child {
  color: #888;
}

.debug-row span:last-child {
  color: #4CAF50;
  font-weight: bold;
}

.warning {
  color: #ff5722 !important;
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>