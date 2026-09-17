<!-- frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue -->
<template>
  <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Enabling or Disabled -->
        <MessageContent v-if="showMessage" :key="multiroomClientStore.transitionState" :loading="isLoading" :loading-delay="0"
          :icon="isLoading ? null : 'multiroom'" :title="messageTitle" />
        <!-- SETTINGS: Active and ready -->
        <SettingsContainer v-else key="settings">
          <!-- Discovered Speakers Section (pending ethernet + wifi hotspots) -->
          <SettingsSection v-if="discoveryItems.length > 0">
            <template #header>
              <SectionHeader :title="t('multiroom.pending.title')" />
            </template>
            <div class="discovery-list">
              <SystemListItem
                v-for="item in discoveryItems"
                :key="item.key"
                :name="item.name"
                :discovery-source="item.source"
                :signal="item.signal ?? null"
                :status="item.status"
                :status-variant="item.statusVariant"
                :action="item.disabled ? 'none' : 'caret'"
                :disabled="item.disabled"
                @click="handleDiscoveryClick(item)"
              />
            </div>
          </SettingsSection>

          <SettingsSection>
            <template #header>
              <SectionHeader :title="t('multiroom.zonesAndSystems')">
                <template #actions>
                  <Button v-if="ungroupedClients.length >= 2" variant="brand" size="small" @click="handleCreateZone">
                    {{ t('equalizer.zones.createZone') }}
                  </Button>
                </template>
              </SectionHeader>
            </template>

            <div v-if="snapcastStore.isLoading" class="loading-state">
              <p class="text-mono-medium">{{ t('multiroom.loadingSystems') }}</p>
            </div>

            <div v-else-if="sortedMultiroomClients.length === 0" class="no-clients-state">
              <p class="text-mono-medium">{{ t('multiroom.noSystems') }}</p>
            </div>

            <div v-else class="speakers-list">
              <div v-for="zone in zones" :key="zone.id" class="zone-group">
                <button type="button" class="zone-header" @click="handleEditZone(zone.id)">
                  <span class="zone-header__name heading-3">{{ zone.displayName }}</span>
                  <SvgIcon name="caretRight" :size="20" class="zone-header__caret" />
                  <!-- Crossover badge -->
                  <span v-if="zone.crossover_enabled" class="crossover-badge crossover-badge--active text-mono-medium"
                    :title="t('multiroom.crossover.badgeActive')">
                    {{ zone.crossover_frequency}} Hz
                  </span>
                  <span v-else-if="zone.has_subwoofer" class="crossover-badge crossover-badge--inactive text-mono-medium"
                    :title="t('multiroom.crossover.subwooferOffline')">
                    {{ t('multiroom.crossover.badgeInactive') }}
                  </span>
                </button>
                <div class="zone-clients">
                  <SystemListItem v-for="client in zone.clients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </div>

              <template v-if="ungroupedClients.length > 0">
                <h3 v-if="zones.length > 0" class="heading-3 section-subtitle">{{ t('multiroom.individualSystems') }}
                </h3>
                <div class="ungrouped-clients">
                  <SystemListItem v-for="client in ungroupedClients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </template>
            </div>
          </SettingsSection>

          <!-- No modes. The analysis is an action, not a state one sits in --
               tabs claimed otherwise, and made the codec selection invisible
               besides, because a disabled ButtonGroup drops its active mark. -->
          <SettingsSection>
            <template #header>
              <SectionHeader :title="t('multiroomSettings.latencyAndQuality')">
                <template #actions>
                  <Button v-if="canReset" variant="outline" size="small"
                    :disabled="snapcastStore.isApplyingServerConfig || calibration.running"
                    @click="resetToDefault">
                    {{ t('multiroomSettings.reset') }}
                  </Button>
                </template>
              </SectionHeader>
            </template>

            <SettingItem :label="t('multiroomSettings.globalBuffer')">
              <RangeSlider v-model="snapcastStore.serverConfig.buffer_ms" :min="150" :max="3000" :step="100"
                value-unit="ms" :disabled="busy" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.chunkSize')">
              <RangeSlider v-model="snapcastStore.serverConfig.chunk_ms" :min="15" :max="50" :step="5"
                value-unit="ms" :disabled="busy" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.snapclientBuffer')">
              <RangeSlider v-model="snapcastStore.serverConfig.snapclient_buffer_time" :min="60" :max="300" :step="10"
                value-unit="ms" :disabled="busy" />
            </SettingItem>

            <SettingItem :label="t('multiroomSettings.codec')">
              <ButtonGroup :model-value="snapcastStore.serverConfig.codec" :options="codecOptions"
                :disabled="busy" mobile-layout="column" @change="selectCodec" />
            </SettingItem>

            <div class="section-divider"></div>

            <!-- The result of this button is the sliders above moving. The only
                 thing they cannot say is which speaker held the house back, so
                 that is the one line printed underneath. -->
            <Button variant="outline" size="medium" class="auto-tune"
              :loading="calibration.running" :disabled="busy" @click="startAnalysis">
              {{ t('multiroomSettings.autoTune') }}
            </Button>

            <!-- Thirty seconds with no feedback reads as a hang. Same strip the
                 library scan uses: a second progress bar drawn here is how two
                 of them come to look different in one app. It stops short of
                 full — only the result may finish it, so a slow network never
                 shows a completed bar over a running analysis. -->
            <ProgressStrip :open="calibration.running" :percent="progressPercent"
              :step-ms="PROGRESS_TICK_MS"
              :label="calibration.running ? stageLabel : ''"
              :hint="calibration.running ? t('multiroomSettings.remaining', { seconds: remainingSeconds }) : ''" />

            <p v-if="!calibration.running && analysisNote" class="text-mono-medium analysis-note">
              {{ analysisNote }}
            </p>

            <!-- What the measurement found, and only what a person can act
                 on: which speakers were weighed, how each connects, and which
                 one held the house back. The round-trip in milliseconds is
                 gone -- it was the one figure here nobody can rank. -->
            <template v-if="!calibration.running && measuredLinks.length">
              <p v-if="showsMeasuredValues" class="text-mono-medium analysis-badge">
                {{ t('multiroomSettings.valuesAreMeasured') }}
              </p>

              <!-- A table once it has headers: "1.59 ms" beside "0 %" needs
                   naming, and naming a column is what a header row is for. -->
              <table class="analysis-links">
                <thead>
                  <tr class="analysis-links__row">
                    <th class="analysis-links__icon"></th>
                    <th class="text-mono-small analysis-links__head">{{ t('multiroomSettings.speaker') }}</th>
                    <th class="text-mono-small analysis-links__head analysis-links__value">
                      {{ t('multiroomSettings.latency') }}
                    </th>
                    <th class="text-mono-small analysis-links__head analysis-links__value">
                      {{ t('multiroomSettings.loss') }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in measuredLinks" :key="row.mac_id" class="analysis-links__row">
                    <td class="analysis-links__icon">
                      <SvgIcon v-if="row.link === 'ethernet'" name="network" :size="16" />
                      <WifiSignal v-else :signal="row.signal_percent ?? 100" :size="16" />
                    </td>
                    <td class="text-mono-medium analysis-links__name">{{ row.name }}</td>
                    <td class="text-mono-medium analysis-links__value">{{ row.rtt_max_ms }} ms</td>
                    <td class="text-mono-medium analysis-links__value"
                      :class="{ 'analysis-links__value--warn': row.loss_pct > 0 }">
                      {{ row.loss_pct }} %
                    </td>
                  </tr>
                </tbody>
              </table>
            </template>
          </SettingsSection>

          <Button v-if="snapcastStore.hasServerConfigChanges" variant="brand" size="medium"
            class="apply-button-sticky" :loading="snapcastStore.isApplyingServerConfig"
            :disabled="snapcastStore.isApplyingServerConfig" @click="applyServerConfig">
            {{ snapcastStore.isApplyingServerConfig ? t('multiroom.restarting') : t('multiroomSettings.apply') }}
          </Button>
        </SettingsContainer>
  </Transition>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { useI18n } from '@/services/i18n';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDiscoveryStore } from '@/stores/discoveryStore';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SystemListItem from '@/components/settings/categories/multiroom/SystemListItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ProgressStrip from '@/components/settings/ProgressStrip.vue';

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client', 'configure-system']);

const { t } = useI18n();
const snapcastStore = useSnapcastStore();
const unifiedStore = useUnifiedAudioStore();
const multiroomClientStore = useMultiroomStore();
const discoveryStore = useDiscoveryStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// Message display logic (reads centralized transitionState from multiroomStore)
const showMessage = computed(() => {
  return multiroomClientStore.transitionState !== 'idle' || !isMultiroomActive.value;
});
const isLoading = computed(() => multiroomClientStore.transitionState === 'enabling');
const messageTitle = computed(() => {
  if (multiroomClientStore.transitionState === 'error') {
    return multiroomClientStore.transitionError || t('multiroom.error');
  }
  return multiroomClientStore.transitionState === 'enabling' ? t('multiroom.starting') : t('multiroom.disabled');
});

// Clients are already sorted (local first, then alphabetical) from multiroomStore
const sortedMultiroomClients = computed(() => snapcastStore.clients);

// Get zones with client details from multiroomStore (single source of truth)
// Uses clientList which is already sorted (local first, online first, alphabetical)
const zones = computed(() => {
  return multiroomClientStore.zoneList.map((zone, index) => {
    const zoneClientIds = new Set(zone.client_ids || []);
    // Filter from already-sorted clientList to preserve correct order
    const clients = multiroomClientStore.clientList
      .filter(c => zoneClientIds.has(c.mac_id))
      .map(client => ({
        id: client.id,
        mac_id: client.mac_id,
        host: client.host,
        name: client.name || client.host,
        online: client.online
      }));

    return {
      id: zone.id,
      displayName: zone.name || `Zone ${index + 1}`,
      clients,
      crossover_enabled: zone.crossover_enabled,
      crossover_frequency: zone.crossover_frequency,
      has_subwoofer: zone.has_subwoofer
    };
  });
});

// Get clients not in any zone from multiroomStore (single source of truth)
const ungroupedClients = computed(() => {
  const groupedIds = new Set();
  multiroomClientStore.zoneList.forEach(zone => {
    (zone.client_ids || []).forEach(id => groupedIds.add(id));
  });

  return multiroomClientStore.clientList
    .filter(client => !groupedIds.has(client.mac_id))
    .map(client => ({
      id: client.id,
      mac_id: client.mac_id,
      host: client.host,
      name: client.name || client.host,
      online: client.online
    }));
});

// Unified discovery list: pending ethernet clients + visible wifi hotspots.
// Each item carries a discovery `source` so the parent knows which adoption
// flow to launch (ethernet via configure-pending, wifi via adopt-speaker).
const discoveryItems = computed(() => {
  const items = [];

  for (const client of multiroomClientStore.pendingClientList) {
    const configuring = multiroomClientStore.isClientConfiguring(client.mac_id);
    items.push({
      key: `eth:${client.mac_id}`,
      source: 'ethernet',
      name: client.name || client.ip,
      status: configuring ? t('multiroom.pending.rebooting') : t('multiroom.pending.notConfigured'),
      statusVariant: configuring ? 'configuring' : '',
      disabled: configuring,
      macId: client.mac_id
    });
  }

  for (const hotspot of discoveryStore.hotspots) {
    items.push({
      key: `wifi:${hotspot.ssid}`,
      source: 'wifi',
      name: hotspot.ssid,
      status: t('multiroom.pending.notConfigured'),
      statusVariant: '',
      disabled: false,
      ssid: hotspot.ssid,
      signal: hotspot.signal
    });
  }

  return items;
});

// Navigation: dispatch to ConfigureSystem with the right discovery context.
function handleDiscoveryClick(item) {
  if (item.disabled) return;
  if (item.source === 'ethernet') {
    emit('configure-system', { source: 'ethernet', macId: item.macId });
  } else {
    emit('configure-system', {
      source: 'wifi',
      ssid: item.ssid,
      signal: item.signal
    });
  }
}

function handleEditZone(groupId) {
  emit('edit-zone', groupId);
}

function handleCreateZone() {
  emit('create-zone');
}

function handleEditClient(macId) {
  emit('edit-client', macId);
}

// === LATENCY AND QUALITY ===

const calibration = computed(() => snapcastStore.calibration);
const busy = computed(() =>
  snapcastStore.isApplyingServerConfig || calibration.value.running
);

// The factory configuration, as the backend declares it — never restated here,
// so "reset" restores what a freshly flashed unit actually runs.
const defaultPreset = computed(() =>
  snapcastStore.capabilities.presets.find(preset => preset.id === 'default') || null
);

// Offered only when it would change something: a reset button on a machine
// already at its factory values is a control that does nothing.
const canReset = computed(() => {
  const config = defaultPreset.value?.config;
  return Boolean(config) &&
    Object.keys(config).some(key => snapcastStore.serverConfig[key] !== config[key]);
});

function resetToDefault() {
  if (defaultPreset.value) {
    snapcastStore.applyPreset(defaultPreset.value);
  }
}

const ANALYSIS_ERROR_KEYS = {
  no_remote_client: 'failedNoRemote',
  probe_failed: 'failedProbe',
  start_failed: 'failedStart',
};

// Only a failure, or a caveat the numbers cannot carry. Progress has its own
// bar and the result has its own table, so the line that used to name the
// limiting speaker said less than the four figures beside it.
const analysisNote = computed(() => {
  if (calibration.value.error) {
    return t(`multiroomSettings.${ANALYSIS_ERROR_KEYS[calibration.value.error] || 'failedProbe'}`,
      { detail: calibration.value.detail || '' });
  }
  if (calibration.value.result?.assumed?.length) return t('multiroomSettings.partlyAssumed');
  return null;
});

const measuredLinks = computed(() =>
  // The local speaker has no link to weigh: its row was three em-dashes.
  (calibration.value.result?.measurements || []).filter(row => !row.is_local)
);

// True only while the sliders still hold what was measured. Drag one and the
// badge goes, because the settings are no longer the measurement.
const showsMeasuredValues = computed(() => {
  const config = calibration.value.result?.config;
  return Boolean(config) &&
    Object.keys(config).every(key => snapcastStore.serverConfig[key] === config[key]);
});

// === PROGRESS ===

const elapsedMs = ref(0);
const timer = useTimer();
let progressTicker = null;

// Capped: the bar may approach the end but only the result event completes it.
const PROGRESS_CEILING = 95;

// One second, matched by the strip's CSS transition. A shorter tick with the
// same transition is what made the bar advance in visible steps: the animation
// finished long before the next value arrived and the fill sat still between.
const PROGRESS_TICK_MS = 1000;

const progressPercent = computed(() => {
  const expected = calibration.value.expectedSeconds * 1000;
  if (!expected) return 0;
  return Math.min(PROGRESS_CEILING, (elapsedMs.value / expected) * 100);
});

const remainingSeconds = computed(() => {
  const expected = calibration.value.expectedSeconds;
  if (!expected) return 0;
  return Math.max(0, Math.round(expected - elapsedMs.value / 1000));
});

watch(() => calibration.value.running, (running) => {
  if (progressTicker) {
    timer.clear(progressTicker);
    progressTicker = null;
  }
  if (!running) return;
  elapsedMs.value = Date.now() - (calibration.value.startedAt || Date.now());
  progressTicker = timer.setInterval(() => {
    elapsedMs.value = Date.now() - (calibration.value.startedAt || Date.now());
  }, PROGRESS_TICK_MS);
}, { immediate: true });

// Numbers, not a verdict. "Sets the limit" singled out whichever speaker had
// the marginally worse jitter — 1.02 ms against 0.36 ms on two gigabit links —
// and which one that was flipped between runs. It read as "this speaker is the
// problem" about a fleet where nothing was wrong. Two measured figures side by
// side say the same thing without accusing anyone.

const ANALYSIS_STAGE_KEYS = { probing: 'stageProbing', computing: 'stageComputing' };
const stageLabel = computed(() =>
  t(`multiroomSettings.${ANALYSIS_STAGE_KEYS[calibration.value.stage] || 'stageProbing'}`)
);

async function startAnalysis() {
  // Armed only once the run actually began. Set before the request, a refused
  // POST left it armed and the next result the store saw — a restored one from
  // some later reload — was staged as though the user had asked for it.
  awaitingResult.value = await snapcastStore.startCalibration('lossless');
}

// A measured result lands in the sliders, which is where every other setting is
// read. Staging is not writing — the sticky Apply is, so snapserver.conf keeps
// exactly one writer.
// Only a run started here. Without the flag, opening the panel restored the
// last stored proposal and staged it — the same trap the tab switch had, back
// through the reload path: an hours-old measurement on the sliders and an
// Apply button for a change nobody asked for.
const awaitingResult = ref(false);

watch(() => calibration.value.result, (result) => {
  if (result && awaitingResult.value) {
    awaitingResult.value = false;
    snapcastStore.stageCalibrationResult();
  }
});

// A run that ends without a proposal must disarm too, or the flag outlives it
// and claims the next result as this user's request.
watch(() => calibration.value.error, (error) => {
  if (error) awaitingResult.value = false;
});

// Codec options for ButtonGroup — the list comes from the backend
// capabilities; only the display casing is presentation-side.
const CODEC_LABELS = { flac: 'FLAC', pcm: 'PCM', opus: 'Opus', ogg: 'Ogg' };
const codecOptions = computed(() =>
  snapcastStore.capabilities.codecs.map(codec => ({
    label: CODEC_LABELS[codec] || codec.toUpperCase(),
    value: codec
  }))
);

// === MULTIROOM - CLIENTS ===

async function loadMultiroomData() {
  // Load clients, server config, and pending clients
  // Zone/client data comes from multiroomStore (initialized in App.vue)
  await Promise.all([
    snapcastStore.loadClients(),
    snapcastStore.loadServerConfig(),
    snapcastStore.loadCalibration(),
    multiroomClientStore.fetchPendingClients(),
  ]);

  // Volume data comes from unifiedAudioStore.volumeState via WebSocket
}

// === MULTIROOM - SERVER CONFIG ===

function selectCodec(codecName) {
  snapcastStore.selectCodec(codecName);
}

async function applyServerConfig() {
  await snapcastStore.applyServerConfig();
}

// Reload data when multiroom becomes ready after a transition
watch(() => multiroomClientStore.transitionState, (newState, oldState) => {
  if (newState === 'idle' && (oldState === 'enabling' || oldState === 'disabling')) {
    loadMultiroomData();
  }
});

// Opened before the store knew multiroom was on, `onMounted` skipped the fetch
// and only a later enable/disable transition would have retried — so the panel
// sat on PLACEHOLDER_SERVER_CONFIG forever, showing a 1000 ms buffer as though
// it were real and an empty codec list. Load on the flip instead.
watch(isMultiroomActive, (active) => {
  if (active && !snapcastStore.capabilities.codecs.length) {
    loadMultiroomData();
  }
});

onMounted(async () => {
  if (isMultiroomActive.value) {
    // loadMultiroomData() already fetches pending clients
    await loadMultiroomData();
  } else {
    // Fetch pending clients even when multiroom is off (they register regardless)
    multiroomClientStore.fetchPendingClients();
  }

  // Start hotspot polling + load the server's wifi creds for adoption auto-fill.
  discoveryStore.startPolling();
  discoveryStore.loadServerWifiCreds();
});

onBeforeUnmount(() => {
  discoveryStore.stopPolling();
});
</script>

<style scoped>
.section-divider {
  height: 1px;
  background: var(--color-border);
}

.loading-state,
.no-clients-state {
  text-align: center;
  padding: var(--space-04);
  color: var(--color-text-secondary);
}

/* Speakers list */
.speakers-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Zone group (zone header + clients) */
.zone-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Zone clients */
.zone-clients {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Ungrouped clients grid */
.ungrouped-clients {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Zone header button */
.zone-header {
  display: flex;
  align-items: flex-end;
  gap: var(--space-01);
  width: 100%;
  cursor: pointer;
}

.zone-header__name {
  color: var(--color-brand);
}

.zone-header__caret {
  color: var(--color-brand);
}

/* Crossover badge */
.crossover-badge {
  display: inline-flex;
  align-items: center;
  margin-left: auto;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
  white-space: nowrap;
}

.crossover-badge--active {
  background: var(--color-background);
  color: var(--color-text-secondary);
}

.crossover-badge--inactive {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
  opacity: 0.8;
}

/* Section subtitle (e.g., "Individual speakers") */
.section-subtitle {
  color: var(--color-text-secondary);
  margin-top: var(--space-03);
  margin-bottom: var(--space-01);
}

/* Automatic analysis */
.auto-tune {
  width: 100%;
}

.analysis-note {
  color: var(--color-text-secondary);
  margin: var(--space-02) 0 0;
}

.analysis-badge {
  color: var(--color-brand);
  margin: var(--space-03) 0 0;
}

/* Framed, with the light separators a settings card uses — the heaviness of
   the first table came from its borders, not from having columns. */
.analysis-links {
  width: 100%;
  border-collapse: collapse;
  margin-top: var(--space-02);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  overflow: hidden;
}

.analysis-links__row > * {
  padding: var(--space-02);
  text-align: left;
}

.analysis-links tbody .analysis-links__row > * {
  border-top: 1px solid var(--color-border);
}

.analysis-links__head {
  color: var(--color-text-light);
}

.analysis-links__icon {
  width: 16px;
  padding-right: 0;
}

.analysis-links__name {
  width: 100%;
}

.analysis-links__value {
  text-align: right;
  white-space: nowrap;
  color: var(--color-text-secondary);
}

.analysis-links__value--warn {
  color: var(--color-brand);
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Discovered speakers list (pending ethernet + wifi hotspots) */
.discovery-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .zone-clients,
  .ungrouped-clients {
    grid-template-columns: 1fr;
  }
}
</style>
