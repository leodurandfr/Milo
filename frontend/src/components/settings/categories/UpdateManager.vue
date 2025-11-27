<!-- frontend/src/components/settings/categories/UpdateManager.vue -->
<template>
  <div class="update-manager">
    <!-- Local programs (main Milo) -->
    <section class="settings-section">
      <div v-if="localProgramsLoading" class="loading-state">
        <div class="loading-message text-mono">
          {{ $t('updates.checking') }}
        </div>
      </div>

      <div v-else-if="localProgramsError" class="error-state">
        <div class="error-message text-mono">
          {{ $t('updates.error') }}
        </div>
        <Button variant="background-strong" @click="loadLocalPrograms">
          {{ $t('updates.retry') }}
        </Button>
      </div>

      <div v-else class="programs-list">
        <!-- Milo OS - displayed first, before the title -->
        <div v-if="localPrograms.milo" class="program-item">
          <AppIcon :name="getProgramIcon('milo')" :size="52" class="program-icon" />
          <span class="program-name heading-3">{{ localPrograms.milo.name }}</span>
          <span class="program-version text-mono">
            milo {{ getLocalInstalledVersion(localPrograms.milo) || $t('updates.notAvailable') }}
            <template v-if="localPrograms.milo.update_available && !isLocalUpdating('milo') && !isLocalUpdateCompleted('milo')">
              <span class="version-new">> {{ getLocalLatestVersion(localPrograms.milo) }}</span>
            </template>
          </span>

          <!-- Progress bar (shown during update) -->
          <div v-if="isLocalUpdating('milo')" class="update-progress">
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: getLocalUpdateProgress('milo') + '%' }"></div>
            </div>
            <p class="progress-message text-mono">{{ getLocalUpdateMessage('milo') }}</p>
          </div>

          <!-- Update button or Up-to-date button -->
          <Button
            v-else-if="localPrograms.milo.update_available && canUpdateLocal('milo') && !isLocalUpdateCompleted('milo')"
            variant="brand"
            class="program-button"
            @click="startLocalUpdate('milo')"
            :disabled="isAnyUpdateInProgress()">
            {{ $t('updates.update') }}
          </Button>
          <Button v-else variant="background-strong" class="program-button" disabled>
            {{ $t('updates.upToDate') }}
          </Button>
        </div>

        <!-- Title for other programs -->
        <h1 class="heading-2">{{ $t('updates.miloTitle') }}</h1>

        <!-- Other programs container -->
        <div class="programs-container">
          <template v-for="(program, key) in localPrograms" :key="key">
            <div v-if="key !== 'milo'" class="program-item">
              <AppIcon :name="getProgramIcon(key)" :size="52" class="program-icon" />
              <span class="program-name heading-3">{{ getProgramDisplayName(program, key) }}</span>
              <span class="program-version text-mono">
                {{ key }} {{ getLocalInstalledVersion(program) || $t('updates.notAvailable') }}
                <template v-if="program.update_available && !isLocalUpdating(key) && !isLocalUpdateCompleted(key)">
                  <span class="version-new">> {{ getLocalLatestVersion(program) }}</span>
                </template>
              </span>

              <!-- Progress bar (shown during update) -->
              <div v-if="isLocalUpdating(key)" class="update-progress">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: getLocalUpdateProgress(key) + '%' }"></div>
                </div>
                <p class="progress-message text-mono">{{ getLocalUpdateMessage(key) }}</p>
              </div>

              <!-- Update button or Up-to-date button -->
              <Button
                v-else-if="program.update_available && canUpdateLocal(key) && !isLocalUpdateCompleted(key)"
                variant="brand"
                class="program-button"
                @click="startLocalUpdate(key)"
                :disabled="isAnyUpdateInProgress()">
                {{ $t('updates.update') }}
              </Button>
              <Button v-else variant="background-strong" class="program-button" disabled>
                {{ $t('updates.upToDate') }}
              </Button>
            </div>
          </template>
        </div>
      </div>
    </section>

    <!-- Connected satellites -->
    <section v-if="isMultiroomEnabled" class="settings-section">
      <h1 class="heading-2">{{ $t('updates.satellitesTitle') }}</h1>

      <div v-if="satellitesLoading" class="loading-state">
        <div class="loading-message text-mono">
          {{ $t('updates.searchingSatellites') }}
        </div>
      </div>

      <div v-else-if="satellitesError" class="error-state">
        <div class="error-message text-mono">
          {{ $t('updates.errorDetectingSatellites') }}
        </div>
        <Button variant="background-strong" @click="loadSatellites">
          {{ $t('updates.retry') }}
        </Button>
      </div>

      <div v-else-if="satellites.length === 0" class="empty-state">
        <p class="text-mono">{{ $t('updates.noSatellites') }}</p>
      </div>

      <div v-else class="programs-list">
        <div v-for="satellite in satellites" :key="satellite.hostname" class="program-item">
          <AppIcon name="multiroom" :size="52" class="program-icon" />
          <span class="program-name heading-3">{{ $t('updates.snapclientOf') }} {{ satellite.display_name }}</span>
          <span class="program-version text-mono">
            snapclient {{ satellite.snapclient_version || $t('updates.notAvailable') }}
            <template v-if="satellite.update_available && !isSatelliteUpdating(satellite.hostname) && !isSatelliteUpdateCompleted(satellite.hostname)">
              <span class="version-new">> {{ satellite.latest_version }}</span>
            </template>
          </span>

          <!-- Progress bar (shown during update) -->
          <div v-if="isSatelliteUpdating(satellite.hostname)" class="update-progress">
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: getSatelliteUpdateProgress(satellite.hostname) + '%' }"></div>
            </div>
            <p class="progress-message text-mono">{{ getSatelliteUpdateMessage(satellite.hostname) }}</p>
          </div>

          <!-- Update button or Up-to-date button -->
          <Button
            v-else-if="satellite.update_available && satellite.online && !isSatelliteUpdateCompleted(satellite.hostname)"
            variant="brand"
            class="program-button"
            @click="startSatelliteUpdate(satellite.hostname)"
            :disabled="isAnyUpdateInProgress()">
            {{ $t('updates.update') }}
          </Button>
          <Button v-else variant="background-strong" class="program-button" disabled>
            {{ $t('updates.upToDate') }}
          </Button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import axios from 'axios';
import useWebSocket from '@/services/websocket';
import Button from '@/components/ui/Button.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

function getProgramIcon(programKey) {
  const iconMap = {
    'milo': 'milo',
    'go-librespot': 'librespot',
    'snapserver': 'multiroom',
    'snapclient': 'multiroom',
    'bluez-alsa': 'bluetooth',
    'roc-toolkit': 'roc'
  };
  return iconMap[programKey] || 'settings';
}

function getProgramDisplayName(program, key) {
  const nameOverrides = {
    'bluez-alsa': 'Bluetooth',
    'roc-toolkit': 'Récepteur audio macOS'
  };
  return nameOverrides[key] || program.name;
}

const { t } = useI18n();
const { on } = useWebSocket();
const unifiedStore = useUnifiedAudioStore();

const isMultiroomEnabled = computed(() => unifiedStore.systemState.multiroom_enabled);

// Local state
const localPrograms = ref({});
const localProgramsLoading = ref(false);
const localProgramsError = ref(false);

const satellites = ref([]);
const satellitesLoading = ref(false);
const satellitesError = ref(false);

// Update states
const localUpdateStates = ref({});
const localCompletedUpdates = ref(new Set());

const satelliteUpdateStates = ref({});
const satelliteCompletedUpdates = ref(new Set());

const supportedLocalUpdates = ['milo', 'go-librespot', 'snapserver', 'snapclient'];

// === LOCAL PROGRAMS ===

async function loadLocalPrograms() {
  if (localProgramsLoading.value) return;

  try {
    localProgramsLoading.value = true;
    localProgramsError.value = false;

    const response = await axios.get('/api/programs');

    if (response.data.status === 'success') {
      localPrograms.value = response.data.programs || {};
      localProgramsError.value = false;
    } else {
      localProgramsError.value = true;
    }
  } catch (error) {
    console.error('Error loading programs:', error);
    localProgramsError.value = true;
  } finally {
    localProgramsLoading.value = false;
  }
}

function getLocalInstallStatus(program) {
  return program.installed?.status || 'unknown';
}

function getLocalInstalledVersion(program) {
  const versions = program.installed?.versions || {};
  const versionValues = Object.values(versions);
  return versionValues.length > 0 ? versionValues[0] : null;
}

function getLocalLatestVersion(program) {
  return program.latest?.version || null;
}

function getLocalGitHubStatus(program) {
  return program.latest?.status || 'unknown';
}

function canUpdateLocal(programKey) {
  return supportedLocalUpdates.includes(programKey);
}

function isLocalUpdating(programKey) {
  return localUpdateStates.value[programKey]?.updating || false;
}

function isLocalUpdateCompleted(programKey) {
  return localCompletedUpdates.value.has(programKey);
}

function getLocalUpdateProgress(programKey) {
  return localUpdateStates.value[programKey]?.progress || 0;
}

function getLocalUpdateMessage(programKey) {
  return localUpdateStates.value[programKey]?.message || '';
}

async function startLocalUpdate(programKey) {
  if (!canUpdateLocal(programKey) || isLocalUpdating(programKey)) return;

  try {
    localUpdateStates.value[programKey] = {
      updating: true,
      progress: 0,
      message: t('updates.updatingInit')
    };

    const response = await axios.post(`/api/programs/${programKey}/update`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start update');
    }

    console.log(`Update started for ${programKey}: ${response.data.message}`);

  } catch (error) {
    console.error(`Error starting update for ${programKey}:`, error);
    delete localUpdateStates.value[programKey];
  }
}

// === SATELLITES ===

async function loadSatellites() {
  if (satellitesLoading.value) return;

  try {
    satellitesLoading.value = true;
    satellitesError.value = false;

    const response = await axios.get('/api/programs/satellites');

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
      message: t('updates.updatingInit')
    };

    const response = await axios.post(`/api/programs/satellites/${hostname}/update`);

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
  'program_update_progress': (msg) => {
    const { program, progress, message, status } = msg.data;
    if (program && localUpdateStates.value[program]) {
      localUpdateStates.value[program] = {
        updating: status === 'updating',
        progress: progress || 0,
        message: message || ''
      };
    }
  },
  'program_update_complete': (msg) => {
    const { program, success, message, error, old_version, new_version } = msg.data;

    if (program) {
      delete localUpdateStates.value[program];

      if (success) {
        console.log(`Update completed for ${program}: ${old_version} → ${new_version}`);
        localCompletedUpdates.value.add(program);
        loadLocalPrograms();
      } else {
        console.error(`Update failed for ${program}: ${error}`);
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
  await loadLocalPrograms();
  await loadSatellites();

  // Register WebSocket listeners
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('programs', eventType, handler);
  });
});
</script>

<style scoped>
.update-manager {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
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

.programs-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.programs-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.program-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-areas:
    "icon name button"
    "icon version button";
  align-items: center;
  gap: var(--space-01) var(--space-04);
}

.program-icon {
  grid-area: icon;
}

.program-name {
  grid-area: name;
  color: var(--color-text);
  align-self: end;
}

.program-version {
  grid-area: version;
  color: var(--color-text-secondary);
  align-self: start;
}

.version-new {
  color: var(--color-brand);
}

.program-button,
.update-progress {
  grid-area: button;
}

.update-progress {
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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .program-item {
    grid-template-columns: auto 1fr;
    grid-template-areas:
      "icon name"
      "version version"
      "button button";
    gap: var(--space-02) var(--space-03);
  }

  .program-icon {
    width: 32px !important;
    height: 32px !important;
  }

  .program-name {
    align-self: center;
  }

  .program-version {
    align-self: center;
  }

  .program-button {
    width: 100%;
  }
}
</style>