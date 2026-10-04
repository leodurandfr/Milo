import { computed, inject, nextTick, reactive, toValue } from 'vue';
import { useInfiniteScroll } from './useInfiniteScroll';
import { NAVIGATION_ENTRY_STATE } from './useNavigationStack';

// Rows mounted per step. A track list arrives whole (a Spotify listing, a
// Navidrome playlist, a genre's 500 songs): this paces the RENDER, not the
// fetch — 1159 rows mounted at once is what made Spotify's Liked Songs heavy.
export const RENDER_CHUNK = 50;

/**
 * A long list mounted a chunk at a time, the next chunk mounted ahead of the
 * end as it scrolls into reach. Bind `sentinelRef` to an element after the
 * rows, rendered while `hasMore`.
 *
 * The count is kept in the navigation entry the list belongs to: going back
 * remounts the view and restores its scroll, and the rows it scrolled to must
 * be there again — while the same list opened anew starts from its first rows.
 * Outside a navigation stack it is the view's own.
 *
 * @param {import('vue').MaybeRefOrGetter<Array>} items - The whole list
 * @param {Object} [options]
 * @param {string} [options.key] - Name of the count in the entry, for a view with two lists
 */
export function useRenderWindow(items, { key = 'rendered' } = {}) {
  const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});
  const count = computed(() => state[key] ?? RENDER_CHUNK);
  const visible = computed(() => toValue(items).slice(0, count.value));
  const hasMore = computed(() => count.value < toValue(items).length);

  const { sentinelRef, recheck } = useInfiniteScroll({
    onLoadMore: async () => {
      state[key] = Math.min(count.value + RENDER_CHUNK, toValue(items).length);
      await nextTick();
      recheck();
    },
    canLoadMore: hasMore,
    rootMargin: '600px',
  });

  return { visible, hasMore, sentinelRef };
}
