<!-- frontend/src/components/multiroom/MultiroomItem.vue -->
<template>
  <div class="multiroom-item" :class="{ 'is-zone': isZone, 'is-expanded': isExpanded }">
    <!-- ZONE/CLIENT HEADER ROW (always visible) -->
    <div class="item-header">
      <!-- Icon column: expand button (zones) OR speaker icon (standalone clients) -->
      <div class="icon-column">
        <!-- Skeleton shimmer -->
        <div
          class="icon-skeleton shimmer"
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
            :class="{ 'muted': client.equalizerMuted, 'offline': !client.online }"
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
          <div class="item-name-skeleton shimmer"></div>
        </div>

        <!-- Real content -->
        <div
          class="client-name heading-3"
          :class="{
            'visible': !isLoading,
            'muted': client.equalizerMuted,
            'offline': !client.online && !isZone
          }"
        >
          <span class="item-name">{{ client.name }}</span>
        </div>
      </div>

      <!-- VOLUME CONTROL (zone average or single client) -->
      <div class="volume-wrapper">
        <!-- Skeleton shimmer -->
        <div
          class="volume-skeleton shimmer"
          :class="{ 'visible': isLoading }"
        ></div>

        <!-- Offline indicator (standalone offline clients) -->
        <div
          v-if="!client.online && !isZone"
          class="client-offline text-mono-small"
          :class="{ 'visible': !isLoading }"
        >
          {{ t('multiroom.offline') }}
        </div>

        <!-- External volume indicator (DAC clients or all-DAC zones) -->
        <div
          v-else-if="isExternalVolume"
          class="client-external-volume text-mono-small"
          :class="{ 'visible': !isLoading }"
        >
          {{ t('multiroom.externalVolume') }}
        </div>

        <!-- Real content (online clients or zones) -->
        <div
          v-else
          class="volume-control"
          :class="{
            'visible': !isLoading,
            'muted': client.equalizerMuted
          }"
        >
          <RangeSlider
            :model-value="displayVolume"
            :min="sliderMin"
            :max="sliderMax"
            :step="1"
            :disabled="isLoading"
            :muted="client.equalizerMuted"
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
          class="toggle-skeleton shimmer"
          :class="{ 'visible': isLoading }"
        ></div>

        <!-- Real content -->
        <div
          class="control-toggle"
          :class="{ 'visible': !isLoading }"
        >
          <Toggle
            v-if="client.online || isZone"
            :model-value="!client.equalizerMuted"
            variant="secondary"
            @change="handleMuteToggle"
          />
          <div v-else class="toggle-offline-placeholder"></div>
        </div>
      </div>
    </div>

    <!-- EXPANDED CLIENT LIST - Height wrapper for single-step ResizeObserver update -->
    <div
      class="expanded-wrapper"
      :style="{ height: expandedWrapperHeight }"
    >
      <!-- Content always rendered when data exists (for measurement), visibility controlled by CSS -->
      <div
        v-if="zoneClientDetails?.length > 1"
        ref="expandedContentRef"
        class="expanded-clients"
        :class="{ 'is-visible': isExpanded }"
      >
        <div
          v-for="(zoneClient, index) in zoneClientDetails"
          :key="zoneClient.mac_id"
          class="client-row"
          :style="{ '--row-delay': `${60 + index * 90}ms` }"
        >
          <!-- Speaker icon -->
          <div class="client-icon" :class="{ 'muted': zoneClient.equalizerMuted, 'offline': !zoneClient.online }">
            <SvgIcon :name="getSpeakerIcon(zoneClient.speakerType)" :size="24" />
          </div>

          <!-- Client name -->
          <span
            class="client-row-name heading-3"
            :class="{ 'muted': zoneClient.equalizerMuted, 'offline': !zoneClient.online }"
          >
            {{ zoneClient.name }}
          </span>

          <!-- Offline indicator (when offline) -->
          <div v-if="!zoneClient.online" class="client-offline text-mono-small">
            {{ t('multiroom.offline') }}
          </div>

          <!-- Volume not managed indicator (DAC client) -->
          <div v-else-if="zoneClient.volume_control === false" class="client-external-volume text-mono-small">
            {{ t('multiroom.externalVolume') }}
          </div>

          <!-- Client volume slider (when online) -->
          <div v-else class="client-volume">
            <RangeSlider
              :model-value="getClientDisplayVolume(zoneClient.mac_id, zoneClient.equalizerVolume)"
              :min="sliderMin"
              :max="sliderMax"
              :step="1"
              :disabled="isLoading"
              :muted="zoneClient.equalizerMuted"
              show-value
              value-unit=" dB"
              @input="(v) => handleClientVolumeInput(zoneClient.mac_id, v)"
              @change="(v) => handleClientVolumeChange(zoneClient.mac_id, v)"
            />
          </div>

          <!-- Client mute toggle (online) or offline placeholder -->
          <Toggle
            v-if="zoneClient.online"
            :model-value="!zoneClient.equalizerMuted"
            variant="secondary"
            @change="(enabled) => handleClientMuteToggle(zoneClient.mac_id, !enabled)"
          />
          <div v-else class="toggle-offline-placeholder"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, inject } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { useSettingsStore } from '@/stores/settingsStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useVolumeThrottle, useVolumeThrottleMap } from '@/composables/useVolumeThrottle';
import { useTimer } from '@/composables/useTimer';
import { useI18n } from '@/services/i18n';

const { t } = useI18n();
const timer = useTimer();
const settingsStore = useSettingsStore();
const equalizerStore = useEqualizerStore();

const props = defineProps({
  client: {
    type: Object,
    default: () => ({})
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  // Whether this item represents a zone (multiple linked clients)
  isZone: {
    type: Boolean,
    default: false
  },
  // Detailed client list for expanded view
  // [{mac_id, name, equalizerVolume, equalizerMuted, speakerType, online}]
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

// Modal height coordination (null outside a Modal). Expand and collapse both get the
// spring/bounce, via a different clip driver each:
// - expand   → requestHeightDelta: the rows appear at full height at once, the clip
//   springs to reveal them and overshoots into empty space above (the bounce).
// - collapse → springCollapse: the wrapper eases its OWN height full → 0 on the SAME
//   spring curve (masking its rows in place, items below rise), while the clip springs
//   to the collapsed height. Synced → no gap; the clip's end bounce dips into the
//   modal's bottom padding. Correct wherever the zone sits in the list.
const requestHeightDelta = inject('modalRequestHeightDelta', null);
const springCollapse = inject('modalSpringCollapse', null);

// === LOCAL STATE ===
const isExpanded = ref(false);
const localDisplayVolume = ref(null);
const clientLocalVolumes = ref({});

// Ref for measuring expanded content height
const expandedContentRef = ref(null);
// Explicit height for the wrapper: snaps to full instantly on expand (Modal clip
// springs to reveal), eases to 0 on collapse (Modal clip follows it 1:1).
const expandedWrapperHeight = ref('0px');

// Clear local volume when backend confirms the update (via WebSocket)
watch(
  () => props.client.equalizerVolume,
  (newServerVolume) => {
    // If we have a pending local value and server now matches (within 1dB tolerance)
    if (localDisplayVolume.value !== null && newServerVolume != null) {
      const diff = Math.abs(newServerVolume - localDisplayVolume.value);
      if (diff <= 1) {
        // Backend confirmed our value, clear local state
        localDisplayVolume.value = null;
      }
    }
  }
);

// === THROTTLE MANAGEMENT (unified via composable) ===
// Zone header slider: MEDIUM preset (80ms throttle, 300ms final)
const { throttledFn: throttledZoneVolume, flush: flushZoneVolume } = useVolumeThrottle(
  (volumeDb) => {
    if (!props.isLoading) {
      // Use mac_id as the unique identifier (id is undefined for all clients)
      emit('volume-change', props.client.mac_id, volumeDb, { isZone: props.isZone });
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

// DAC mode: external amplifier manages volume
const isExternalVolume = computed(() => {
  if (props.isZone) return props.client.all_external_volume === true;
  return props.client.volume_control === false;
});

// Slider configuration - always in dB, respecting volume limits
const sliderMin = computed(() => settingsStore.volumeLimits.min_db);
const sliderMax = computed(() => settingsStore.volumeLimits.max_db);

// Speaker type for standalone client (not zone)
const clientSpeakerType = computed(() => {
  if (props.isZone) return null;
  return equalizerStore.getClientSpeakerType(props.client.mac_id) || 'bookshelf';
});

// Volume is always in dB (zone average or single client)
const displayVolume = computed(() => {
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }

  // Use equalizerVolume from client (populated by parent), clamp to limits
  const volume = props.client.equalizerVolume ?? -60;
  return Math.max(sliderMin.value, Math.min(sliderMax.value, Math.round(volume)));
});

// === HELPERS ===
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
  if (!canExpand.value) return;

  if (!isExpanded.value) {
    // OPEN: the rows snap to full height at once (no wrapper transition when expanded);
    // the Modal clip springs to reveal them and overshoots slightly (the bounce).
    // Pre-announce the exact delta: full rows height added, minus the .multiroom-item
    // padding-bottom that's removed when expanded (--space-04 = 16px).
    if (expandedContentRef.value) {
      const el = expandedContentRef.value;
      const marginTop = parseFloat(getComputedStyle(el).marginTop) || 0;
      const fullHeight = el.offsetHeight + marginTop;
      requestHeightDelta?.(fullHeight - 16);
      expandedWrapperHeight.value = `${fullHeight}px`;
    }
    isExpanded.value = true;
  } else {
    // CLOSE: measure the delta before mutating, then spring the clip to the collapsed
    // height while the wrapper eases its own height full → 0 on the same curve (masking
    // its rows in place). The clip's bounce dips into the modal's bottom padding.
    if (expandedContentRef.value) {
      const el = expandedContentRef.value;
      const marginTop = parseFloat(getComputedStyle(el).marginTop) || 0;
      const fullHeight = el.offsetHeight + marginTop;
      springCollapse?.(-(fullHeight - 16));
    }
    isExpanded.value = false;
    expandedWrapperHeight.value = '0px';
  }
}

function handleVolumeInput(newDisplayVolume) {
  localDisplayVolume.value = newDisplayVolume;
  throttledZoneVolume(newDisplayVolume);
}

function handleVolumeChange(newDisplayVolume) {
  // Don't clear localDisplayVolume here - keep showing the user's chosen value
  // until the backend confirms via WebSocket (handled by watcher above)
  flushZoneVolume();
  if (!props.isLoading) {
    // Use mac_id as the unique identifier (id is undefined for all clients)
    emit('volume-change', props.client.mac_id, newDisplayVolume, { isZone: props.isZone });
  }
  // Fallback: clear local value after 2s if WebSocket didn't confirm
  timer.setTimeout(() => {
    if (localDisplayVolume.value === newDisplayVolume) {
      localDisplayVolume.value = null;
    }
  }, 2000);
}

function handleMuteToggle(enabled) {
  if (!props.isLoading) {
    const newMuted = !enabled;
    // Use mac_id as the unique identifier (id is undefined for all clients)
    emit('mute-toggle', props.client.mac_id, newMuted, { isZone: props.isZone });
  }
}

// === INDIVIDUAL CLIENT HANDLERS (expanded view) ===
function handleClientVolumeInput(clientMacId, value) {
  // Update local display volume for smooth UI
  clientLocalVolumes.value[clientMacId] = value;
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

/* Collapse eases the bottom padding back on the SAME spring curve as .expanded-wrapper
   and the Modal clip, so the whole item stays in sync; expand removes it instantly so
   the reveal is a single clip spring. */
.multiroom-item.is-zone {
  transition: padding-bottom var(--transition-spring-light);
}

/* Remove bottom padding when the zone is expanded (moved to .expanded-clients) */
.multiroom-item.is-zone.is-expanded {
  padding-bottom: 0;
  transition: none;
}

/* === ITEM HEADER (zone/client row) === */
.item-header {
  display: grid;
  grid-template-columns: 40px var(--name-width, auto) 1fr auto;
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
  opacity: 0;
  transition: opacity 450ms ease 0ms;
  pointer-events: none;
}

.icon-skeleton.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
}

/* Real icon content */
.icon-content {
  opacity: 0;
  transition: opacity 450ms ease 0ms;
}

.icon-content.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
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
  transition: opacity 450ms ease 0ms;
  pointer-events: none;
}

.client-name-skeletons.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
}

.item-name-skeleton {
  height: 20px;
  width: 64%;
  border-radius: var(--radius-full);
}

/* Real name content */
.client-name {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
  color: var(--color-text);
  overflow: hidden;
  width: 100%;
  opacity: 0;
  transition: opacity var(--transition-fast), color var(--transition-fast);
}

.client-name.visible {
  opacity: 1;
}

.client-name.muted,
.client-name.offline {
  color: var(--color-text-light);
}

.item-name {
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
  opacity: 0;
  transition: opacity 450ms ease 0ms;
  pointer-events: none;
}

.volume-skeleton.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
}

/* Real volume content */
.volume-control {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  opacity: 0;
  transition: opacity 450ms ease 0ms;
}

.volume-control.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
}

.volume-control.muted :deep(.slider-container) {
  --slider-accent: var(--color-text-light);
}

/* === TOGGLE WRAPPER === */
.toggle-wrapper {
  width: 60px;
  height: 36px;
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
  opacity: 0;
  transition: opacity 450ms ease 0ms;
  pointer-events: none;
}

.toggle-skeleton.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
}

/* Real toggle content */
.control-toggle {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 450ms ease 0ms;
}

.control-toggle.visible {
  opacity: 1;
  transition: opacity 450ms ease 0ms;
}

/* === EXPANDED CLIENTS SECTION === */
/* Asymmetric height animation, keyed off .is-expanded (the target state):
   - EXPAND → instant (transition:none below): the rows are there at once and the
     Modal clip springs to reveal them (a single native CSS spring = the bounce).
   - COLLAPSE → springs here full → 0 on the SAME curve as the Modal clip (--transition-
     spring-light), so the two stay in sync (no gap). The wrapper masks its own rows in
     place; its height clamps at 0 while the clip's bounce lands in the modal padding. */
.expanded-wrapper {
  height: 0;
  overflow: hidden;
  transition: height var(--transition-spring-light);
}

.multiroom-item.is-expanded .expanded-wrapper {
  transition: none;
}

.expanded-clients {
  display: flex;
  flex-direction: column;
  margin-top: var(--space-03);
  padding-top: var(--space-03);
  padding-bottom: var(--space-04); /* Bottom padding moved from .multiroom-item */
  border-top: 1px solid var(--color-border);
  /* Hidden by default, visible when expanded */
  opacity: 0;
  visibility: hidden;
  transition: opacity var(--transition-fast), visibility 0ms linear 200ms;
}

.expanded-clients.is-visible {
  opacity: 1;
  visibility: visible;
  transition: opacity var(--transition-fast), visibility 0ms linear 0ms;
}

/* Individual client row in expanded zone */
.client-row {
  display: grid;
  grid-template-columns: 40px var(--name-width, auto) 1fr auto;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-03) 0;
  /* Fade animation base state */
  opacity: 0;
  transition: opacity var(--transition-fast);
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
  transition: color var(--transition-fast);
}

.client-icon.muted,
.client-icon.offline {
  color: var(--color-text-light);
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
  height: 36px;
  background: var(--color-background-strong);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  padding-left: var(--space-04);
  text-transform: uppercase;
}

.client-external-volume {
  display: flex;
  align-items: center;
  height: 36px;
  background: var(--color-text-secondary);
  border-radius: var(--radius-full);
  color: var(--color-text-contrast-50);
  padding-left: var(--space-04);
}

/* In volume-wrapper context: absolute positioning for skeleton transition.
   margin-block: auto resolves the over-constrained inset+height box by centering it,
   so the pill sits exactly where the slider does inside the 40px wrapper. */
.volume-wrapper .client-offline,
.volume-wrapper .client-external-volume {
  position: absolute;
  inset: 0;
  margin-block: auto;
  opacity: 0;
  transition: opacity 450ms ease 0ms;
}

.volume-wrapper .client-offline.visible,
.volume-wrapper .client-external-volume.visible {
  opacity: 1;
}

/* === TOGGLE OFFLINE PLACEHOLDER === */
.toggle-offline-placeholder {
  width: 60px;
  height: 36px;
  background: var(--color-background-strong);
  border-radius: var(--radius-full);
}

/* Staggered fade-in animation for client rows (when parent is visible) */
/* backwards: opacity 0 during delay. CSS opacity: 1 takes over after animation for proper fade-out transition */
.expanded-clients.is-visible .client-row {
  opacity: 1;
  animation: fadeInRow var(--transition-normal) backwards;
  animation-delay: var(--row-delay, 0ms);
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

/* Custom shimmer colors for multiroom skeletons */
.icon-skeleton,
.item-name-skeleton,
.volume-skeleton,
.toggle-skeleton {
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-medium-16);
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
  .client-row .client-offline,
  .client-row .client-external-volume {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .client-offline,
  .client-external-volume {
    height: 30px;
  }

  .toggle-offline-placeholder {
    width: 50px;
    height: 30px;
  }
}
</style>
