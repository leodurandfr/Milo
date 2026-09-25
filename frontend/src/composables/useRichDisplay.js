// frontend/src/composables/useRichDisplay.js
// Single source of truth for "does the selected source show its full view (its
// dedicated component / AudioPlayerFull / AudioSourceLayout), or the
// AudioSourceStatus card?" (docs: "le fil", §9, read with D13).
//
// Consumed by AudioSourceView (to pick which component to mount) AND by
// MainView (the logo is hidden exactly when a full view is on screen), so the
// two cannot drift apart.
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { UNTRUSTED_SENDER_MIN_ARTWORK_PX } from '@/constants/imageQuality';

// The three sources played from Milō's own browser: their view is where the
// first station, episode or album is chosen, so it is shown with nothing
// playing too (D13).
const BROWSER_SOURCES = ['radio', 'podcast', 'music_library'];

// What takes a browser source off its view: the link. The Music Library draws
// its own storage and catalog states, with the storage wizard at hand (D13).
const LINK_REASONS = ['no_network', 'no_internet'];

/**
 * Pure rule on the backend's AudioState: the source to draw in full, or null
 * for the card. Exported for tests and the gallery.
 */
export function richSourceFor(state) {
  const { source, switching, service, session, resume, availability, details } = state;
  if (!source || source === 'none' || switching || service !== 'running') return null;

  const reason = availability?.[source] ?? null;
  // A track still coming out keeps its player whatever the link says: a
  // buffered track plays on after the link drops (measured 2026-09-04), and
  // the card would take away the only control that stops it (D13).
  const playing = session?.phase === 'playing';

  if (BROWSER_SOURCES.includes(source)) {
    return LINK_REASONS.includes(reason) && !playing ? null : source;
  }
  if (reason && !playing) return null;

  // A sender Milō cannot tell playing from paused is "Connecté à X" (§9) —
  // until it names a cover, a title and an artist: an iPhone's AirPlay stream
  // stays connected from start to end while naming every track (measured
  // 2026-09-25), and the card hid all of it (D14).
  if (session?.phase === 'connected'
      && !(session.title && session.artist && session.artwork)) return null;

  // Something to name: the session, or what play would bring back (a disc).
  const shown = session?.title || (!session && resume?.title);
  if (!shown) return null;

  // An untrusted sender's tiny cover (browser audio, an app icon) is the card's.
  if (source === 'airplay') {
    return (details?.artwork_width || 0) > UNTRUSTED_SENDER_MIN_ARTWORK_PX ? source : null;
  }
  return source;
}

/**
 * @returns {{ richSource: import('vue').ComputedRef<string|null> }}
 *   richSource — the selected source resolved to a full view, or null when the
 *   AudioSourceStatus card should be shown (incl. while switching).
 */
export function useRichDisplay() {
  const unifiedStore = useUnifiedAudioStore();
  const richSource = computed(() => richSourceFor(unifiedStore.systemState));
  return { richSource };
}
