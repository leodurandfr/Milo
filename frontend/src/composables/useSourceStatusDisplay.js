// frontend/src/composables/useSourceStatusDisplay.js
// The single owner of the vocabulary AudioSourceStatus renders.
//
// The card does not read `source_state`: it reads a *display* state, which is
// the backend enum plus the three screens CD reaches through metadata alone and
// no backend member describes (no drive, reading the disc, ejecting). Both
// vocabularies used to arrive through the same string prop, derived in
// AudioSourceView and re-narrated in the gallery — two places deciding one
// thing. They are declared here instead, so the card's validator, the gallery's
// select and the app all read the same list.
//
// ERROR is not derived: since a failed transition writes SourceState.ERROR it
// arrives on the wire like any other member, and the card's error screen is a
// state rather than an inference.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

/**
 * Every value the card can be handed: the four backend members, then CD's
 * three. Exported so `AudioSourceStatus`'s prop validator and the gallery's
 * scenario select are the same list rather than two copies of it.
 */
export const DISPLAY_STATES = [
  'starting',
  'ready',
  'active',
  'error',
  'no_drive',
  'loading_disc',
  'ejecting'
];

// Minimum display time for "starting": a short anti-flash buffer so a fast
// backend transition (e.g. CD's quick starting -> loading_disc) doesn't flicker
// the card. Kept just above the flash-perception threshold so fast sources feel
// near-instant instead of being padded to a uniform delay.
const STARTING_MIN_MS = 500;

/**
 * @returns {{ displayState: import('vue').Ref<string> }}
 *   displayState — one of DISPLAY_STATES, already through the anti-flash floor.
 */
export function useSourceStatusDisplay() {
  const unifiedStore = useUnifiedAudioStore();
  const timer = useTimer();

  const rawDisplayState = computed(() => {
    const { active_source, source_state, metadata, transitioning } = unifiedStore.systemState;
    const meta = metadata || {};

    // `transitioning` and STARTING encode the same fact, and the state machine
    // deliberately sets the flag alone during a multiroom reroute — so the flag
    // is what the card follows.
    if (transitioning) return 'starting';

    // CD's three, all of them READY on the wire: the drive is the source's
    // hardware rather than its engine, so none of them means the source failed.
    if (active_source === 'cd' && source_state === 'ready') {
      if (meta.ejecting) return 'ejecting';
      // Disc present but TOC or metadata not yet attached. disc_id is emitted by
      // _build_metadata only once `_current_disc` is set — true on either
      // MusicBrainz success OR fallback. Its absence covers the activation
      // window where the TOC has been read but the lookup hasn't completed;
      // once `_current_disc` is populated has_disc flips the source state to
      // ACTIVE and we leave 'loading_disc' anyway.
      if (meta.disc_present && (!meta.cache_ready || !meta.disc_id)) return 'loading_disc';
      if (meta.drive_connected === false) return 'no_drive';
    }

    return source_state;
  });

  const displayState = ref(rawDisplayState.value);
  let startingEnteredAt = null;
  let startingTimer = null;

  watch(rawDisplayState, (newState, oldState) => {
    timer.clear(startingTimer);

    if (newState === 'starting') {
      startingEnteredAt = Date.now();
      displayState.value = 'starting';
      return;
    }

    // Leaving "starting" — enforce minimum display time
    if (oldState === 'starting' && startingEnteredAt) {
      const remaining = STARTING_MIN_MS - (Date.now() - startingEnteredAt);
      if (remaining > 0) {
        startingTimer = timer.setTimeout(() => {
          displayState.value = rawDisplayState.value;
          startingEnteredAt = null;
        }, remaining);
        return;
      }
    }

    startingEnteredAt = null;
    displayState.value = newState;
  });

  return { displayState };
}
