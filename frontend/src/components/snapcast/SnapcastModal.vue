<!-- frontend/src/components/snapcast/SnapcastModal.vue -->
<template>
  <div class="snapcast-modal">
    <!-- Écran principal -->
    <div v-if="modalStore.currentScreen === 'main'" class="screen-main">
      <!-- Toggle Multiroom avec IconButton intégré -->
      <div class="toggle-wrapper">
        <div class="toggle-header">
          <h3>Multiroom</h3>
          <div class="controls-wrapper">
            <IconButton
              v-if="isMultiroomActive"
              icon="⚙️"
              @click="modalStore.openSnapcastSettings()"
              title="Paramètres Snapcast"
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
      <SnapclientDetails 
        v-if="modalStore.selectedClient" 
        :client="modalStore.selectedClient" 
      />
      <div v-else class="error-state">
        <p>Aucun client sélectionné</p>
        <button @click="modalStore.goBack()" class="back-btn">Retour</button>
      </div>
    </div>

    <!-- Écran de fallback -->
    <div v-else class="screen-fallback">
      <p>Écran inconnu: {{ modalStore.currentScreen }}</p>
      <button @click="modalStore.goToScreen('main')" class="back-btn">Retour à l'accueil</button>
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

// Debug pour vérifier les changements d'écran
import { watch } from 'vue';
watch(() => modalStore.currentScreen, (newScreen, oldScreen) => {
  console.log(`🖥️ Modal screen changed: ${oldScreen} → ${newScreen}`);
  if (newScreen === 'client-details') {
    console.log('👤 Selected client:', modalStore.selectedClient?.name);
  }
});
</script>

<style scoped>
.snapcast-modal {
  display: flex;
  flex-direction: column;
}

/* Écrans */
.screen-main,
.screen-settings,
.screen-client-details,
.screen-fallback {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Toggle wrapper */
.toggle-wrapper {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 16px;
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

/* États d'erreur */
.error-state {
  text-align: center;
  padding: 40px 20px;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 16px;
}

.error-state p {
  margin: 0 0 16px 0;
  color: #666;
}

.back-btn {
  padding: 8px 16px;
  background: #6c757d;
  color: white;
  border: none;
  cursor: pointer;
  border-radius: 4px;
}

.back-btn:hover {
  background: #545b62;
}

/* Responsive */
@media (max-width: 600px) {
  .controls-wrapper {
    gap: 8px;
  }
}
</style>