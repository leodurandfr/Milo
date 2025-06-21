<!-- frontend/src/components/snapcast/SnapclientItem.vue - Version corrigée interpolation -->
<template>
  <div class="snapclient-item">
    <!-- Informations du client -->
    <div class="client-info">
      <div class="client-name">{{ client.name }}</div>
    </div>

    <!-- Contrôles du client -->
    <div class="client-controls">
      <!-- Contrôle du volume avec RangeSlider (affichage 0-100%) -->
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
      <button @click="handleShowDetails" class="details-btn" title="Voir les détails du client">
        ℹ️
      </button>

      <!-- Toggle Mute -->
      <div class="mute-control">
        <Toggle :model-value="!client.muted" :disabled="updating" @change="handleMuteToggle" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useVolumeStore } from '@/stores/volumeStore';
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

// LIMITES DE VOLUME (récupérées automatiquement du backend via store)
const volumeStore = useVolumeStore();

// Limites dynamiques depuis le backend (avec fallback CORRIGÉ)
const MIN_VOLUME = computed(() => volumeStore.volumeLimits?.min ?? 5);  // ✅ Corrigé : 5 au lieu de 40
const MAX_VOLUME = computed(() => volumeStore.volumeLimits?.max ?? 60); // ✅ Corrigé : 60 au lieu de 75

// État local optimisé
const localDisplayVolume = ref(null);
const updating = ref(false);

// === FONCTIONS D'INTERPOLATION CORRIGÉES ===

function interpolateToDisplay(actualVolume) {
  /**
   * Convertit le volume réel (MIN-MAX) en volume d'affichage (0-100%)
   * ✅ AJOUT : Clamp pour gérer les valeurs hors bornes existantes
   */
  // Clamp dans les limites avant interpolation
  const clampedVolume = Math.max(MIN_VOLUME.value, Math.min(MAX_VOLUME.value, actualVolume));
  
  const actualRange = MAX_VOLUME.value - MIN_VOLUME.value;
  const normalized = clampedVolume - MIN_VOLUME.value;
  return Math.round((normalized / actualRange) * 100);
}

function interpolateFromDisplay(displayVolume) {
  /**
   * Convertit le volume d'affichage (0-100%) en volume réel (MIN-MAX)
   */
  const actualRange = MAX_VOLUME.value - MIN_VOLUME.value;
  return Math.round((displayVolume / 100) * actualRange) + MIN_VOLUME.value;
}

// === VOLUME AFFICHÉ ===

// Volume affiché avec feedback immédiat (conversion avec clamp)
const displayVolume = computed(() => {
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }
  
  // Convertir le volume client vers affichage (avec clamp automatique)
  return interpolateToDisplay(props.client.volume);
});

// === WATCHERS ===

// Watcher pour nettoyer localDisplayVolume quand client.volume change (mise à jour WebSocket)
watch(() => props.client.volume, (newVolume, oldVolume) => {
  // Si le volume client change et qu'on n'a pas de modification locale en cours
  if (newVolume !== oldVolume && localDisplayVolume.value === null) {
    console.log(`Client ${props.client.name} volume updated externally: ${oldVolume}% → ${newVolume}%`);
  }
  
  // Si le volume change ET qu'on a une valeur locale différente, nettoyer
  if (localDisplayVolume.value !== null) {
    const expectedDisplay = interpolateToDisplay(newVolume);
    if (Math.abs(localDisplayVolume.value - expectedDisplay) > 2) {
      console.log(`Clearing local volume for ${props.client.name} due to external update`);
      localDisplayVolume.value = null;
    }
  }
});

// === GESTIONNAIRES D'ÉVÉNEMENTS ===

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

function handleVolumeInput(newDisplayVolume) {
  // Feedback visuel immédiat (garde la valeur d'affichage)
  localDisplayVolume.value = newDisplayVolume;

  // Convertir vers volume réel pour l'envoi
  const realVolume = interpolateFromDisplay(newDisplayVolume);
  
  console.log(`Volume input: display=${newDisplayVolume}% → real=${realVolume}%`);
  
  // Émettre pour throttling dans le parent (avec la valeur réelle)
  emit('volume-change', props.client.id, realVolume, 'input');
}

function handleVolumeChange(newDisplayVolume) {
  // Nettoyer le volume local
  localDisplayVolume.value = null;
  
  // Convertir vers volume réel
  const realVolume = interpolateFromDisplay(newDisplayVolume);
  
  // Mise à jour immédiate du client avec la valeur réelle
  props.client.volume = realVolume;
  
  console.log(`Volume change: display=${newDisplayVolume}% → real=${realVolume}%`);
  
  // Émettre la valeur finale (avec la valeur réelle)
  emit('volume-change', props.client.id, realVolume, 'change');
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
  background: #fff;
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
  display: inline-block;
  width: 96px;
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

.volume-label {
  display: inline-block;
  width: 48px;
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