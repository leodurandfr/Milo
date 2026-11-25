<!-- frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue -->
<template>
  <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Enabling or Disabled, and the header switch refused -->
        <SectionStack v-if="showMessage" :key="multiroomClientStore.transitionState">
          <MessageContent :loading="isLoading" :loading-delay="0"
            :icon="isLoading ? null : 'multiroom'" :title="messageTitle" />
          <NoticeBox v-if="toggleRefusal !== null" :detail="toggleRefusal">{{ t('settings.writeRefused') }}</NoticeBox>
          <!-- A speaker presenting itself opens this modal (App.vue) whether
               multiroom is on or not: off, this is what says why it opened. -->
          <NoticeBox v-if="waitingSpeakers > 0" kind="warning">
            {{ t('multiroom.pending.enableToAdd', { count: waitingSpeakers }) }}
          </NoticeBox>
        </SectionStack>
        <!-- SETTINGS: Active and ready -->
        <SectionStack v-else key="settings">
          <NoticeBox v-if="toggleRefusal !== null" :detail="toggleRefusal">{{ t('settings.writeRefused') }}</NoticeBox>
          <!-- Discovered Speakers Section (pending ethernet + wifi hotspots) -->
          <SectionCard v-if="discoveryItems.length > 0">
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
          </SectionCard>

          <SectionCard>
            <template #header>
              <SectionHeader :title="t('multiroom.zonesAndSystems')">
                <template #actions>
                  <Button v-if="ungroupedClients.length >= 2" variant="tinted" size="small" @click="handleCreateZone">
                    {{ t('equalizer.zones.createZone') }}
                  </Button>
                </template>
              </SectionHeader>
            </template>

            <div v-if="snapcastStore.isLoading" class="ungrouped-clients">
              <SkeletonListItem v-for="i in 2" :key="i" subtitle="mono" />
            </div>

            <NoticeBox v-else-if="sortedMultiroomClients.length === 0" kind="empty">
              {{ t('multiroom.noSystems') }}
            </NoticeBox>

            <div v-else class="speakers-list">
              <div v-for="zone in zones" :key="zone.id" class="zone-group">
                <button type="button" class="zone-header" @click="handleEditZone(zone.id)">
                  <span class="zone-header__name heading-3">{{ zone.displayName }}</span>
                  <SvgIcon name="caretRight" :size="20" class="zone-header__caret" />
                  <!-- Crossover badge -->
                  <Badge v-if="zone.crossover_enabled" class="crossover-badge"
                    :title="t('multiroom.crossover.badgeActive')">
                    {{ formatUnit(zone.crossover_frequency, 'Hz') }}
                  </Badge>
                  <Badge v-else-if="zone.has_subwoofer" class="crossover-badge" tone="warning"
                    :title="t('multiroom.crossover.subwooferOffline')">
                    {{ t('multiroom.crossover.badgeInactive') }}
                  </Badge>
                </button>
                <div class="zone-clients">
                  <SystemListItem v-for="client in zone.clients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    :status="client.status" :status-variant="client.statusVariant"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </div>

              <template v-if="ungroupedClients.length > 0">
                <h3 v-if="zones.length > 0" class="heading-3 section-subtitle">{{ t('multiroom.individualSystems') }}
                </h3>
                <div class="ungrouped-clients">
                  <SystemListItem v-for="client in ungroupedClients" :key="client.id"
                    :name="client.name" :mac-id="client.mac_id" :online="client.online"
                    :status="client.status" :status-variant="client.statusVariant"
                    @click="handleEditClient(client.mac_id)" />
                </div>
              </template>
            </div>

            <!-- A speaker that refuses this server's secret answers another
                 server (this one reflashed): the gesture that moves it, under
                 the row that says "paired with another Milō". -->
            <NoticeBox v-for="speaker in otherServerSpeakers" :key="speaker.macId" kind="warning">
              {{ t('multiroom.otherServer.notice', { name: speaker.name }) }}
            </NoticeBox>
          </SectionCard>

          <!-- No modes. The analysis is an action, not a state one sits in --
               tabs claimed otherwise, and made the codec selection invisible
               besides, because a disabled ButtonGroup drops its active mark. -->
          <SectionCard>
            <template #header>
              <SectionHeader :title="t('multiroomSettings.latencyAndQuality')">
                <template #actions>
                  <Button v-if="canReset" variant="tinted" size="small"
                    :disabled="snapcastStore.isApplyingServerConfig || calibration.running"
                    @click="resetToDefault">
                    {{ t('multiroomSettings.reset') }}
                  </Button>
                </template>
              </SectionHeader>
            </template>

            <!-- Drawn only over values the server reported: a form over
                 placeholders showed them as the running config, and an Apply
                 wrote them. -->
            <template v-if="serverConfigReady">
              <RangeSlider :label="t('multiroomSettings.globalBuffer')" v-model="snapcastStore.serverConfig.buffer_ms" :min="150" :max="3000" :step="10"
                unit="ms" :disabled="busy" />

              <RangeSlider :label="t('multiroomSettings.chunkSize')" v-model="snapcastStore.serverConfig.chunk_ms" :min="15" :max="50" :step="5" ticks
                unit="ms" :disabled="busy" />

              <RangeSlider :label="t('multiroomSettings.snapclientBuffer')" v-model="snapcastStore.serverConfig.snapclient_buffer_time" :min="60" :max="300" :step="10"
                unit="ms" :disabled="busy" />

              <SettingItem :label="t('multiroomSettings.codec')">
                <ButtonGroup :model-value="snapcastStore.serverConfig.codec" :options="codecOptions"
                  :disabled="busy" mobile-layout="row" @change="selectCodec" />
              </SettingItem>
            </template>

            <template v-else-if="snapcastStore.serverConfigStatus === 'unavailable'">
              <NoticeBox kind="warning">{{ t('multiroomSettings.configUnavailable') }}</NoticeBox>
              <Button variant="tinted" size="small" class="config-retry" :loading="retryingConfig"
                :disabled="retryingConfig" @click="retryServerConfig">
                {{ t('multiroomSettings.retry') }}
              </Button>
            </template>

          </SectionCard>

          <!-- The result of a run is the sliders above moving. The only thing
               they cannot say is which speaker held the house back, so that is
               what the results show. -->
          <AnalysisSection :title="t('analysis.title')" :running="calibration.running" :disabled="busy || !serverConfigReady"
            :percent="progressPercent" :step-ms="PROGRESS_TICK_MS" :label="stageLabel"
            :hint="t('multiroomSettings.remaining', { time: formatUnit(remainingSeconds, 's') })"
            :error="analysisError" :note="analysisNote" :has-results="measuredLinks.length > 0" @start="startAnalysis">
            <!-- What the measurement found, and only what a person can act
                 on: which speakers were weighed, how each connects, and which
                 one held the house back. The round-trip in milliseconds is
                 gone -- it was the one figure here nobody can rank. -->
            <p v-if="showsMeasuredValues" class="text-mono-medium analysis-badge">
              {{ t('multiroomSettings.valuesAreMeasured') }}
            </p>

            <!-- One card per measured speaker, in the same grid the info
                 panel uses for its figures: the table's headers were there
                 to name two columns, and a label beside each value names
                 them without a header row to align. -->
            <div class="analysis-grid">
              <div v-for="row in measuredLinks" :key="row.mac_id" class="analysis-item">
                <div class="analysis-item__head">
                  <SvgIcon v-if="row.link === 'ethernet'" name="network" :size="20" />
                  <WifiSignal v-else :signal="row.signal_percent ?? 100" :size="20" />
                  <!-- The face the same speaker's name wears in the list
                       above, where ListItemButton titles a row carrying a
                       subtitle. -->
                  <span class="heading-4 analysis-item__name">{{ row.name }}</span>
                </div>
                <div class="analysis-item__metrics">
                  <div class="analysis-item__metric">
                    <span class="text-mono-medium analysis-item__label">
                      {{ t('multiroomSettings.latency') }}
                    </span>
                    <span class="text-mono-medium analysis-item__value">{{ formatUnit(row.rtt_max_ms, 'ms') }}</span>
                  </div>
                  <div class="analysis-item__metric">
                    <span class="text-mono-medium analysis-item__label">
                      {{ t('multiroomSettings.loss') }}
                    </span>
                    <span class="text-mono-medium analysis-item__value"
                      :class="{ 'analysis-item__value--warn': row.loss_pct > 0 }">
                      {{ formatUnit(row.loss_pct, '%') }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </AnalysisSection>

          <!-- What the server refused, and Apply, in one sticky footer: two at
               bottom 0 would overlap. -->
          <div class="sticky-footer">
            <SettingsRefusal :detail="applyRefusal" />
            <Button v-if="snapcastStore.hasServerConfigChanges" variant="brand" size="medium"
              class="apply-button" floating :loading="snapcastStore.isApplyingServerConfig"
              :disabled="snapcastStore.isApplyingServerConfig" @click="applyServerConfig">
              {{ snapcastStore.isApplyingServerConfig ? t('multiroom.restarting') : t('multiroomSettings.apply') }}
            </Button>
          </div>
        </SectionStack>
  </Transition>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useAnalysisRun, PROGRESS_TICK_MS } from '@/composables/useAnalysisRun';
import { useI18n } from '@/services/i18n';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDiscoveryStore } from '@/stores/discoveryStore';
import { useHotspotPolling } from '@/composables/useHotspotPolling';
import Badge from '@/components/ui/Badge.vue';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SystemListItem from '@/components/settings/categories/multiroom/SystemListItem.vue';
import SkeletonListItem from '@/components/ui/SkeletonListItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import SettingsRefusal from '@/components/settings/SettingsRefusal.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SectionHeader from '@/components/ui/SectionHeader.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import AnalysisSection from '@/components/settings/AnalysisSection.vue';

defineProps({
  // The server's detail when the header's switch (SettingsModal) was refused.
  toggleRefusal: { type: String, default: null }
});

const emit = defineEmits(['edit-zone', 'create-zone', 'edit-client', 'configure-system']);

const { t, formatUnit } = useI18n();
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
// The hotspots are drawn in the settings branch only.
useHotspotPolling(() => !showMessage.value);
const isLoading = computed(() => multiroomClientStore.transitionState === 'enabling');
// Speakers waiting while multiroom is off, at rest (not while it starts).
const waitingSpeakers = computed(() =>
  multiroomClientStore.transitionState === 'idle' && !isMultiroomActive.value
    ? multiroomClientStore.pendingClientList.length
    : 0
);
const messageTitle = computed(() => {
  if (multiroomClientStore.transitionState === 'error') {
    return multiroomClientStore.transitionError || t('multiroom.error');
  }
  return multiroomClientStore.transitionState === 'enabling' ? t('multiroom.starting') : t('multiroom.disabled');
});

// Clients are already sorted (local first, then alphabetical) from multiroomStore
const sortedMultiroomClients = computed(() => snapcastStore.clients);

// A row's status line: empty for a speaker that answers this server.
function rowStatus(client) {
  return client.other_server
    ? { status: t('multiroom.otherServer.status'), statusVariant: 'warning' }
    : { status: '', statusVariant: '' };
}

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
        online: client.online,
        ...rowStatus(client)
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
      online: client.online,
      ...rowStatus(client)
    }));
});

// Unified discovery list: pending ethernet clients + visible wifi hotspots.
// Each item carries a discovery `source` so the parent knows which adoption
// flow to launch (ethernet via configure-pending, wifi via adopt-speaker).
const discoveryItems = computed(() => {
  const items = [];

  for (const client of multiroomClientStore.pendingClientList) {
    const configuring = multiroomClientStore.isClientConfiguring(client.mac_id);
    let status = { status: t('multiroom.pending.notConfigured'), statusVariant: '' };
    if (configuring) status = { status: t('multiroom.pending.rebooting'), statusVariant: 'configuring' };
    else if (client.other_server) status = rowStatus(client);
    items.push({
      key: `eth:${client.mac_id}`,
      source: 'ethernet',
      name: client.name || client.ip,
      ...status,
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

// Every speaker, pending or admitted, whose answer to this server's secret
// says it answers another server — once, though an admission can leave it in
// both lists for a moment.
const otherServerSpeakers = computed(() => {
  const speakers = new Map();
  for (const client of multiroomClientStore.pendingClientList) {
    if (client.other_server) speakers.set(client.mac_id, client.name || client.ip);
  }
  for (const client of multiroomClientStore.clientList) {
    if (client.other_server) speakers.set(client.mac_id, client.name || client.host);
  }
  return Array.from(speakers, ([macId, name]) => ({ macId, name }));
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
const serverConfigReady = computed(() => snapcastStore.serverConfigStatus === 'ready');

// The status stays 'unavailable' while a retry is in flight, so a retry that
// fails again would leave the card exactly as it was: the button says it ran.
const retryingConfig = ref(false);
async function retryServerConfig() {
  retryingConfig.value = true;
  try {
    await snapcastStore.loadServerConfig();
  } finally {
    retryingConfig.value = false;
  }
}

// The factory configuration, as the backend declares it — never restated here,
// so "reset" restores what a freshly flashed unit actually runs.
const defaultPreset = computed(() =>
  snapcastStore.capabilities.presets.find(preset => preset.id === 'default') || null
);

// Offered only when it would change something: a reset button on a machine
// already at its factory values is a control that does nothing.
const canReset = computed(() => {
  const config = defaultPreset.value?.config;
  return serverConfigReady.value && Boolean(config) &&
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
const analysisError = computed(() => (calibration.value.error
  ? t(`multiroomSettings.${ANALYSIS_ERROR_KEYS[calibration.value.error] || 'failedProbe'}`,
    { detail: calibration.value.detail || '' })
  : ''));

const analysisNote = computed(() =>
  (calibration.value.result?.assumed?.length ? t('multiroomSettings.partlyAssumed') : ''));

const measuredLinks = computed(() =>
  // The server's own row is left out of the table, though its link is measured
  // and weighed in the codec choice (calibration_probe.py): it is the route every
  // stream leaves by, not a speaker's link, and the table compares speakers.
  (calibration.value.result?.measurements || []).filter(row => !row.is_local)
);

// True when what is RUNNING came from the measurement, which is what the line
// claims. Comparing the sliders instead read false after any reload: they hold
// the applied configuration then, not the proposal, so the mention vanished on
// a system whose settings had in fact been measured. `hasServerConfigChanges`
// is what makes the sliders and the applied config the same question.
const showsMeasuredValues = computed(() => {
  const config = calibration.value.result?.config;
  return Boolean(config) && serverConfigReady.value && !snapcastStore.hasServerConfigChanges &&
    Object.keys(config).every(key => snapcastStore.serverConfig[key] === config[key]);
});

// === PROGRESS AND STAGING ===

// A measured result lands in the sliders, which is where every other setting is
// read. Staging is not writing — the sticky Apply is, so snapserver.conf keeps
// exactly one writer.
const { progressPercent, remainingSeconds, startAnalysis } = useAnalysisRun(calibration, {
  start: () => snapcastStore.startCalibration('lossless'),
  stage: () => snapcastStore.stageCalibrationResult(),
});

// Numbers, not a verdict. "Sets the limit" singled out whichever speaker had
// the marginally worse jitter — 1.02 ms against 0.36 ms on two gigabit links —
// and which one that was flipped between runs. It read as "this speaker is the
// problem" about a fleet where nothing was wrong. Two measured figures side by
// side say the same thing without accusing anyone.

const ANALYSIS_STAGE_KEYS = { probing: 'stageProbing', computing: 'stageComputing' };
const stageLabel = computed(() =>
  t(`multiroomSettings.${ANALYSIS_STAGE_KEYS[calibration.value.stage] || 'stageProbing'}`)
);

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

// The server's detail when Apply was refused; the staged values stay on screen.
const applyRefusal = ref(null);

async function applyServerConfig() {
  applyRefusal.value = null;
  await snapcastStore.applyServerConfig({ errorRef: applyRefusal });
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

  // The server's network, offered as an adopted speaker's target.
  discoveryStore.loadServerWifi();
});
</script>

<style scoped>
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
  margin-left: auto;
}

/* Section subtitle (e.g., "Individual speakers") */
.section-subtitle {
  color: var(--color-text-secondary);
  margin-top: var(--space-03);
  margin-bottom: var(--space-01);
}

/* Retry under the notice of a config the server did not report */
.config-retry {
  align-self: flex-start;
}

/* Automatic analysis */
.analysis-badge {
  color: var(--color-brand);
  margin: 0;
}

/* Same card grid as the info panel's figures: a framed table was the one
   element on these screens drawing its own borders. */
.analysis-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
  margin-top: var(--space-02);
}

.analysis-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-inset);
}

.analysis-item__head {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  color: var(--color-text-secondary);
}

.analysis-item__name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Who was measured, then what was measured: the hairline the settings cards
   already use, so the name is not read as a third figure's label. */
.analysis-item__metrics {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding-top: var(--space-03);
  border-top: 1px solid var(--color-border);
}

.analysis-item__metric {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-02);
}

.analysis-item__label {
  color: var(--color-text-secondary);
}

.analysis-item__value {
  color: var(--color-text);
  white-space: nowrap;
}

.analysis-item__value--warn {
  color: var(--color-warning);
}

/* Sticky footer: the refusal and the apply button (as in HardwareSettings) */
.sticky-footer {
  position: sticky;
  bottom: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.sticky-footer:empty {
  display: none;
}

.apply-button {
  width: 100%;
}

/* Discovered speakers list (pending ethernet + wifi hotspots) */
.discovery-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .zone-clients,
  .ungrouped-clients,
  .analysis-grid {
    grid-template-columns: 1fr;
  }
}
</style>
