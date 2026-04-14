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
                      v-if="localPrograms.milo.update_available && !isLocalUpdateCompleted('milo')">
                      <span class="version-new">> {{ getLocalLatestVersion(localPrograms.milo) }}</span>
                    </template>
                  </span>
                </div>

                <!-- Update button (loading state during update) -->
                <Button
                  v-if="isLocalUpdating('milo') || debugForceUpdating || (localPrograms.milo.update_available && canUpdateLocal('milo') && !isLocalUpdateCompleted('milo'))"
                  size="small" variant="brand" class="program-button"
                  :loading="isLocalUpdating('milo') || debugForceUpdating"
                  @click="startLocalUpdate('milo')"
                  :disabled="debugForceUpdating || isAnyUpdateInProgress()">
                  {{ (isLocalUpdating('milo') || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
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
              <div v-for="n in enabledProgramCount" :key="n" class="program-item-skeleton">
                <div class="skeleton-icon shimmer"></div>
                <div class="skeleton-text shimmer skeleton-name"></div>
                <div class="skeleton-text shimmer skeleton-version"></div>
                <div class="skeleton-button shimmer"></div>
              </div>
            </div>

            <div v-else key="content" class="programs-list">
              <template v-for="(program, key) in localPrograms" :key="key">
                <div v-if="key !== 'milo' && isProgramEnabled(key)" class="program-item">
                  <div class="program-info">
                    <AppIcon :name="getProgramIcon(key)" :size="48" class="program-icon" />
                    <span class="program-name heading-4">{{ getProgramDisplayName(program, key) }}</span>
                    <span class="program-version text-mono">
                      {{ getVersionLabel(key) }} {{ getLocalInstalledVersion(program) || t('updates.notAvailable') }}
                      <template
                        v-if="program.update_available && !isLocalUpdateCompleted(key)">
                        <span class="version-new">> {{ getLocalLatestVersion(program) }}</span>
                      </template>
                    </span>
                  </div>

                  <!-- Update button (loading state during update) -->
                  <Button
                    v-if="isLocalUpdating(key) || debugForceUpdating || (program.update_available && canUpdateLocal(key) && !isLocalUpdateCompleted(key))"
                    size="small" variant="brand" class="program-button"
                    :loading="isLocalUpdating(key) || debugForceUpdating"
                    @click="startLocalUpdate(key)"
                    :disabled="debugForceUpdating || isAnyUpdateInProgress()">
                    {{ (isLocalUpdating(key) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
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

      <!-- Section 3: Satellite Programs (error) -->
      <SettingsSection v-if="isMultiroomEnabled && satellitesError"
        :title="t('updates.satelliteProgramsTitle')">
        <div class="error-state">
          <div class="error-message text-mono">
            {{ t('updates.errorDetectingSatellites') }}
          </div>
          <Button size="small" variant="background-strong" @click="loadSatellites">
            {{ t('updates.retry') }}
          </Button>
        </div>
      </SettingsSection>

      <!-- Section 3: Satellite Programs (one section per anticipated client, crossfade skeleton → content) -->
      <template v-if="isMultiroomEnabled && !satellitesError">
        <SettingsSection v-for="client in anticipatedSatellites" :key="client.mac_id">
          <template #header>
            <h2 class="heading-2">{{ t('updates.satelliteSectionTitle') }} <span class="satellite-name">·&nbsp;{{
              client.name || client.mac_id }}</span></h2>
          </template>
          <div class="crossfade-wrapper">
            <Transition name="crossfade">
              <div v-if="!satelliteByMacId[client.mac_id]" key="skeleton" class="programs-list">
                <div class="program-item-skeleton">
                  <div class="skeleton-icon shimmer"></div>
                  <div class="skeleton-text shimmer skeleton-name"></div>
                  <div class="skeleton-text shimmer skeleton-version"></div>
                  <div class="skeleton-button shimmer"></div>
                </div>
                <div class="program-item-skeleton">
                  <div class="skeleton-icon shimmer"></div>
                  <div class="skeleton-text shimmer skeleton-name"></div>
                  <div class="skeleton-text shimmer skeleton-version"></div>
                  <div class="skeleton-button shimmer"></div>
                </div>
                <div class="program-item-skeleton">
                  <div class="skeleton-icon shimmer"></div>
                  <div class="skeleton-text shimmer skeleton-name"></div>
                  <div class="skeleton-text shimmer skeleton-version"></div>
                  <div class="skeleton-button shimmer"></div>
                </div>
              </div>

              <div v-else key="content" class="programs-list">
                <!-- Milo Client row -->
                <div class="program-item">
                  <div class="program-info">
                    <AppIcon name="milo-client" :size="48" class="program-icon" />
                    <span class="program-name heading-4">Milō Client</span>
                    <span class="program-version text-mono">
                      milo-client {{ formatGitVersion(satelliteByMacId[client.mac_id].app_version) || t('updates.notAvailable') }}
                      <template
                        v-if="satelliteByMacId[client.mac_id].app_update_available && !isSatelliteAppUpdateCompleted(client.mac_id)">
                        <span class="version-new">> {{ formatGitVersion(satelliteByMacId[client.mac_id].server_version) }}</span>
                      </template>
                    </span>
                  </div>

                  <Button
                    v-if="isSatelliteAppUpdating(client.mac_id) || debugForceUpdating || (satelliteByMacId[client.mac_id].app_update_available && satelliteByMacId[client.mac_id].online && !isSatelliteAppUpdateCompleted(client.mac_id))"
                    size="small" variant="brand" class="program-button"
                    :loading="isSatelliteAppUpdating(client.mac_id) || debugForceUpdating"
                    @click="startSatelliteAppUpdate(client.mac_id)"
                    :disabled="debugForceUpdating || isAnyUpdateInProgress()">
                    {{ (isSatelliteAppUpdating(client.mac_id) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
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
                      snapclient {{ satelliteByMacId[client.mac_id].snapclient_version || t('updates.notAvailable') }}
                      <template
                        v-if="satelliteByMacId[client.mac_id].update_available && !isSatelliteUpdateCompleted(client.mac_id)">
                        <span class="version-new">> {{ satelliteByMacId[client.mac_id].latest_version }}</span>
                      </template>
                    </span>
                  </div>

                  <Button
                    v-if="isSatelliteUpdating(client.mac_id) || debugForceUpdating || (satelliteByMacId[client.mac_id].update_available && satelliteByMacId[client.mac_id].online && !isSatelliteUpdateCompleted(client.mac_id))"
                    size="small" variant="brand" class="program-button"
                    :loading="isSatelliteUpdating(client.mac_id) || debugForceUpdating"
                    @click="startSatelliteUpdate(client.mac_id)"
                    :disabled="debugForceUpdating || isAnyUpdateInProgress()">
                    {{ (isSatelliteUpdating(client.mac_id) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
                  </Button>
                  <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                    {{ t('updates.upToDate') }}
                  </Button>
                </div>

                <!-- CamillaDSP row -->
                <div class="program-item">
                  <div class="program-info">
                    <AppIcon name="equalizer" :size="48" class="program-icon" />
                    <span class="program-name heading-4">{{ t('equalizer.title') }}</span>
                    <span class="program-version text-mono">
                      camilladsp {{ satelliteByMacId[client.mac_id].camilladsp_version || t('updates.notAvailable') }}
                      <template
                        v-if="satelliteByMacId[client.mac_id].camilladsp_update_available && !isSatelliteCamillaUpdateCompleted(client.mac_id)">
                        <span class="version-new">> {{ satelliteByMacId[client.mac_id].camilladsp_latest_version }}</span>
                      </template>
                    </span>
                  </div>

                  <Button
                    v-if="isSatelliteCamillaUpdating(client.mac_id) || debugForceUpdating || (satelliteByMacId[client.mac_id].camilladsp_update_available && satelliteByMacId[client.mac_id].online && !isSatelliteCamillaUpdateCompleted(client.mac_id))"
                    size="small" variant="brand" class="program-button"
                    :loading="isSatelliteCamillaUpdating(client.mac_id) || debugForceUpdating"
                    @click="startSatelliteCamillaUpdate(client.mac_id)"
                    :disabled="debugForceUpdating || isAnyUpdateInProgress()">
                    {{ (isSatelliteCamillaUpdating(client.mac_id) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
                  </Button>
                  <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                    {{ t('updates.upToDate') }}
                  </Button>
                </div>
              </div>
            </Transition>
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
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useSettingsStore } from '@/stores/settingsStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

function getProgramIcon(programKey) {
  const iconMap = {
    'milo': 'milo',
    'go-librespot': 'spotify',
    'multiroom': 'multiroom',
    'bluez-alsa': 'bluetooth',
    'roc-toolkit': 'mac',
    'shairport-sync': 'airplay',
    'camilladsp': 'equalizer'
  };
  return iconMap[programKey] || 'settings';
}

function getProgramDisplayName(program, key) {
  const nameOverrides = {
    'go-librespot': t('audioSources.spotify'),
    'multiroom': t('audioSources.multiroom'),
    'bluez-alsa': t('audioSources.bluetooth'),
    'roc-toolkit': t('audioSources.macOS'),
    'shairport-sync': t('audioSources.airplay'),
    'camilladsp': t('equalizer.title')
  };
  return nameOverrides[key] || program.name;
}

function getVersionLabel(key) {
  const labelOverrides = {
    'multiroom': 'snapcast',
    'shairport-sync': 'shairport-sync',
    'camilladsp': 'camilladsp'
  };
  return labelOverrides[key] || key;
}

function formatGitVersion(version) {
  if (!version) return null;
  // Extract base version only (no commit hash)
  //   "v0.0.1"                → "0.0.1"
  //   "v0.0.1-533-gc6d74a1"  → "0.0.1"
  //   "b601da9"               → "b601da9"
  const match = version.match(/v?(\d+\.\d+\.\d+)/);
  return match ? match[1] : version.replace(/^v/, '');
}

const { t } = useI18n();
const { on, onReconnect } = useWebSocket();
const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();
const settingsStore = useSettingsStore();

const isMultiroomEnabled = computed(() => unifiedStore.systemState.multiroom_enabled);

// Local state
const localPrograms = ref({});
const localProgramsLoading = ref(true);
const localProgramsError = ref(false);

const satellites = ref(null); // null = not loaded, [] = loaded empty, [...] = loaded
const satellitesError = ref(false);

// Lookup map: mac_id → satellite data (for matching anticipated clients to loaded satellites)
const satelliteByMacId = computed(() => {
  if (!satellites.value) return {};
  const map = {};
  for (const sat of satellites.value) {
    map[sat.mac_id] = sat;
  }
  return map;
});

// Non-local satellites: online clients + clients with an active update (anticipates snapclient restart during update)
const anticipatedSatellites = computed(() =>
  multiroomStore.clientList.filter(c => {
    if (c.is_local) return false;
    if (c.online) return true;
    return isSatelliteUpdating(c.mac_id) || isSatelliteAppUpdating(c.mac_id) || isSatelliteCamillaUpdating(c.mac_id);
  })
);

// Update states
const localUpdateStates = ref({});
const localCompletedUpdates = ref(new Set());

const satelliteUpdateStates = ref({});
const satelliteCompletedUpdates = ref(new Set());

const satelliteAppUpdateStates = ref({});
const satelliteAppCompletedUpdates = ref(new Set());

const satelliteCamillaUpdateStates = ref({});
const satelliteCamillaCompletedUpdates = ref(new Set());

const supportedLocalUpdates = ['milo', 'go-librespot', 'shairport-sync', 'multiroom', 'camilladsp'];

// Debug: toggle via console with window.__miloDebugUpdating(true/false)
const debugForceUpdating = ref(false);
if (typeof window !== 'undefined') {
  window.__miloDebugUpdating = (val) => { debugForceUpdating.value = val; };
}

const programToDockApp = {
  'go-librespot': 'spotify',
  'multiroom': 'multiroom',
  'bluez-alsa': 'bluetooth',
  'roc-toolkit': 'mac',
  'shairport-sync': 'airplay'
};

function isProgramEnabled(programKey) {
  const dockKey = programToDockApp[programKey];
  if (!dockKey) return true;
  return settingsStore.dockApps[dockKey] !== false;
}

// +1 for CamillaDSP which is always visible (not dock-gated)
const enabledProgramCount = computed(() =>
  Object.keys(programToDockApp).filter(isProgramEnabled).length + 1
);

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

async function startLocalUpdate(programKey) {
  if (!canUpdateLocal(programKey) || isLocalUpdating(programKey)) return;

  try {
    localUpdateStates.value[programKey] = { updating: true };

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
  try {
    satellites.value = null;
    satellitesError.value = false;

    const response = await axios.get('/api/programs/satellites');

    if (response.data.status === 'success') {
      satellites.value = response.data.satellites || [];
    } else {
      satellitesError.value = true;
    }
  } catch (error) {
    console.error('Error loading satellites:', error);
    satellitesError.value = true;
  }
}

function isSatelliteUpdating(macId) {
  return satelliteUpdateStates.value[macId]?.updating || false;
}

function isSatelliteUpdateCompleted(macId) {
  return satelliteCompletedUpdates.value.has(macId);
}

async function startSatelliteUpdate(macId) {
  if (isSatelliteUpdating(macId)) return;

  try {
    satelliteUpdateStates.value[macId] = { updating: true };

    const response = await axios.post(`/api/programs/satellites/${macId}/update`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start update');
    }

  } catch (error) {
    console.error(`Error starting update for satellite ${macId}:`, error);
    delete satelliteUpdateStates.value[macId];
  }
}

// === SATELLITE APP UPDATES ===

function isSatelliteAppUpdating(macId) {
  return satelliteAppUpdateStates.value[macId]?.updating || false;
}

function isSatelliteAppUpdateCompleted(macId) {
  return satelliteAppCompletedUpdates.value.has(macId);
}

async function startSatelliteAppUpdate(macId) {
  if (isSatelliteAppUpdating(macId)) return;

  try {
    satelliteAppUpdateStates.value[macId] = { updating: true };

    const response = await axios.post(`/api/programs/satellites/${macId}/update-app`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start app update');
    }

  } catch (error) {
    console.error(`Error starting app update for satellite ${macId}:`, error);
    delete satelliteAppUpdateStates.value[macId];
  }
}

// === SATELLITE CAMILLADSP UPDATES ===

function isSatelliteCamillaUpdating(macId) {
  return satelliteCamillaUpdateStates.value[macId]?.updating || false;
}

function isSatelliteCamillaUpdateCompleted(macId) {
  return satelliteCamillaCompletedUpdates.value.has(macId);
}

async function startSatelliteCamillaUpdate(macId) {
  if (isSatelliteCamillaUpdating(macId)) return;

  try {
    satelliteCamillaUpdateStates.value[macId] = { updating: true };

    const response = await axios.post(`/api/programs/satellites/${macId}/update-camilladsp`);

    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'Failed to start CamillaDSP update');
    }

  } catch (error) {
    console.error(`Error starting CamillaDSP update for satellite ${macId}:`, error);
    delete satelliteCamillaUpdateStates.value[macId];
  }
}

function isAnyUpdateInProgress() {
  return Object.values(localUpdateStates.value).some(state => state.updating) ||
    Object.values(satelliteUpdateStates.value).some(state => state.updating) ||
    Object.values(satelliteAppUpdateStates.value).some(state => state.updating) ||
    Object.values(satelliteCamillaUpdateStates.value).some(state => state.updating);
}

// === WEBSOCKET HANDLERS ===

const wsListeners = {
  'program_update_progress': (msg) => {
    const { program, status } = msg.data;
    if (program && localUpdateStates.value[program]) {
      localUpdateStates.value[program].updating = status === 'updating';
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
    const { mac_id, status } = msg.data;
    if (mac_id && satelliteUpdateStates.value[mac_id]) {
      satelliteUpdateStates.value[mac_id].updating = status === 'updating';
    }
  },
  'satellite_update_complete': (msg) => {
    const { mac_id, success } = msg.data;

    if (mac_id) {
      delete satelliteUpdateStates.value[mac_id];

      if (success) {
        satelliteCompletedUpdates.value.add(mac_id);
        loadSatellites();
      }
    }
  },
  'satellite_app_update_progress': (msg) => {
    const { mac_id, status } = msg.data;
    if (mac_id && satelliteAppUpdateStates.value[mac_id]) {
      satelliteAppUpdateStates.value[mac_id].updating = status === 'updating';
    }
  },
  'satellite_app_update_complete': (msg) => {
    const { mac_id, success } = msg.data;

    if (mac_id) {
      delete satelliteAppUpdateStates.value[mac_id];

      if (success) {
        satelliteAppCompletedUpdates.value.add(mac_id);
        loadSatellites();
      }
    }
  },
  'satellite_camilladsp_update_progress': (msg) => {
    const { mac_id, status } = msg.data;
    if (mac_id && satelliteCamillaUpdateStates.value[mac_id]) {
      satelliteCamillaUpdateStates.value[mac_id].updating = status === 'updating';
    }
  },
  'satellite_camilladsp_update_complete': (msg) => {
    const { mac_id, success } = msg.data;

    if (mac_id) {
      delete satelliteCamillaUpdateStates.value[mac_id];

      if (success) {
        satelliteCamillaCompletedUpdates.value.add(mac_id);
        loadSatellites();
      }
    }
  }
};

// === LIFECYCLE ===


onMounted(async () => {
  // Register WebSocket listeners first so no events are missed during loading
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('programs', eventType, handler);
  });

  // Re-sync state after WS reconnect (prevents stuck update buttons if events were missed)
  onReconnect(() => {
    loadLocalPrograms();
    if (isMultiroomEnabled.value) loadSatellites();
  });

  const tasks = [loadLocalPrograms()];
  if (isMultiroomEnabled.value) {
    tasks.push(loadSatellites());
  }
  await Promise.all(tasks);
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
  padding-bottom: var(--space-03);
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

/* Override loading appearance to show disabled style when updating */
.program-button:disabled {
  background-color: var(--color-background) !important;
  color: var(--color-text-light) !important;
  cursor: not-allowed !important;
  pointer-events: auto !important;
}

/* Crossfade transition */
.crossfade-wrapper {
  display: grid;
}

.crossfade-wrapper>* {
  grid-area: 1 / 1;
}

.crossfade-enter-active {
  transition: opacity var(--transition-normal);
}

.crossfade-leave-active {
  transition: opacity var(--transition-normal-leave);
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
  padding-bottom: var(--space-03);
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

  .program-button {
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
