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
        @click="toggleMute"
        :class="['mute-btn', { muted: client.muted }]"
        :title="client.muted ? 'Activer le son' : 'Couper le son'"
      >
        {{ client.muted ? '🔇' : '🔊' }}
      </button>
      
      <!-- Contrôle du volume -->
      <div class="volume-control">
        <input
          type="range"
          min="0"
          max="100"
          :value="localVolume !== null ? localVolume : client.volume"
          @input="handleVolumeInput"
          @change="handleVolumeChange"
          :disabled="client.muted"
          class="volume-slider"
        >
        <span class="volume-label">{{ localVolume !== null ? localVolume : client.volume }}%</span>
      </div>
      
      <!-- Bouton Détails -->
      <button 
        @click="showDetails"
        class="details-btn"
        title="Voir les détails du client"
      >
        ℹ️
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';

// Props
const props = defineProps({
  client: {
    type: Object,
    required: true
  }
});

// Émissions
const emit = defineEmits(['volume-change', 'mute-toggle', 'show-details']);

// État local pour feedback immédiat
const localVolume = ref(null);

// === MÉTHODES ===

function toggleMute() {
  const newMuted = !props.client.muted;
  emit('mute-toggle', props.client.id, newMuted);
}

function handleVolumeInput(event) {
  const newVolume = parseInt(event.target.value);
  
  // 1. Feedback visuel IMMÉDIAT - SYSTÈME ORIGINAL
  localVolume.value = newVolume;
  
  // 2. Émettre l'événement avec throttling intelligent dans le parent
  emit('volume-change', props.client.id, newVolume);
}

function handleVolumeChange(event) {
  // @change se déclenche quand on relâche le slider
  const newVolume = parseInt(event.target.value);
  
  // Nettoyer le volume local et émettre la valeur finale
  localVolume.value = null;
  emit('volume-change', props.client.id, newVolume);
}

function showDetails() {
  emit('show-details', props.client);
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
}

/* Contrôles du client */
.client-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.mute-btn, .details-btn {
  width: 36px;
  height: 36px;
  border: 1px solid #ddd;
  background: white;
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mute-btn:hover:not(:disabled), .details-btn:hover {
  background: #f0f0f0;
}

.mute-btn.muted {
  background: #dc3545;
  color: white;
  border-color: #dc3545;
}

.mute-btn:disabled, .details-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.details-btn {
  background: #e9ecef;
  border-color: #ced4da;
}

.details-btn:hover {
  background: #dee2e6;
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