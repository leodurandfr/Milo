import { ref, watch, onMounted, onBeforeUnmount } from 'vue';

/**
 * Infinite scroll via IntersectionObserver.
 * Bind the returned `sentinelRef` to a DOM element; `onLoadMore` fires when it enters the viewport.
 *
 * @param {Object} options
 * @param {Function} options.onLoadMore - Called when sentinel is visible and loading is allowed
 * @param {import('vue').Ref<boolean>} options.canLoadMore - Whether more items can be loaded
 * @param {import('vue').Ref<boolean>} [options.isLoading] - Whether a load is already in progress
 * @returns {{ sentinelRef: import('vue').Ref<HTMLElement|null> }}
 */
export function useInfiniteScroll({
  onLoadMore,
  canLoadMore,
  isLoading,
  rootMargin = '100px'
}) {
  const sentinelRef = ref(null);
  let observer = null;

  function setup() {
    if (observer) {
      observer.disconnect();
    }

    observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && canLoadMore.value && !isLoading?.value) {
          onLoadMore();
        }
      },
      { rootMargin, threshold: 0 }
    );

    if (sentinelRef.value) {
      observer.observe(sentinelRef.value);
    }
  }

  // Re-observe when the sentinel element appears/disappears (v-if toggling)
  watch(sentinelRef, (el, oldEl) => {
    if (oldEl && observer) observer.unobserve(oldEl);
    if (el && observer) observer.observe(el);
  });

  onMounted(setup);

  onBeforeUnmount(() => {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  });

  return { sentinelRef };
}
