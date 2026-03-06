import { ref, unref } from 'vue';

/**
 * Composable for scroll-aware view transitions with NavigationHeader clone support.
 *
 * Provides Vue <Transition> hooks that handle:
 * - Wrapper height pinning (only when entering element is positioned absolutely)
 * - Scroll position save / CSS offset restore
 * - NavigationHeader clone mechanism when scrolled (clone fades out, real header
 *   fades in for forward nav or appears after scroll reset for back nav)
 *
 * IMPORTANT: prepareNavigation() must run AFTER the navigation state mutation
 * (so pendingScrollRestore is set) but BEFORE Vue patches the DOM (so the clone
 * captures old header content). Two valid patterns:
 * - Call prepareNavigation() synchronously after push()/back() (SettingsModal)
 * - Call prepareNavigation() in onBeforeUpdate() on contentKey change (AudioSourceLayout)
 *
 * @param {Object} options
 * @param {import('vue').Ref<HTMLElement|null>} options.scrollElRef
 *   Ref to the scroll container (Modal's modalContentRef or layout's $el).
 * @param {import('vue').Ref<{$el: HTMLElement}|null>} options.headerRef
 *   Ref to the NavigationHeader component instance (for clone mechanism).
 * @param {import('vue').Ref<number|null>} [options.pendingScrollRestore]
 *   Target scroll position on back navigation. Null = reset to 0.
 * @param {() => void} [options.onScrollRestored]
 *   Called after scroll restore completes (consumer clears pendingScrollRestore).
 * @param {((fn: () => void) => void)|null} [options.deferScrollRestore]
 *   When provided, scroll restore is deferred via this callback (e.g., force
 *   overflow-y on Modal content before setting scrollTop).
 */
export function useViewTransition({
  scrollElRef,
  headerRef,
  pendingScrollRestore = ref(null),
  onScrollRestored,
  deferScrollRestore = null,
}) {
  const transitionWrapperRef = ref(null);

  // Scroll-aware cross-fade state
  let wasScrolled = false;
  let savedScrollTop = 0;
  let enteringEl = null;
  let headerClone = null;
  let heightPinned = false;
  const SCROLL_FADE_THRESHOLD = 16;

  /**
   * Pin wrapper minHeight to prevent collapse when both elements are absolute.
   */
  function pinHeight(el) {
    if (transitionWrapperRef.value) {
      transitionWrapperRef.value.style.minHeight = `${el.offsetHeight}px`;
      heightPinned = true;
    }
  }

  /**
   * Pre-create header clone BEFORE the view changes (before push/back).
   * This captures the header's current (old) content before Vue re-renders
   * NavigationHeader with new props. Must be called synchronously after
   * the navigation stack mutation (DOM hasn't updated yet, but
   * pendingScrollRestore is already set).
   */
  function prepareNavigation() {
    // Clean up any stale clone
    if (headerClone && headerClone.parentNode) {
      headerClone.parentNode.removeChild(headerClone);
    }
    headerClone = null;

    const scrollEl = unref(scrollElRef);
    const scrollTop = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;

    // Clone when currently scrolled OR when navigating back to a scrolled position
    if (scrollTop > SCROLL_FADE_THRESHOLD || targetScroll > SCROLL_FADE_THRESHOLD) {
      const headerEl = headerRef.value?.$el;
      if (headerEl) {
        // Clone with OLD content — not yet inserted into DOM
        headerClone = headerEl.cloneNode(true);
      }
    }
  }

  /**
   * Called before the leaving element starts its leave transition.
   * Saves scroll, inserts pre-created clone, and pins height when needed.
   */
  function onBeforeLeave(el) {
    const scrollEl = unref(scrollElRef);
    const scrollTop = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const isScrolled = scrollTop > SCROLL_FADE_THRESHOLD;
    const willBeScrolled = targetScroll > SCROLL_FADE_THRESHOLD;

    if (isScrolled || willBeScrolled) {
      wasScrolled = true;
      savedScrollTop = scrollTop;

      // Pin height — entering element will be positioned absolutely
      pinHeight(el);

      const headerEl = headerRef.value?.$el;
      if (headerEl) {
        const parentEl = headerEl.parentNode;

        // Read flow position BEFORE inserting clone (clone would push header down)
        const headerFlowTop = headerEl.offsetTop;

        // Use pre-created clone (old content) or create one now (may have new content)
        if (!headerClone) {
          headerClone = headerEl.cloneNode(true);
        }

        // Insert clone before real header — clone takes the flow position
        parentEl.insertBefore(headerClone, headerEl);
        headerEl.style.position = 'absolute';
        headerEl.style.top = `${headerFlowTop}px`;
        headerEl.style.left = '0';
        headerEl.style.width = '100%';
        headerEl.style.transform = `translateY(${scrollTop}px)`;
        headerEl.style.transition = 'none';
        headerEl.style.opacity = '0';

        // Fade out clone
        requestAnimationFrame(() => {
          if (headerClone) {
            headerClone.style.transition = 'opacity var(--transition-fast)';
            headerClone.style.opacity = '0';
          }
        });
      }

      // Leaving element stays at scroll position (in flow, overrides leave-active absolute)
      el.style.position = 'static';
    } else {
      // Clean up unused pre-created clone (scroll was below threshold)
      if (headerClone) {
        headerClone = null;
      }

      wasScrolled = false;
      savedScrollTop = scrollTop;

      // Only pin height if scroll restore will position entering element absolutely
      if (savedScrollTop !== targetScroll) {
        pinHeight(el);
      }

      // Reset minor scroll immediately
      if (scrollTop > 0 && scrollEl) {
        scrollEl.scrollTop = 0;
      }
    }
  }

  /**
   * Called when the entering element starts its enter transition.
   * Positions the entering element at the scroll offset when scrolled.
   * Forward nav: fades in header with new content.
   * Back nav: header stays hidden, appears after scroll reset in onAfterLeave.
   */
  function onEnter(el) {
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const isBackNavigation = unref(pendingScrollRestore) !== null;

    if (wasScrolled) {
      enteringEl = el;
      // Position entering content so target scroll position aligns with viewport.
      // Forward (target=0): top = savedScrollTop (content top visible at scroll offset)
      // Back (target=T): top = savedScrollTop - T (content at T aligns with viewport)
      el.style.position = 'absolute';
      el.style.top = `${savedScrollTop - targetScroll}px`;
      el.style.left = '0';
      el.style.width = '100%';

      if (!isBackNavigation) {
        // Forward navigation: fade in real header with new content.
        // Split transition/opacity across frames so browser detects the transition trigger.
        requestAnimationFrame(() => {
          const headerEl = headerRef.value?.$el;
          if (headerEl) {
            headerEl.style.transition = '';
          }
          requestAnimationFrame(() => {
            const headerEl2 = headerRef.value?.$el;
            if (headerEl2) {
              headerEl2.style.opacity = '';
            }
          });
        });
      }
      // Back navigation: header stays hidden, appears in onAfterLeave
    } else if (savedScrollTop !== targetScroll) {
      enteringEl = el;
      // CSS offset trick: position at visual scroll offset for scroll restore
      el.style.position = 'absolute';
      el.style.top = `${savedScrollTop - targetScroll}px`;
      el.style.width = '100%';
    }
  }

  /**
   * Called after the leaving element has fully left the DOM.
   * Cleans up clone, resets header styles, restores scroll.
   * In Modal context, scroll restore forces overflow-y before setting scrollTop.
   */
  function onAfterLeave() {
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const shouldSignalRestore = unref(pendingScrollRestore) !== null;

    // --- Phase 1: Reset inline styles ---

    if (wasScrolled) {
      // Remove clone from DOM
      if (headerClone && headerClone.parentNode) {
        headerClone.parentNode.removeChild(headerClone);
        headerClone = null;
      }

      // Reset real header inline styles (header becomes visible at flow position)
      const headerEl = headerRef.value?.$el;
      if (headerEl) {
        headerEl.style.position = '';
        headerEl.style.top = '';
        headerEl.style.left = '';
        headerEl.style.width = '';
        headerEl.style.transform = '';
        headerEl.style.transition = '';
        headerEl.style.opacity = '';
      }

      // Reset entering element inline styles
      if (enteringEl) {
        enteringEl.style.position = '';
        enteringEl.style.top = '';
        enteringEl.style.left = '';
        enteringEl.style.width = '';
      }

      enteringEl = null;
      wasScrolled = false;
    } else if (enteringEl && savedScrollTop !== targetScroll) {
      // Non-scrolled case with scroll restore: reset entering element
      enteringEl.style.position = '';
      enteringEl.style.top = '';
      enteringEl.style.width = '';
      enteringEl = null;
    }

    // --- Phase 2: Clear height pin (may trigger Modal height transition) ---

    if (heightPinned && transitionWrapperRef.value) {
      transitionWrapperRef.value.style.minHeight = '';
      heightPinned = false;
    }

    // --- Phase 3: Restore scroll (deferred in Modal context) ---

    const doScrollRestore = () => {
      const scrollEl = unref(scrollElRef);
      if (scrollEl) scrollEl.scrollTop = targetScroll;
      if (shouldSignalRestore) {
        onScrollRestored?.();
      }
    };

    if (deferScrollRestore) {
      deferScrollRestore(doScrollRestore);
    } else {
      doScrollRestore();
    }
  }

  return {
    transitionWrapperRef,
    prepareNavigation,
    onBeforeLeave,
    onEnter,
    onAfterLeave,
  };
}
