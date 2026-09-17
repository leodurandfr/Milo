// frontend/src/composables/useSourceProgress.js
// Playback progress tracking composable with local interpolation and seek
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

// Below this, a broadcast position disagreeing with our own clock is the source
// correcting drift, not the playhead moving. See isMinorCorrection.
const CORRECTION_TOLERANCE_MS = 1200;

export function useSourceProgress(source, { exactCorrections = false } = {}) {
  const unifiedStore = useUnifiedAudioStore();
  const timer = useTimer();

  // Seed value for localPosition: the broadcast position advanced by how long
  // ago it was received. A broadcast is only true as of its own arrival, so an
  // instance mounting mid-song — the Lyrics modal, and the player remounted
  // under it when the modal closes — must not restart its clock from an anchor
  // that has since aged. Measured: with the Lyrics view open 30 s over Spotify,
  // closing it brought the player's bar back at 1:02 against a room at 1:32,
  // and nothing corrected it afterwards (Spotify and Qobuz broadcast no
  // periodic position at all; the mpv sources only every 30 s). Live updates
  // set the store timestamp in the same tick → staleness ≈ 0, so steady-state
  // behaviour is unchanged.
  function seedFrom(position) {
    const meta = unifiedStore.systemState.metadata || {};
    const ts = unifiedStore.positionTimestamp;
    if (!ts || !meta.is_playing || meta.is_buffering) return position;
    // Scaled by playback_speed for the same reason the tick loop is: the
    // anchor is media time, and a podcast at 2x advances two seconds of it per
    // second of wall clock. Seeding on raw wall clock would hand a remounted
    // bar the age of the anchor at 1x whatever the speed.
    const speed = meta.playback_speed || 1;
    const seeded = position + Math.max(0, performance.now() - ts) * speed;
    const dur = meta.duration || 0;
    return dur > 0 ? Math.min(seeded, dur) : seeded;
  }

  const localPosition = ref(null);
  let intervalId = null;
  // Clock reading the last interpolation step was taken at. The bar advances by
  // elapsed wall clock, never by a fixed step per tick: a backgrounded tab has
  // its interval throttled to ~1 Hz by the browser, and counting ticks made the
  // bar crawl ten times too slowly with nothing to correct it until the next
  // broadcast — 10 to 30 s depending on the source, never on Spotify between
  // two events.
  let lastTickAt = 0;
  let isApiSyncing = false;

  const duration = computed(() => unifiedStore.systemState.metadata?.duration || 0);
  const currentPosition = computed(() => localPosition.value ?? 0);
  const isPositionInitialized = computed(() => localPosition.value !== null);
  const progressPercentage = computed(() => {
    if (!duration.value || duration.value === 0) return 0;
    return (currentPosition.value / duration.value) * 100;
  });

  // Synchronization with store metadata
  // Watch both position AND duration so that track changes (where position
  // stays 0 but duration changes) still reset localPosition correctly — and the
  // store's anchor timestamp, so an anchor that repeats the stored number lands
  // too. A Previous restarting the current track answers position 0, and the
  // last anchor is already 0 for the whole 30 s the source waits before its next
  // periodic sync (the one a track start left behind): watching the value alone,
  // nothing fired and the bar carried on from wherever our own clock had reached.
  watch(
    [
      () => unifiedStore.systemState.metadata?.position,
      () => unifiedStore.systemState.metadata?.duration,
      () => unifiedStore.positionTimestamp,
    ],
    ([newPosition, newDuration], previous) => {
      if (newPosition === undefined || isApiSyncing) return;

      if (isMinorCorrection(newPosition, newDuration, previous?.[1])) return;
      localPosition.value = seedFrom(newPosition);
    },
    { immediate: true }
  );

  // Whether a broadcast position is close enough to the clock we are already
  // running that adopting it would only make the display stutter.
  //
  // Sources correct an absolute position periodically while this composable
  // interpolates in real time, so the two drift by a fraction of a second and
  // every correction drags the seconds digit backwards. Bluetooth is the loud
  // case: BlueZ's playhead advances slower than real time for the first seconds
  // of a track (measured over the WebSocket, 815 ms then 915 ms with 780 ms of
  // wall clock between them), so the digit flickered 0:00/0:01 on every skip.
  //
  // Only ever skips a correction *while playing and on the same track* — a
  // paused source must land exactly, and a track change reseeds through the
  // duration check. Everything that genuinely moves a playhead (a seek, a 15 s
  // skip, a restart) clears the threshold by an order of magnitude. The Lyrics
  // view opts out via exactCorrections: it syncs lines, and there a fraction of
  // a second is the whole point.
  function isMinorCorrection(newPosition, newDuration, oldDuration) {
    if (exactCorrections || localPosition.value === null) return false;
    if (newDuration !== oldDuration) return false;
    if (!unifiedStore.systemState.metadata?.is_playing) return false;
    return Math.abs(newPosition - localPosition.value) < CORRECTION_TOLERANCE_MS;
  }

  // Local animation while playing. Gated on the active source: systemState
  // metadata belongs to the active source, so an instance created for another
  // source (e.g. the screensaver's podcast tracker while Spotify plays) must
  // not tick — it would interpolate someone else's position.
  watch(
    [
      () => unifiedStore.systemState.active_source === source,
      () => unifiedStore.systemState.metadata?.is_playing,
    ],
    ([isActiveSource, isPlaying]) => {
      stopProgressTimer();
      if (isActiveSource && isPlaying) {
        startProgressTimer();
      }
    },
    { immediate: true }
  );

  function startProgressTimer() {
    if (!intervalId) {
      lastTickAt = performance.now();
      intervalId = timer.setInterval(() => {
        const now = performance.now();
        const elapsed = now - lastTickAt;
        // Re-anchored on every tick, including the ones that advance nothing:
        // time spent buffering is not media time, and crediting it back on
        // resume is the failure mode a clock reading brings with it.
        lastTickAt = now;

        const meta = unifiedStore.systemState.metadata;
        if (localPosition.value !== null && meta?.is_playing && !meta?.is_buffering && localPosition.value < duration.value) {
          // Scale interpolation by mpv playback_speed so the bar tracks real
          // playback at 1.5x/2x. Defaults to 1 for sources without speed
          // control (Spotify, AirPlay). Read on every tick so a speed change
          // is reflected immediately via unifiedStore.systemState.metadata.
          const speed = meta?.playback_speed || 1;
          localPosition.value = Math.min(localPosition.value + elapsed * speed, duration.value);
        }
      }, 100);
    }
  }

  function stopProgressTimer() {
    if (intervalId) {
      timer.clear(intervalId);
      intervalId = null;
    }
  }

  async function seekTo(position) {
    isApiSyncing = true;
    localPosition.value = position;

    try {
      await unifiedStore.sendCommand(source, 'seek', { position_ms: position });
    } finally {
      // Small delay to let WebSocket event arrive first
      await new Promise(resolve => timer.setTimeout(resolve, 50));
      isApiSyncing = false;
    }
  }

  return {
    currentPosition,
    duration,
    progressPercentage,
    seekTo,
    isPositionInitialized
  };
}
