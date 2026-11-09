<!-- frontend/src/components/volume/VolumeOverlay.vue -->
<template>
  <!-- `modal-overlay` is load-bearing: the dock refuses to reveal, and ignores
       swipes, over any element carrying it (Dock.vue showDock, useDockDrag). -->
  <Transition name="volume-overlay" :duration="{ enter: 500, leave: 250 }">
    <div v-if="unifiedStore.volumePanelOpen" class="modal-overlay volume-overlay"
      @pointerdown.capture="onOverlayPointerdown" @click="backdrop.onClick">
      <!-- The VolumeBar itself, larger and held: same plate, same track and
           fill, the level in mono on the fill — flanked by − / + as thick as
           the plate. -->
      <div class="volume-panel" :class="`volume-panel--${variant}`">
        <button v-press class="volume-step glass-shell button-interactive-subtle"
          :aria-label="t('volumePanel.decrease')"
          @pointerdown="(e) => volumeHold.onVolumeHoldStart(stepDb(-1), e)"
          @pointerup="volumeHold.onVolumeHoldEnd" @pointercancel="volumeHold.onVolumeHoldEnd"
          @pointerleave="volumeHold.cancel" @click="onStepKeyboardClick($event, -1)">
          <SvgIcon name="minus" :size="48" />
        </button>

        <div class="volume-plate glass-shell">
          <div ref="track" class="volume-track" :class="{ 'volume-track--held': dragging }"
            role="slider" tabindex="0" :aria-label="t('settings.volume')"
            aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Math.round(level * 100)"
            :aria-valuetext="readout"
            @pointerdown="onTrackPointerdown" @pointermove="onTrackPointermove"
            @pointerup="onTrackPointerup" @pointercancel="onTrackPointerup" @keydown="onTrackKeydown">
            <div class="volume-fill" :style="fillStyle"></div>
            <div class="volume-readout text-mono-display">{{ readout }}</div>
          </div>
        </div>

        <button v-press class="volume-step glass-shell button-interactive-subtle"
          :aria-label="t('volumePanel.increase')"
          @pointerdown="(e) => volumeHold.onVolumeHoldStart(stepDb(1), e)"
          @pointerup="volumeHold.onVolumeHoldEnd" @pointercancel="volumeHold.onVolumeHoldEnd"
          @pointerleave="volumeHold.cancel" @click="onStepKeyboardClick($event, 1)">
          <SvgIcon name="plus" :size="48" />
        </button>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useModalPresence } from '@/composables/useModalPresence';
import { useBackdropDismiss } from '@/composables/useBackdropDismiss';
import { useVolumeHold } from '@/composables/useVolumeHold';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import SvgIcon from '@/components/ui/SvgIcon.vue';

// Nothing here is read for long, unlike a modal's 120 s: a panel left open on
// the kiosk also holds back the auto-player (useAutoPlayer).
const INACTIVITY_CLOSE_MS = 30000;

// How long a released level is held on screen waiting for its echo. Past it the
// server's own level is shown again: an echo can be skipped (a state identical
// to the last one published is not re-sent) or land elsewhere (rooms clamp).
const ECHO_HOLD_MS = 2000;
// One step of a 0..1 level, rounded server-side to 4 decimals.
const ECHO_TOLERANCE = 0.01;

const { t, formatUnit } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const settingsStore = useSettingsStore();
const timer = useTimer();
const presence = useModalPresence();

defineProps({
  // VolumeBar's own prop, with the same meaning: the tone of the ground drawn
  // over. App.vue gives both the same answer, so the panel and the bar it grew
  // out of never disagree.
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'on-contrast'].includes(v)
  }
});

const track = ref(null);
const dragging = ref(false);
const localLevel = ref(0);
const pendingLevel = ref(null);

// The one pointer a drag follows: a second finger on the track would otherwise
// rebase the drag on its own position and jump the house to either end.
let dragPointerId = null;
let dragStartX = 0;
let dragStartLevel = 0;
let echoTimer = null;
let inactivityTimer = null;

function close() {
  unifiedStore.closeVolumePanel();
}

const backdrop = useBackdropDismiss(close);

// The level drawn: the finger's while it is down, then the released one until
// the server answers, then the server's — so a rotary turned while the panel is
// open moves the fill too.
const level = computed(() => {
  if (dragging.value) return localLevel.value;
  if (pendingLevel.value !== null) return pendingLevel.value;
  return unifiedStore.volumeState.global_volume;
});

// The server's figure whenever it has one for what is drawn; while a level of
// ours is in flight, the same denormalization the route applies, on the limits
// this screen holds (kept current by settings/volume_limits_changed).
const readout = computed(() => {
  let db = unifiedStore.volumeState.global_volume_db;
  if (dragging.value || pendingLevel.value !== null) {
    const { min_db, max_db } = settingsStore.volumeLimits;
    db = min_db + level.value * (max_db - min_db);
  }
  // `|| 0`: a level just under 0 rounds to -0, which Intl prints as "-0 dB".
  return formatUnit(Math.round(db) || 0, 'dB');
});

// VolumeBar's own fill: a full-width bar slid in from the left, so its rounded
// end is the level's edge.
const fillStyle = computed(() => ({ transform: `translateX(${level.value * 100 - 100}%)` }));

function clamp(value) {
  return Math.max(0, Math.min(1, value));
}

function clearPending() {
  timer.clear(echoTimer);
  echoTimer = null;
  pendingLevel.value = null;
}

watch(() => unifiedStore.volumeState.global_volume, (serverLevel) => {
  if (pendingLevel.value !== null && Math.abs(serverLevel - pendingLevel.value) < ECHO_TOLERANCE) {
    clearPending();
  }
});

// Relative, as a phone's control centre does: the level moves from where it
// stands by the distance the finger travels, so a touch alone never jumps the
// house to wherever it landed.
function onTrackPointerdown(event) {
  if (dragging.value || !event.isPrimary) return;
  if (event.button !== undefined && event.button !== 0) return;
  // Read before the hold is dropped: a level released a moment ago and not yet
  // echoed is where the fill stands, and the server's is the one it left.
  const start = level.value;
  clearPending();
  dragPointerId = event.pointerId;
  track.value.setPointerCapture?.(event.pointerId);
  dragStartX = event.clientX;
  dragStartLevel = start;
  localLevel.value = start;
  dragging.value = true;
}

function onTrackPointermove(event) {
  if (!dragging.value || event.pointerId !== dragPointerId) return;
  armInactivityClose();
  // Measured in the same client pixels as the pointer, so the kiosk's page
  // zoom cancels out.
  const width = track.value.getBoundingClientRect().width || 1;
  const next = clamp(dragStartLevel + (event.clientX - dragStartX) / width);
  if (next === localLevel.value) return;
  localLevel.value = next;
  unifiedStore.setGlobalVolume(next);
}

function onTrackPointerup(event) {
  if (!dragging.value || event.pointerId !== dragPointerId) return;
  dragPointerId = null;
  dragging.value = false;
  if (localLevel.value === dragStartLevel) return;
  pendingLevel.value = localLevel.value;
  timer.clear(echoTimer);
  echoTimer = timer.setTimeout(clearPending, ECHO_HOLD_MS);
}

function stepDb(sign) {
  return sign * settingsStore.volumeSteps.step_interface_db;
}

function adjust(delta) {
  clearPending();
  armInactivityClose();
  unifiedStore.adjustVolume(delta);
}

function step(sign) {
  adjust(stepDb(sign));
}

// The dock's − / +, held to repeat: a tap is one step on release, a hold one
// step at 400 ms then one every 50 ms (useVolumeHold). Nothing here drags, so
// the gesture never counts as moved; sliding off the button abandons it, with no
// step for a press that never became a hold.
const gestureHasMoved = ref(false);
const gestureStartPosition = ref({ x: 0, y: 0 });
const volumeHold = useVolumeHold({
  adjustVolume: adjust,
  gestureHasMoved,
  gestureStartPosition,
  getEventX: (event) => event.clientX,
  getEventY: (event) => event.clientY,
});

// Pointer presses are the hold's; a click with no pointer behind it (detail 0:
// Enter or Space on the focused button) is the keyboard's one step.
function onStepKeyboardClick(event, sign) {
  if (event.detail === 0) step(sign);
}

function onTrackKeydown(event) {
  const sign = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[event.key];
  if (!sign) return;
  event.preventDefault();
  step(sign);
}

// Any key counts as use, as a touch does: arrows on the track, Tab, the buttons.
function onDocumentKeydown(event) {
  if (event.key === 'Escape') {
    close();
    return;
  }
  armInactivityClose();
}

function armInactivityClose() {
  timer.clear(inactivityTimer);
  // A finger resting on the track is still in use, moving or not.
  inactivityTimer = timer.setTimeout(() => (dragging.value ? armInactivityClose() : close()),
    INACTIVITY_CLOSE_MS);
}

function onOverlayPointerdown(event) {
  backdrop.onPointerdown(event);
  armInactivityClose();
}

watch(() => unifiedStore.volumePanelOpen, (open) => {
  presence.setOpen(open);
  // Each opening starts clean, whatever the last one was left holding.
  volumeHold.cancel();
  dragPointerId = null;
  dragging.value = false;
  clearPending();
  if (open) {
    document.addEventListener('keydown', onDocumentKeydown);
    armInactivityClose();
  } else {
    document.removeEventListener('keydown', onDocumentKeydown);
    timer.clear(inactivityTimer);
  }
}, { immediate: true });

// The tile goes with the last device that manages a volume; so does the panel.
watch(() => unifiedStore.volumeState.any_volume_control, (any) => {
  if (!any) close();
});

onUnmounted(() => {
  document.removeEventListener('keydown', onDocumentKeydown);
  if (unifiedStore.volumePanelOpen) close();
});
</script>

<style scoped>
/* The veil is Modal.vue's: the same tone and blur, opacity-only so the blurred
   layer never carries a transform (the iOS compositor shimmers on one). */
.volume-overlay {
  position: fixed;
  inset: 0;
  z-index: 5000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-overlay);
  backdrop-filter: blur(var(--blur-04));
  -webkit-backdrop-filter: blur(var(--blur-04));
  --glass-backdrop: none;
}

/* One measure for the three: the buttons are as thick as the plate around the
   track, so the row reads as one object. */
.volume-panel {
  --volume-track-height: 72px;
  --volume-plate-padding: var(--space-04);
  --volume-step-size: calc(var(--volume-track-height) + 2 * var(--volume-plate-padding));
  display: flex;
  align-items: center;
  gap: var(--space-05);
}

/* VolumeBar's variants, same tones: the fill takes the far end of the ramp and
   the readout the muted tone of whichever end it sits on (VolumeBar.vue says
   why each was picked). */
.volume-panel--default {
  --volume-track: var(--color-glass-strong);
  --volume-fill: var(--color-fill);
  --volume-text: var(--color-text-tertiary);
}

.volume-panel--on-contrast {
  --volume-track: var(--color-glass);
  --volume-fill: var(--color-text-on-contrast);
  --volume-text: var(--color-text-secondary);
  color-scheme: dark;
}

.volume-panel--on-contrast .volume-readout {
  color-scheme: light;
}

.volume-plate {
  width: min(640px, calc(100vw - 2 * var(--volume-step-size) - 2 * var(--space-05) - 2 * var(--space-07)));
  padding: var(--volume-plate-padding);
  border-radius: var(--radius-full);
}

.volume-track {
  position: relative;
  height: var(--volume-track-height);
  border-radius: var(--radius-full);
  background: var(--volume-track);
  overflow: hidden;
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  outline: none;
}

.volume-track:focus-visible {
  box-shadow: 0 0 0 2px var(--volume-fill);
}

.volume-track--held {
  cursor: grabbing;
}

.volume-fill {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  background: var(--volume-fill);
  /* Smooths the steps between two echoes; a held finger is followed exactly. */
  transition: transform var(--transition-fast);
}

.volume-track--held .volume-fill {
  transition: none;
}

.volume-readout {
  position: absolute;
  top: 0;
  bottom: 0;
  left: var(--space-05);
  display: flex;
  align-items: center;
  color: var(--volume-text);
  pointer-events: none;
}

.volume-step {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: var(--volume-step-size);
  height: var(--volume-step-size);
  border: none;
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  cursor: pointer;
}

/* Enter: the veil fades in; the panel rises from a slight shrink, the way a
   modal's shell does, on an element that carries no blur. */
.volume-overlay-enter-active {
  transition: opacity 350ms var(--easeOutCubic);
}

/* Inert while it fades: a press on the leaving track would start a drag whose
   release never arrives. */
.volume-overlay-leave-active {
  transition: opacity 250ms var(--easeOutCubic);
  pointer-events: none;
}

.volume-overlay-enter-from,
.volume-overlay-leave-to {
  opacity: 0;
}

.volume-overlay-enter-active .volume-panel {
  transition: transform var(--transition-spring-snappy), opacity 300ms var(--easeOutCubic);
  transition-delay: 80ms;
}

.volume-overlay-leave-active .volume-panel {
  transition: transform 180ms var(--easeOutCubic), opacity 180ms var(--easeOutCubic);
}

.volume-overlay-enter-from .volume-panel {
  opacity: 0;
  transform: scale(0.95);
}

.volume-overlay-leave-to .volume-panel {
  opacity: 0;
  transform: scale(0.98);
}

/* The tile is not offered on a phone, which has − / + in its dock already; a
   window narrowed past the breakpoint while the panel is open still gets a
   layout that fits: the plate on a row of its own, the buttons under it. */
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .volume-panel {
    --volume-track-height: 64px;
    --volume-plate-padding: var(--space-03);
    display: grid;
    grid-template-columns: auto auto;
    justify-content: center;
    column-gap: var(--space-03);
    row-gap: var(--space-05);
  }

  .volume-plate {
    grid-column: 1 / -1;
    grid-row: 1;
    width: calc(100vw - 2 * var(--space-04));
  }
}
</style>
