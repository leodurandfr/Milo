<!-- frontend/src/views/HomeView.vue - Handler WebSocket corrigé -->
<template>
  <div class="home-view">


    <div v-if="unifiedStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>

    <component v-else-if="currentComponent" :is="currentComponent" />

    <!-- Navigation en bas (composant réutilisable) -->
    <BottomNavigation />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import BluetoothDisplay from '@/components/sources/bluetooth/BluetoothDisplay.vue';
import RocDisplay from '@/components/sources/roc/RocDisplay.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// Références pour nettoyage
let unsubscribeFunctions = [];

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
  // Abonnements WebSocket système
  const systemSubscriptions = [
    on('system', 'state_changed', (event) => {
      unifiedStore.updateState(event);
    }),
    on('system', 'transition_start', (event) => {
      unifiedStore.updateState(event);
    }),
    on('system', 'transition_complete', (event) => {
      unifiedStore.updateState(event);
    }),
    on('system', 'error', (event) => {
      unifiedStore.updateState(event);
    })
  ];

  // Abonnements WebSocket plugins
  const pluginSubscriptions = [
    on('plugin', 'state_changed', (event) => {
      unifiedStore.updateState(event);
    }),
    on('plugin', 'metadata', (event) => {
      unifiedStore.updateState(event);
    })
  ];

  unsubscribeFunctions.push(...systemSubscriptions, ...pluginSubscriptions);
});

onUnmounted(() => {
  // Nettoyer tous les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
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