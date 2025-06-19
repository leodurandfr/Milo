<!-- frontend/src/components/snapcast/SnapclientItem.vue - Version avec RangeSlider et Toggle -->
<template>
  <div class="snapclient-item">
    <!-- Informations du client -->
    <div class="client-info">
      <div class="client-name">{{ client.name }}</div>
    </div>
    
    <!-- Contrôles du client -->
    <div class="client-controls">
      <!-- Contrôle du volume avec RangeSlider -->
      <div class="volume-control">
        <RangeSlider
          :model-value="displayVolume"
          :min="0"
          :max="100"
          :step="1"
          orientation="horizontal"
          :disabled="client.muted || updating"
          @input="handleVolumeInput"
          @change="handleVolumeChange"
        />
        <span class="volume-label">{{ displayVolume }}%</span>
      </div>
      
      <!-- Bouton Détails -->
      <button 
        @click="handleShowDetails"
        class="details-btn"
        title="Voir les détails du client"
      >
        ℹ️
      </button>

      <!-- Toggle Mute -->
      <div class="mute-control">
        <Toggle
          :model-value="!client.muted"
          :disabled="updating"
          @change="handleMuteToggle"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';

// Props
const props = defineProps({
  client: {
    type: Object,
    required: true
  }
});

// Émissions
const emit = defineEmits(['volume-change', 'mute-toggle', 'show-details']);

// État local optimisé
const localVolume = ref(null);
const updating = ref(false);

// Volume affiché avec feedback immédiat
const displayVolume = computed(() => {
  return localVolume.value !== null ? localVolume.value : props.client.volume;
});

// === GESTIONNAIRES D'ÉVÉNEMENTS OPTIMISÉS ===

async function handleMuteToggle(enabled) {
  if (updating.value) return;
  
  updating.value = true;
  const newMuted = !enabled; // Toggle inversé (enabled = pas muted)
  
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

function handleVolumeInput(newVolume) {
  // Feedback visuel immédiat
  localVolume.value = newVolume;
  
  // Émettre pour throttling dans le parent
  emit('volume-change', props.client.id, newVolume, 'input');
}

function handleVolumeChange(newVolume) {
  // Nettoyer le volume local et émettre la valeur finale
  localVolume.value = null;
  props.client.volume = newVolume; // Mise à jour immédiate
  
  emit('volume-change', props.client.id, newVolume, 'change');
}

function handleShowDetails() {
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
  transition: background-color 0.2s;
}

.snapclient-item:hover {
  background: #f0f0f0;
}

/* Informations du client */
.client-info {
  flex: 1;
  min-width: 0;
  margin-right: 16px;
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

/* Contrôle du volume */
.volume-control {
  min-width: 140px;
  flex-shrink: 0;
}

/* Bouton détails */
.details-btn {
  width: 36px;
  height: 36px;
  border: 1px solid #ced4da;
  background: #e9ecef;
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
}

.details-btn:hover {
  background: #dee2e6;
}

/* Contrôle mute */
.mute-control {
  flex-shrink: 0;
}

/* Responsive */
@media (max-width: 600px) {
  .snapclient-item {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  
  .client-info {
    margin-right: 0;
    text-align: center;
  }
  
  .client-controls {
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }
  
  .volume-control {
    order: 1;
    flex: 1;
    min-width: 120px;
  }
  
  .details-btn {
    order: 2;
  }
  
  .mute-control {
    order: 3;
  }
}
</style>