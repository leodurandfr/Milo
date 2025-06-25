<!-- frontend/src/views/MultiroomView.vue - Version OPTIM ultra-simple -->
<template>
  <div class="multiroom-view">
    <!-- Toggle Multiroom avec IconButton intégré -->
    <div class="toggle-wrapper">
      <div class="toggle-header">
        <h3>Multiroom</h3>
        <div class="controls-wrapper">
          <IconButton
            v-if="isMultiroomActive"
            icon="⚙️"
            @click="showSettings = true"
          />
          <Toggle
            v-model="isMultiroomActive"
            :disabled="unifiedStore.isTransitioning"
            @change="handleMultiroomToggle"
          />
        </div>
      </div>
    </div>

    <!-- Contenu principal -->
    <div class="main-content">
      <SnapcastControl />
    </div>


    <!-- Modals -->
    <SnapcastSettings
      v-if="showSettings"
      @close="showSettings = false"
    />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SnapcastControl from '@/components/snapcast/SnapcastControl.vue';
import SnapcastSettings from '@/components/snapcast/SnapcastSettings.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État local ultra-simple
const showSettings = ref(false);

// État computed
const isMultiroomActive = computed(() => 
  unifiedStore.multiroomEnabled
);

// === GESTION TOGGLE ===

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

// Plus besoin de gestion complexe - SnapcastControl s'occupe de tout !
</script>

<style scoped>
.multiroom-view {
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Toggle wrapper */
.toggle-wrapper {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
}

.toggle-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toggle-header h3 {
  margin: 0;
  color: #333;
  font-size: 16px;
}

.controls-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* Contenu principal */
.main-content {
  flex: 1;
  margin-bottom: 20px;
}

/* Responsive */
@media (max-width: 600px) {
  .controls-wrapper {
    gap: 8px;
  }
}
</style>