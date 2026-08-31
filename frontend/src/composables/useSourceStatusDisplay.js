// frontend/src/composables/useSourceStatusDisplay.js
// The single owner of the vocabulary AudioSourceStatus renders.
//
// The card does not read `source_state`: it reads a *display* state, which is
// the backend enum plus the two operations CD reaches through metadata alone
// and no backend member describes (reading the disc, ejecting). The third
// screen a drive can produce — no drive at all — is not an operation under way
// but a missing prerequisite, so it is a reason below rather than a member of
// DISPLAY_STATES, and the gallery's CD page says the same. Both
// vocabularies used to arrive through the same string prop, derived in
// AudioSourceView and re-narrated in the gallery — two places deciding one
// thing. They are declared here instead, so the card's validator, the gallery's
// select and the app all read the same list.
//
// ERROR is not derived: since a failed transition writes SourceState.ERROR it
// arrives on the wire like any other member, and the card's error screen is a
// state rather than an inference.
//
// Alongside the state, this owns the second question the card asks: *can* this
// source work at all right now? Four prerequisites can be missing — the link,
// the internet, a Qobuz account, a CD drive — and they used to be four
// mechanisms (a global banner, a prop, a pseudo-state, a per-request flag).
// They are one value here, `unavailableReason`, because they render the same
// way and differ only in their CTA.
import { ref, computed, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';

/**
 * Every value the card can be handed: the four backend members, then CD's two
 * transient operations. Exported so `AudioSourceStatus`'s prop validator and
 * the gallery's scenario select are the same list rather than two copies of it.
 */
export const DISPLAY_STATES = [
  'starting',
  'ready',
  'active',
  'error',
  'loading_disc',
  'ejecting'
];

/**
 * Every prerequisite whose absence makes the source unusable. Ordered as the
 * card resolves them: a missing link is upstream of everything else, so it is
 * what a Qobuz session with neither internet nor an account is told about.
 */
export const UNAVAILABLE_REASONS = [
  'no_network',
  'no_internet',
  'no_account',
  'no_drive'
];

/**
 * Pure rule: what stops this source from working, or null.
 *
 * `networkUnavailable` is the backend's answer — it already crossed the
 * NetworkManager level with the source's own NETWORK_REQUIREMENT, so a Radio
 * gets `no_internet` where a Bluetooth gets null on the same broken link.
 * The other two come from metadata, which only the frontend reads.
 *
 * Exported for useRichDisplay, which must drop to the card for exactly these:
 * a browser whose every tap fails is a worse answer than saying why.
 */
export function unavailableReasonFor(source, metadata, networkUnavailable) {
  if (networkUnavailable) return networkUnavailable;

  const meta = metadata || {};
  // Only an explicit false — the proxy confirming there is no account — arms
  // this. An absent field (pre-first-poll) must not flash the CTA.
  if (source === 'qobuz' && meta.account_authenticated === false) return 'no_account';
  if (source === 'cd' && meta.drive_connected === false) return 'no_drive';
  return null;
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

  const rawDisplayState = computed(() => {
    const { active_source, source_state, metadata, transitioning } = unifiedStore.systemState;
    const meta = metadata || {};

    // `transitioning` and STARTING encode the same fact, and the state machine
    // deliberately sets the flag alone during a multiroom reroute — so the flag
    // is what the card follows.
    if (transitioning) return 'starting';

    // CD's two, both READY on the wire: the drive is the source's hardware
    // rather than its engine, so neither means the source failed. The third
    // screen the drive can produce — no drive at all — is a missing
    // prerequisite, not an operation under way, and lives in the reason below.
    if (active_source === 'cd' && source_state === 'ready') {
      if (meta.ejecting) return 'ejecting';
      // Disc present but TOC or metadata not yet attached. disc_id is emitted by
      // _build_metadata only once `_current_disc` is set — true on either
      // MusicBrainz success OR fallback. Its absence covers the activation
      // window where the TOC has been read but the lookup hasn't completed;
      // once `_current_disc` is populated has_disc flips the source state to
      // ACTIVE and we leave 'loading_disc' anyway.
      if (meta.disc_present && (!meta.cache_ready || !meta.disc_id)) return 'loading_disc';
    }

    return source_state;
  });

  // Not floored like `displayState`: a prerequisite is a standing fact, not a
  // step being taken, so there is no flash to absorb.
  const unavailableReason = computed(() => {
    const { active_source, metadata, network_unavailable } = unifiedStore.systemState;
    return unavailableReasonFor(active_source, metadata, network_unavailable);
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
