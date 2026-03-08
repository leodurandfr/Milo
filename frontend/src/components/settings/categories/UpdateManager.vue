<!-- frontend/src/components/settings/categories/UpdateManager.vue -->
<template>
  <SettingsContainer>
    <!-- Error state -->
    <template v-if="localProgramsError">
      <div class="error-state">
        <div class="error-message text-mono">
          {{ t('updates.error') }}
        </div>
        <Button size="small" variant="background-strong" @click="loadLocalPrograms">
          {{ t('updates.retry') }}
        </Button>
      </div>
    </template>

    <template v-else>
      <!-- Section 1: Operating System (Milo OS only) -->
      <SettingsSection v-if="localProgramsLoading || localPrograms.milo" :title="t('updates.osTitle')">
        <div class="crossfade-wrapper">
          <Transition name="crossfade">
            <div v-if="localProgramsLoading" key="skeleton" class="programs-list">
              <div class="program-item-skeleton">
                <div class="skeleton-icon shimmer"></div>
                <div class="skeleton-text shimmer skeleton-name"></div>
                <div class="skeleton-text shimmer skeleton-version"></div>
                <div class="skeleton-button shimmer"></div>
              </div>
            </div>

            <div v-else key="content" class="programs-list">
              <div class="program-item">
                <div class="program-info">
                  <AppIcon :name="getProgramIcon('milo')" :size="48" class="program-icon" />
                  <span class="program-name heading-4">{{ localPrograms.milo.name }}</span>
                  <span class="program-version text-mono">
                    milo {{ getLocalInstalledVersion(localPrograms.milo) || t('updates.notAvailable') }}
                    <template
                      v-if="localPrograms.milo.update_available && !isLocalUpdating('milo') && !isLocalUpdateCompleted('milo')">
                      <span class="version-new">> {{ getLocalLatestVersion(localPrograms.milo) }}</span>
                    </template>
                  </span>
                </div>

                <!-- Progress bar (shown during update) -->
                <div v-if="isLocalUpdating('milo')" class="update-progress">
                  <p class="progress-message text-mono-small">{{ getLocalUpdateMessage('milo') }}</p>
                  <div class="progress-bar">
                    <div class="progress-fill" :style="{ width: getLocalUpdateProgress('milo') + '%' }"></div>
                  </div>
                </div>

                <!-- Update button or Up-to-date button -->
                <Button
                  v-else-if="localPrograms.milo.update_available && canUpdateLocal('milo') && !isLocalUpdateCompleted('milo')"
                  size="small" variant="brand" class="program-button" @click="startLocalUpdate('milo')"
                  :disabled="isAnyUpdateInProgress()">
                  {{ t('updates.update') }}
                </Button>
                <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                  {{ t('updates.upToDate') }}
                </Button>
              </div>
            </div>
          </Transition>
        </div>
      </SettingsSection>

      <!-- Section 2: Milo Programs -->
      <SettingsSection :title="t('updates.programsTitle')">
        <div class="crossfade-wrapper">
          <Transition name="crossfade">
            <div v-if="localProgramsLoading" key="skeleton" class="programs-list">
              <div v-for="n in 5" :key="n" class="program-item-skeleton">
                <div class="skeleton-icon shimmer"></div>
                <div class="skeleton-text shimmer skeleton-name"></div>
                <div class="skeleton-text shimmer skeleton-version"></div>
                <div class="skeleton-button shimmer"></div>
              </div>
            </div>

            <div v-else key="content" class="programs-list">
              <template v-for="(program, key) in localPrograms" :key="key">
                <div v-if="key !== 'milo'" class="program-item">
                  <div class="program-info">
                    <AppIcon :name="getProgramIcon(key)" :size="48" class="program-icon" />
                    <span class="program-name heading-4">{{ getProgramDisplayName(program, key) }}</span>
                    <span class="program-version text-mono">
                      {{ getVersionLabel(key) }} {{ getLocalInstalledVersion(program) || t('updates.notAvailable') }}
                      <template
                        v-if="program.update_available && !isLocalUpdating(key) && !isLocalUpdateCompleted(key)">
                        <span class="version-new">> {{ getLocalLatestVersion(program) }}</span>
                      </template>
                    </span>
                  </div>

                  <!-- Progress bar (shown during update) -->
                  <div v-if="isLocalUpdating(key)" class="update-progress">
                    <p class="progress-message text-mono-small">{{ getLocalUpdateMessage(key) }}</p>
                    <div class="progress-bar">
                      <div class="progress-fill" :style="{ width: getLocalUpdateProgress(key) + '%' }"></div>
                    </div>
                  </div>

                  <!-- Update button or Up-to-date button -->
                  <Button v-else-if="program.update_available && canUpdateLocal(key) && !isLocalUpdateCompleted(key)"
                    size="small" variant="brand" class="program-button" @click="startLocalUpdate(key)"
                    :disabled="isAnyUpdateInProgress()">
                    {{ t('updates.update') }}
                  </Button>
                  <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date"
                    disabled>
                    {{ t('updates.upToDate') }}
                  </Button>
                </div>
              </template>
            </div>
          </Transition>
        </div>
      </SettingsSection>

      <!-- Section 3: Satellite Programs (loading/error) -->
      <SettingsSection v-if="isMultiroomEnabled && (satellitesLoading || satellitesError)"
        :title="t('updates.satelliteProgramsTitle')">
        <div v-if="satellitesError" class="error-state">
          <div class="error-message text-mono">
            {{ t('updates.errorDetectingSatellites') }}
          </div>
          <Button size="small" variant="background-strong" @click="loadSatellites">
            {{ t('updates.retry') }}
          </Button>
        </div>

        <div v-else class="programs-list">
          <div class="program-item-skeleton">
            <div class="skeleton-icon shimmer"></div>
            <div class="skeleton-text shimmer skeleton-name"></div>
            <div class="skeleton-text shimmer skeleton-version"></div>
          </div>
        </div>
      </SettingsSection>

      <!-- Section 3: Satellite Programs (one section per client) -->
      <template v-if="isMultiroomEnabled && !satellitesLoading && !satellitesError">
        <SettingsSection v-for="satellite in satellites" :key="satellite.hostname">
          <template #header>
            <h2 class="heading-2">Programmes de Milō Client <span class="satellite-name">·&nbsp;{{
              satellite.display_name }}</span></h2>
          </template>
          <div class="programs-list">
            <!-- Milo Client row -->
            <div class="program-item">
              <div class="program-info">
                <AppIcon name="milo-client" :size="48" class="program-icon" />
                <span class="program-name heading-4">Milō Client</span>
                <span class="program-version text-mono">
                  milo-client {{ extractBaseTag(satellite.app_version) || t('updates.notAvailable') }}
                  <template
                    v-if="satellite.app_update_available && !isSatelliteAppUpdating(satellite.hostname) && !isSatelliteAppUpdateCompleted(satellite.hostname)">
                    <span class="version-new">> {{ extractBaseTag(satellite.server_version) }}</span>
                  </template>
                </span>
              </div>

              <div v-if="isSatelliteAppUpdating(satellite.hostname)" class="update-progress">
                <p class="progress-message text-mono-small">{{ getSatelliteAppUpdateMessage(satellite.hostname) }}</p>
                <div class="progress-bar">
                  <div class="progress-fill"
                    :style="{ width: getSatelliteAppUpdateProgress(satellite.hostname) + '%' }">
                  </div>
                </div>
              </div>

              <Button
                v-else-if="satellite.app_update_available && satellite.online && !isSatelliteAppUpdateCompleted(satellite.hostname)"
                size="small" variant="brand" class="program-button" @click="startSatelliteAppUpdate(satellite.hostname)"
                :disabled="isAnyUpdateInProgress()">
                {{ t('updates.update') }}
              </Button>
              <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                {{ t('updates.upToDate') }}
              </Button>
            </div>

            <!-- Snapclient row -->
            <div class="program-item">
              <div class="program-info">
                <AppIcon name="multiroom" :size="48" class="program-icon" />
                <span class="program-name heading-4">Multiroom Client</span>
                <span class="program-version text-mono">
                  snapclient {{ satellite.snapclient_version || t('updates.notAvailable') }}
                  <template
                    v-if="satellite.update_available && !isSatelliteUpdating(satellite.hostname) && !isSatelliteUpdateCompleted(satellite.hostname)">
                    <span class="version-new">> {{ satellite.latest_version }}</span>
                  </template>
                </span>
              </div>

              <div v-if="isSatelliteUpdating(satellite.hostname)" class="update-progress">
                <p class="progress-message text-mono-small">{{ getSatelliteUpdateMessage(satellite.hostname) }}</p>
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: getSatelliteUpdateProgress(satellite.hostname) + '%' }">
                  </div>
                </div>
              </div>

              <Button
                v-else-if="satellite.update_available && satellite.online && !isSatelliteUpdateCompleted(satellite.hostname)"
                size="small" variant="brand" class="program-button" @click="startSatelliteUpdate(satellite.hostname)"
                :disabled="isAnyUpdateInProgress()">
                {{ t('updates.update') }}
              </Button>
              <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                {{ t('updates.upToDate') }}
              </Button>
            </div>
          </div>
        </SettingsSection>
      </template>
    </template>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import axios from 'axios';
import useWebSocket from '@/services/websocket';
import Button from '@/components/ui/Button.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

function getProgramIcon(programKey) {
  const iconMap = {
    'milo': 'milo',
    'go-librespot': 'spotify',
    'multiroom': 'multiroom',
    'bluez-alsa': 'bluetooth',
    'roc-toolkit': 'mac',
    'shairport-sync': 'airplay'
  };
  return iconMap[programKey] || 'settings';
}

function getProgramDisplayName(program, key) {
  const nameOverrides = {
    'go-librespot': t('audioSources.spotify'),
    'multiroom': t('audioSources.multiroom'),
    'bluez-alsa': t('audioSources.bluetooth'),
    'roc-toolkit': t('audioSources.macOS'),
    'shairport-sync': t('audioSources.airplay')
  };
  return nameOverrides[key] || program.name;
}

function getVersionLabel(key) {
  const labelOverrides = {
    'multiroom': 'snapcast',
    'shairport-sync': 'shairport-sync'
  };
  return labelOverrides[key] || key;
}

function extractBaseTag(version) {
  if (!version) return null;
  // git describe format: <tag>-<N>-g<hash> or just <tag>
  const parts = version.split('-');
  if (parts.length >= 3 && parts[parts.length - 1].startsWith('g')) {
    return parts.slice(0, -2).join('-');
  }
  return version;
}

const { t } = useI18n();
const { on } = useWebSocket();
const unifiedStore = useUnifiedAudioStore();

const isMultiroomEnabled = computed(() => unifiedStore.systemState.multiroom_enabled);

// Local state
const localPrograms = ref({});
const localProgramsLoading = ref(true);
const localProgramsError = ref(false);

const satellites = ref([]);
const satellitesLoading = ref(false);
const satellitesError = ref(false);

// Update states
const localUpdateStates = ref({});
const localCompletedUpdates = ref(new Set());

const satelliteUpdateStates = ref({});
const satelliteCompletedUpdates = ref(new Set());

const satelliteAppUpdateStates = ref({});
const satelliteAppCompletedUpdates = ref(new Set());

const supportedLocalUpdates = ['milo', 'go-librespot', 'shairport-sync', 'multiroom'];

// === LOCAL PROGRAMS ===

async function loadLocalPrograms() {
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

function getLocalInstalledVersion(program) {
  const versions = program.installed?.versions || {};
  const versionValues = Object.values(versions);
  return versionValues.length > 0 ? versionValues[0] : null;
}

function getLocalLatestVersion(program) {
  return program.latest?.version || null;
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

  } catch (error) {
    console.error(`Error starting update for satellite ${hostname}:`, error);
    delete satelliteUpdateStates.value[hostname];
  }
}

// === SATELLITE APP UPDATES ===

function isSatelliteAppUpdating(hostname) {
  return satelliteAppUpdateStates.value[hostname]?.updating || false;
}

function isSatelliteAppUpdateCompleted(hostname) {
  return satelliteAppCompletedUpdates.value.has(hostname);
}

function getSatelliteAppUpdateProgress(hostname) {
  return satelliteAppUpdateStates.value[hostname]?.progress || 0;
}

function getSatelliteAppUpdateMessage(hostname) {
  return satelliteAppUpdateStates.value[hostname]?.message || '';
}

async function startSatelliteAppUpdate(hostname) {
  if (isSatelliteAppUpdating(hostname)) return;

  try {
    satelliteAppUpdateStates.value[hostname] = {
      updating: true,
      progress: 0,
      message: t('updates.updatingInit')
    };

    const response = await axios.post(`/api/programs/satellites/${hostname}/update-app`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start app update');
    }

  } catch (error) {
    console.error(`Error starting app update for satellite ${hostname}:`, error);
    delete satelliteAppUpdateStates.value[hostname];
  }
}

function isAnyUpdateInProgress() {
  return Object.values(localUpdateStates.value).some(state => state.updating) ||
    Object.values(satelliteUpdateStates.value).some(state => state.updating) ||
    Object.values(satelliteAppUpdateStates.value).some(state => state.updating);
}

// === WEBSOCKET HANDLERS ===

const wsListeners = {
  'program_update_progress': (msg) => {
    const { program, progress, message, status } = msg.data;
    if (program && localUpdateStates.value[program]) {
      localUpdateStates.value[program] = {
        updating: status === 'updating',
        progress: progress || 0,
        message: t(message) || message
      };
    }
  },
  'program_update_complete': (msg) => {
    const { program, success, message, error, old_version, new_version } = msg.data;

    if (program) {
      delete localUpdateStates.value[program];

      if (success) {
        localCompletedUpdates.value.add(program);
        loadLocalPrograms();
      }
    }
  },
  'satellite_update_progress': (msg) => {
    const { hostname, progress, message, status } = msg.data;
    if (hostname && satelliteUpdateStates.value[hostname]) {
      satelliteUpdateStates.value[hostname] = {
        updating: status === 'updating',
        progress: progress || 0,
        message: t(message) || message
      };
    }
  },
  'satellite_update_complete': (msg) => {
    const { hostname, success, message, error, new_version } = msg.data;

    if (hostname) {
      delete satelliteUpdateStates.value[hostname];

      if (success) {
        satelliteCompletedUpdates.value.add(hostname);
        loadSatellites();
      }
    }
  },
  'satellite_app_update_progress': (msg) => {
    const { hostname, progress, message, status } = msg.data;
    if (hostname && satelliteAppUpdateStates.value[hostname]) {
      satelliteAppUpdateStates.value[hostname] = {
        updating: status === 'updating',
        progress: progress || 0,
        message: t(message) || message
      };
    }
  },
  'satellite_app_update_complete': (msg) => {
    const { hostname, success, message, error, new_version } = msg.data;

    if (hostname) {
      delete satelliteAppUpdateStates.value[hostname];

      if (success) {
        satelliteAppCompletedUpdates.value.add(hostname);
        loadSatellites();
      }
    }
  }
};

// === LIFECYCLE ===


onMounted(async () => {
  await loadLocalPrograms();
  if (isMultiroomEnabled.value) {
    await loadSatellites();
  }

  // Register WebSocket listeners
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('programs', eventType, handler);
  });
});
</script>

<style scoped>
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
  gap: var(--space-03);
}

.program-item {
  display: grid;
  grid-template-columns: 1fr 1fr;
  align-items: center;
  gap: var(--space-03);
}

.program-item:first-child {
  padding-top: var(--space-02);
}

.program-item:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: var(--space-04);
}

.program-info {
  display: grid;
  grid-template-columns: auto 1fr;
  grid-template-areas:
    "icon name"
    "icon version";
  gap: var(--space-01) var(--space-03);
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

.satellite-name {
  color: var(--color-text-secondary);
}

.program-button {
  justify-self: end;
}

.update-progress {
  display: flex;
  flex-direction: column;
  background: var(--color-background-strong);
  border-radius: var(--radius-02);
  padding: 8px 12px 12px 12px;
  gap: 8px;
  width: 100%;
}

.progress-message {
  color: var(--color-text-secondary);
  text-align: center;
}

.progress-bar {
  width: 100%;
  height: 4px;
  background: var(--color-background-neutral);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-brand);
  border-radius: var(--radius-full);
  transition: width var(--transition-normal);
}

/* Crossfade transition */
.crossfade-wrapper {
  display: grid;
}

.crossfade-wrapper>* {
  grid-area: 1 / 1;
}

.crossfade-enter-active,
.crossfade-leave-active {
  transition: opacity var(--transition-normal);
}

.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}

.program-item-skeleton {
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-areas:
    "icon name button"
    "icon version button";
  align-items: center;
  gap: var(--space-01) var(--space-04);
}

.program-item-skeleton:first-child {
  padding-top: var(--space-02);
}

.program-item-skeleton:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: var(--space-04);
}

.skeleton-icon,
.skeleton-text,
.skeleton-button {
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background);
}

.skeleton-icon {
  grid-area: icon;
  width: 48px;
  height: 48px;
  border-radius: var(--radius-03);
}

.skeleton-name {
  grid-area: name;
  width: 120px;
  height: calc(var(--font-size-h4) * 1.2);
  border-radius: var(--radius-02);
}

.skeleton-version {
  grid-area: version;
  width: 180px;
  height: calc(var(--font-size-mono) * 1.4);
  border-radius: var(--radius-02);
}


.skeleton-button {
  grid-area: button;
  width: 100px;
  height: 36px;
  border-radius: var(--radius-02);
  justify-self: end;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .program-item {
    grid-template-columns: 1fr;
    gap: var(--space-02);
  }

  .program-item:first-child {
    padding-top: 0;
  }

  .program-info {
    gap: var(--space-01) var(--space-02);
  }

  .program-icon {
    width: 44px !important;
    height: 44px !important;
  }

  .program-button,
  .update-progress {
    width: 100%;
  }

  /* Hide "Up to date" button and skeleton button on mobile */
  .btn-up-to-date,
  .skeleton-button {
    display: none;
  }

  /* Skeleton responsive */
  .program-item-skeleton {
    grid-template-columns: auto 1fr;
    grid-template-areas:
      "icon name"
      "icon version";
    gap: var(--space-01) var(--space-02);
  }

  .program-item-skeleton:first-child {
    padding-top: 0;

  }

  .skeleton-icon {
    width: 44px;
    height: 44px;
  }

  .skeleton-name {
    width: 100px;
  }

  .skeleton-version {
    width: 160px;
  }

}
</style>
