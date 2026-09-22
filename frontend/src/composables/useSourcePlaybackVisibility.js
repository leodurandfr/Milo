// frontend/src/composables/useSourcePlaybackVisibility.js
// Playback state detection + player visibility for audio source components.
//
// Visibility follows whether the source has something to show, which the
// backend now publishes: a source that stops with something to resume keeps
// its identity in the metadata (the station a press would re-tune, the episode
// and the second it stopped at, the saved queue). So the pane stays up on a
// stop and goes down on an ending — and there is nothing left to key on
// `source_state`, which answers a different question ("is a session live").
//
// It used to hide on the READY transition, which forced each source to keep a
// sticky copy of what it had just lost: displayStation in RadioSource.vue for
// `auto_stop_delay`, displayEpisode and displayTrack in their stores for the
// length of a fade. Three copies of one fact, on three lifetimes, none of them
// the published state — and none surviving a reload. The state carries it now.
//
// A pause never hides the pane, as before: a paused source still has its
// identity, so this is keyed on neither `is_playing` nor a timer.
//
// What the three copies DID legitimately buy is the leave animation: the pane
// takes time to go, and binding it straight to the live value blanks its
// artwork and title mid-fade on the endings that publish nothing — an episode
// played out, a queue finished, a source switched away. So the latch stays, in
// one place instead of three, and is released by the player's own
// `@after-hide` rather than by a 600 ms guess at how long its transition runs.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

/**
 * @param {string} source - Audio source identifier (e.g. 'radio', 'podcast')
 * @param {Object} options
 * @param {Function} options.content - Getter for what this source would draw
 *   right now, or a falsy value when it has nothing. Lives in the caller
 *   because only it knows what that is — the station, the episode, the queue
 *   entry — and each already reads its own store for the rest.
 */
export function useSourcePlaybackVisibility(source, { content }) {
  const unifiedStore = useUnifiedAudioStore();
  const shouldShowPlayer = ref(false);
  const displayed = ref(null);

  const isPlaying = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_playing || false;
  });

  const isBuffering = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return unifiedStore.systemState.metadata?.is_buffering || false;
  });

  const hasSomethingToShow = computed(() => {
    if (unifiedStore.systemState.active_source !== source) return false;
    return !!content();
  });

  // Latched: takes every new value, drops none until the pane has finished
  // leaving. `onAfterHide` is what empties it.
  watch(
    () => content(),
    (value) => {
      if (value) displayed.value = value;
    },
    { immediate: true }
  );

  watch(
    hasSomethingToShow,
    (present) => {
      if (!present) {
        shouldShowPlayer.value = false;
        return;
      }
      // Two frames so the enter transition has a painted "before" to run from;
      // a single one still lands inside the same paint on this panel.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          shouldShowPlayer.value = hasSomethingToShow.value;
        });
      });
    },
    { immediate: true }
  );

  /** Wire to AudioPlayer's `@after-hide`. Guarded because a stop→replay inside
   *  the leave re-shows the pane, and clearing then would blank a live one. */
  function onAfterHide() {
    if (!shouldShowPlayer.value) displayed.value = null;
  }

  return {
    isPlaying,
    isBuffering,
    shouldShowPlayer,
    displayed,
    onAfterHide
  };
}
