// frontend/src/composables/useSourceProgress.js
// The playhead of one source, from its position anchor, and seeking it.
//
// The one interpolation every client uses (docs: "le fil", §2):
//   position_now = ms + (phase == "playing" ? (now − at) × 1000 × rate : 0),
//   bounded to [0, duration_ms].
// The backend republishes the anchor only on a discontinuity (a seek, a speed
// change, a drift past 2 s), so between two anchors the bar is this formula and
// nothing else — no local counter to drift, no staleness to compensate: a
// component mounting mid-track (the Lyrics view) reads the same number as one
// that has been open for an hour.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

// How often the bar is redrawn while the playhead moves.
const TICK_MS = 100;

// How long a seek's target is shown before the anchor it caused is awaited no
// longer (the command answered; a source that publishes nothing keeps its own).
const SEEK_HOLD_MS = 1000;

/**
 * Where an anchor puts the playhead at `nowMs` (epoch milliseconds). Pure, and
 * exported for the one other reader of a playhead (the lyrics sync).
 */
export function positionAt(anchor, phase, durationMs, nowMs) {
  if (!anchor) return null;
  let ms = anchor.ms;
  if (phase === 'playing') ms += (nowMs - anchor.at * 1000) * anchor.rate;
  if (durationMs != null) ms = Math.min(ms, durationMs);
  return Math.max(0, ms);
}

export function useSourceProgress(source) {
  const unifiedStore = useUnifiedAudioStore();
  const timer = useTimer();

  // This source's session, or null: another source's anchor is not ours to draw.
  const session = computed(() =>
    unifiedStore.systemState.source === source ? unifiedStore.systemState.session : null,
  );
  // Without a session, the resume point is what the bar shows (where play resumes).
  const resume = computed(() =>
    unifiedStore.systemState.source === source && !session.value ? unifiedStore.systemState.resume : null,
  );

  const now = ref(Date.now());
  let intervalId = null;
  const seekTarget = ref(null);
  let seekTimer = null;

  const duration = computed(() => session.value?.duration_ms ?? resume.value?.duration_ms ?? 0);

  const anchoredPosition = computed(() => {
    if (session.value) {
      return positionAt(session.value.position, session.value.phase, session.value.duration_ms, now.value);
    }
    return resume.value?.position_ms ?? null;
  });

  const currentPosition = computed(() => seekTarget.value ?? anchoredPosition.value ?? 0);
  const isPositionInitialized = computed(() => seekTarget.value !== null || anchoredPosition.value !== null);
  const progressPercentage = computed(() => {
    if (!duration.value) return 0;
    return (currentPosition.value / duration.value) * 100;
  });

  // Redraw while the playhead moves, and only then.
  watch(
    () => session.value?.phase === 'playing' && !!session.value?.position,
    (moving) => {
      stopTicking();
      now.value = Date.now();
      if (moving) {
        intervalId = timer.setInterval(() => { now.value = Date.now(); }, TICK_MS);
      }
    },
    { immediate: true },
  );

  // The anchor a seek causes replaces its target at once. Watched by value:
  // every `source/state` hands over fresh objects, and a state that did not
  // move the anchor (a phase flip, a favorite) must not snap the bar back.
  watch(() => {
    const anchor = session.value?.position;
    return anchor ? `${anchor.ms}:${anchor.at}:${anchor.rate}` : null;
  }, () => clearSeek());

  function stopTicking() {
    if (intervalId) {
      timer.clear(intervalId);
      intervalId = null;
    }
  }

  function clearSeek() {
    seekTarget.value = null;
    if (seekTimer) {
      timer.clear(seekTimer);
      seekTimer = null;
    }
  }

  async function seekTo(position) {
    clearSeek();
    seekTarget.value = position;
    await unifiedStore.sendCommand(source, 'seek', { position_ms: position });
    seekTimer = timer.setTimeout(clearSeek, SEEK_HOLD_MS);
  }

  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo,
    isPositionInitialized,
  };
}
