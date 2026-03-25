// frontend/src/composables/useSourcePlaybackVisibility.js
// Playback state detection + player visibility lifecycle for audio source components.
// Handles the show/hide animation with timers, source switching, and source state transitions.
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

/**
 * @param {string} source - Audio source identifier (e.g. 'radio', 'podcast')
 * @param {Object} [options]
 * @param {number} [options.hideDelayMs=5000] - Delay before hiding the player after playback stops
 * @param {boolean} [options.hideOnReady=false] - Hide immediately when source_state becomes 'waiting'
 * @param {Function} [options.onHideTimeout] - Called when the hide timer fires (e.g. to stop backend playback)
 * @param {Function} [options.onFadeOutStart] - Called when shouldShowPlayer transitions true → false
 * @param {Function} [options.shouldStartTimer] - Custom predicate: (isPlaying, isBuffering) => boolean.
 *   Returns true when the hide timer should start. Defaults to: !isPlaying
 */
export function useSourcePlaybackVisibility(source, options = {}) {
  const {
    hideDelayMs = 5000,
    hideOnReady = false,
    onHideTimeout,
    onFadeOutStart,
    shouldStartTimer
  } = options;

  const unifiedStore = useUnifiedAudioStore();
  const shouldShowPlayer = ref(false);
  const stopTimer = ref(null);

  const isPlaying = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_playing || false;
  });

  const isBuffering = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_buffering || false;
  });

  function clearTimer() {
    if (stopTimer.value) {
      clearTimeout(stopTimer.value);
      stopTimer.value = null;
    }
  }

  // Show player when active (with smooth entrance via double rAF)
  watch(
    () => unifiedStore.systemState.source_state,
    (newState) => {
      const isActive = unifiedStore.systemState.active_source === source;

      if (isActive && newState === 'active') {
        clearTimer();
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            shouldShowPlayer.value = true;
          });
        });
      } else if (
        hideOnReady &&
        isActive &&
        newState === 'waiting' &&
        shouldShowPlayer.value
      ) {
        clearTimer();
        shouldShowPlayer.value = false;
      }
    },
    { immediate: true }
  );

  // Hide immediately when switching to another source
  watch(
    () => unifiedStore.systemState.active_source,
    (newSource) => {
      if (newSource !== source) {
        clearTimer();
        shouldShowPlayer.value = false;
      }
    },
    { immediate: true }
  );

  // Auto-hide after delay when playback stops.
  // Uses a getter so Vue tracks all reactive deps inside shouldStartTimer (e.g. store refs).
  watch(
    () =>
      shouldStartTimer
        ? shouldStartTimer(isPlaying.value, isBuffering.value)
        : !isPlaying.value,
    (shouldStart) => {
      clearTimer();

      if (shouldStart && shouldShowPlayer.value) {
        stopTimer.value = setTimeout(() => {
          shouldShowPlayer.value = false;
          onHideTimeout?.();
        }, hideDelayMs);
      }
    },
    { immediate: true }
  );

  // Notify on fade-out start (visible → hidden)
  if (onFadeOutStart) {
    watch(shouldShowPlayer, (isVisible, wasVisible) => {
      if (wasVisible && !isVisible) {
        onFadeOutStart();
      }
    });
  }

  onBeforeUnmount(() => {
    clearTimer();
  });

  return {
    isPlaying,
    isBuffering,
    shouldShowPlayer
  };
}
