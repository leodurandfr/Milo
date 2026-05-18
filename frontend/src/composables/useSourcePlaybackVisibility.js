// frontend/src/composables/useSourcePlaybackVisibility.js
// Playback state detection + player visibility for audio source components.
// Visibility tracks `source_state` exclusively: shown on 'active', hidden on
// 'waiting'. Backend is the single source of truth — no parallel frontend
// timer that could desync with the backend's auto_disconnect_delay.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

/**
 * @param {string} source - Audio source identifier (e.g. 'radio', 'podcast')
 * @param {Object} [options]
 * @param {Function} [options.onFadeOutStart] - Called when shouldShowPlayer transitions true → false
 */
export function useSourcePlaybackVisibility(source, options = {}) {
  const { onFadeOutStart } = options;

  const unifiedStore = useUnifiedAudioStore();
  const shouldShowPlayer = ref(false);

  const isPlaying = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_playing || false;
  });

  const isBuffering = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_buffering || false;
  });

  // Show on 'active', hide on 'waiting' — driven by backend transitions only.
  watch(
    () => unifiedStore.systemState.source_state,
    (newState) => {
      const isActive = unifiedStore.systemState.active_source === source;

      if (isActive && newState === 'active') {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            shouldShowPlayer.value = true;
          });
        });
      } else if (isActive && newState === 'waiting' && shouldShowPlayer.value) {
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
