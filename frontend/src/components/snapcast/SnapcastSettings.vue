<!-- frontend/src/components/snapcast/SnapcastSettings.vue -->
<template>
  <div class="settings-overlay" @click.self="closeSettings">
    <div class="settings-modal">
      <!-- En-tête -->
      <div class="modal-header">
        <h2>Configuration Snapcast</h2>
        <button @click="closeSettings" class="close-btn">✕</button>
      </div>

      <!-- Contenu -->
      <div class="modal-content">
        <div v-if="loading" class="loading-state">
          <div class="loading-spinner"></div>
          <p>Chargement de la configuration...</p>
        </div>

        <div v-else-if="error" class="error-state">
          <p class="error-message">{{ error }}</p>
          <button @click="loadServerConfig" class="retry-btn">Réessayer</button>
        </div>

        <div v-else class="settings-form">
          <!-- Configuration serveur -->
          <section class="config-section">
            <h3>Paramètres Serveur</h3>
            
            <!-- Buffer serveur -->
            <div class="form-group">
              <label for="buffer">Buffer Serveur (ms)</label>
              <div class="input-with-value">
                <input
                  id="buffer"
                  type="range"
                  min="100"
                  max="2000"
                  step="50"
                  v-model.number="config.buffer_ms"
                  class="range-input"
                >
                <span class="value-display">{{ config.buffer_ms }}ms</span>
              </div>
              <p class="help-text">Latence end-to-end du système (100-2000ms)</p>
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
                <input
                  id="chunk"
                  type="range"
                  min="10"
                  max="100"
                  step="5"
                  v-model.number="config.chunk_ms"
                  class="range-input"
                >
                <span class="value-display">{{ config.chunk_ms }}ms</span>
              </div>
              <p class="help-text">Taille de lecture du buffer source (10-100ms)</p>
            </div>

            <!-- Format d'échantillonnage -->
            <div class="form-group">
              <label for="sampleformat">Format d'Échantillonnage</label>
              <select id="sampleformat" v-model="config.sampleformat" class="select-input">
                <option value="48000:16:2">48kHz 16-bit Stéréo</option>
                <option value="44100:16:2">44.1kHz 16-bit Stéréo</option>
                <option value="48000:24:2">48kHz 24-bit Stéréo</option>
                <option value="96000:16:2">96kHz 16-bit Stéréo</option>
              </select>
              <p class="help-text">Format audio utilisé par les sources</p>
            </div>
          </section>

          <!-- Informations actuelles -->
          <section class="config-section">
            <h3>État Actuel</h3>
            
            <div class="info-grid">
              <div class="info-item">
                <span class="info-label">Version RPC:</span>
                <span class="info-value">{{ serverInfo.rpc_version?.major }}.{{ serverInfo.rpc_version?.minor }}.{{ serverInfo.rpc_version?.patch }}</span>
              </div>
              
              <div class="info-item">
                <span class="info-label">Streams actifs:</span>
                <span class="info-value">{{ serverInfo.streams?.length || 0 }}</span>
              </div>
              
              <div class="info-item">
                <span class="info-label">Host serveur:</span>
                <span class="info-value">{{ serverInfo.server_info?.server?.host?.name || 'Inconnu' }}</span>
              </div>
              
              <div class="info-item">
                <span class="info-label">Version Snapserver:</span>
                <span class="info-value">{{ serverInfo.server_info?.server?.snapserver?.version || 'Inconnu' }}</span>
              </div>
            </div>
          </section>

          <!-- Warning redémarrage -->
          <div class="warning-box">
            <span class="warning-icon">!</span>
            <div>
              <strong>Attention:</strong> Les modifications nécessitent un redémarrage du serveur Snapcast.
              Tous les clients seront temporairement déconnectés.
            </div>
          </div>
        </div>
      </div>

      <!-- Actions -->
      <div class="modal-actions">
        <button @click="closeSettings" class="cancel-btn">Annuler</button>
        <button 
          @click="applyConfig" 
          :disabled="loading || applying || !hasChanges"
          class="apply-btn"
        >
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

// Configuration actuelle et originale
const config = ref({
  buffer_ms: 1000,
  codec: 'flac',
  chunk_ms: 20,
  sampleformat: '48000:16:2'
});

const originalConfig = ref({});

// Détection des changements
const hasChanges = computed(() => {
  return JSON.stringify(config.value) !== JSON.stringify(originalConfig.value);
});

// === MÉTHODES ===

async function loadServerConfig() {
  loading.value = true;
  error.value = null;
  
  try {
    const response = await axios.get('/api/routing/snapcast/server-config');
    serverInfo.value = response.data.config;
    
    // Extraire la configuration actuelle
    const streamConfig = serverInfo.value.stream_config || {};
    config.value = {
      buffer_ms: parseInt(streamConfig.buffer_ms) || 1000,
      codec: streamConfig.codec || 'flac',
      chunk_ms: parseInt(streamConfig.chunk_ms) || 20,
      sampleformat: streamConfig.sampleformat || '48000:16:2'
    };
    
    // Sauvegarder la config originale pour détecter les changements
    originalConfig.value = { ...config.value };
    
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
  
  try {
    const response = await axios.post('/api/routing/snapcast/server/config', {
      config: config.value
    });
    
    if (response.data.status === 'success') {
      // Mise à jour réussie
      originalConfig.value = { ...config.value };
      emit('config-updated');
      
      // Fermer la modal après un court délai
      setTimeout(() => {
        closeSettings();
      }, 1000);
      
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

function resetConfig() {
  config.value = { ...originalConfig.value };
}

// === LIFECYCLE ===

onMounted(() => {
  loadServerConfig();
});
</script>

<style scoped>
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
  max-width: 600px;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* En-tête */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px;
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

.close-btn:hover {
  background: #e9ecef;
}

/* Contenu */
.modal-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.loading-state, .error-state {
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
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
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

/* Formulaire */
.settings-form {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.config-section {
  background: #f8f9fa;
  padding: 16px;
}

.config-section h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
}

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
  min-width: 60px;
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

/* Informations */
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

/* Warning box */
.warning-box {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  background: #fff3cd;
  border: 1px solid #ffeaa7;
  color: #856404;
}

.warning-icon {
  font-size: 16px;
  flex-shrink: 0;
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

.cancel-btn, .apply-btn {
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
</style>