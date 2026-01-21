<!-- frontend/src/components/multiroom/MultiroomItem.vue -->
<template>
  <div class="multiroom-item" :class="{ 'is-zone': isZone, 'is-expanded': isExpanded }">
    <!-- ZONE/CLIENT HEADER ROW (always visible) -->
    <div class="item-header">
      <!-- Icon column: expand button (zones) OR speaker icon (standalone clients) -->
      <div class="icon-column">
        <!-- Skeleton shimmer -->
        <div
          class="icon-skeleton"
          :class="{ 'visible': isLoading }"
        ></div>

        <!-- Real content -->
        <div
          class="icon-content"
          :class="{ 'visible': !isLoading }"
        >
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

          <!-- Speaker icon (standalone clients only) -->
          <div
            v-else-if="!isZone"
            class="client-icon"
          >
            <SvgIcon :name="getSpeakerIcon(clientSpeakerType)" :size="24" />
          </div>
        </div>
      </div>

      <!-- CLIENT NAME -->
      <div class="client-name-wrapper" :class="{ 'is-zone': isZone }">
        <!-- Skeleton wrapper -->
        <div
          class="client-name-skeletons"
          :class="{ 'visible': isLoading }"
        >
          <div class="item-name-skeleton"></div>
        </div>

        <!-- Real content -->
        <div
          class="client-name heading-3"
          :class="{
            'visible': !isLoading,
            'muted': client.dspMuted,
            'offline': !client.online && !isZone
          }"
        >
          <span class="item-name">{{ client.name }}</span>
          <span v-if="!client.online && !isZone" class="offline-badge text-mono">
            {{ t('multiroom.offline') }}
          </span>
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
            :disabled="isLoading || (!client.online && !isZone)"
            :muted="client.dspMuted"
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
            variant="secondary"
            :disabled="!client.online && !isZone"
            @change="handleMuteToggle"
          />
        </div>
      </div>
    </div>

    <!-- EXPANDED CLIENT LIST (only when zone is expanded) -->
    <Transition name="expand">
      <div v-if="isExpanded && zoneClientDetails" class="expanded-clients">
        <div
          v-for="(zoneClient, index) in zoneClientDetails"
          :key="zoneClient.mac_id"
          class="client-row"
          :style="{ animationDelay: `${150 + index * 120}ms` }"
        >
          <!-- Speaker icon -->
          <div class="client-icon">
            <SvgIcon :name="getSpeakerIcon(zoneClient.speakerType)" :size="24" />
          </div>

          <!-- Client name -->
          <span
            class="client-row-name heading-3"
            :class="{ 'muted': zoneClient.dspMuted, 'offline': !zoneClient.online }"
          >
            {{ zoneClient.name }}
          </span>

          <!-- Client volume slider (when online) -->
          <div v-if="zoneClient.online" class="client-volume">
            <RangeSlider
              :model-value="getClientDisplayVolume(zoneClient.mac_id, zoneClient.dspVolume)"
              :min="sliderMin"
              :max="sliderMax"
              :step="1"
              :disabled="isLoading"
              :muted="zoneClient.dspMuted"
              show-value
              value-unit=" dB"
              @input="(v) => handleClientVolumeInput(zoneClient.mac_id, v)"
              @change="(v) => handleClientVolumeChange(zoneClient.mac_id, v)"
            />
          </div>

          <!-- Offline indicator (when offline) -->
          <div v-else class="client-offline text-mono">
            {{ t('multiroom.offline') }}
          </div>

          <!-- Client mute toggle -->
          <Toggle
            :model-value="!zoneClient.dspMuted"
            variant="secondary"
            :disabled="!zoneClient.online"
            @change="(enabled) => handleClientMuteToggle(zoneClient.mac_id, !enabled)"
          />
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { useSettingsStore } from '@/stores/settingsStore';
import { useDspStore } from '@/stores/dspStore';
import { useVolumeThrottle, useVolumeThrottleMap } from '@/composables/useVolumeThrottle';
import { useI18n } from '@/services/i18n';

const { t } = useI18n();
const settingsStore = useSettingsStore();
const dspStore = useDspStore();

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
  // [{mac_id, name, dspVolume, dspMuted, speakerType, online}]
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
const clientLocalVolumes = ref({});

// Optimistic mute state (similar to localDisplayVolume for volume)
const localMutedState = ref(null);
const clientLocalMutes = ref({});

// === THROTTLE MANAGEMENT (unified via composable) ===
// Zone header slider: MEDIUM preset (80ms throttle, 300ms final)
const { throttledFn: throttledZoneVolume, flush: flushZoneVolume } = useVolumeThrottle(
  (volumeDb) => {
    if (!props.isLoading) {
      emit('volume-change', props.client.id, volumeDb);
    }
  },
  'MEDIUM'
);

// Individual client sliders: use throttle map with FAST preset (50ms throttle, 150ms final)
const { getThrottledFn: getClientThrottledFn } = useVolumeThrottleMap(
  (clientMacId) => (value) => {
    emit('client-volume-change', clientMacId, value);
  },
  'FAST'
);

// === COMPUTED ===
// Check if zone can be expanded (has client details with multiple clients)
const canExpand = computed(() => props.isZone && props.zoneClientDetails?.length > 1);

// Slider configuration - always in dB, respecting volume limits
const sliderMin = computed(() => settingsStore.volumeLimits.min_db);
const sliderMax = computed(() => settingsStore.volumeLimits.max_db);

// Speaker type for standalone client (not zone)
const clientSpeakerType = computed(() => {
  if (props.isZone) return null;
  return dspStore.getClientSpeakerType(props.client.mac_id) || 'bookshelf';
});

// Volume is always in dB (zone average or single client)
const displayVolume = computed(() => {
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }

  // Use dspVolume from client (populated by parent), clamp to limits
  const volume = props.client.dspVolume ?? -60;
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
function getClientDisplayVolume(macId, serverVolume) {
  if (clientLocalVolumes.value[macId] !== undefined) {
    return clientLocalVolumes.value[macId];
  }
  return Math.max(sliderMin.value, Math.min(sliderMax.value, Math.round(serverVolume ?? -60)));
}

// === ZONE HEADER HANDLERS ===
function toggleExpand() {
  if (canExpand.value) {
    isExpanded.value = !isExpanded.value;
  }
}

function handleVolumeInput(newDisplayVolume) {
  localDisplayVolume.value = newDisplayVolume;
  throttledZoneVolume(newDisplayVolume);
}

function handleVolumeChange(newDisplayVolume) {
  localDisplayVolume.value = null;
  flushZoneVolume();
  if (!props.isLoading) {
    emit('volume-change', props.client.id, newDisplayVolume);
  }
}

function handleMuteToggle(enabled) {
  if (!props.isLoading) {
    const newMuted = !enabled;
    emit('mute-toggle', props.client.id, newMuted);
  }
}

// === INDIVIDUAL CLIENT HANDLERS (expanded view) ===
function handleClientVolumeInput(clientMacId, value) {
  // Update local display volume for smooth UI
  clientLocalVolumes.value[clientMacId] = value;
  // Use throttled function from composable
  getClientThrottledFn(clientMacId)(value);
}

function handleClientVolumeChange(clientMacId, value) {
  // Clear local display volume on release (reassign object to guarantee Vue 3 reactivity)
  const { [clientMacId]: _, ...rest } = clientLocalVolumes.value;
  clientLocalVolumes.value = rest;
  // Emit final value immediately (composable's final timer handles any pending)
  emit('client-volume-change', clientMacId, value);
}

function handleClientMuteToggle(clientMacId, muted) {
  emit('client-mute-toggle', clientMacId, muted);
}

// Note: Cleanup handled automatically by useVolumeThrottle and useVolumeThrottleMap composables
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
  display: grid;
  grid-template-columns: 40px auto 1fr auto;
  align-items: center;
  gap: var(--space-04);
  min-height: 40px;
}

/* === ICON COLUMN === */
.icon-column {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  position: relative;
}

/* Skeleton for icon column */
.icon-skeleton {
  position: absolute;
  inset: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-03);
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

.icon-skeleton.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* Real icon content */
.icon-content {
  opacity: 0;
  transition: opacity 300ms ease 0ms;
}

.icon-content.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
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
  min-width: 100px;
  max-width: none;
  min-height: 24px;
  position: relative;
  display: flex;
  align-items: center;
}

.client-name-wrapper.is-zone {
  min-height: 42px;
}

/* Skeleton wrapper for name */
.client-name-skeletons {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
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

.client-name.offline {
  color: var(--color-text-light);
}

.client-name {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
}

.offline-badge {
  color: var(--color-text-secondary);
  text-transform: uppercase;
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
  min-width: 0;
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
  gap: var(--space-04);
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

.client-offline {
  display: flex;
  align-items: center;
  height: 40px;
  background: var(--color-background);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  padding-left: var(--space-04);
  text-transform: uppercase;
}

/* Staggered fade-in animation for client rows */
.expanded-clients .client-row {
  opacity: 0;
  animation: fadeInRow 300ms ease forwards;
}

@keyframes fadeInRow {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* === EXPAND TRANSITION === */
.expand-enter-active {
  transition: all 400ms ease;
  overflow: hidden;
}

.expand-leave-active {
  transition: all 300ms ease;
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
  opacity: 1;
  max-height: 300px;
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
    display: grid;
    grid-template-columns: 40px 1fr auto;
    grid-template-rows: auto auto;
    align-items: center;
    gap: var(--space-03);
  }

  .icon-column {
    grid-column: 1;
    grid-row: 1;
  }

  .client-name-wrapper {
    grid-column: 2;
    grid-row: 1;
    min-width: 0;
    max-width: none;
  }

  .toggle-wrapper {
    grid-column: 3;
    grid-row: 1;
    width: 56px;
    height: 32px;
    justify-self: end;
  }

  .toggle-skeleton {
    width: 56px;
    height: 32px;
  }

  .volume-wrapper {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  /* Client row mobile layout */
  .client-row {
    grid-template-columns: 40px 1fr auto;
    grid-template-rows: auto auto;
    gap: var(--space-03);
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

  .client-row .client-volume,
  .client-row .client-offline {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}
</style>
