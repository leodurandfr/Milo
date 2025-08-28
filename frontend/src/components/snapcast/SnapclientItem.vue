<!-- frontend/src/components/snapcast/SnapclientItem.vue - Version volume affiché simplifié -->
<template>
  <div class="snapclient-item">
    <!-- Informations du client -->
    <div class="client-name heading-2">{{ client.name }}</div>

    <!-- Contrôle du volume - SIMPLIFIÉ pour volume affiché uniquement -->
    <div class="volume-control">
      <RangeSlider 
        :model-value="displayVolume" 
        :min="0" 
        :max="100" 
        :step="1"
        orientation="horizontal" 
        :disabled="client.muted" 
        @input="handleVolumeInput" 
        @change="handleVolumeChange" 
      />
      <span class="volume-label text-mono">{{ displayVolume }} %</span>
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

// === ÉTAT LOCAL POUR DRAG TEMPS RÉEL ===
const localDisplayVolume = ref(null); // Feedback visuel immédiat pendant drag
let throttleTimeout = null;
let finalTimeout = null;

// === LIMITES ALSA ET INTERPOLATION ===

// Limites ALSA (doivent correspondre à celles du VolumeService)
const ALSA_MIN = 0;
const ALSA_MAX = 65;

// Fonctions d'interpolation
function alsaToDisplay(alsaVolume) {
  const alsaRange = ALSA_MAX - ALSA_MIN;
  const normalized = alsaVolume - ALSA_MIN;
  return Math.round((normalized / alsaRange) * 100);
}

function displayToAlsa(displayVolume) {
  const alsaRange = ALSA_MAX - ALSA_MIN;
  return Math.round((displayVolume / 100) * alsaRange) + ALSA_MIN;
}

// === VOLUME AFFICHÉ AVEC INTERPOLATION ===

const displayVolume = computed(() => {
  // Utiliser le volume local pendant le drag, sinon interpoler depuis ALSA
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }
  
  // Le client.volume vient de Snapcast en ALSA brut (0-65)
  // L'interpoler pour affichage (0-100%)
  return alsaToDisplay(props.client.volume);
});

// === GESTIONNAIRES D'ÉVÉNEMENTS AVEC THROTTLING ===

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

  // Timeout de sécurité (500ms)
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
  // Convertir le volume affiché (0-100%) vers volume ALSA (0-65)
  const alsaVolume = displayToAlsa(displayVolume);
  
  console.log(`Volume update: display=${displayVolume}% → ALSA=${alsaVolume} (limits: ${ALSA_MIN}-${ALSA_MAX})`);
  
  // Envoyer le volume ALSA (car Snapcast attend des valeurs 0-65)
  emit('volume-change', props.client.id, alsaVolume);
}

function handleMuteToggle(enabled) {
  // Toggle inversé (enabled = pas muted)
  const newMuted = !enabled;
  emit('mute-toggle', props.client.id, newMuted);
}

function handleShowDetails() {
  console.log('Showing details for client:', props.client.name);
  emit('show-details', props.client);
}

// === NETTOYAGE ===
import { onUnmounted } from 'vue';

onUnmounted(() => {
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

.details-btn:hover {
  background: #dee2e6;
}

.mute-control {
  flex-shrink: 0;
}

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