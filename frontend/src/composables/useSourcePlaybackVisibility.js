// frontend/src/composables/useSourcePlaybackVisibility.js
// Playback state detection + player visibility for audio source components.
// Visibility tracks `source_state`: shown on 'active', hidden on 'ready'.
// Backend is the single source of truth. The optional `stoppedLingerMs` keeps
// the player visible for a while AFTER the backend reports the source stopped
// ('active' → 'ready') — a deliberate frontend-only persistence (e.g. radio
// keeps the last station on screen for auto_stop_delay). It is keyed on the
// READY transition, never on `!isPlaying`, so a pause (source stays 'active')
// never triggers it — that was the desync the old timer caused.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

/**
 * @param {string} source - Audio source identifier (e.g. 'radio', 'podcast')
 * @param {Object} [options]
 * @param {number|Function} [options.stoppedLingerMs=0] - Delay (ms) before
 *   hiding the player once the source stops (source_state 'active' → 'ready').
 *   Pass a getter to track a live setting; it is resolved at the moment the
 *   source stops. 0 hides immediately. Keyed on the READY transition only —
 *   never on a pause, which keeps source_state 'active'.
 * @param {Function} [options.onFadeOutStart] - Called when shouldShowPlayer transitions true → false
 */
export function useSourcePlaybackVisibility(source, options = {}) {
  const { stoppedLingerMs = 0, onFadeOutStart } = options;

  const unifiedStore = useUnifiedAudioStore();
  const timer = useTimer();
  const shouldShowPlayer = ref(false);
  let lingerHandle = null;

  // Resolved at stop-time so a getter tracks the current setting value.
  const resolveLingerMs = () =>
    (typeof stoppedLingerMs === 'function' ? stoppedLingerMs() : stoppedLingerMs) || 0;

  function cancelLinger() {
    if (lingerHandle !== null) {
      timer.clear(lingerHandle);
      lingerHandle = null;
    }
  }

  const isPlaying = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_playing || false;
  });

  const isBuffering = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_buffering || false;
  });

  // Show on 'active', hide on 'ready' — driven by backend transitions only.
  watch(
    () => unifiedStore.systemState.source_state,
    (newState) => {
      const isActive = unifiedStore.systemState.active_source === source;

      if (isActive && newState === 'active') {
        // Re-playing during a linger window cancels the pending hide.
        cancelLinger();
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            shouldShowPlayer.value = true;
          });
        });
      } else if (isActive && newState === 'ready' && shouldShowPlayer.value) {
        // Optional frontend persistence: keep the stopped player visible for
        // stoppedLingerMs before fading out. 0 hides immediately.
        const lingerMs = resolveLingerMs();
        if (lingerMs > 0) {
          cancelLinger();
          lingerHandle = timer.setTimeout(() => {
            lingerHandle = null;
            shouldShowPlayer.value = false;
          }, lingerMs);
        } else {
          shouldShowPlayer.value = false;
        }
      }
    },
    { immediate: true }
  );

  // Hide immediately when switching to another source
  watch(
    () => unifiedStore.systemState.active_source,
    (newSource) => {
      if (newSource !== source) {
        cancelLinger();
        shouldShowPlayer.value = false;
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

  return {
    isPlaying,
    isBuffering,
    shouldShowPlayer
  };
}
