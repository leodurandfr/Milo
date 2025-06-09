<!-- frontend/src/components/snapcast/SnapclientItem.vue -->
<template>
  <div class="snapclient-item">
    <!-- Informations du client -->
    <div class="client-info">
      <div class="client-name">{{ client.name }}</div>
    </div>
    
    <!-- Contrôles du client -->
    <div class="client-controls">
      <!-- Bouton Mute -->
      <button 
        @click="handleMuteToggle"
        :class="['mute-btn', { muted: client.muted }]"
        :title="client.muted ? 'Activer le son' : 'Couper le son'"
        :disabled="updating"
      >
        {{ client.muted ? '🔇' : '🔊' }}
      </button>
      
      <!-- Contrôle du volume -->
      <div class="volume-control">
        <input
          type="range"
          min="0"
          max="100"
          :value="displayVolume"
          @input="handleVolumeInput"
          @change="handleVolumeChange"
          :disabled="client.muted"
          class="volume-slider"
        >
        <span class="volume-label">{{ displayVolume }}%</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';

// Props
const props = defineProps({
  client: {
    type: Object,
    required: true
  }
});

// Émissions
const emit = defineEmits(['volume-change', 'mute-toggle']);

// État local optimisé
const localVolume = ref(null);
const updating = ref(false);

// Volume affiché avec feedback immédiat
const displayVolume = computed(() => {
  return localVolume.value !== null ? localVolume.value : props.client.volume;
});

// === GESTIONNAIRES D'ÉVÉNEMENTS OPTIMISÉS ===

async function handleMuteToggle() {
  if (updating.value) return;
  
  updating.value = true;
  const newMuted = !props.client.muted;
  
  // Feedback immédiat
  props.client.muted = newMuted;
  
  try {
    emit('mute-toggle', props.client.id, newMuted);
  } catch (error) {
    // Restaurer en cas d'erreur
    props.client.muted = !newMuted;
    console.error('Error toggling mute:', error);
  } finally {
    updating.value = false;
  }
}

function handleVolumeInput(event) {
  const newVolume = parseInt(event.target.value);
  
  // Feedback visuel immédiat
  localVolume.value = newVolume;
  
  // Émettre pour throttling dans le parent
  emit('volume-change', props.client.id, newVolume, 'input');
}

function handleVolumeChange(event) {
  const newVolume = parseInt(event.target.value);
  
  // Nettoyer le volume local et émettre la valeur finale
  localVolume.value = null;
  props.client.volume = newVolume; // Mise à jour immédiate
  
  emit('volume-change', props.client.id, newVolume, 'change');
}
</script>

<style scoped>
.snapclient-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border: 1px solid #e0e0e0;
  background: #fafafa;
  transition: background-color 0.2s;
}

.snapclient-item:hover {
  background: #f0f0f0;
}

/* Informations du client */
.client-info {
  flex: 1;
  min-width: 0;
}

.client-name {
  font-weight: bold;
  font-size: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 2px;
}



/* Contrôles du client */
.client-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.mute-btn {
  width: 36px;
  height: 36px;
  border: 1px solid #ddd;
  background: white;
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.mute-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.mute-btn.muted {
  background: #dc3545;
  color: white;
  border-color: #dc3545;
}

.mute-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Contrôle du volume */
.volume-control {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 140px;
}

.volume-slider {
  flex: 1;
  height: 4px;
  background: #ddd;
  outline: none;
  appearance: none;
  transition: background-color 0.2s;
}

.volume-slider:hover:not(:disabled) {
  background: #ccc;
}

.volume-slider::-webkit-slider-thumb {
  appearance: none;
  width: 16px;
  height: 16px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
  transition: background-color 0.2s;
}

.volume-slider::-webkit-slider-thumb:hover {
  background: #1976D2;
}

.volume-slider::-moz-range-thumb {
  width: 16px;
  height: 16px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
  border: none;
  transition: background-color 0.2s;
}

.volume-slider::-moz-range-thumb:hover {
  background: #1976D2;
}

.volume-slider:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.volume-slider:disabled::-webkit-slider-thumb {
  cursor: not-allowed;
}

.volume-label {
  font-size: 12px;
  color: #666;
  width: 36px;
  text-align: right;
  font-weight: 500;
}

/* Responsive */
@media (max-width: 600px) {
  .snapclient-item {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  
  .client-controls {
    justify-content: space-between;
  }
  
  .volume-control {
    min-width: 120px;
  }
}
</style>