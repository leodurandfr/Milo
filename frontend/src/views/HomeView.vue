<!-- frontend/src/views/HomeView.vue -->
<template>
  <div class="home-view">
    <div class="status-panel">
      <h1>oakOS</h1>
      <h2>Source: {{ sourceLabel }}</h2>
      <div v-if="unifiedStore.error" class="error-message">
        {{ unifiedStore.error }}
      </div>
    </div>

    <div v-if="unifiedStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>

    <component v-else-if="currentComponent" :is="currentComponent" />

    <!-- Navigation en bas (composant réutilisable) -->
    <BottomNavigation />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import BluetoothDisplay from '@/components/sources/bluetooth/BluetoothDisplay.vue';
import RocDisplay from '@/components/sources/roc/RocDisplay.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const sources = [
  { id: 'librespot', label: 'Spotify' },
  { id: 'bluetooth', label: 'Bluetooth' },
  { id: 'roc', label: 'ROC for Mac' }
];

const sourceLabel = computed(() => {
  const source = sources.find(s => s.id === unifiedStore.currentSource);
  return source ? source.label : 'Aucune';
});

const currentComponent = computed(() => {
  switch (unifiedStore.currentSource) {
    case 'librespot': return LibrespotDisplay;
    case 'bluetooth': return BluetoothDisplay;
    case 'roc': return RocDisplay;
    default: return null;
  }
});

onMounted(() => {
  on('system', 'state_changed', (event) => {
    unifiedStore.updateState(event);
  });

  on('system', 'transition_start', (event) => {
    unifiedStore.updateState(event);
  });

  on('system', 'transition_complete', (event) => {
    unifiedStore.updateState(event);
  });

  on('system', 'error', (event) => {
    unifiedStore.updateState(event);
  });

  on('plugin', 'plugin_state_changed', (event) => {
    unifiedStore.updateState(event);
  });

  on('plugin', 'plugin_metadata', (event) => {
    unifiedStore.updateState(event);
  });
});
</script>

<style scoped>
.home-view {
  padding: 20px;
  width: 462px;
  margin: 0 auto;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.status-panel {
  background: white;
  border: 1px solid #ddd;
  padding: 20px;
  margin-bottom: 20px;
  text-align: center;
}

.status-panel h1 {
  margin: 0 0 10px 0;
  font-size: 28px;
}

.status-panel h2 {
  margin: 0;
  color: #666;
  font-size: 18px;
  font-weight: normal;
}

.error-message {
  background: #ffe6e6;
  color: #d00;
  padding: 10px;
  margin-top: 10px;
}

.transition-state {
  text-align: center;
  padding: 40px;
  color: #666;
  flex: 1;
}

.transition-state h2 {
  margin: 0;
  font-size: 20px;
}
</style>