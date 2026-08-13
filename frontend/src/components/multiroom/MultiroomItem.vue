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
          :style="{ '--row-delay': `${Math.round(index * rowDelayStep)}ms` }"
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

// Modal height coordination (null outside a Modal). Both directions run the SAME
// mechanism: the wrapper animates its own height 0 ↔ full while the modal clip moves by
// the same delta on the same curve, so frame and content are equal at every frame. The
// curve itself is per-direction — see COLLAPSE_TRANSITION.
const springHeightDelta = inject('modalSpringHeightDelta', null);

// === LOCAL STATE ===
const isExpanded = ref(false);
const localDisplayVolume = ref(null);
const clientLocalVolumes = ref({});

// Ref for measuring expanded content height
const expandedContentRef = ref(null);
// Explicit height for the wrapper — px both ways, since a spring needs two numbers.
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

// Per-row delay of the expand stagger. 60ms reads as a stagger where 40 did not, but
// the TOTAL is what must stay bounded: the reveal is over at ~170ms, so a zone with
// many clients compresses the step rather than pushing its last rows into an already
// open box — the exact defect a flat `index * 90ms` produced.
const rowDelayStep = computed(() => {
  const count = props.zoneClientDetails?.length || 0;
  return count > 1 ? Math.min(60, 180 / (count - 1)) : 0;
});

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
// A height that LANDS on 0 cannot render an overshooting spring: CSS clamps the negative
// lobe away, so the rows are already gone ~170ms in while the curve still has 650ms to
// run — and the modal clip, whose own height stays positive, plays that lobe for real
// (it dips ~7% of the rows' height below its target, holds, then springs back up). Hence
// a monotone curve for the collapse, written identically on the wrapper (CSS below) and
// on the clip; only the expand, where both sides can overshoot, keeps the bounce.
const COLLAPSE_TRANSITION = 'height var(--transition-medium)';
// Follow window: the 300ms collapse plus a margin. Nothing wobbles afterwards, so there
// is no reason to hold the clip away from the observer for the spring's full 900ms.
const COLLAPSE_FOLLOW_MS = 450;

function toggleExpand() {
  if (!canExpand.value || !expandedContentRef.value) return;

  // Measure BEFORE mutating: the rows sit at their natural height either way (the
  // wrapper clips them, it doesn't compress them). The delta is the rows height and
  // nothing else — the item's own box is the same expanded or not, so there is no
  // padding correction to apply here.
  const fullHeight = expandedContentRef.value.offsetHeight;
  const opening = !isExpanded.value;

  springHeightDelta?.(
    opening ? fullHeight : -fullHeight,
    opening ? {} : { transition: COLLAPSE_TRANSITION, durationMs: COLLAPSE_FOLLOW_MS }
  );
  isExpanded.value = opening;
  expandedWrapperHeight.value = opening ? `${fullHeight}px` : '0px';
}

function handleVolumeInput(newDisplayVolume) {
  localDisplayVolume.value = newDisplayVolume;
  throttledZoneVolume(newDisplayVolume);
}

function handleVolumeChange(newDisplayVolume) {
  // Don't clear localDisplayVolume here - keep showing the user's chosen value
  // until the backend confirms via WebSocket (handled by watcher above)
  // The flush is the ONLY emit on release. The parent reads a zone change as a DELTA
  // against the average captured when the drag began, and clears that capture once it
  // has applied it — so a second emit in the same tick recaptures a state the WS has
  // not corrected yet and applies the same delta twice. RangeSlider only ever emits
  // `change` after an `input` carrying the same value, so the flush has it.
  flushZoneVolume();
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
/* The bottom padding lives on the header (and on .expanded-clients), never on the item
   itself: the item's box must not change when the zone expands, or the height delta and
   the spring would have to carry a correction that silently tracks --space-04. */
.multiroom-item {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-06);
  padding: var(--space-04) var(--space-04) 0;
  background: var(--color-background-neutral);
}

/* === ITEM HEADER (zone/client row) === */
.item-header {
  display: grid;
  grid-template-columns: 40px var(--name-width, auto) 1fr auto;
  align-items: center;
  gap: var(--space-04);
  min-height: 40px;
  padding-bottom: var(--space-04);
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
/* Both directions ride the SAME curve as the Modal clip (see toggleExpand), so content
   and frame are equal at every frame: no gap, nothing cut early. overflow:hidden is what
   hides the rows at height 0.
   Expand springs — the overshoot is above the target, renderable on both sides.
   Collapse does NOT: its target IS 0, where the spring's negative lobe is clamped away
   on this wrapper but not on the clip. Kept in sync with COLLAPSE_TRANSITION. */
.expanded-wrapper {
  height: 0;
  overflow: hidden;
  transition: height var(--transition-spring-light);
}

.multiroom-item:not(.is-expanded) .expanded-wrapper {
  transition: height var(--transition-medium);
}

.expanded-clients {
  display: flex;
  flex-direction: column;
  padding-top: var(--space-03);
  padding-bottom: var(--space-04); /* The item has none — see .multiroom-item */
  border-top: 1px solid var(--color-border);
  opacity: 0;
  transition: opacity var(--transition-medium);
}

.expanded-clients.is-visible {
  opacity: 1;
}

/* Individual client row in expanded zone */
.client-row {
  display: grid;
  grid-template-columns: 40px var(--name-width, auto) 1fr auto;
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

/* Staggered fade-in for the rows, made perceptible through AMPLITUDE rather than through
   longer delays (see rowDelayStep): each row rises 8px out of the still-masked area while
   the reveal sweeps down over it. At 300ms the last row settles around 480ms, just as the
   container spring does — so the stagger reads as part of the opening, not after it.
   `backwards` holds the from-state during the delay; the fade-out is the parent's own
   opacity, so the rows need no resting state of their own. */
.expanded-clients.is-visible .client-row {
  animation: fadeInRow var(--transition-medium) backwards;
  animation-delay: var(--row-delay, 0ms);
}

@keyframes fadeInRow {
  from {
    opacity: 0;
    transform: translateY(8px);
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
