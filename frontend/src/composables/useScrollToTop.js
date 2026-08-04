import { ref, unref, onMounted, onBeforeUnmount } from 'vue';

/**
 * Back-to-top affordance for a long scroller, with nothing on the scroll path.
 *
 * Bind `sentinelRef` to a 1px element at the very top of the scrolled content.
 * The observer's root margin grows the root box upward by `screens` times its
 * own height, so that sentinel keeps intersecting until at least that much has
 * been scrolled past — the threshold is expressed in screenfuls and re-resolves
 * against the live root height, so it survives a resize and the mobile toolbar
 * dance without a single scroll listener. That matters on the Pi, where the
 * deepest scrollers hold a few thousand covers.
 *
 * A view shorter than the threshold never stops intersecting, so `isVisible`
 * stays false with no length check of its own; a navigation that resets
 * scrollTop makes it intersect again, so nothing has to hide the button by hand.
 *
 * @param {import('vue').Ref<HTMLElement|null>} scrollElRef - The scroll container (also the observer root).
 * @param {Object} [options]
 * @param {number} [options.screens=2] - Scrolled screenfuls before the button shows.
 * @returns {{ sentinelRef: import('vue').Ref<HTMLElement|null>, isVisible: import('vue').Ref<boolean>, scrollToTop: () => void }}
 */
export function useScrollToTop(scrollElRef, { screens = 2 } = {}) {
  const sentinelRef = ref(null);
  const isVisible = ref(false);
  let observer = null;

  // Smooth rather than an instant write: the NavigationHeader lives inside the
  // scroller, so a jump would pop it back in one frame — the very artefact
  // useViewTransition's header fade exists to avoid on navigations.
  function scrollToTop() {
    unref(scrollElRef)?.scrollTo({ top: 0, behavior: 'smooth' });
  }

  onMounted(() => {
    const root = unref(scrollElRef);
    if (!root || !sentinelRef.value) return;

    observer = new IntersectionObserver(
      ([entry]) => { isVisible.value = !entry.isIntersecting; },
      { root, rootMargin: `${screens * 100}% 0px 0px 0px`, threshold: 0 }
    );
    observer.observe(sentinelRef.value);
  });

  onBeforeUnmount(() => {
    observer?.disconnect();
    observer = null;
  });

  return { sentinelRef, isVisible, scrollToTop };
}
