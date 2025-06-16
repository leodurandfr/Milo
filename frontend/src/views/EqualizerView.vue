<!-- frontend/src/views/EqualizerView.vue - Version refactorisée -->
<template>
  <div class="equalizer-view">
    <!-- En-tête -->
    <div class="equalizer-header">
      <h1>Equalizer</h1>
    </div>

    <!-- Toggle Equalizer - Composant séparé -->
    <EqualizerToggle />

    <!-- Contenu principal -->
    <div class="main-content">
      <div v-if="!isEqualizerEnabled" class="equalizer-disabled">
        <h2>Equalizer désactivé</h2>
        <p>Activez l'equalizer pour accéder aux réglages audio.</p>
      </div>

      <div v-else class="equalizer-active">
        <h2>Configuration de l'equalizer</h2>
        
        <!-- Informations sur l'equalizer -->
        <div class="equalizer-info">
          <div class="info-item">
            <strong>Status:</strong> Actif
          </div>
          <div class="info-item">
            <strong>Source active:</strong> {{ activeSourceLabel }}
          </div>
          <div class="info-item">
            <strong>Device audio:</strong> {{ currentDevicePattern }}
          </div>
        </div>

        <!-- Placeholder pour les futurs contrôles -->
        <div class="equalizer-controls">
          <div class="placeholder-message">
            <h3>Contrôles de l'equalizer</h3>
            <p>Les réglages des bandes de fréquences seront disponibles ici.</p>
            <p>Utilisez <code>alsamixer -D equal</code> en attendant.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Navigation en bas -->
    <BottomNavigation />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import EqualizerToggle from '@/components/routing/EqualizerToggle.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

// État computed
const isEqualizerEnabled = computed(() => 
  unifiedStore.equalizerEnabled
);

const activeSourceLabel = computed(() => {
  const sources = {
    'librespot': 'Spotify',
    'bluetooth': 'Bluetooth',
    'roc': 'ROC for Mac',
    'none': 'Aucune'
  };
  return sources[unifiedStore.currentSource] || unifiedStore.currentSource;
});

const currentDevicePattern = computed(() => {
  const source = unifiedStore.currentSource;
  if (source === 'none') return 'N/A';
  
  let device = `oakos_${source === 'librespot' ? 'spotify' : source}`;
  
  if (unifiedStore.multiroomEnabled) {  // Refactorisé
    device += '_multiroom';
  } else {
    device += '_direct';  // Ajout pour clarification
  }
  
  if (unifiedStore.equalizerEnabled) {
    device += '_eq';
  }
  
  return device;
});

// Gestion des unsubscribe functions WebSocket
let unsubscribeFunctions = [];

// === GESTION WEBSOCKET ===

function handleEqualizerUpdate(event) {
  // Synchronisation des changements d'equalizer entre devices
  if (event.data.equalizer_changed) {
    console.log('Equalizer update received from another device, syncing...');
    // Le store sera automatiquement mis à jour via le WebSocket oakOS principal
  }
}

// === LIFECYCLE ===

onMounted(() => {
  console.log('EqualizerView mounted');
  
  // Écouter les événements système pour synchronisation multi-devices
  const unsubscribe = on('system', 'state_changed', (event) => {
    // Synchroniser le store unifié
    unifiedStore.updateState(event);
    
    // Synchronisation spécifique equalizer
    if (event.source === 'equalizer' && event.data.equalizer_changed) {
      handleEqualizerUpdate(event);
    }
  });
  
  unsubscribeFunctions.push(unsubscribe);
  console.log('Multi-device sync activated for Equalizer');
});

onUnmounted(() => {
  // Nettoyer les abonnements WebSocket
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  console.log('Multi-device sync deactivated');
});
</script>

<style scoped>
.equalizer-view {
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* En-tête */
.equalizer-header {
  background: white;
  border: 1px solid #ddd;
  padding: 20px;
  margin-bottom: 20px;
  text-align: center;
}

.equalizer-header h1 {
  margin: 0;
  font-size: 24px;
}

/* Contenu principal */
.main-content {
  flex: 1;
  margin-bottom: 20px;
}

.equalizer-disabled {
  background: white;
  border: 1px solid #ddd;
  padding: 40px;
  text-align: center;
  color: #666;
}

.equalizer-disabled h2 {
  margin: 0 0 16px 0;
  color: #333;
}

.equalizer-disabled p {
  margin: 0;
  font-size: 14px;
}

.equalizer-active {
  background: white;
  border: 1px solid #ddd;
  padding: 20px;
}

.equalizer-active h2 {
  margin: 0 0 20px 0;
  font-size: 18px;
}

/* Informations equalizer */
.equalizer-info {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 20px;
}

.info-item {
  margin-bottom: 8px;
  font-size: 12px;
}

.info-item:last-child {
  margin-bottom: 0;
}

.info-item strong {
  color: #333;
}

/* Contrôles equalizer */
.equalizer-controls {
  border: 2px dashed #ccc;
  border-radius: 8px;
  padding: 30px;
  text-align: center;
}

.placeholder-message h3 {
  margin: 0 0 12px 0;
  color: #666;
  font-size: 16px;
}

.placeholder-message p {
  margin: 0 0 8px 0;
  color: #888;
  font-size: 14px;
}

.placeholder-message p:last-child {
  margin-bottom: 0;
}

.placeholder-message code {
  background: #f1f3f4;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: #333;
}
</style>