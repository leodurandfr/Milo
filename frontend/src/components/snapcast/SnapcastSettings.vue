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
        <h3>Presets Audio</h3>
        <p class="section-description">Configurations prédéfinies pour différents usages</p>

        <div class="presets-grid">
          <button v-for="preset in audioPresets" :key="preset.id" @click="applyPreset(preset)"
            :class="['preset-btn', { active: isPresetActive(preset) }]" :disabled="applying">
            <div class="preset-title">{{ preset.name }}</div>
          </button>
        </div>
      </section>

      <!-- Configuration serveur -->
      <section class="config-section">
        <h3>Paramètres Audio</h3>

        <!-- Buffer global -->
        <div class="form-group">
          <label for="buffer">Buffer Global (ms)</label>
          <div class="input-with-value">
            <input id="buffer" type="range" min="100" max="2000" step="50" v-model.number="config.buffer"
              class="range-input">
            <span class="value-display">{{ config.buffer }}ms</span>
          </div>
          <p class="help-text">Latence end-to-end totale du système (100-2000ms)</p>
        </div>

        <!-- Codec -->
        <div class="form-group">
          <label for="codec">Codec Audio</label>
          <select id="codec" v-model="config.codec" class="select-input">
            <option value="flac">FLAC (Sans perte, ~26ms latence)</option>
            <option value="pcm">PCM (Sans perte, aucune latence)</option>
            <option value="opus">Opus (Avec perte, faible latence)</option>
            <option value="ogg">OGG (Avec perte)</option>
          </select>
          <p class="help-text">Codec utilisé pour compresser l'audio</p>
        </div>

        <!-- Chunk size -->
        <div class="form-group">
          <label for="chunk">Taille des Chunks (ms)</label>
          <div class="input-with-value">
            <input id="chunk" type="range" min="10" max="100" step="5" v-model.number="config.chunk_ms"
              class="range-input">
            <span class="value-display">{{ config.chunk_ms }}ms</span>
          </div>
          <p class="help-text">Taille de lecture du buffer source (10-100ms)</p>
        </div>
      </section>

      <!-- Informations actuelles -->
      <section class="config-section">
        <h3>État du Serveur</h3>

        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">Version Snapserver:</span>
            <span class="info-value">
              {{ serverInfo.server_info?.server?.snapserver?.version || 'Inconnu' }}
            </span>
          </div>

          <div class="info-item">
            <span class="info-label">Host:</span>
            <span class="info-value">{{ serverInfo.server_info?.server?.host?.name || 'Inconnu' }}</span>
          </div>
        </div>
      </section>

      <!-- Actions -->
      <div class="modal-actions">
        <button @click="applyConfig" :disabled="loading || applying || !hasChanges" class="apply-btn">
          {{ applying ? 'Application...' : 'Appliquer & Redémarrer' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';

// Émissions
const emit = defineEmits(['close', 'config-updated']);

// État local
const loading = ref(false);
const applying = ref(false);
const error = ref(null);
const serverInfo = ref({});

// Presets audio prédéfinis - SIMPLIFIÉ
const audioPresets = [
  {
    id: 'reactivity',
    name: 'Réactivité',
    config: {
      buffer: 150,
      codec: 'pcm',
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
  if (!hasChanges.value) return;

  applying.value = true;
  error.value = null;

  try {
    console.log('Applying config:', config.value);

    const response = await axios.post('/api/routing/snapcast/server/config', {
      config: config.value
    });

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

/* En-tête simple */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-radius: 16px;
  border-bottom: 1px solid #eee;
  background: #f8f9fa;
}

.modal-header h2 {
  margin: 0;
  font-size: 20px;
}

.close-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  padding: 4px;
}

/* Contenu */
.modal-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* Formulaire de configuration */
.settings-form {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.config-section {
  background: #f8f9fa;
  padding: 16px;
  border-radius:16px
}

.config-section h3 {
  margin: 0 0 8px 0;
  font-size: 16px;
}

.section-description {
  margin: 0 0 16px 0;
  font-size: 13px;
  color: #666;
  font-style: italic;
}

/* Presets grid - SIMPLIFIÉ */
.presets-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
}

.preset-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px;
  border: 2px solid #ddd;
  background: white;
  cursor: pointer;
  text-align: center;
  transition: all 0.2s;
}

.preset-btn:hover:not(:disabled) {
  border-color: #2196F3;
  background: #f0f8ff;
}

.preset-btn.active {
  border-color: #2196F3;
  background: #e3f2fd;
}

.preset-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.preset-title {
  font-weight: bold;
  font-size: 14px;
}

/* Formulaire */
.form-group {
  margin-bottom: 16px;
}

.form-group:last-child {
  margin-bottom: 0;
}

.form-group label {
  display: block;
  font-weight: bold;
  margin-bottom: 8px;
  font-size: 14px;
}

.input-with-value {
  display: flex;
  align-items: center;
  gap: 12px;
}

.range-input {
  flex: 1;
  height: 6px;
  background: #ddd;
  outline: none;
  appearance: none;
}

.range-input::-webkit-slider-thumb {
  appearance: none;
  width: 18px;
  height: 18px;
  background: #2196F3;
  border-radius: 50%;
  cursor: pointer;
}

.value-display {
  font-weight: bold;
  color: #2196F3;
  min-width: 70px;
  text-align: right;
}

.select-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  background: white;
  font-size: 14px;
}

.help-text {
  font-size: 12px;
  color: #666;
  margin: 4px 0 0 0;
  line-height: 1.4;
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px;
  background: white;
}

.info-label {
  font-size: 12px;
  color: #666;
}

.info-value {
  font-weight: bold;
  font-size: 12px;
}

/* États communs */
.loading-state,
.error-state {
  text-align: center;
  padding: 40px 20px;
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #2196F3;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

.error-message {
  color: #dc3545;
  margin-bottom: 16px;
}

.retry-btn {
  padding: 8px 16px;
  background: #dc3545;
  color: white;
  border: none;
  cursor: pointer;
}

/* Actions */
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 20px;
  border-top: 1px solid #eee;
  background: #f8f9fa;
}

.cancel-btn,
.apply-btn {
  padding: 10px 20px;
  border: none;
  cursor: pointer;
  font-weight: bold;
}

.cancel-btn {
  background: #6c757d;
  color: white;
}

.cancel-btn:hover {
  background: #545b62;
}

.apply-btn {
  background: #28a745;
  color: white;
}

.apply-btn:hover:not(:disabled) {
  background: #218838;
}

.apply-btn:disabled {
  background: #6c757d;
  cursor: not-allowed;
  opacity: 0.6;
}

/* Responsive pour presets */
@media (max-aspect-ratio: 4/3) {
  .presets-grid {
    grid-template-columns: 1fr;
  }

  .preset-btn {
    padding: 12px;
  }
}
</style>