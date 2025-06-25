<!-- frontend/src/components/snapcast/SnapcastModal.vue -->
<template>
  <div class="snapcast-modal">
    <!-- Écran principal -->
    <div v-if="modalStore.currentScreen === 'main'" class="screen-main">
      <!-- Toggle Multiroom avec IconButton intégré -->
      <div class="toggle-wrapper">
        <div class="toggle-header">
          <h3>Configuration Multiroom</h3>
          <div class="controls-wrapper">
            <IconButton
              v-if="isMultiroomActive"
              icon="⚙️"
              @click="modalStore.openSnapcastSettings()"
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
    </div>

    <!-- Écran Settings -->
    <div v-else-if="modalStore.currentScreen === 'settings'" class="screen-settings">
      <SnapcastSettings />
    </div>

    <!-- Écran Client Details -->
    <div v-else-if="modalStore.currentScreen === 'client-details'" class="screen-client-details">
      <SnapclientDetails :client="modalStore.selectedClient" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useModalStore } from '@/stores/modalStore';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SnapcastControl from './SnapcastControl.vue';
import SnapcastSettings from './SnapcastSettings.vue';
import SnapclientDetails from './SnapclientDetails.vue';

const unifiedStore = useUnifiedAudioStore();
const modalStore = useModalStore();

// État computed
const isMultiroomActive = computed(() => 
  unifiedStore.multiroomEnabled
);

// === GESTION TOGGLE ===
async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}
</script>

<style scoped>
.snapcast-modal {
  display: flex;
  flex-direction: column;
}

/* Écrans */
.screen-main,
.screen-settings,
.screen-client-details {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* Toggle wrapper */
.toggle-wrapper {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 8px;
  padding: 16px;
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
}

/* Responsive */
@media (max-width: 600px) {
  .controls-wrapper {
    gap: 8px;
  }
}
</style>