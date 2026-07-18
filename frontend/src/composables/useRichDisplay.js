// frontend/src/composables/useRichDisplay.js
// Single source of truth for "does the active source show a rich full-screen
// player (its dedicated component / AudioPlayerFull / AudioSourceLayout), or
// does it fall back to the AudioSourceStatus card?".
//
// Consumed by AudioSourceView (to pick which component to mount) AND by
// MainView (the logo is hidden exactly when a rich display is on screen).
// Keeping the rule here — not duplicated per consumer — is what prevents the
// logo and the player view from drifting out of sync (e.g. the CD status card
// showing while the logo was wrongly hidden during disc loading/ejecting).
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { AIRPLAY_MIN_ARTWORK_PX } from '@/constants/imageQuality';

// Pure rule: given a source + its state + metadata, does it earn a rich view?
// AirPlay's artwork-quality gate (AIRPLAY_MIN_ARTWORK_PX) is shared with the
// screensaver — see @/constants/imageQuality.
function hasRichDisplay(source, state, meta) {
  const m = meta || {};
  switch (source) {
    case 'spotify':
      // Trusted metadata provider: title + artist is enough.
      return state === 'active' && !!m.title && !!m.artist;
    case 'airplay':
      // Untrusted sender: require title, artist AND a real cover (>300px).
      // A small/absent image means browser audio → status card.
      // Passive source (no Milō controls), so also require audio to be flowing:
      // when the sender stops/quits, the route stays connected and the backend
      // keeps the stale cover but flips is_playing=false → drop to the status
      // card rather than freeze on a cover for audio that no longer plays.
      return state === 'active' && !!m.is_playing && !!m.title && !!m.artist &&
        (m.album_art_width || 0) > AIRPLAY_MIN_ARTWORK_PX;
    case 'radio':
    case 'podcast':
      // Own component (AudioSourceLayout) handles internal empty/loading states.
      return true;
    case 'cd':
      // Rich player whenever a disc is loaded and ready — playing (ACTIVE) OR
      // idle (WAITING: tracklist + resume affordance, disc still visible). The
      // loading (no cache_ready), ejecting, and no-drive windows stay on the
      // AudioSourceStatus card.
      return !!m.disc_present && !!m.cache_ready && !m.ejecting;
    case 'dlna':
      // Same gate as AirPlay: untrusted external sender, require title, artist,
      // a real cover (>300px) AND audio flowing (drop the stale cover when the
      // controller stops).
      return state === 'active' && !!m.is_playing && !!m.title && !!m.artist &&
        (m.album_art_width || 0) > AIRPLAY_MIN_ARTWORK_PX;
    case 'qobuz':
      // Trusted metadata provider (Qobuz CDN cover, always full-size — no
      // album_art_width is emitted). Unlike AirPlay/DLNA the proxy reports idle
      // explicitly (→ WAITING) instead of leaving stale metadata, so no
      // is_playing gate is needed: title + artist is enough (like Spotify), and
      // a paused track keeps its cover on screen.
      return state === 'active' && !!m.title && !!m.artist;
    case 'music_library':
      // Own component (AudioSourceLayout) handles its internal empty/loading/
      // browsing states, like radio/podcast — the library UI is shown whenever
      // the source is active, docked player appears once a queue plays.
      return true;
    default:
      // bluetooth, mac, none → no rich view, always the status card.
      return false;
  }
}

/**
 * @returns {{ richSource: import('vue').ComputedRef<string|null> }}
 *   richSource — the active source resolved to a rich full-screen view, or null
 *   when the AudioSourceStatus card should be shown (incl. during transitions).
 */
export function useRichDisplay() {
  const unifiedStore = useUnifiedAudioStore();

  // Transitions always defer to the status card.
  const richSource = computed(() => {
    const { active_source, source_state, metadata, transitioning } = unifiedStore.systemState;
    return !transitioning && hasRichDisplay(active_source, source_state, metadata)
      ? active_source
      : null;
  });

  return { richSource };
}
