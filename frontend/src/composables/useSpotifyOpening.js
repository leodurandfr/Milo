import { computed, inject, onBeforeUnmount, provide, ref, toValue, watch } from 'vue';
import { useSpotifyStore } from '@/stores/spotifyStore';

export const SPOTIFY_OPENING = Symbol('spotifyOpening');

/**
 * A card of the browser opens its page once the page has something to draw:
 * until then the card spins, where the page would have said "Loading" for a
 * second. The page then carries on the listing itself, from where the card
 * left it. A page with nothing yet past its first answer (go-librespot still
 * describing) opens on its own progress.
 *
 * Provided by SpotifySource; a card reads it through useCardOpening.
 */
export function useSpotifyOpening(push) {
  const store = useSpotifyStore();
  // The uri of the card opening, or null.
  const opening = ref(null);
  let controller = null;

  provide(SPOTIFY_OPENING, opening);

  // A listing's first answer, or its failure: the page draws either.
  const answered = (uri) => !!(store.contexts[uri] || store.contextErrors[uri]);
  const ready = (view, uri) => {
    if (view !== 'artist') return answered(uri);
    return !!(store.artists[uri] || store.artistErrors[uri]) && answered(uri);
  };

  function cancel() {
    controller?.abort();
    controller = null;
    opening.value = null;
  }

  async function open(card, view, params) {
    // Tapped again while it spins: the page opens now, on its own loading.
    const again = opening.value === card.uri;
    cancel();
    if (again) {
      push(view, params);
      return;
    }
    const { uri } = params;
    if (!ready(view, uri)) {
      const own = new AbortController();
      controller = own;
      opening.value = card.uri;
      if (view === 'artist') store.loadArtist(uri, { force: !!store.artistErrors[uri] });
      store.loadContext(uri, { signal: own.signal });
      if (!(await until(() => ready(view, uri), own.signal))) return;
      cancel();
    }
    push(view, params);
  }

  onBeforeUnmount(cancel);

  return { open, cancel };
}

/** Whether the card for `uri` is the one opening its page. */
export function useCardOpening(uri) {
  const opening = inject(SPOTIFY_OPENING, null);
  return computed(() => !!opening?.value && opening.value === toValue(uri));
}

// Resolves true once `condition` holds, false if `signal` aborts first.
function until(condition, signal) {
  return new Promise((resolve) => {
    const stop = watch(condition, (met) => {
      if (!met) return;
      stop();
      resolve(true);
    });
    signal.addEventListener('abort', () => {
      stop();
      resolve(false);
    }, { once: true });
  });
}
