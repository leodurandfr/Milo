<!-- frontend/src/components/multiroom/MultiroomItem.vue -->
<template>
  <div class="multiroom-item" :class="{ 'is-zone': isZone, 'is-expanded': isExpanded }">
    <!-- ZONE/CLIENT HEADER ROW (always visible) -->
    <div class="item-header">
      <!-- Expand button (zones only) -->
      <button
        v-if="canExpand"
        type="button"
        class="expand-button"
        :class="{ 'expanded': isExpanded }"
        @click="toggleExpand"
      >
        <SvgIcon name="caretDown" :size="24" />
      </button>

      <!-- CLIENT NAME -->
      <div class="client-name-wrapper" :class="{ 'is-zone': isZone, 'has-expand': canExpand }">
        <!-- Skeleton wrapper -->
        <div
          class="client-name-skeletons"
          :class="{ 'visible': isLoading }"
        >
          <div class="item-name-skeleton"></div>
          <div
            v-if="isZone"
            class="zone-clients-skeleton"
          ></div>
        </div>

        <!-- Real content -->
        <div
          class="client-name heading-2"
          :class="{
            'visible': !isLoading,
            'muted': client.dspMuted
          }"
        >
          <span class="item-name">{{ client.name }}</span>
        </div>
      </div>

      <!-- VOLUME CONTROL (zone average or single client) -->
      <div class="volume-wrapper">
        <!-- Skeleton shimmer -->
        <div
          class="volume-skeleton"
          :class="{ 'visible': isLoading }"
        ></div>

        <!-- Real content -->
        <div
          class="volume-control"
          :class="{
            'visible': !isLoading,
            'muted': client.dspMuted
          }"
        >
          <RangeSlider
            :model-value="displayVolume"
            :min="sliderMin"
            :max="sliderMax"
            :step="1"
            :disabled="client.dspMuted || isLoading"
            show-value
            value-unit=" dB"
            @input="handleVolumeInput"
            @change="handleVolumeChange"
          />
        </div>
      </div>

      <!-- TOGGLE CONTROL -->
      <div class="toggle-wrapper">
        <!-- Skeleton shimmer -->
        <div
          class="toggle-skeleton"
          :class="{ 'visible': isLoading }"
        ></div>

        <!-- Real content -->
        <div
          class="control-toggle"
          :class="{ 'visible': !isLoading }"
        >
          <Toggle
            :model-value="!client.dspMuted"
            type="background-strong"
            @change="handleMuteToggle"
          />
        </div>
      </div>
    </div>

    <!-- EXPANDED CLIENT LIST (only when zone is expanded) -->
    <Transition name="expand">
      <div v-if="isExpanded && zoneClientDetails" class="expanded-clients">
        <div
          v-for="zoneClient in zoneClientDetails"
          :key="zoneClient.dsp_id"
          class="client-row"
        >
          <!-- Speaker icon -->
          <div class="client-icon">
            <SvgIcon :name="getSpeakerIcon(zoneClient.speakerType)" :size="24" />
          </div>

          <!-- Client name -->
          <span
            class="client-row-name heading-4"
            :class="{ 'muted': zoneClient.dspMuted, 'offline': !zoneClient.available }"
          >
            {{ zoneClient.name }}
            <span v-if="!zoneClient.available" class="badge-offline">Offline</span>
          </span>

          <!-- Client volume slider -->
          <div class="client-volume">
            <RangeSlider
              :model-value="getClientDisplayVolume(zoneClient.dsp_id, zoneClient.dspVolume)"
              :min="sliderMin"
              :max="sliderMax"
              :step="1"
              :disabled="zoneClient.dspMuted || !zoneClient.available || isLoading"
              show-value
              value-unit=" dB"
              @input="(v) => handleClientVolumeInput(zoneClient.dsp_id, v)"
              @change="(v) => handleClientVolumeChange(zoneClient.dsp_id, v)"
            />
          </div>

          <!-- Client mute toggle -->
          <Toggle
            :model-value="!zoneClient.dspMuted"
            variant="secondary"
            @change="(enabled) => handleClientMuteToggle(zoneClient.dsp_id, !enabled)"
          />
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { useSettingsStore } from '@/stores/settingsStore';

const settingsStore = useSettingsStore();

const props = defineProps({
  client: {
    type: Object,
    default: () => ({})
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  // Client names string to show when item represents a zone (e.g., "Client1 · Client2")
  zoneClients: {
    type: String,
    default: ''
  },
  // Whether this item represents a zone (multiple linked clients)
  isZone: {
    type: Boolean,
    default: false
  },
  // Detailed client list for expanded view
  // [{dsp_id, name, dspVolume, dspMuted, speakerType, available}]
  zoneClientDetails: {
    type: Array,
    default: null
  }
});

const emit = defineEmits([
  'volume-change',
  'mute-toggle',
  'client-volume-change',
  'client-mute-toggle'
]);

// === LOCAL STATE ===
const isExpanded = ref(false);
const localDisplayVolume = ref(null);
let throttleTimeout = null;
let finalTimeout = null;

// Per-client throttle map for expanded clients
const clientThrottleMap = new Map();
const clientLocalVolumes = ref({});

// Optimistic mute state (similar to localDisplayVolume for volume)
const localMutedState = ref(null);
const clientLocalMutes = ref({});

// === COMPUTED ===
// Check if zone can be expanded (has client details with multiple clients)
const canExpand = computed(() => props.isZone && props.zoneClientDetails?.length > 1);

// Slider configuration - always in dB, respecting volume limits
const sliderMin = computed(() => settingsStore.volumeLimits.min_db);
const sliderMax = computed(() => settingsStore.volumeLimits.max_db);

// Volume is always in dB (zone average or single client)
const displayVolume = computed(() => {
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }

  // Use dspVolume from client (populated by parent), clamp to limits
  const volume = props.client.dspVolume ?? -30;
  return Math.max(sliderMin.value, Math.min(sliderMax.value, Math.round(volume)));
});

// === HELPERS ===
// Get speaker icon name based on type
function getSpeakerIcon(speakerType) {
  const iconMap = {
    satellite: 'speakerSatellite',
    bookshelf: 'speakerShelf',
    tower: 'speakerColumn',
    subwoofer: 'speakerSub'
  };
  return iconMap[speakerType] || 'speakerShelf';
}

// Get display volume for individual client (uses local value during drag)
function getClientDisplayVolume(dspId, serverVolume) {
  if (clientLocalVolumes.value[dspId] !== undefined) {
    return clientLocalVolumes.value[dspId];
  }
  return Math.max(sliderMin.value, Math.min(sliderMax.value, Math.round(serverVolume ?? -30)));
}

// === ZONE HEADER HANDLERS ===
function toggleExpand() {
  if (canExpand.value) {
    isExpanded.value = !isExpanded.value;
  }
}

function handleVolumeInput(newDisplayVolume) {
  localDisplayVolume.value = newDisplayVolume;

  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  throttleTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 50);

  finalTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 500);
}

function handleVolumeChange(newDisplayVolume) {
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  localDisplayVolume.value = null;
  sendVolumeUpdate(newDisplayVolume);
}

function sendVolumeUpdate(volumeDb) {
  if (!props.isLoading) {
    emit('volume-change', props.client.id, volumeDb);
  }
}

function handleMuteToggle(enabled) {
  if (!props.isLoading) {
    const newMuted = !enabled;
    emit('mute-toggle', props.client.id, newMuted);
  }
}

// === INDIVIDUAL CLIENT HANDLERS (expanded view) ===
function handleClientVolumeInput(clientDspId, value) {
  // Update local display volume for smooth UI
  clientLocalVolumes.value[clientDspId] = value;

  let throttleState = clientThrottleMap.get(clientDspId) || {};

  if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
  if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);

  throttleState.throttleTimeout = setTimeout(() => {
    emit('client-volume-change', clientDspId, value);
  }, 30);

  throttleState.finalTimeout = setTimeout(() => {
    emit('client-volume-change', clientDspId, value);
  }, 100);

  clientThrottleMap.set(clientDspId, throttleState);
}

function handleClientVolumeChange(clientDspId, value) {
  // Clear throttle timers
  const throttleState = clientThrottleMap.get(clientDspId);
  if (throttleState) {
    if (throttleState.throttleTimeout) clearTimeout(throttleState.throttleTimeout);
    if (throttleState.finalTimeout) clearTimeout(throttleState.finalTimeout);
    clientThrottleMap.delete(clientDspId);
  }

  // Clear local display volume
  delete clientLocalVolumes.value[clientDspId];

  // Final update
  emit('client-volume-change', clientDspId, value);
}

function handleClientMuteToggle(clientDspId, muted) {
  emit('client-mute-toggle', clientDspId, muted);
}

// === CLEANUP ===
onUnmounted(() => {
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  // Clear client throttle timers
  clientThrottleMap.forEach(state => {
    if (state.throttleTimeout) clearTimeout(state.throttleTimeout);
    if (state.finalTimeout) clearTimeout(state.finalTimeout);
  });
  clientThrottleMap.clear();
});
</script>

<style scoped>
.multiroom-item {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-06);
  padding: var(--space-04);
  background: var(--color-background-neutral);
}

/* === ITEM HEADER (zone/client row) === */
.item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-04);
  min-height: 40px;
}

/* === EXPAND BUTTON === */
.expand-button {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-strong);
  border: none;
  cursor: pointer;
  color: var(--color-text-secondary);
  flex-shrink: 0;
  border-radius: var(--radius-03);
}

.expand-button :deep(svg) {
  transition: transform var(--transition-fast);
}

.expand-button.expanded :deep(svg) {
  transform: rotate(180deg);
}

/* === CLIENT NAME WRAPPER === */
.client-name-wrapper {
  min-width: 190px;
  max-width: 190px;
  min-height: 24px;
  position: relative;
  display: flex;
  align-items: center;
}

.client-name-wrapper.has-expand {
  min-width: 158px;
  max-width: 158px;
}

.client-name-wrapper.is-zone {
  min-height: 42px;
}

.client-name-wrapper.is-zone.has-expand {
  min-height: 24px;
}

/* Skeleton wrapper for name */
.client-name-skeletons {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: var(--space-01);
  opacity: 0;
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.client-name-skeletons.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

.item-name-skeleton {
  height: 20px;
  width: 64%;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

.zone-clients-skeleton {
  height: 14px;
  width: 80%;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  animation-delay: 0.2s;
}

/* Real name content */
.client-name {
  color: var(--color-text);
  overflow: hidden;
  opacity: 0;
  transition: opacity 300ms ease 0ms, color 300ms ease;
  width: 100%;
}

.client-name.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms, color 300ms ease;
}

.client-name.muted {
  color: var(--color-text-light);
}

.client-name {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
}

.item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  width: 100%;
}

.zone-clients {
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  width: 100%;
}

/* === VOLUME WRAPPER === */
.volume-wrapper {
  flex: 1;
  height: 40px;
  position: relative;
  display: flex;
  align-items: center;
}

/* Skeleton for volume */
.volume-skeleton {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.volume-skeleton.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* Real volume content */
.volume-control {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
}

.volume-control.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

.volume-control.muted :deep(.slider-value) {
  color: var(--color-text-light);
}

/* === TOGGLE WRAPPER === */
.toggle-wrapper {
  width: 70px;
  height: 40px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Skeleton for toggle */
.toggle-skeleton {
  position: absolute;
  width: 70px;
  height: 40px;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.toggle-skeleton.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* Real toggle content */
.control-toggle {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
}

.control-toggle.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* === EXPANDED CLIENTS SECTION === */
.expanded-clients {
  display: flex;
  flex-direction: column;
  margin-top: var(--space-03);
  padding-top: var(--space-03);
  border-top: 1px solid var(--color-border);
}

/* Individual client row in expanded zone */
.client-row {
  display: grid;
  grid-template-columns: 40px auto 1fr auto;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) 0;
}

.client-row:first-child {
  padding-top: 0;
}

.client-row:last-child {
  padding-bottom: 0;
}

.client-row:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
}

.client-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-strong);
  border-radius: var(--radius-03);
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.client-row-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 100px;
  transition: color var(--transition-fast);
}

.client-row-name.muted,
.client-row-name.offline {
  color: var(--color-text-light);
}

.client-volume {
  min-width: 0;
}

.badge-offline {
  display: inline-block;
  background: var(--color-background-medium);
  color: var(--color-text-light);
  padding: 1px 6px;
  border-radius: var(--radius-02);
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  margin-left: var(--space-02);
  vertical-align: middle;
}

/* === EXPAND TRANSITION === */
.expand-enter-active,
.expand-leave-active {
  transition: all var(--transition-normal);
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  opacity: 0;
  max-height: 0;
  padding-top: 0;
  margin-top: 0;
}

.expand-enter-to,
.expand-leave-from {
  max-height: 500px;
}

/* === ANIMATIONS === */
@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

/* === MOBILE ADJUSTMENTS === */
@media (max-aspect-ratio: 4/3) {
  .multiroom-item {
    border-radius: var(--radius-05);
  }

  .item-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-03);
  }

  .expand-button {
    order: 0;
  }

  .client-name-wrapper {
    flex: 1;
    order: 1;
    min-width: 0;
    max-width: none;
  }

  .client-name-wrapper.has-expand {
    min-width: 0;
    max-width: none;
  }

  .toggle-wrapper {
    order: 2;
    margin-left: auto;
    width: 56px;
    height: 32px;
    align-self: flex-start;
  }

  .toggle-skeleton {
    width: 56px;
    height: 32px;
  }

  .volume-wrapper {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }

  /* Client row mobile layout */
  .client-row {
    grid-template-columns: 40px 1fr auto;
    grid-template-rows: auto auto;
    gap: var(--space-02);
  }

  .client-row .client-icon {
    grid-column: 1;
    grid-row: 1;
  }

  .client-row .client-row-name {
    grid-column: 2;
    grid-row: 1;
    align-self: center;
  }

  .client-row > :deep(.toggle) {
    grid-column: 3;
    grid-row: 1;
  }

  .client-row .client-volume {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}
</style>
