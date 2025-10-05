<!-- frontend/src/components/settings/DependenciesManager.vue -->
<template>
  <div class="dependencies-manager">
    <!-- Dépendances locales (Milo principal) -->
    <section class="dependencies-section">
      <h1 class="heading-1">{{ $t('Dépendances de Milō') }}</h1>

      <div v-if="localDependenciesLoading" class="loading-state">
        <div class="loading-message text-mono">
          {{ $t('Vérification des versions...') }}
        </div>
      </div>

      <div v-else-if="localDependenciesError" class="error-state">
        <div class="error-message text-mono">
          {{ $t('Erreur lors du chargement des dépendances') }}
        </div>
        <Button variant="secondary" @click="loadLocalDependencies">
          {{ $t('Réessayer') }}
        </Button>
      </div>

      <div v-else class="dependencies-list">
        <div v-for="(dep, key) in localDependencies" :key="key" class="dependency-item">
          <div class="dependency-header">
            <div class="dependency-info">
              <h3 class="dependency-name text-body">{{ dep.name }}</h3>
              <p class="dependency-description text-mono">{{ dep.description }}</p>
            </div>

            <div class="dependency-status">
              <!-- État de mise à jour en cours -->
              <div v-if="isLocalUpdating(key)" class="update-progress-badge text-mono">
                {{ $t('Mise à jour...') }}
              </div>
              <!-- Mise à jour terminée -->
              <div v-else-if="isLocalUpdateCompleted(key)" class="status-badge status-updated text-mono">
                {{ $t('Mise à jour terminée') }}
              </div>
              <!-- Bouton mettre à jour -->
              <Button v-else-if="dep.update_available && canUpdateLocal(key)" variant="primary" size="small"
                @click="startLocalUpdate(key)" :disabled="isAnyUpdateInProgress()">
                {{ $t('Mettre à jour') }}
              </Button>
              <!-- Badges d'état -->
              <div v-else-if="dep.update_available" class="update-badge text-mono">
                {{ $t('Mise à jour disponible') }}
              </div>
              <div v-else-if="getLocalInstallStatus(dep) === 'installed'" class="status-badge status-ok text-mono">
                {{ $t('À jour') }}
              </div>
              <div v-else-if="getLocalInstallStatus(dep) === 'not_installed'" class="status-badge status-error text-mono">
                {{ $t('Non installé') }}
              </div>
              <div v-else class="status-badge status-unknown text-mono">
                {{ $t('État inconnu') }}
              </div>
            </div>
          </div>

          <!-- Message de progression si mise à jour en cours -->
          <div v-if="isLocalUpdating(key)" class="update-progress">
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: getLocalUpdateProgress(key) + '%' }"></div>
            </div>
            <p class="progress-message text-mono">{{ getLocalUpdateMessage(key) }}</p>
          </div>

          <div class="dependency-versions">
            <div class="version-info">
              <span class="version-label text-mono">{{ $t('Installé') }}</span>
              <span class="version-value text-mono">
                <span v-if="getLocalInstalledVersion(dep)">{{ getLocalInstalledVersion(dep) }}</span>
                <span v-else class="text-error">{{ $t('Non disponible') }}</span>
              </span>
            </div>

            <div v-if="dep.update_available" class="version-info">
              <span class="version-label text-mono">{{ $t('Disponible') }}</span>
              <span class="version-value text-mono">
                <span v-if="getLocalLatestVersion(dep)">{{ getLocalLatestVersion(dep) }}</span>
                <span v-else-if="getLocalGitHubStatus(dep) === 'error'" class="text-error">{{ $t('Erreur API') }}</span>
                <span v-else>{{ $t('Chargement...') }}</span>
              </span>
            </div>
          </div>

          <!-- Détails d'erreur si nécessaire -->
          <div v-if="dep.installed?.errors?.length" class="dependency-errors">
            <details class="error-details">
              <summary class="text-mono">{{ $t('Détails des erreurs') }}</summary>
              <ul class="error-list">
                <li v-for="error in dep.installed.errors" :key="error" class="text-mono">{{ error }}</li>
              </ul>
            </details>
          </div>
        </div>
      </div>
    </section>

    <!-- Satellites connectés -->
    <section class="dependencies-section">
      <h1 class="heading-1">{{ $t('Dépendances des satellites connectés') }}</h1>

      <div v-if="satellitesLoading" class="loading-state">
        <div class="loading-message text-mono">
          {{ $t('Recherche des satellites...') }}
        </div>
      </div>

      <div v-else-if="satellitesError" class="error-state">
        <div class="error-message text-mono">
          {{ $t('Erreur lors de la détection des satellites') }}
        </div>
        <Button variant="secondary" @click="loadSatellites">
          {{ $t('Réessayer') }}
        </Button>
      </div>

      <div v-else-if="satellites.length === 0" class="empty-state">
        <p class="text-mono">{{ $t('Aucun satellite détecté sur le réseau') }}</p>
      </div>

      <div v-else class="dependencies-list">
        <div v-for="satellite in satellites" :key="satellite.hostname" class="dependency-item">
          <div class="dependency-header">
            <div class="dependency-info">
              <h3 class="dependency-name text-body">{{ $t('Snapclient de') }} {{ satellite.display_name }}</h3>
              <p class="dependency-description text-mono">Client multiroom {{ satellite.hostname }}</p>
            </div>

            <div class="dependency-status">
              <!-- État de mise à jour en cours -->
              <div v-if="isSatelliteUpdating(satellite.hostname)" class="update-progress-badge text-mono">
                {{ $t('Mise à jour...') }}
              </div>
              <!-- Mise à jour terminée -->
              <div v-else-if="isSatelliteUpdateCompleted(satellite.hostname)" class="status-badge status-updated text-mono">
                {{ $t('Mise à jour terminée') }}
              </div>
              <!-- Bouton mettre à jour -->
              <Button v-else-if="satellite.update_available && satellite.online" variant="primary" size="small"
                @click="startSatelliteUpdate(satellite.hostname)" :disabled="isAnyUpdateInProgress()">
                {{ $t('Mettre à jour') }}
              </Button>
              <!-- Badges d'état -->
              <div v-else-if="satellite.update_available" class="update-badge text-mono">
                {{ $t('Mise à jour disponible') }}
              </div>
              <div v-else-if="satellite.online" class="status-badge status-ok text-mono">
                {{ $t('À jour') }}
              </div>
              <div v-else class="status-badge status-error text-mono">
                {{ $t('Hors ligne') }}
              </div>
            </div>
          </div>

          <!-- Message de progression si mise à jour en cours -->
          <div v-if="isSatelliteUpdating(satellite.hostname)" class="update-progress">
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: getSatelliteUpdateProgress(satellite.hostname) + '%' }"></div>
            </div>
            <p class="progress-message text-mono">{{ getSatelliteUpdateMessage(satellite.hostname) }}</p>
          </div>

          <div class="dependency-versions">
            <div class="version-info">
              <span class="version-label text-mono">{{ $t('Installé') }}</span>
              <span class="version-value text-mono">
                <span v-if="satellite.snapclient_version">{{ satellite.snapclient_version }}</span>
                <span v-else class="text-error">{{ $t('Non disponible') }}</span>
              </span>
            </div>

            <div v-if="satellite.update_available" class="version-info">
              <span class="version-label text-mono">{{ $t('Disponible') }}</span>
              <span class="version-value text-mono">
                <span v-if="satellite.latest_version">{{ satellite.latest_version }}</span>
                <span v-else>{{ $t('Chargement...') }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import axios from 'axios';
import useWebSocket from '@/services/websocket';
import Button from '@/components/ui/Button.vue';
import { useI18n } from '@/services/i18n';

const { t } = useI18n();
const { on } = useWebSocket();

// États locaux
const localDependencies = ref({});
const localDependenciesLoading = ref(false);
const localDependenciesError = ref(false);

const satellites = ref([]);
const satellitesLoading = ref(false);
const satellitesError = ref(false);

// États pour les mises à jour
const localUpdateStates = ref({});
const localCompletedUpdates = ref(new Set());

const satelliteUpdateStates = ref({});
const satelliteCompletedUpdates = ref(new Set());

const supportedLocalUpdates = ['go-librespot', 'snapserver', 'snapclient'];

// === DÉPENDANCES LOCALES ===

async function loadLocalDependencies() {
  if (localDependenciesLoading.value) return;

  try {
    localDependenciesLoading.value = true;
    localDependenciesError.value = false;

    const response = await axios.get('/api/dependencies');

    if (response.data.status === 'success') {
      localDependencies.value = response.data.dependencies || {};
      localDependenciesError.value = false;
    } else {
      localDependenciesError.value = true;
    }
  } catch (error) {
    console.error('Error loading dependencies:', error);
    localDependenciesError.value = true;
  } finally {
    localDependenciesLoading.value = false;
  }
}

function getLocalInstallStatus(dep) {
  return dep.installed?.status || 'unknown';
}

function getLocalInstalledVersion(dep) {
  const versions = dep.installed?.versions || {};
  const versionValues = Object.values(versions);
  return versionValues.length > 0 ? versionValues[0] : null;
}

function getLocalLatestVersion(dep) {
  return dep.latest?.version || null;
}

function getLocalGitHubStatus(dep) {
  return dep.latest?.status || 'unknown';
}

function canUpdateLocal(depKey) {
  return supportedLocalUpdates.includes(depKey);
}

function isLocalUpdating(depKey) {
  return localUpdateStates.value[depKey]?.updating || false;
}

function isLocalUpdateCompleted(depKey) {
  return localCompletedUpdates.value.has(depKey);
}

function getLocalUpdateProgress(depKey) {
  return localUpdateStates.value[depKey]?.progress || 0;
}

function getLocalUpdateMessage(depKey) {
  return localUpdateStates.value[depKey]?.message || '';
}

async function startLocalUpdate(depKey) {
  if (!canUpdateLocal(depKey) || isLocalUpdating(depKey)) return;

  try {
    localUpdateStates.value[depKey] = {
      updating: true,
      progress: 0,
      message: t('Initialisation de la mise à jour...')
    };

    const response = await axios.post(`/api/dependencies/${depKey}/update`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start update');
    }

    console.log(`Update started for ${depKey}: ${response.data.message}`);

  } catch (error) {
    console.error(`Error starting update for ${depKey}:`, error);
    delete localUpdateStates.value[depKey];
  }
}

// === SATELLITES ===

async function loadSatellites() {
  if (satellitesLoading.value) return;

  try {
    satellitesLoading.value = true;
    satellitesError.value = false;

    const response = await axios.get('/api/dependencies/satellites');

    if (response.data.status === 'success') {
      satellites.value = response.data.satellites || [];
      satellitesError.value = false;
    } else {
      satellitesError.value = true;
    }
  } catch (error) {
    console.error('Error loading satellites:', error);
    satellitesError.value = true;
  } finally {
    satellitesLoading.value = false;
  }
}

function isSatelliteUpdating(hostname) {
  return satelliteUpdateStates.value[hostname]?.updating || false;
}

function isSatelliteUpdateCompleted(hostname) {
  return satelliteCompletedUpdates.value.has(hostname);
}

function getSatelliteUpdateProgress(hostname) {
  return satelliteUpdateStates.value[hostname]?.progress || 0;
}

function getSatelliteUpdateMessage(hostname) {
  return satelliteUpdateStates.value[hostname]?.message || '';
}

async function startSatelliteUpdate(hostname) {
  if (isSatelliteUpdating(hostname)) return;

  try {
    satelliteUpdateStates.value[hostname] = {
      updating: true,
      progress: 0,
      message: t('Initialisation de la mise à jour...')
    };

    const response = await axios.post(`/api/dependencies/satellites/${hostname}/update`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start update');
    }

    console.log(`Update started for satellite ${hostname}: ${response.data.message}`);

  } catch (error) {
    console.error(`Error starting update for satellite ${hostname}:`, error);
    delete satelliteUpdateStates.value[hostname];
  }
}

function isAnyUpdateInProgress() {
  return Object.values(localUpdateStates.value).some(state => state.updating) ||
    Object.values(satelliteUpdateStates.value).some(state => state.updating);
}

// === WEBSOCKET HANDLERS ===

const wsListeners = {
  'dependency_update_progress': (msg) => {
    const { dependency, progress, message, status } = msg.data;
    if (dependency && localUpdateStates.value[dependency]) {
      localUpdateStates.value[dependency] = {
        updating: status === 'updating',
        progress: progress || 0,
        message: message || ''
      };
    }
  },
  'dependency_update_complete': (msg) => {
    const { dependency, success, message, error, old_version, new_version } = msg.data;

    if (dependency) {
      delete localUpdateStates.value[dependency];

      if (success) {
        console.log(`Update completed for ${dependency}: ${old_version} → ${new_version}`);
        localCompletedUpdates.value.add(dependency);
        loadLocalDependencies();
      } else {
        console.error(`Update failed for ${dependency}: ${error}`);
      }
    }
  },
  'satellite_update_progress': (msg) => {
    const { hostname, progress, message, status } = msg.data;
    if (hostname && satelliteUpdateStates.value[hostname]) {
      satelliteUpdateStates.value[hostname] = {
        updating: status === 'updating',
        progress: progress || 0,
        message: message || ''
      };
    }
  },
  'satellite_update_complete': (msg) => {
    const { hostname, success, message, error, new_version } = msg.data;

    if (hostname) {
      delete satelliteUpdateStates.value[hostname];

      if (success) {
        console.log(`Update completed for satellite ${hostname}: ${new_version}`);
        satelliteCompletedUpdates.value.add(hostname);
        loadSatellites();
      } else {
        console.error(`Update failed for satellite ${hostname}: ${error}`);
      }
    }
  }
};

// === LIFECYCLE ===

onMounted(async () => {
  await loadLocalDependencies();
  await loadSatellites();

  // Enregistrer les listeners WebSocket
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('dependencies', eventType, handler);
  });
});
</script>

<style scoped>
.dependencies-manager {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.dependencies-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-06) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.loading-state,
.error-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-05);
  text-align: center;
}

.loading-message,
.error-message {
  color: var(--color-text-secondary);
}

.empty-state .text-mono {
  color: var(--color-text-secondary);
}

.dependencies-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.dependency-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.dependency-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-03);
}

.dependency-info {
  flex: 1;
}

.dependency-name {
  color: var(--color-text);
  margin-bottom: var(--space-01);
}

.dependency-description {
  color: var(--color-text-secondary);
}

.dependency-status {
  flex-shrink: 0;
}

.status-error,
.status-unknown {
  color: var(--color-error);
}

.status-ok {
  color: var(--color-text-secondary);
}

.status-updated {
  color: var(--color-brand);
}

.update-badge {
  color: var(--color-text-secondary);
}

.update-progress-badge {
  color: var(--color-brand);
}

.update-progress {
  margin-top: var(--space-03);
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.progress-bar {
  width: 100%;
  height: 6px;
  background: var(--color-background);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-brand);
  border-radius: var(--radius-full);
  transition: width var(--transition-normal);
}

.progress-message {
  color: var(--color-text-secondary);
  text-align: center;
}

.dependency-versions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

.version-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.version-label {
  color: var(--color-text-secondary);
}

.version-value {
  color: var(--color-text);
}

.dependency-errors {
  margin-top: var(--space-02);
}

.error-details {
  background: var(--color-background);
  border-radius: var(--radius-02);
  padding: var(--space-02);
}

.error-details summary {
  cursor: pointer;
  color: var(--color-text-secondary);
}

.error-list {
  margin: var(--space-02) 0 0 0;
  padding: 0;
  list-style: none;
}

.error-list li {
  color: var(--color-error);
  padding: var(--space-01) 0;
}

.text-error {
  color: var(--color-destructive);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .dependency-versions {
    grid-template-columns: 1fr;
    gap: var(--space-02);
  }
}
</style>