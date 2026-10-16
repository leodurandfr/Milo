// frontend/src/composables/useSourceProgress.js
// The playhead of one source, from its position anchor, and seeking it.
//
// The one interpolation every client uses (docs: "le fil", §2):
//   position_now = ms + (phase == "playing" ? (now − at) × 1000 × rate : 0),
//   bounded to [0, duration_ms].
// The backend republishes the anchor only on a discontinuity (a seek, a drift
// past 2 s), so between two anchors the bar is this formula and nothing else —
// no local counter to drift, no staleness to compensate: a
// component mounting mid-track (the Lyrics view) reads the same number as one
// that has been open for an hour.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';
import { anyModalOpen } from '@/composables/useModalPresence';
import { remoteRecordOf } from '@/utils/nowPlayingMetadata';

// How often the playhead is read while it moves, at most and at least. Between
// the two, a bar that told its width is redrawn when its fill gains a device
// pixel, and every reader when the elapsed time turns a second: a redraw in
// between changes nothing on screen, and on the kiosk each one re-blurs the
// glass the player sits on — the playing card's 10 Hz kept the Pi's GPU at
// 22 % over a still screen.
const TICK_MS = 100;
const MAX_TICK_MS = 1000;

// How long a target is held after the last press when no anchor agreeing with
// it arrives (a source that publishes nothing keeps its own).
const SEEK_HOLD_MS = 3000;

// How close an anchor must land to the target to take the bar over from it:
// the backend's own tolerance for moving an anchor (audio_source.py
// POSITION_TOLERANCE_MS, held equal by the test).
export const SEEK_AGREEMENT_MS = 2000;

/**
 * Where an anchor puts the playhead at `nowMs` (epoch milliseconds). Pure, and
 * exported for the one other reader of a playhead (podcastStore, for the
 * episode cards).
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
  // Without either, what another device of the account plays: its playhead
  // moves there, by the same formula.
  const remote = computed(() =>
    unifiedStore.systemState.source === source && !session.value && !resume.value
      ? remoteRecordOf(unifiedStore.systemState.details)
      : null,
  );
  const live = computed(() => session.value ?? remote.value);

  const now = ref(Date.now());
  let tickId = null;
  // The width of the bar this playhead is drawn on, in device pixels (0: none
  // told, every tick is TICK_MS — the lyrics follow the playhead finer than a bar).
  const trackWidthPx = ref(0);
  // Where the last press put the playhead, as an anchor of its own: it moves
  // with the session's phase and is re-stamped when the phase changes, exactly
  // like the backend's (`_rebase_anchor`).
  const seekTarget = ref(null);
  let seekTimer = null;
  let presses = 0;

  const duration = computed(() => live.value?.duration_ms ?? resume.value?.duration_ms ?? 0);

  function anchoredAt(nowMs) {
    if (live.value) {
      return positionAt(live.value.position, live.value.phase, live.value.duration_ms, nowMs);
    }
    return resume.value?.position_ms ?? null;
  }

  function targetAt(nowMs) {
    if (!seekTarget.value) return null;
    return positionAt(seekTarget.value, session.value?.phase, duration.value || null, nowMs);
  }

  const anchoredPosition = computed(() => anchoredAt(now.value));

  const currentPosition = computed(() => targetAt(now.value) ?? anchoredPosition.value ?? 0);
  const isPositionInitialized = computed(() => seekTarget.value !== null || anchoredPosition.value !== null);
  const progressPercentage = computed(() => {
    if (!duration.value) return 0;
    return (currentPosition.value / duration.value) * 100;
  });

  function nextTickMs() {
    const pixelMs = trackWidthPx.value && duration.value ? duration.value / trackWidthPx.value : TICK_MS;
    const step = Math.min(MAX_TICK_MS, Math.max(TICK_MS, pixelMs));
    if (step === TICK_MS) return TICK_MS;
    const rate = live.value?.position?.rate || 1;
    const toNextSecond = (1000 - (currentPosition.value % 1000)) / rate;
    return Math.max(1, Math.min(step, Math.ceil(toNextSecond) + 1));
  }

  function scheduleTick() {
    tickId = timer.setTimeout(() => {
      now.value = Date.now();
      scheduleTick();
    }, nextTickMs());
  }

  // Redraw while the playhead moves, and only then. Not under a modal either:
  // its overlay blurs the page beneath, where the bar's motion cannot be seen
  // but each redraw re-blurs the whole screen; the formula puts the bar back
  // where it belongs the moment the modal closes.
  const moving = computed(() => live.value?.phase === 'playing' && !!live.value?.position && !anyModalOpen.value);

  // Read the clock afresh whenever what the formula reads changes — even with
  // no tick running, or an anchor taken under a modal would be extrapolated
  // from the instant the modal opened — and plan the next tick from there,
  // since a seek, a new track or a new width moves the next pixel and second.
  watch(
    [moving, () => live.value?.position, () => live.value?.phase, seekTarget, duration, trackWidthPx],
    () => {
      stopTicking();
      now.value = Date.now();
      if (moving.value) scheduleTick();
    },
    { immediate: true },
  );

  // An anchor takes the bar back from a target only if it agrees with it: in a
  // burst of presses, the anchor the first one caused arrives after the second
  // one moved the target on, and landing on it would pull the bar back to a
  // point already skipped past. Watched by value: every `source/state` hands
  // over fresh objects, and a state that did not move the anchor (a phase
  // flip, a favorite) must not be read as one. Another session, or none, is
  // not the one the target was aimed at.
  watch(() => session.value?.id ?? null, () => clearSeek());
  // A track change inside the session (next, a skip past the end) is not
  // where the target was aimed either.
  watch(() => `${session.value?.title}|${session.value?.duration_ms}`, () => clearSeek());
  watch(() => session.value?.phase, (_phase, before) => {
    if (!seekTarget.value) return;
    const nowMs = Date.now();
    seekTarget.value = {
      ...seekTarget.value,
      ms: positionAt(seekTarget.value, before, duration.value || null, nowMs),
      at: nowMs / 1000,
    };
  });
  watch(() => {
    if (!session.value) return resume.value ? `resume:${resume.value.position_ms}` : null;
    const anchor = session.value.position;
    return anchor ? `${anchor.ms}:${anchor.at}:${anchor.rate}` : null;
  }, (anchor) => {
    if (!seekTarget.value || anchor === null) return;
    const nowMs = Date.now();
    if (Math.abs(anchoredAt(nowMs) - targetAt(nowMs)) <= SEEK_AGREEMENT_MS) clearSeek();
  });

  function stopTicking() {
    if (tickId) {
      timer.clear(tickId);
      tickId = null;
    }
  }

  function setTrackWidth(px) {
    trackWidthPx.value = px;
  }

  function clearSeek() {
    seekTarget.value = null;
    if (seekTimer) {
      timer.clear(seekTimer);
      seekTimer = null;
    }
  }

  // Show `ms` at once and hold it until an anchor agrees with it, or until
  // SEEK_HOLD_MS after the last press. Answers the press's number.
  function hold(ms) {
    if (seekTimer) timer.clear(seekTimer);
    seekTarget.value = { ms, at: Date.now() / 1000, rate: session.value?.position?.rate ?? 1 };
    seekTimer = timer.setTimeout(clearSeek, SEEK_HOLD_MS);
    return ++presses;
  }

  // A refused command lets its target go — unless a later press has
  // replaced it, which is still in flight.
  async function send(command, data, press) {
    if (!await unifiedStore.sendCommand(source, command, data) && press === presses) clearSeek();
  }

  async function seekTo(position) {
    await send('seek', { position_ms: position }, hold(position));
  }

  // A relative move (−15 / +30). The source adds it to where its playhead is,
  // so presses in a burst add up there; here they add up on the target.
  async function skip(seconds) {
    let ms = Math.max(0, currentPosition.value + seconds * 1000);
    if (duration.value) ms = Math.min(ms, duration.value);
    await send('skip', { seconds }, hold(ms));
  }

  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo,
    skip,
    isPositionInitialized,
    setTrackWidth,
  };
}
