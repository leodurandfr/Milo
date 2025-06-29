<!-- frontend/src/components/snapcast/SnapclientDetails.vue -->
<template>
  <div class="snapclient-details">
    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p class="text-body">Chargement des détails...</p>
    </div>

    <div v-else-if="error" class="error-state">
      <p class="error-message text-body">{{ error }}</p>
      <Button variant="secondary" @click="loadClientDetails">Réessayer</Button>
    </div>

    <div v-else class="details-content">
      <!-- Configuration éditable -->
      <section class="config-section">
        <h2 class="heading-2">Configuration</h2>

        <!-- Nom du client -->
        <div class="form-group">
          <label for="client-name" class="text-mono">Nom affiché</label>
          <input id="client-name" type="text" v-model="editableConfig.name" :placeholder="clientDetails.host"
            class="text-input text-body" maxlength="50">
        </div>

        <!-- Latence -->
        <div class="form-group">
          <div class="latency-info-wrapper">
            <label for="client-latency" class="text-mono">Latence (ms)</label>
            <span v-if="clientDetails.latency !== editableConfig.latency" class="change-indicator text-mono">
              Actuellement: {{ clientDetails.latency }}ms
            </span>
          </div>
          <div class="input-with-value">
            <RangeSlider v-model="editableConfig.latency" :min="0" :max="500" :step="5" class="range-input" />
            <span class="text-mono">{{ editableConfig.latency }}ms</span>

          </div>
        </div>
      </section>

      <!-- Informations générales -->
      <section class="config-section">
        <h2 class="heading-2">Informations générales</h2>

        <div class="info-grid">
          <div class="info-item">
            <span class="info-label text-mono">Adresse IP</span>
            <span class="info-value text-mono">{{ clientDetails.ip }}</span>
          </div>

          <div class="info-item">
            <span class="info-label text-mono">Nom</span>
            <span class="info-value text-mono">{{ clientDetails.host }}</span>
          </div>

          <div class="info-item">
            <span class="info-label text-mono">Version Snapclient</span>
            <span class="info-value text-mono">{{ clientDetails.snapclient_info?.version || 'Inconnu' }}</span>
          </div>

          <div class="info-item">
            <span class="info-label text-mono">Connexion</span>
            <div class="connection-status">
              <span :class="['status-dot', getQualityClass(clientDetails.connection_quality)]"></span>
              <span class="text-mono">{{ getQualityText(clientDetails.connection_quality) }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- Actions -->
      <Button variant="primary" :disabled="!hasChanges || isUpdating" @click="validateChanges">
        {{ isUpdating ? 'Validation...' : 'Valider' }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

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

// Détection des changements
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
.snapclient-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.loading-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-07) var(--space-05);
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-background-strong);
  border-top: 3px solid var(--color-brand);
  border-radius: var(--radius-full);
  animation: spin 1s linear infinite;
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
  color: var(--color-error);
}

.details-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.config-section {
  display: flex;
  flex-direction: column;
  background: var(--color-background-neutral);
  padding: var(--space-05);
  border-radius: var(--radius-04);
  gap: var(--space-05);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.text-input {
  padding: var(--space-03) var(--space-04);
  border: 2px solid var(--color-background-strong);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text);
}

.text-input:focus {
  outline: none;
  border-color: var(--color-brand);
}

.text-input::placeholder {
  color: var(--color-text-light);
}

.input-with-value {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.input-with-value .text-mono {
  color: var(--color-text-secondary);
  text-align: right;
  width: var(--space-08);
}

.range-input {
  flex: 1;
}



.help-text {
  color: var(--color-text-light);
}

.change-indicator {
  color: var(--color-brand);
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

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
}

.connection-status {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
}

.status-dot.good {
  background-color: var(--color-success);
}

.status-dot.poor {
  background-color: var(--color-warning);
}

.status-dot.unknown {
  background-color: var(--color-text-light);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>