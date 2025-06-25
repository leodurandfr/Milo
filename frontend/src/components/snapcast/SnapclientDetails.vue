<!-- frontend/src/components/snapcast/SnapclientDetails.vue -->
<template>
  <div class="snapclient-details">
    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p>Chargement des détails...</p>
    </div>

    <div v-else-if="error" class="error-state">
      <p class="error-message">{{ error }}</p>
      <button @click="loadClientDetails" class="retry-btn">Réessayer</button>
    </div>

    <div v-else class="details-content">
      <!-- Configuration éditable - MAINTENANT EN PREMIER -->
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

      <!-- Informations générales - MAINTENANT EN SECOND -->
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
          
          <!-- AJOUT : Version Snapclient -->
          <div class="info-item">
            <span class="info-label">Version Snapclient:</span>
            <span class="info-value">{{ clientDetails.snapclient_info?.version || 'Inconnu' }}</span>
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

      <!-- Actions -->
      <div class="modal-actions">
        <button 
          @click="validateChanges"
          :disabled="!hasChanges || isUpdating"
          class="validate-btn"
        >
          {{ isUpdating ? 'Validation...' : 'Valider' }}
        </button>
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

// État local
const loading = ref(false);
const error = ref(null);
const clientDetails = ref({});
const isUpdating = ref(false);

// Configuration éditable
const editableConfig = ref({
  name: '',
  latency: 0
});

// Configuration originale pour détecter les changements
const originalConfig = ref({
  name: '',
  latency: 0
});

// Détection des changements - OPTIMISÉ
const hasChanges = computed(() => {
  return editableConfig.value.name.trim() !== originalConfig.value.name ||
         editableConfig.value.latency !== originalConfig.value.latency;
});

// === MÉTHODES ===

async function loadClientDetails() {
  loading.value = true;
  error.value = null;
  
  try {
    console.log('📋 Loading details for client:', props.client.id);
    const response = await axios.get(`/api/routing/snapcast/client/${props.client.id}/details`);
    clientDetails.value = response.data.client;
    
    // Initialiser la configuration éditable et originale
    const configData = {
      name: clientDetails.value.name || '',
      latency: clientDetails.value.latency || 0
    };
    
    editableConfig.value = { ...configData };
    originalConfig.value = { ...configData };
    
    console.log('✅ Client details loaded:', clientDetails.value);
    
  } catch (err) {
    console.error('Error loading client details:', err);
    error.value = 'Impossible de charger les détails du client';
  } finally {
    loading.value = false;
  }
}

// NOUVEAU : Fonction de validation centralisée
async function validateChanges() {
  if (!hasChanges.value) return;
  
  isUpdating.value = true;
  error.value = null;
  
  try {
    console.log('💾 Validating changes for client:', props.client.id);
    const updates = [];
    
    // Mettre à jour le nom si changé
    if (editableConfig.value.name.trim() !== originalConfig.value.name) {
      console.log('📝 Updating name:', editableConfig.value.name.trim());
      updates.push(updateClientName());
    }
    
    // Mettre à jour la latence si changée
    if (editableConfig.value.latency !== originalConfig.value.latency) {
      console.log('⏱️ Updating latency:', editableConfig.value.latency);
      updates.push(updateClientLatency());
    }
    
    // Exécuter toutes les mises à jour en parallèle
    await Promise.all(updates);
    
    // Mettre à jour la config originale
    originalConfig.value = { ...editableConfig.value };
    
    console.log('✅ Client configuration updated successfully');
    
  } catch (err) {
    console.error('Error validating changes:', err);
    error.value = 'Erreur lors de la validation des modifications';
  } finally {
    isUpdating.value = false;
  }
}

async function updateClientName() {
  const response = await axios.post(`/api/routing/snapcast/client/${props.client.id}/name`, {
    name: editableConfig.value.name.trim()
  });
  
  if (response.data.status === 'success') {
    clientDetails.value.name = editableConfig.value.name.trim();
  } else {
    throw new Error('Erreur lors de la mise à jour du nom');
  }
}

async function updateClientLatency() {
  const response = await axios.post(`/api/routing/snapcast/client/${props.client.id}/latency`, {
    latency: editableConfig.value.latency
  });
  
  if (response.data.status === 'success') {
    clientDetails.value.latency = editableConfig.value.latency;
  } else {
    throw new Error('Erreur lors de la mise à jour de la latence');
  }
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

// === LIFECYCLE ===

onMounted(() => {
  loadClientDetails();
});

// Watcher pour mettre à jour si le client change
watch(() => props.client.id, () => {
  console.log('🔄 Client changed, reloading details');
  loadClientDetails();
}, { immediate: false });
</script>

<style scoped>
/* Styles adaptés pour intégration dans le système modal */
.snapclient-details {
  display: flex;
  flex-direction: column;
  gap: 16px;
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
  border-radius: 4px;
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
  border-radius: 16px;
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
  border-radius: 4px;
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
  border-radius: 4px;
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
  gap: 12px;
  padding-top: 20px;
  border-top: 1px solid #eee;
}

.validate-btn {
  padding: 10px 20px;
  border: none;
  cursor: pointer;
  font-weight: bold;
  border-radius: 4px;
  background: #28a745;
  color: white;
}

.validate-btn:hover:not(:disabled) {
  background: #218838;
}

.validate-btn:disabled {
  background: #6c757d;
  cursor: not-allowed;
  opacity: 0.6;
}
</style>