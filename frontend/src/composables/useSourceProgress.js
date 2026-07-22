// frontend/src/composables/useSourceProgress.js
// Playback progress tracking composable with local interpolation and seek
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

export function useSourceProgress(source, { compensateStaleness = false } = {}) {
  const unifiedStore = useUnifiedAudioStore();
  const timer = useTimer();

  // Seed value for localPosition. Normally the raw broadcast position; when
  // compensateStaleness is set (e.g. the Lyrics modal, which mounts mid-song and
  // needs tight line-level sync), advance it by how long ago that value was
  // received so a new instance isn't behind by the source's broadcast interval.
  // Live updates set the store timestamp in the same tick → staleness ≈ 0, so
  // steady-state behaviour is unchanged.
  function seedFrom(position) {
    if (!compensateStaleness) return position;
    const meta = unifiedStore.systemState.metadata || {};
    const ts = unifiedStore.positionTimestamp;
    if (!ts || !meta.is_playing || meta.is_buffering) return position;
    const seeded = position + Math.max(0, performance.now() - ts);
    const dur = meta.duration || 0;
    return dur > 0 ? Math.min(seeded, dur) : seeded;
  }

  const localPosition = ref(null);
  let intervalId = null;
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
  // stays 0 but duration changes) still reset localPosition correctly.
  watch(
    [
      () => unifiedStore.systemState.metadata?.position,
      () => unifiedStore.systemState.metadata?.duration,
    ],
    ([newPosition]) => {
      if (newPosition !== undefined && !isApiSyncing) {
        localPosition.value = seedFrom(newPosition);
      }
    },
    { immediate: true }
  );

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
      intervalId = timer.setInterval(() => {
        const meta = unifiedStore.systemState.metadata;
        if (localPosition.value !== null && meta?.is_playing && !meta?.is_buffering && localPosition.value < duration.value) {
          // Scale interpolation by mpv playback_speed so the bar tracks real
          // playback at 1.5x/2x. Defaults to 1 for sources without speed
          // control (Spotify, AirPlay). Read on every tick so a speed change
          // is reflected immediately via unifiedStore.systemState.metadata.
          const speed = meta?.playback_speed || 1;
          localPosition.value += 100 * speed;
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
