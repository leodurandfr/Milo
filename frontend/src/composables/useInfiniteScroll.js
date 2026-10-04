import { ref, watch, onBeforeUnmount } from 'vue';
import { scrollParentOf } from '@/utils/scroll';

/**
 * Infinite scroll via IntersectionObserver.
 * Bind the returned `sentinelRef` to a DOM element; `onLoadMore` fires when it
 * comes within `rootMargin` of the visible part of the box it scrolls in.
 *
 * @param {Object} options
 * @param {Function} options.onLoadMore - Called when sentinel is visible and loading is allowed
 * @param {import('vue').Ref<boolean>} options.canLoadMore - Whether more items can be loaded
 * @param {import('vue').Ref<boolean>} [options.isLoading] - Whether a load is already in progress
 * @returns {{ sentinelRef: import('vue').Ref<HTMLElement|null>, recheck: Function }}
 *   `recheck()` asks again whether the sentinel is in reach — after an append
 *   that left it there, which the observer alone never reports.
 */
export function useInfiniteScroll({
  onLoadMore,
  canLoadMore,
  isLoading,
  rootMargin = '100px'
}) {
  const sentinelRef = ref(null);
  let observer = null;

  // Observed against the box the sentinel scrolls in: against the viewport, the
  // margin never reaches a sentinel that box clips, and nothing loads before
  // the end is on screen. Observing anew also reports where the sentinel is now.
  function observe(el) {
    observer?.disconnect();
    observer = null;
    if (!el) return;
    observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && canLoadMore.value && !isLoading?.value) {
          onLoadMore();
        }
      },
      { root: scrollParentOf(el), rootMargin, threshold: 0 }
    );
    observer.observe(el);
  }

  function recheck() {
    observe(sentinelRef.value);
  }

  // Post-flush: the sentinel's ancestors are laid out by then, so its scroll
  // box can be found.
  watch(sentinelRef, observe, { flush: 'post' });

  onBeforeUnmount(() => {
    observer?.disconnect();
    observer = null;
  });

  return { sentinelRef, recheck };
}
