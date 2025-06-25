<!-- frontend/src/components/snapcast/SnapclientItem.vue - Version OPTIM avec drag temps réel -->
<template>
  <div class="snapclient-item">
    <!-- Informations du client -->
    <div class="client-name">{{ client.name }}</div>

    <!-- Contrôles du client -->
    <!-- Contrôle du volume avec drag temps réel -->
    <div class="volume-control">
      <RangeSlider :model-value="displayVolume" :min="minVolumeDisplay" :max="maxVolumeDisplay" :step="1"
        orientation="horizontal" :disabled="client.muted" @input="handleVolumeInput" @change="handleVolumeChange" />
      <span class="volume-label">{{ displayVolume }}%</span>
    </div>

    <!-- Bouton Détails -->
    <button @click="handleShowDetails" class="details-btn" title="Voir les détails du client">
      ℹ️
    </button>

    <!-- Toggle Mute -->
    <div class="mute-control">
      <Toggle :model-value="!client.muted" @change="handleMuteToggle" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
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

// Store volume pour les limites
const volumeStore = useVolumeStore();

// === ÉTAT LOCAL POUR DRAG ===

const localDisplayVolume = ref(null); // Feedback visuel immédiat pendant drag
let throttleTimeout = null;
let finalTimeout = null;

// === LIMITES DE VOLUME ===

// Limites réelles depuis le backend (avec fallback)
const MIN_VOLUME = computed(() => volumeStore.volumeLimits?.min ?? 0);
const MAX_VOLUME = computed(() => volumeStore.volumeLimits?.max ?? 55);

// Conversion pour affichage (0-100%)
const minVolumeDisplay = computed(() => 0);
const maxVolumeDisplay = computed(() => 100);

// === FONCTIONS D'INTERPOLATION ===

function interpolateToDisplay(actualVolume) {
  // Convertit le volume réel (MIN-MAX) en volume d'affichage (0-100%)
  const clampedVolume = Math.max(MIN_VOLUME.value, Math.min(MAX_VOLUME.value, actualVolume));
  const actualRange = MAX_VOLUME.value - MIN_VOLUME.value;
  const normalized = clampedVolume - MIN_VOLUME.value;
  return Math.round((normalized / actualRange) * 100);
}

function interpolateFromDisplay(displayVolume) {
  // Convertit le volume d'affichage (0-100%) en volume réel (MIN-MAX)
  const actualRange = MAX_VOLUME.value - MIN_VOLUME.value;
  return Math.round((displayVolume / 100) * actualRange) + MIN_VOLUME.value;
}

// === VOLUME AFFICHÉ ===

const displayVolume = computed(() => {
  // Utiliser volume local pendant le drag, sinon volume du client
  return localDisplayVolume.value !== null
    ? localDisplayVolume.value
    : interpolateToDisplay(props.client.volume);
});

// === GESTIONNAIRES D'ÉVÉNEMENTS AVEC THROTTLING OPTIM ===

function handleVolumeInput(newDisplayVolume) {
  // Feedback visuel immédiat
  localDisplayVolume.value = newDisplayVolume;

  // Nettoyer les timeouts existants
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  // Throttling pendant le drag (25ms)
  throttleTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 25);

  // Timeout de sécurité pour garantir l'envoi final (500ms)
  finalTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 500);
}

function handleVolumeChange(newDisplayVolume) {
  // Fin du drag - nettoyer et envoyer la valeur finale
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  // Reset du volume local
  localDisplayVolume.value = null;

  // Envoyer la valeur finale
  sendVolumeUpdate(newDisplayVolume);
}

function sendVolumeUpdate(displayVolume) {
  // Convertir vers volume réel avec limites
  const realVolume = interpolateFromDisplay(displayVolume);

  console.log(`Volume update: display=${displayVolume}% → real=${realVolume}% (limits: ${MIN_VOLUME.value}-${MAX_VOLUME.value}%)`);

  // Le WebSocket Snapcast se chargera de la mise à jour
  emit('volume-change', props.client.id, realVolume);
}

function handleMuteToggle(enabled) {
  // Toggle inversé (enabled = pas muted)
  const newMuted = !enabled;
  emit('mute-toggle', props.client.id, newMuted);
}

function handleShowDetails() {
  console.log('🔍 Showing details for client:', props.client.name);
  emit('show-details', props.client);
}

// === NETTOYAGE ===

import { onUnmounted } from 'vue';

onUnmounted(() => {
  // Nettoyer les timeouts
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);
});
</script>

<style scoped>
.snapclient-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: 16px;
  gap:16px;
  padding: 16px;
  background: #fff;
}



.client-name {
  font-weight: bold;
  font-size: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  min-width: 96px;
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
  display: flex;
  width: 100%;
  gap: 16px;
  align-items: center;
}

.volume-label {
  position: absolute;
  margin-left: 16px;
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
  border-radius: 4px;
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