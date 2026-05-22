import { ref, unref } from 'vue';
import { modalDebugLog } from '@/services/modalDebug';

/**
 * Composable for scroll-aware view transitions with NavigationHeader clone support.
 *
 * Provides Vue <Transition> hooks that handle:
 * - Pre-calculated height delta via requestHeightDelta (avoids double-spring)
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
 * @param {import('vue').Ref<HTMLElement|null>} [options.contentInnerRef]
 *   Ref to Modal's contentInner element for height delta measurement.
 * @param {((delta: number, duration?: number) => void)|null} [options.requestHeightDelta]
 *   Pre-announce height changes to Modal's animated height system.
 * @param {(() => void)|null} [options.cancelDeferred]
 *   Called by prepareNavigation() when a new navigation starts, to invalidate
 *   any pending deferScrollRestore finalize from a previous (interrupted)
 *   transition. Without this, transitioncancel of the previous height spring
 *   would fire the stale finalize during the new transition, corrupting state.
 */
export function useViewTransition({
  scrollElRef,
  headerRef,
  pendingScrollRestore = ref(null),
  onScrollRestored,
  deferScrollRestore = null,
  contentInnerRef = null,
  requestHeightDelta = null,
  cancelDeferred = null,
}) {
  // Scroll-aware cross-fade state
  let wasScrolled = false;
  let savedScrollTop = 0;
  let enteringEl = null;
  let headerClone = null;
  let savedInnerHeight = 0;
  let savedLeavingHeight = 0;
  let pinnedWrapper = null;
  const SCROLL_FADE_THRESHOLD = 0;

  /**
   * Pre-create header clone BEFORE the view changes (before push/back).
   * Also saves contentInner height for delta calculation in onEnter.
   */
  function prepareNavigation() {
    // Cancel any pending deferred finalize from a previous (still-springing or
    // just-cancelled) transition. Otherwise transitioncancel from the spring we're
    // about to interrupt fires the stale finalize during the new navigation, which
    // overwrites scrollTop with the old target and resets headers we just restyled.
    cancelDeferred?.();

    // Clean up stale state from an interrupted transition
    if (pinnedWrapper) {
      pinnedWrapper.style.minHeight = '';
      pinnedWrapper = null;
    }
    if (enteringEl) {
      enteringEl.style.position = '';
      enteringEl.style.top = '';
      enteringEl.style.left = '';
      enteringEl.style.width = '';
      enteringEl = null;
    }
    if (headerClone && headerClone.parentNode) {
      headerClone.parentNode.removeChild(headerClone);
    }
    headerClone = null;

    // Reset real header inline styles so the upcoming clone (cloneNode below) and
    // savedInnerHeight measurement reflect the in-flow header. Without this, leaked
    // styles from a cancelled transition (position:absolute, translateY(...)) would
    // be cloned into the new clone, breaking layout, and would exclude the header
    // from contentInner's offsetHeight (skewing the height-delta calculation).
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

    // Save current contentInner height BEFORE Vue patches the DOM.
    // Used in onEnter to calculate the height delta for the Modal spring.
    savedInnerHeight = contentInnerRef?.value?.offsetHeight ?? 0;

    const scrollEl = unref(scrollElRef);
    const scrollTop = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;

    // Clone when currently scrolled OR when navigating back to a scrolled position
    if (scrollTop > SCROLL_FADE_THRESHOLD || targetScroll > SCROLL_FADE_THRESHOLD) {
      const headerEl = headerRef.value?.$el;
      if (headerEl) {
        // Clone with OLD content — not yet inserted into DOM
        headerClone = headerEl.cloneNode(true);
        // Remove actions from clone — they have their own cross-fade transition
        // and their absolute positioning would escape the clone's bounding box
        const cloneActions = headerClone.querySelector('.actions-container');
        if (cloneActions) cloneActions.remove();
      }
    }
  }

  /**
   * Called before the leaving element starts its leave transition.
   * Saves scroll, inserts pre-created clone, and captures leaving height.
   */
  function onBeforeLeave(el) {
    const scrollEl = unref(scrollElRef);
    const scrollTop = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const isScrolled = scrollTop > SCROLL_FADE_THRESHOLD;
    const willBeScrolled = targetScroll > SCROLL_FADE_THRESHOLD;

    // Save leaving element height for delta calculation
    savedLeavingHeight = el.offsetHeight;

    // Pin the transition-wrapper height so it doesn't shrink when the leaving
    // element goes position:absolute. This keeps the container stable during
    // the crossfade (no flash/crop on the leaving content).
    // Only needed in Modal context where animated height depends on contentInner.
    if (requestHeightDelta) {
      const wrapper = el.parentNode;
      if (wrapper) {
        wrapper.style.minHeight = `${wrapper.offsetHeight}px`;
        pinnedWrapper = wrapper;
      }
    }

    if (isScrolled || willBeScrolled) {
      wasScrolled = true;
      savedScrollTop = scrollTop;

      const headerEl = headerRef.value?.$el;
      if (headerEl) {
        const parentEl = headerEl.parentNode;

        // Read flow position BEFORE inserting clone (clone would push header down)
        const headerFlowTop = headerEl.offsetTop;

        // Use pre-created clone (old content) or create one now (may have new content)
        if (!headerClone) {
          headerClone = headerEl.cloneNode(true);
          const cloneActions = headerClone.querySelector('.actions-container');
          if (cloneActions) cloneActions.remove();
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
   * Pre-calculates height delta for Modal spring animation.
   */
  function onEnter(el) {
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const isBackNavigation = unref(pendingScrollRestore) !== null;

    if (wasScrolled) {
      enteringEl = el;
      // Position entering content so target scroll position aligns with viewport.
      // Forward (target=0): top = savedScrollTop (content top visible at scroll offset)
      // Back (target=T): top = savedScrollTop - T (content at T aligns with viewport)
      const cssOffset = savedScrollTop - targetScroll;
      el.style.position = 'absolute';
      el.style.top = `${cssOffset}px`;
      el.style.left = '0';
      el.style.width = '100%';

      // When CSS offset is negative (back nav to scrolled view), the entering
      // element is shifted up and its visible portion is shorter than its full
      // height. Expand the wrapper's minHeight to match the visible content so
      // contentInner fills the container (avoids empty space at the bottom).
      if (cssOffset < 0 && pinnedWrapper) {
        const visibleHeight = el.offsetHeight + cssOffset;
        const currentMinHeight = parseFloat(pinnedWrapper.style.minHeight) || 0;
        if (visibleHeight > currentMinHeight) {
          pinnedWrapper.style.minHeight = `${visibleHeight}px`;
        }
      }

      // For back navigation, reposition the header at the target scroll offset
      // so it matches the CSS-offset entering content. This runs while
      // transition:'none' is still active (set in onBeforeLeave), so it's instant.
      if (isBackNavigation && targetScroll > 0) {
        const headerEl = headerRef.value?.$el;
        if (headerEl) {
          headerEl.style.transform = `translateY(-${targetScroll}px)`;
        }
      }

      // Fade in real header with new content during the crossfade.
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
    } else if (savedScrollTop !== targetScroll) {
      enteringEl = el;
      // CSS offset trick: position at visual scroll offset for scroll restore
      el.style.position = 'absolute';
      el.style.top = `${savedScrollTop - targetScroll}px`;
      el.style.width = '100%';
    }

    // Pre-calculate height delta for Modal spring animation.
    // When content overflows, cap element heights to the visible area
    // to avoid overshooting the delta.
    if (requestHeightDelta && savedLeavingHeight > 0) {
      requestAnimationFrame(() => {
        const scrollEl = unref(scrollElRef);
        const enteringHeight = el.offsetHeight;
        let delta = enteringHeight - savedLeavingHeight;
        let usedOverflowPath = false;

        modalDebugLog(`[ViewTransition] onEnter rAF — leaving=${savedLeavingHeight} entering=${enteringHeight} savedInner=${savedInnerHeight} rawDelta=${delta}`);

        // When both old and new views overflow the modal, cap heights to the visible
        // slot area so the modal stays at max height (avoids double-spring).
        // Use savedInnerHeight (captured before DOM change) instead of scrollEl.scrollHeight
        // — scrollHeight is polluted by absolutely positioned elements during the
        // CSS offset trick (scrolled transitions).
        // Include scroll padding in the overflow check: contentInner + padding
        // is the total content that must fit within clientHeight.
        const scrollStyle = scrollEl ? getComputedStyle(scrollEl) : null;
        const scrollPadding = scrollStyle
          ? parseFloat(scrollStyle.paddingTop) + parseFloat(scrollStyle.paddingBottom)
          : 0;

        if (scrollEl && savedInnerHeight + scrollPadding > scrollEl.clientHeight + 2) {
          usedOverflowPath = true;
          const visibleContent = scrollEl.clientHeight - scrollPadding;
          const otherContentHeight = savedInnerHeight - savedLeavingHeight;
          const maxSlotVisible = Math.max(0, visibleContent - otherContentHeight);

          const effectiveLeaving = Math.min(savedLeavingHeight, maxSlotVisible);
          const effectiveEntering = Math.min(enteringHeight, maxSlotVisible);

          delta = effectiveEntering - effectiveLeaving;
          modalDebugLog(`[ViewTransition] overflow path — clientH=${scrollEl.clientHeight} scrollPad=${scrollPadding} effectiveLeaving=${effectiveLeaving} effectiveEntering=${effectiveEntering} finalDelta=${delta}`);
        }

        modalDebugLog(`[ViewTransition] onEnter — finalDelta=${delta} overflow=${usedOverflowPath} → ${Math.abs(delta) > 2 ? 'calling requestHeightDelta' : 'skipped (< 2px)'}`);

        if (Math.abs(delta) > 2) {
          // skipUnlockCorrection: the wrapper is un-pinned in onAfterLeave's finalize
          // (after this delta's spring settles), and the ResizeObserver reconciles the
          // real height then. Re-measuring at unlock would read the still-pinned height
          // and apply a spurious correction the ResizeObserver immediately reverts.
          requestHeightDelta(delta, 800, { skipOverflowCheck: true, skipUnlockCorrection: true });
        }
      });
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

    // [DEBUG-SCROLL]
    const _scrollEl = unref(scrollElRef);
    modalDebugLog(`[ViewTransition/onAfterLeave] ${performance.now().toFixed(0)}ms — targetScroll=${targetScroll} wasScrolled=${wasScrolled} savedScrollTop=${savedScrollTop}` + (_scrollEl ? ` currentScrollTop=${_scrollEl.scrollTop}` : ''));

    // Phases 1+2+3 must happen ATOMICALLY in the same frame to avoid visual jumps:
    // - Phase 1 resets entering element from absolute (top:-X, simulating scroll) to in-flow.
    // - Phase 2 unpins the wrapper.
    // - Phase 3 sets scrollTop to the target.
    // Until these run, the entering element shows the "scrolled" view via CSS offset.
    //
    // In Modal context, the height spring may overshoot (linear() bezier > 1) and clamp
    // modal-content.clientHeight ≥ scrollHeight, making maxScroll = 0 and silently
    // clamping any scrollTop write to 0. So we defer the entire finalize() until
    // isHeightTransitioning becomes false (spring settled), guaranteeing maxScroll
    // is correct when scrollTop is written.
    const finalize = () => {
      // --- Phase 1: Reset inline styles ---
      if (wasScrolled) {
        if (headerClone && headerClone.parentNode) {
          headerClone.parentNode.removeChild(headerClone);
          headerClone = null;
        }
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
        if (enteringEl) {
          enteringEl.style.position = '';
          enteringEl.style.top = '';
          enteringEl.style.left = '';
          enteringEl.style.width = '';
        }
        enteringEl = null;
        wasScrolled = false;
        savedScrollTop = 0;
      } else if (enteringEl && savedScrollTop !== targetScroll) {
        enteringEl.style.position = '';
        enteringEl.style.top = '';
        enteringEl.style.width = '';
        enteringEl = null;
      }

      // --- Phase 2: Unpin wrapper height ---
      if (pinnedWrapper) {
        pinnedWrapper.style.minHeight = '';
        pinnedWrapper = null;
      }

      // --- Phase 3: Restore scroll ---
      const scrollEl = unref(scrollElRef);
      if (scrollEl) scrollEl.scrollTop = targetScroll;
      if (shouldSignalRestore) {
        onScrollRestored?.();
      }
    };

    if (deferScrollRestore) {
      deferScrollRestore(finalize);
    } else {
      finalize();
    }
  }

  return {
    prepareNavigation,
    onBeforeLeave,
    onEnter,
    onAfterLeave,
  };
}
