<!-- frontend/src/components/snapcast/SnapclientDetails.vue -->
<template>
  <div class="details-overlay" @click.self="closeDetails">
    <div class="details-modal">
      <!-- En-tête -->
      <div class="modal-header">
        <h2>Détails du Client</h2>
        <button @click="closeDetails" class="close-btn">✕</button>
      </div>

      <!-- Contenu -->
      <div class="modal-content">
        <div v-if="loading" class="loading-state">
          <div class="loading-spinner"></div>
          <p>Chargement des détails...</p>
        </div>

        <div v-else-if="error" class="error-state">
          <p class="error-message">{{ error }}</p>
          <button @click="loadClientDetails" class="retry-btn">Réessayer</button>
        </div>

        <div v-else class="details-content">
          <!-- Informations générales -->
          <section class="details-section">
            <h3>Informations Générales</h3>
            
            <div class="info-grid">
              <div class="info-item">
                <span class="info-label">Adresse IP:</span>
                <span class="info-value">{{ clientDetails.ip }}</span>
              </div>
              
              <div class="info-item">
                <span class="info-label">Hostname:</span>
                <span class="info-value">{{ clientDetails.host }}</span>
              </div>
              
              <div class="info-item">
                <span class="info-label">Connexion:</span>
                <div class="connection-status">
                  <span :class="['status-dot', getQualityClass(clientDetails.connection_quality)]"></span>
                  <span>{{ getQualityText(clientDetails.connection_quality) }}</span>
                </div>
              </div>
            </div>
          </section>

          <!-- Configuration éditable -->
          <section class="details-section">
            <h3>Configuration</h3>
            
            <!-- Nom du client -->
            <div class="form-group">
              <label for="client-name">Nom Affiché</label>
              <div class="input-group">
                <input
                  id="client-name"
                  type="text"
                  v-model="editableConfig.name"
                  :placeholder="clientDetails.host"
                  class="text-input"
                  maxlength="50"
                >
                <button 
                  @click="updateClientName"
                  :disabled="updating.name || !hasNameChanged"
                  class="update-btn"
                >
                  {{ updating.name ? '...' : 'OK' }}
                </button>
              </div>
              <p class="help-text">Nom personnalisé pour identifier ce client</p>
            </div>

            <!-- Latence -->
            <div class="form-group">
              <label for="client-latency">Latence (ms)</label>
              <div class="latency-control">
                <input
                  id="client-latency"
                  type="range"
                  min="0"
                  max="500"
                  step="5"
                  v-model.number="editableConfig.latency"
                  class="range-input"
                >
                <div class="latency-display">
                  <span class="latency-value">{{ editableConfig.latency }}ms</span>
                  <button 
                    @click="updateClientLatency"
                    :disabled="updating.latency || !hasLatencyChanged"
                    class="update-btn small"
                  >
                    {{ updating.latency ? '...' : 'OK' }}
                  </button>
                </div>
              </div>
              <p class="help-text">
                Compensation de latence pour synchroniser l'audio
                <span v-if="clientDetails.latency !== editableConfig.latency" class="change-indicator">
                  (actuellement: {{ clientDetails.latency }}ms)
                </span>
              </p>
            </div>
          </section>

          <!-- Informations système -->
          <section class="details-section">
            <h3>Informations Système</h3>
            
            <div class="info-grid">
              <div class="info-item">
                <span class="info-label">Version Snapclient:</span>
                <span class="info-value">{{ clientDetails.snapclient_info?.version || 'Inconnu' }}</span>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- Actions -->
      <div class="modal-actions">
        <button @click="closeDetails" class="close-action-btn">Fermer</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import axios from 'axios';

// Props
const props = defineProps({
  client: {
    type: Object,
    required: true
  }
});

// Émissions
const emit = defineEmits(['close', 'client-updated']);

// État local
const loading = ref(false);
const error = ref(null);
const clientDetails = ref({});
const updating = ref({
  name: false,
  latency: false
});

// Configuration éditable
const editableConfig = ref({
  name: '',
  latency: 0
});

// Détection des changements
const hasNameChanged = computed(() => {
  return editableConfig.value.name.trim() !== (clientDetails.value.name || '');
});

const hasLatencyChanged = computed(() => {
  return editableConfig.value.latency !== clientDetails.value.latency;
});

// === MÉTHODES ===

async function loadClientDetails() {
  loading.value = true;
  error.value = null;
  
  try {
    const response = await axios.get(`/api/routing/snapcast/client/${props.client.id}/details`);
    clientDetails.value = response.data.client;
    
    // Initialiser la configuration éditable
    editableConfig.value = {
      name: clientDetails.value.name || '',
      latency: clientDetails.value.latency || 0
    };
    
  } catch (err) {
    console.error('Error loading client details:', err);
    error.value = 'Impossible de charger les détails du client';
  } finally {
    loading.value = false;
  }
}

async function updateClientName() {
  if (!hasNameChanged.value) return;
  
  updating.value.name = true;
  
  try {
    const response = await axios.post(`/api/routing/snapcast/client/${props.client.id}/name`, {
      name: editableConfig.value.name.trim()
    });
    
    if (response.data.status === 'success') {
      clientDetails.value.name = editableConfig.value.name.trim();
      emit('client-updated');
    } else {
      error.value = 'Erreur lors de la mise à jour du nom';
    }
    
  } catch (err) {
    console.error('Error updating client name:', err);
    error.value = 'Erreur lors de la mise à jour du nom';
  } finally {
    updating.value.name = false;
  }
}

async function updateClientLatency() {
  if (!hasLatencyChanged.value) return;
  
  updating.value.latency = true;
  
  try {
    const response = await axios.post(`/api/routing/snapcast/client/${props.client.id}/latency`, {
      latency: editableConfig.value.latency
    });
    
    if (response.data.status === 'success') {
      clientDetails.value.latency = editableConfig.value.latency;
      emit('client-updated');
    } else {
      error.value = 'Erreur lors de la mise à jour de la latence';
    }
    
  } catch (err) {
    console.error('Error updating client latency:', err);
    error.value = 'Erreur lors de la mise à jour de la latence';
  } finally {
    updating.value.latency = false;
  }
}

function closeDetails() {
  emit('close');
}

// === FONCTIONS UTILITAIRES ===

function getQualityClass(quality) {
  switch (quality) {
    case 'good': return 'good';
    case 'poor': return 'poor';
    default: return 'unknown';
  }
}

function getQualityText(quality) {
  switch (quality) {
    case 'good': return 'Bonne';
    case 'poor': return 'Faible';
    default: return 'Inconnue';
  }
}

function getQualityDescription(quality) {
  switch (quality) {
    case 'good': return 'Connexion stable';
    case 'poor': return 'Connexion instable';
    default: return 'État inconnu';
  }
}

function formatLastSeen(lastSeen) {
  if (!lastSeen || !lastSeen.sec) {
    return 'Jamais vu';
  }
  
  // Conversion du timestamp en date lisible
  const date = new Date(lastSeen.sec * 1000);
  const now = new Date();
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  
  if (diffSec < 60) {
    return `Il y a ${diffSec} seconde${diffSec > 1 ? 's' : ''}`;
  } else if (diffSec < 3600) {
    const diffMin = Math.floor(diffSec / 60);
    return `Il y a ${diffMin} minute${diffMin > 1 ? 's' : ''}`;
  } else {
    return date.toLocaleString();
  }
}

// === LIFECYCLE ===

onMounted(() => {
  loadClientDetails();
});

// Watcher pour mettre à jour si le client change
watch(() => props.client.id, () => {
  loadClientDetails();
});
</script>

<style scoped>
.details-overlay {
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

.details-modal {
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

/* Contenu des détails */
.details-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.details-section {
  background: #f8f9fa;
  padding: 16px;
}

.details-section h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
}

/* Grille d'informations */
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: white;
}

.info-label {
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.info-value {
  font-weight: bold;
  font-size: 12px;
}

/* Statut de connexion */
.connection-status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.good {
  background-color: #4CAF50;
}

.status-dot.poor {
  background-color: #FF9800;
}

.status-dot.unknown {
  background-color: #9E9E9E;
}

/* Formulaire */
.form-group {
  margin-bottom: 20px;
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

.input-group {
  display: flex;
  gap: 8px;
  align-items: center;
}

.text-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #ddd;
  font-size: 14px;
}

.text-input:focus {
  outline: none;
  border-color: #2196F3;
}

.latency-control {
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

.latency-display {
  display: flex;
  align-items: center;
  gap: 8px;
}

.latency-value {
  font-weight: bold;
  color: #2196F3;
  min-width: 50px;
  text-align: right;
}

.update-btn {
  padding: 6px 12px;
  background: #28a745;
  color: white;
  border: none;
  cursor: pointer;
  font-size: 12px;
}

.update-btn.small {
  padding: 4px 8px;
}

.update-btn:hover:not(:disabled) {
  background: #218838;
}

.update-btn:disabled {
  background: #6c757d;
  cursor: not-allowed;
  opacity: 0.6;
}

.help-text {
  font-size: 12px;
  color: #666;
  margin: 4px 0 0 0;
  line-height: 1.4;
}

.change-indicator {
  color: #FF9800;
  font-weight: bold;
}

/* Actions */
.modal-actions {
  display: flex;
  justify-content: flex-end;
  padding: 20px;
  border-top: 1px solid #eee;
  background: #f8f9fa;
}

.close-action-btn {
  padding: 10px 20px;
  background: #6c757d;
  color: white;
  border: none;
  cursor: pointer;
  font-weight: bold;
}

.close-action-btn:hover {
  background: #545b62;
}
</style>