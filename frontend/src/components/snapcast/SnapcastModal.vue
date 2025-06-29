<!-- frontend/src/components/snapcast/SnapcastModal.vue - Navigation locale complète -->
<template>
  <div class="snapcast-modal">
    <!-- Vue principale -->
    <div v-if="currentView === 'main'" class="view-main">
      <div class="modal-header">
        <h2 class="heading-2">Multiroom</h2>
        <div class="controls-wrapper">
          <IconButton v-if="isMultiroomActive" icon="settings" variant="dark" @click="showSettings"
            title="Configuration Multiroom" />
          <Toggle v-model="isMultiroomActive" variant="primary" :disabled="unifiedStore.isTransitioning"
            @change="handleMultiroomToggle" />
        </div>
      </div>

      <div class="main-content">
        <SnapcastControl @show-client-details="showClientDetails" />
      </div>
    </div>

    <!-- Vue Configuration -->
    <div v-else-if="currentView === 'settings'" class="view-settings">
      <div class="modal-header">
        <div class="back-modal-header">
          <IconButton icon="caretLeft" variant="dark" @click="goToMain" />
          <h2 class="heading-2">Configuration Multiroom</h2>
        </div>
      </div>
      <SnapcastSettings />
    </div>

    <!-- Vue Détails Client -->
    <div v-else-if="currentView === 'client-details'" class="view-client-details">
      <div class="modal-header">
        <div class="back-modal-header">
          <IconButton icon="caretLeft" variant="dark" @click="goToMain" />
          <h2 class="heading-2">{{ selectedClient?.name || 'Client' }}</h2>
        </div>
      </div>
      <SnapclientDetails v-if="selectedClient" :client="selectedClient" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SnapcastControl from './SnapcastControl.vue';
import SnapcastSettings from './SnapcastSettings.vue';
import SnapclientDetails from './SnapclientDetails.vue';

const unifiedStore = useUnifiedAudioStore();

// === NAVIGATION LOCALE ULTRA-SIMPLE ===
const currentView = ref('main'); // 'main', 'settings', 'client-details'
const selectedClient = ref(null);

// === GETTERS ===
const isMultiroomActive = computed(() => unifiedStore.multiroomEnabled);

// === NAVIGATION ACTIONS ===
function goToMain() {
  currentView.value = 'main';
  selectedClient.value = null;
}

function showSettings() {
  currentView.value = 'settings';
  selectedClient.value = null;
}

function showClientDetails(client) {
  console.log('🔍 Showing client details for:', client.name);
  selectedClient.value = client;
  currentView.value = 'client-details';
}

// === HANDLERS ===
async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

// === RESET AUTOMATIQUE ===
// Retourner au main quand le multiroom est désactivé
watch(isMultiroomActive, (enabled) => {
  if (!enabled && currentView.value !== 'main') {
    console.log('🔙 Multiroom deactivated, going back to main');
    goToMain();
  }
});

// Debug pour suivre les changements de vue
watch(currentView, (newView, oldView) => {
  console.log(`🖥️ Snapcast view changed: ${oldView} → ${newView}`);
  if (newView === 'client-details') {
    console.log('👤 Selected client:', selectedClient.value?.name);
  }
});
</script>

<style scoped>
.snapcast-modal {
  display: flex;
  flex-direction: column;
}

.view-main,
.view-settings,
.view-client-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.view-client-details .modal-header,
.view-settings .modal-header {
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-04);
}

.modal-header {
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h2 {
  color: var(--color-text-contrast);
}

.controls-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.main-content {
  flex: 1;
}


.back-modal-header {
  gap: var(--space-03);
  display: flex;
  align-items: center;
}
</style>