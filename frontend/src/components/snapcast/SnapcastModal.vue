<!-- frontend/src/components/snapcast/SnapcastModal.vue - Navigation locale complète -->
<template>
  <div class="snapcast-modal">
    <!-- Vue principale -->
    <div v-if="currentView === 'main'" class="view-main">
      <div class="toggle-wrapper">
        <div class="toggle-header">
          <h3>Multiroom</h3>
          <div class="controls-wrapper">
          <IconButton
            v-if="isMultiroomActive"
            icon="settings"
            variant="dark"  
            @click="showSettings"
            title="Configuration Multiroom"
          />
          <Toggle
            v-model="isMultiroomActive"
            variant="primary"
            :disabled="unifiedStore.isTransitioning"
            @change="handleMultiroomToggle"
          />
          </div>
        </div>
      </div>

      <div class="main-content">
        <SnapcastControl @show-client-details="showClientDetails" />
      </div>
    </div>

    <!-- Vue Configuration -->
    <div v-else-if="currentView === 'settings'" class="view-settings">
      <div class="view-header">
        <button @click="goToMain" class="back-btn">←</button>
        <h3>Configuration Multiroom</h3>
      </div>
      <SnapcastSettings />
    </div>

    <!-- Vue Détails Client -->
    <div v-else-if="currentView === 'client-details'" class="view-client-details">
      <div class="view-header">
        <button @click="goToMain" class="back-btn">←</button>
        <h3>{{ selectedClient?.name || 'Client' }}</h3>
      </div>
      <SnapclientDetails 
        v-if="selectedClient" 
        :client="selectedClient" 
      />
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

.toggle-wrapper {
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-04);
}

.toggle-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toggle-header h3 {
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

.view-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 16px;
}

.back-btn {
  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  padding: 8px;
  border-radius: 4px;
  color: #666;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  transition: all 0.2s;
}

.back-btn:hover {
  background: #e9ecef;
  color: #333;
}

.view-header h3 {
  margin: 0;
  color: #333;
  font-size: 16px;
  flex: 1;
}
</style>