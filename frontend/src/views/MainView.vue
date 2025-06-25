<!-- frontend/src/views/MainView.vue - Vue principale OPTIM -->
<template>
  <div class="main-view">
    <div v-if="unifiedStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>

    <component v-else-if="currentComponent" :is="currentComponent" />

    <div v-else class="no-source">
      <h2>Aucune source active</h2>
      <p>Sélectionnez une source audio</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import LibrespotView from './LibrespotView.vue';
import BluetoothView from './BluetoothView.vue';
import RocView from './RocView.vue';

const unifiedStore = useUnifiedAudioStore();

// Composant dynamique selon la source active
const currentComponent = computed(() => {
  switch (unifiedStore.currentSource) {
    case 'librespot': return LibrespotView;
    case 'bluetooth': return BluetoothView;
    case 'roc': return RocView;
    default: return null;
  }
});
</script>

<style scoped>
.main-view {
  padding: 20px;
  width: 64%;
  margin: 0 auto;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.transition-state, .no-source {
  text-align: center;
  padding: 40px;
  color: #666;
  flex: 1;
}

.transition-state h2, .no-source h2 {
  margin: 0 0 16px 0;
  font-size: 20px;
}

.no-source p {
  margin: 0;
  font-size: 14px;
}
</style>