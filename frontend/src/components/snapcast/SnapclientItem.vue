<!-- frontend/src/components/snapcast/SnapclientItem.vue - Version OPTIM avec drag temps réel -->
<template>
  <div class="snapclient-item">
    <!-- Informations du client -->
    <div class="client-name heading-2">{{ client.name }}</div>

    <!-- Contrôles du client -->
    <!-- Contrôle du volume avec drag temps réel -->
    <div class="volume-control">
      <RangeSlider :model-value="displayVolume" :min="minVolumeDisplay" :max="maxVolumeDisplay" :step="1"
        orientation="horizontal" :disabled="client.muted" @input="handleVolumeInput" @change="handleVolumeChange" />
      <span class="volume-label text-mono">{{ displayVolume }}%</span>
    </div>
    <div class="controls-wrapper">
      <IconButton icon="threeDots" @click="handleShowDetails" title="Voir les détails du client" />

      <div class="mute-control">
        <Toggle :model-value="!client.muted" variant="secondary" @change="handleMuteToggle" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useVolumeStore } from '@/stores/volumeStore';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import IconButton from '@/components/ui/IconButton.vue';

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
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  background: var(--color-background-neutral);
}



.client-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  max-width: 112px;
  min-width: 112px;
}

/* Contrôles du client */
.client-controls {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex-shrink: 0;
}

.controls-wrapper {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

/* Contrôle du volume */
.volume-control {
  display: flex;
  width: 100%;
  gap: var(--space-04);
  align-items: center;
  position: relative;
}

.volume-label {
  color: var(--color-text-contrast-50);
  position: absolute;
  margin-left: var(--space-04);
}

/* Bouton détails */
/* .details-btn {
  width: 36px;
  height: 36px;
  border: 1px solid #ced4da;
  background: #e9ecef;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
} */

.details-btn:hover {
  background: #dee2e6;
}

/* Contrôle mute */
.mute-control {
  flex-shrink: 0;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .snapclient-item {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-03);
  }

  .client-name {
    flex: 1;
    order: 1;
  }

  .controls-wrapper {
    order: 2;
    margin-left: auto;
  }

  .volume-control {
    order: 3;
  }

}
</style>