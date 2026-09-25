// frontend/src/composables/useSourceStatusDisplay.js
// The single owner of the vocabulary AudioSourceStatus renders.
//
// The card reads a *display* state composed from the audio state (docs: "le
// fil"): the service (starting, failed), the two drive operations the CD's
// availability names (reading a disc, ejecting — something under way, drawn
// with a spinner), and otherwise the session's own phase — loading, playing,
// paused, or connected ("Connecté à X") — so a paused session no longer says
// "Playing" (E26). With no session, the source is ready.
//
// Alongside the state, this owns the second question the card asks: *can* this
// source work at all right now? It is the source's `availability` entry — the
// link, the internet, a Qobuz account, a CD drive or disc — one value, because
// they render the same way and differ only in their CTA.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

/**
 * Every value the card can be handed. Exported so `AudioSourceStatus`'s prop
 * validator and the gallery's scenario select are the same list.
 */
export const DISPLAY_STATES = [
  'starting',
  'ready',
  'loading',
  'playing',
  'paused',
  'connected',
  'error',
  'loading_disc',
  'ejecting'
];

/** The availability entries that are an operation under way, not a missing prerequisite. */
const OPERATION_STATES = {
  reading_disc: 'loading_disc',
  ejecting: 'ejecting',
};

/**
 * Every prerequisite whose absence the card names. Ordered as the backend
 * resolves them: a missing link is upstream of everything else. The Music
 * Library's own two (no_storage, catalog_unavailable) are not here: its view
 * draws them itself, with the storage wizard at hand (D13).
 */
export const UNAVAILABLE_REASONS = [
  'no_network',
  'no_internet',
  'no_account',
  'no_drive',
  'no_disc',
  'unreadable_disc'
];

/**
 * Pure rule: what the card says stops this source from working, or null.
 * The backend already crossed the link with the source's own requirement, so a
 * Radio gets `no_internet` where a Bluetooth gets null on the same broken link.
 */
export function unavailableReasonFor(source, availability) {
  const reason = availability?.[source] ?? null;
  return UNAVAILABLE_REASONS.includes(reason) ? reason : null;
}

/** Pure rule: the display state of `state` (the backend's AudioState). */
export function displayStateFor(state) {
  const { source, switching, service, session, availability } = state;
  if (switching || service === 'starting') return 'starting';
  if (service === 'failed') return 'error';
  const operation = OPERATION_STATES[availability?.[source]];
  if (operation) return operation;
  if (session) return session.phase;
  return 'ready';
}

// Minimum display time for "starting": a short anti-flash buffer so a fast
// backend transition (e.g. CD's quick starting -> loading_disc) doesn't flicker
// the card. Kept just above the flash-perception threshold so fast sources feel
// near-instant instead of being padded to a uniform delay.
const STARTING_MIN_MS = 500;

/**
 * @returns {{ displayState: import('vue').Ref<string>,
 *             unavailableReason: import('vue').ComputedRef<string|null> }}
 *   displayState — one of DISPLAY_STATES, already through the anti-flash floor.
 *   unavailableReason — one of UNAVAILABLE_REASONS, or null when the source can
 *   work. When set it replaces the state on the card: "Prêt à lire" under a
 *   dead link is the same lie the old terminal fallback told.
 */
export function useSourceStatusDisplay() {
  const unifiedStore = useUnifiedAudioStore();
  const timer = useTimer();

  const rawDisplayState = computed(() => displayStateFor(unifiedStore.systemState));

  // Not floored like `displayState`: a prerequisite is a standing fact, not a
  // step being taken, so there is no flash to absorb.
  const unavailableReason = computed(() => {
    const { source, availability } = unifiedStore.systemState;
    return unavailableReasonFor(source, availability);
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

  return { displayState, unavailableReason };
}
