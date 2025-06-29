<!-- frontend/src/components/snapcast/SnapcastSettings.vue -->
<template>
  <div class="snapcast-settings">
    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p>Chargement de la configuration...</p>
    </div>

    <div v-else-if="error" class="error-state">
      <p class="error-message">{{ error }}</p>
      <button @click="loadServerConfig" class="retry-btn">Réessayer</button>
    </div>

    <div v-else class="settings-content">
      <!-- Presets Audio -->
      <section class="config-section">
        <h2 class="heading-2">Presets audio</h2>

        <div class="presets-buttons">
          <Button v-for="preset in audioPresets" :key="preset.id" variant="toggle" :active="isPresetActive(preset)"
            :disabled="applying" @click="applyPreset(preset)">
            {{ preset.name }}
          </Button>
        </div>
      </section>

      <!-- Configuration serveur -->
      <section class="config-section">
        <h2 class="heading-2">Paramètres audio</h2>

        <!-- Buffer global -->
        <div class="form-group">
          <label for="buffer" class="text-mono">Buffer global (ms)</label>
          <div class="input-with-value">
            <RangeSlider v-model="config.buffer" :min="100" :max="2000" :step="50" class="range-input" />
            <span class="text-mono">{{ config.buffer }}ms</span>
          </div>
        </div>

        <!-- Codec -->
        <div class="form-group">
          <label class="text-mono">Codec Audio</label>
          <div class="codec-buttons">
            <Button variant="toggle" :active="config.codec === 'opus'" @click="selectCodec('opus')">
              Opus
            </Button>
            <Button variant="toggle" :active="config.codec === 'flac'" @click="selectCodec('flac')">
              FLAC
            </Button>
            <Button variant="toggle" :active="config.codec === 'pcm'" @click="selectCodec('pcm')">
              PCM
            </Button>


          </div>
        </div>

        <!-- Chunk size -->
        <div class="form-group">
          <label for="chunk" class="text-mono">Taille des chunks (ms)</label>
          <div class="input-with-value">
            <RangeSlider v-model="config.chunk_ms" :min="10" :max="100" :step="5" class="range-input" />
            <span class="text-mono">{{ config.chunk_ms }}ms</span>
          </div>
        </div>
      </section>

      <!-- Informations actuelles -->
      <section class="config-section">
        <h2 class="heading-2">État du Serveur</h2>

        <div class="info-grid">
          <div class="info-item">
            <span class="info-label text-mono">Version Snapserver :</span>
            <span class="info-value text-mono">
              {{ serverInfo.server_info?.server?.snapserver?.version || 'Inconnu' }}
            </span>
          </div>

          <div class="info-item">
            <span class="info-label text-mono">Nom du serveur :</span>
            <span class="info-value text-mono">{{ serverInfo.server_info?.server?.host?.name || 'Inconnu' }}</span>
          </div>
        </div>
      </section>

      <!-- Actions -->
      <Button variant="primary" :disabled="loading || applying || !hasChanges" @click="applyConfig">
        {{ applying ? 'Redémarrage du multiroom en cours...' : 'Appliquer' }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

// Émissions
const emit = defineEmits(['close', 'config-updated']);

// État local
const loading = ref(false);
const applying = ref(false);
const error = ref(null);
const serverInfo = ref({});
const lastActionTime = ref(0); // Protection contre double-clic

// Presets audio prédéfinis
const audioPresets = [
  {
    id: 'reactivity',
    name: 'Réactivité',
    config: {
      buffer: 150,
      codec: 'opus',
      chunk_ms: 10
    }
  },
  {
    id: 'balanced',
    name: 'Équilibré',
    config: {
      buffer: 1000,
      codec: 'flac',
      chunk_ms: 20
    }
  },
  {
    id: 'quality',
    name: 'Qualité Optimale',
    config: {
      buffer: 1500,
      codec: 'flac',
      chunk_ms: 40
    }
  }
];

// Configuration actuelle et originale
const config = ref({
  buffer: 1000,
  codec: 'flac',
  chunk_ms: 20,
  sampleformat: '48000:16:2'
});

const originalConfig = ref({});

// Détection des changements
const hasChanges = computed(() => {
  return JSON.stringify(config.value) !== JSON.stringify(originalConfig.value);
});

// === MÉTHODES PRESETS ===

function isPresetActive(preset) {
  const current = config.value;
  const presetConfig = preset.config;

  return current.buffer === presetConfig.buffer &&
    current.codec === presetConfig.codec &&
    current.chunk_ms === presetConfig.chunk_ms;
}

function applyPreset(preset) {
  config.value.buffer = preset.config.buffer;
  config.value.codec = preset.config.codec;
  config.value.chunk_ms = preset.config.chunk_ms;

  console.log(`Applied preset: ${preset.name}`, config.value);
}

function selectCodec(codecName) {
  config.value.codec = codecName;
  console.log(`Selected codec: ${codecName}`);
}

// === MÉTHODES PRINCIPALES ===

async function loadServerConfig() {
  loading.value = true;
  error.value = null;

  try {
    const response = await axios.get('/api/routing/snapcast/server-config');
    serverInfo.value = response.data.config;

    // Lire DEPUIS LE FICHIER /etc/snapserver.conf
    const fileConfig = serverInfo.value.file_config?.parsed_config?.stream || {};
    const streamConfig = serverInfo.value.stream_config || {}; // Fallback API

    config.value = {
      buffer: parseInt(fileConfig.buffer || streamConfig.buffer_ms || '1000'),
      codec: fileConfig.codec || streamConfig.codec || 'flac',
      chunk_ms: parseInt(fileConfig.chunk_ms || streamConfig.chunk_ms) || 20,
      sampleformat: '48000:16:2'
    };

    // Sauvegarder la config originale - FIX : Clonage profond
    originalConfig.value = JSON.parse(JSON.stringify(config.value));

    console.log('Server config loaded from file:', config.value);

  } catch (err) {
    console.error('Error loading server config:', err);
    error.value = 'Impossible de charger la configuration du serveur';
  } finally {
    loading.value = false;
  }
}

async function applyConfig() {
  if (!hasChanges.value || applying.value) return;

  applying.value = true;
  error.value = null;

  try {
    console.log('Applying config:', config.value);

    const response = await axios.post('/api/routing/snapcast/server/config', {
      config: config.value
    });

    // 👈 DEBUG : Voir la réponse complète
    console.log('Full response:', response.data);

    if (response.data.status === 'success') {
      // FIX : Mise à jour immédiate de originalConfig pour éviter le bug de bascule
      originalConfig.value = JSON.parse(JSON.stringify(config.value));
      emit('config-updated');

      // Pas de rechargement automatique - laisser l'utilisateur rafraîchir manuellement si besoin

    } else {
      error.value = response.data.message || 'Erreur lors de la mise à jour';
    }

  } catch (err) {
    console.error('Error applying config:', err);
    error.value = 'Erreur lors de l\'application de la configuration';
  } finally {
    applying.value = false;
  }
}

function closeSettings() {
  emit('close');
}

// === LIFECYCLE ===

onMounted(async () => {
  await loadServerConfig();
});
</script>

<style scoped>
.settings-content {
  gap: var(--space-02);
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

.settings-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.settings-modal {
  background: white;
  border: 1px solid #ddd;
  width: 100%;
  max-width: 650px;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-radius: 16px;
  border-bottom: 1px solid #eee;
  background: #f8f9fa;
}


.modal-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.config-section {
  display: flex;
  flex-direction: column;
  background: var(--color-background-neutral);
  padding: var(--space-05);
  border-radius: var(--radius-04);
  gap: var(--space-05);
}

.section-description {
  margin: 0 0 16px 0;
  font-size: 13px;
  color: #666;
  font-style: italic;
}



/* Presets grid - Adapté pour Button */
.presets-buttons {
  display: flex;
  gap: var(--space-02);
}

.presets-buttons .btn {
  flex: 1;
}


/* Formulaire */

.form-group label {
  display: block;
  color: var(--color-text-secondary);
  margin-bottom: var(--space-02);

}

.input-with-value {
  display: flex;
  align-items: center;
  gap: 12px;
}

.range-input {
  flex: 1;
}

.input-with-value .text-mono {
  color: var(--color-text-secondary);
}

.select-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  background: white;
  font-size: 14px;
}



.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.info-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-03) var(--space-04);
    border-radius: var(--radius-04);
    background: var(--color-background-strong);
}

.info-label.text-mono {
  color: var(--color-text-secondary);
}
.info-value.text-mono {
  color: var(--color-text);
}




.error-message {
  color: var(--color-error);
}

.codec-buttons {
  display: flex;
  gap: var(--space-02);
}

.codec-buttons .btn {
  flex: 1;
}




/* Responsive pour presets */
@media (max-aspect-ratio: 4/3) {

  .codec-buttons,
  .presets-buttons {
    flex-direction: column;
  }
  .info-grid {
  grid-template-columns: 1fr;
}
}
</style>