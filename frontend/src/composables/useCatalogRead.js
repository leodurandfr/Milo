// frontend/src/composables/useCatalogRead.js
// A page of the music library read from the catalog: an album, an artist, a
// playlist, a genre's tracks or albums.
//
// The reader answers `{ value, error }`, the shape the library store's fetches
// return: the thing read, or why there is none — 'absent' (the catalog said it
// does not exist) or 'unavailable' (it did not answer: Navidrome restarting, a
// dropped link). The page needs the difference. Read as one, a catalog that
// restarts with every backend restart said "Not found" over an album that
// exists, with nothing to try again by.
import { onScopeDispose, ref, watch } from 'vue';

/**
 * @param {() => any} key - what the page shows (its id); a new key is a new page
 * @param {(key: any, isCurrent: () => boolean) => Promise<{ value: any, error: 'absent'|'unavailable'|null }>} read
 *   `isCurrent` says whether this read is still the page's latest and the page
 *   still open, so a read made of several requests (a genre's pages) can stop
 *   once it is superseded or its page is gone
 * @returns {{ value: import('vue').Ref<any>, error: import('vue').Ref<string|null>,
 *   loading: import('vue').Ref<boolean>, reload: () => Promise<void> }}
 *   `reload` asks again for the page on screen (Retry): what it shows stays
 *   until the answer lands, an error included, so a retry that fails again
 *   does not flash the skeleton between two identical messages.
 */
export function useCatalogRead(key, read) {
  const value = ref(null);
  const error = ref(null);
  const loading = ref(false);
  // Only the latest read lands: a page left for another while its read was on
  // its way must not be drawn over the one now shown.
  let issued = 0;

  async function load() {
    const request = ++issued;
    loading.value = true;
    const result = await read(key(), () => request === issued);
    if (request !== issued) return;
    value.value = result.value;
    error.value = result.error;
    loading.value = false;
  }

  // A closed page supersedes its read too. The library's navigation keys each
  // page by its entry, so a page never sees its key change: closing it (back,
  // or a KeepAlive eviction) is the only way it leaves, and without this a
  // genre left at once went on reading every page of it from Navidrome.
  onScopeDispose(() => {
    issued += 1;
  });

  watch(key, () => {
    value.value = null;
    error.value = null;
    load();
  }, { immediate: true });

  return { value, error, loading, reload: load };
}
