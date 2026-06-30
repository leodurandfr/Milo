import { ref, unref } from 'vue';

/**
 * Composable for scroll-aware view cross-fade transitions.
 *
 * Provides Vue <Transition> hooks that handle:
 * - Pre-calculated height delta via requestHeightDelta (avoids double-spring)
 * - Scroll save / restore across navigations
 * - Freezing the leaving view at its scroll offset during the cross-fade so it
 *   doesn't jump to the top when scrollTop is rewritten
 *
 * The NavigationHeader stays inside the scroller and scrolls with the content
 * (decision D1). Its title cross-fades via its own internal `header-fade`
 * transition — there is no header clone here.
 *
 * Scroll offsets use `position: relative; top` (not `transform`): relative
 * positioning keeps the view in grid flow (so the grid-stack cell still reserves
 * max(leaving, entering)) and leaves `transform` free for the fade-slide
 * enter/leave animation.
 *
 * IMPORTANT: prepareNavigation() must run AFTER the navigation state mutation
 * (so pendingScrollRestore is set) but BEFORE Vue patches the DOM. Two valid
 * patterns:
 * - Call prepareNavigation() synchronously after push()/back() (SettingsModal)
 * - Call prepareNavigation() in onBeforeUpdate() on contentKey change (AudioSourceLayout)
 *
 * @param {Object} options
 * @param {import('vue').Ref<HTMLElement|null>} options.scrollElRef
 *   Ref to the scroll container (Modal's modalContentRef or layout's $el).
 * @param {import('vue').Ref<number|null>} [options.pendingScrollRestore]
 *   Target scroll position on back navigation. Null = reset to 0.
 * @param {() => void} [options.onScrollRestored]
 *   Called after scroll restore completes (consumer clears pendingScrollRestore).
 * @param {((fn: () => void) => void)|null} [options.deferScrollRestore]
 *   When provided, the scroll-restore write is deferred via this callback until
 *   the Modal height spring has settled (so the write isn't clamped to 0).
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
  pendingScrollRestore = ref(null),
  onScrollRestored,
  deferScrollRestore = null,
  contentInnerRef = null,
  requestHeightDelta = null,
  cancelDeferred = null,
}) {
  // Cross-fade state
  let savedScrollTop = 0;
  let enteringEl = null; // entering view holding a relative scroll-restore offset
  let frozenLeavingEl = null; // leaving view frozen at its old offset during forward nav
  let savedInnerHeight = 0;
  let savedLeavingHeight = 0;

  function clearOffset(el) {
    if (!el) return;
    el.style.position = '';
    el.style.top = '';
  }

  /**
   * Runs AFTER the nav state mutation, BEFORE Vue patches the DOM.
   * Scrubs residual offsets from an interrupted transition and captures the
   * pre-patch contentInner height for the delta calculation.
   */
  function prepareNavigation() {
    // Cancel any pending deferred finalize from a previous (still-springing or
    // just-cancelled) transition. Otherwise transitioncancel from the spring we're
    // about to interrupt fires the stale finalize during the new navigation, which
    // would overwrite scrollTop with the old target.
    cancelDeferred?.();

    // Clean up residual offsets from an interrupted transition.
    clearOffset(enteringEl);
    enteringEl = null;
    clearOffset(frozenLeavingEl);
    frozenLeavingEl = null;

    // Capture contentInner height BEFORE Vue patches the DOM; used in onEnter to
    // size the height delta for the Modal spring.
    savedInnerHeight = contentInnerRef?.value?.offsetHeight ?? 0;
  }

  /**
   * Called before the leaving view starts its leave transition.
   * Forward / back-to-top (target 0): land at the top now and freeze the leaving
   * view so it stays put during the fade. Back-restore (target > 0): leave
   * scrollTop where it is; the entering view is previewed in onEnter and the real
   * scrollTop write is deferred to onAfterLeave.
   */
  function onBeforeLeave(el) {
    const scrollEl = unref(scrollElRef);
    const oldScroll = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;

    savedLeavingHeight = el.offsetHeight;
    savedScrollTop = oldScroll;

    if (targetScroll > 0) {
      // Back-restore: keep the leaving view in place (scrollTop unchanged); the
      // entering view is offset to target T in onEnter, scrollTop written later.
      return;
    }

    // Forward / back-to-top: write scrollTop = 0 now (always lands — 0 is valid
    // regardless of the height spring) and freeze the leaving view at its old
    // offset so it doesn't snap to the top during the cross-fade. `position:
    // relative; top` keeps it in grid flow and leaves `transform` for fade-slide.
    if (oldScroll > 0 && scrollEl) {
      el.style.position = 'relative';
      el.style.top = `-${oldScroll}px`;
      frozenLeavingEl = el;
      scrollEl.scrollTop = 0;
    }
  }

  /**
   * Called when the entering view starts its enter transition.
   * Back-restore only: preview the entering view at the target scroll offset so
   * its content already sits at T during the fade (no flash-then-jump). Then
   * pre-calculate the height delta for the Modal spring.
   */
  function onEnter(el) {
    const targetScroll = unref(pendingScrollRestore) ?? 0;

    if (targetScroll > 0 && savedScrollTop !== targetScroll) {
      enteringEl = el;
      // Shift the entering view (relative, stays in grid flow) so the content at
      // offset T aligns with the viewport while scrollTop is still at the old
      // position. savedScrollTop - targetScroll is negative → shifts the view up.
      el.style.position = 'relative';
      el.style.top = `${savedScrollTop - targetScroll}px`;
    }

    // Pre-calculate height delta for the Modal spring animation.
    // When content overflows, cap element heights to the visible area to avoid
    // overshooting the delta.
    if (requestHeightDelta && savedLeavingHeight > 0) {
      requestAnimationFrame(() => {
        const scrollEl = unref(scrollElRef);
        const enteringHeight = el.offsetHeight;
        let delta = enteringHeight - savedLeavingHeight;

        // When both old and new views overflow the modal, cap heights to the
        // visible slot area so the modal stays at max height (avoids double-spring).
        // Use savedInnerHeight (captured before the DOM change) — the scroller's
        // scrollHeight isn't reliable mid-transition while both views are stacked.
        const scrollStyle = scrollEl ? getComputedStyle(scrollEl) : null;
        const scrollPadding = scrollStyle
          ? parseFloat(scrollStyle.paddingTop) + parseFloat(scrollStyle.paddingBottom)
          : 0;

        if (scrollEl && savedInnerHeight + scrollPadding > scrollEl.clientHeight + 2) {
          const visibleContent = scrollEl.clientHeight - scrollPadding;
          const otherContentHeight = savedInnerHeight - savedLeavingHeight;
          const maxSlotVisible = Math.max(0, visibleContent - otherContentHeight);

          const effectiveLeaving = Math.min(savedLeavingHeight, maxSlotVisible);
          const effectiveEntering = Math.min(enteringHeight, maxSlotVisible);

          delta = effectiveEntering - effectiveLeaving;
        }

        if (Math.abs(delta) > 2) {
          // skipUnlockCorrection: during the cross-fade the grid-stack cell holds
          // max(leaving, entering); re-measuring at unlock would read that transient
          // stacked height and apply a spurious correction. Trust the predicted
          // target and let the ResizeObserver reconcile once the leaving view is gone.
          requestHeightDelta(delta, 800, { skipOverflowCheck: true, skipUnlockCorrection: true });
        }
      });
    }
  }

  /**
   * Called after the leaving view has fully left the DOM.
   * Clears offsets and writes the final scrollTop. For back-restore in the Modal,
   * the write is deferred until the height spring settles (so it isn't clamped).
   */
  function onAfterLeave() {
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const shouldSignalRestore = unref(pendingScrollRestore) !== null;

    const finalize = () => {
      clearOffset(enteringEl);
      enteringEl = null;
      // The frozen leaving view is removed from the DOM with the leave transition;
      // its inline offset vanishes with it, so just drop the reference.
      frozenLeavingEl = null;

      const scrollEl = unref(scrollElRef);
      if (scrollEl) scrollEl.scrollTop = targetScroll;
      savedScrollTop = 0;
      if (shouldSignalRestore) {
        onScrollRestored?.();
      }
    };

    // Only back-restore (target > 0) risks the height-spring clamp, so only it
    // needs deferral. Forward / back-to-top already wrote scrollTop = 0 in
    // onBeforeLeave.
    if (targetScroll > 0 && deferScrollRestore) {
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
