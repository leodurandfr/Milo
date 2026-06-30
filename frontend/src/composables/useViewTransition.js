import { ref, unref } from 'vue';

/**
 * Composable for scroll-aware view cross-fade transitions.
 *
 * Provides Vue <Transition> hooks that handle:
 * - Sizing the modal to the destination view (via setNavHeight, Modal only)
 * - Scroll save / restore across navigations, written SYNCHRONOUSLY
 * - Freezing the leaving view at its scroll offset during a forward cross-fade so
 *   it doesn't jump to the top when scrollTop is rewritten
 * - Fading the persistent header bar in/out when a navigation's scroll reset
 *   crosses its height, so the scrolling header doesn't pop (opt-in via headerRef)
 *
 * The NavigationHeader stays inside the scroller and scrolls with the content
 * (decision D1). Its title cross-fades via its own internal `header-fade`
 * transition — there is no header clone here. Because it scrolls, a navigation
 * that resets scrollTop past the header's height makes the whole bar appear or
 * disappear in one frame; headerRef lets us fade that bar instead of popping it.
 *
 * Why this is synchronous (no defer, no generation counter, no double-rAF):
 * the scroller now carries an EXPLICIT px height (set by setNavHeight before the
 * scrollTop write, with a forced reflow in between — see useAnimatedHeight). So
 * `maxScroll` is already final when scrollTop is written and the write always
 * lands; there is nothing to wait for and nothing to cancel.
 *
 * The leaving view is frozen with `position: relative; top` (not `transform`):
 * relative positioning keeps it in grid flow (so the grid-stack cell still
 * reserves max(leaving, entering)) and leaves `transform` free for the fade-slide
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
 *   Ref to the scroll container (Modal's modalContentRef = scroller, or layout's $el).
 * @param {import('vue').Ref<number|null>} [options.pendingScrollRestore]
 *   Target scroll position on back navigation. Null = reset to 0.
 * @param {() => void} [options.onScrollRestored]
 *   Called after scroll restore completes (consumer clears pendingScrollRestore).
 * @param {((beforeClip: () => void) => void)|null} [options.setNavHeight]
 *   Measures the live (stacked) content and writes the scroller + clip height,
 *   running its `beforeClip` argument (the scrollTop restore) between the forced
 *   reflow and the clip spring (Modal only). Omitted by AudioSourceLayout, whose
 *   scroller is a fixed-height viewport that never needs resizing.
 * @param {import('vue').Ref<HTMLElement|{$el: HTMLElement}|null>} [options.headerRef]
 *   The persistent NavigationHeader (component ref or raw element). When provided,
 *   the bar fades instead of popping on a scroll-reset navigation. Omitted → no
 *   header treatment.
 */
export function useViewTransition({
  scrollElRef,
  pendingScrollRestore = ref(null),
  onScrollRestored,
  setNavHeight = null,
  headerRef = null,
}) {
  let savedScrollTop = 0;
  let frozenLeavingEl = null; // leaving view frozen at its old offset during forward nav
  let frozenHeaderEl = null;  // header transform-held + faded out during back-to-scrolled

  const DEFAULT_HEADER_HEIGHT = 72; // px; NavigationHeader min-height (measure fallback)

  function clearOffset(el) {
    if (!el) return;
    el.style.position = '';
    el.style.top = '';
  }

  // headerRef may be a component instance (NavigationHeader) or a raw element.
  function resolveHeaderEl() {
    const h = unref(headerRef);
    return h?.$el ?? h ?? null;
  }

  function headerHeight(el) {
    return el?.offsetHeight || DEFAULT_HEADER_HEIGHT;
  }

  function scrubHeader(el) {
    if (!el) return;
    el.style.transition = '';
    el.style.opacity = '';
    el.style.transform = '';
  }

  // Hold the header at `transform` (with transition:none), commit it with a reflow,
  // then fade opacity to `toOpacity`. The transform doubles as a GPU layer so the
  // opacity animates smoothly on iOS WebKit. `fromOpacity` seeds the start (fade-in);
  // omit it to fade from the current opacity (fade-out).
  function fadeHeader(el, { transform, fromOpacity = null, toOpacity, transition }) {
    el.style.transition = 'none';
    el.style.transform = transform;
    if (fromOpacity !== null) el.style.opacity = fromOpacity;
    void el.offsetHeight; // commit the held position / start opacity before transitioning
    el.style.transition = transition;
    el.style.opacity = toOpacity;
  }

  /**
   * Runs AFTER the nav state mutation, BEFORE Vue patches the DOM.
   * Scrubs a residual offset left on the leaving view by an interrupted transition.
   */
  function prepareNavigation() {
    clearOffset(frozenLeavingEl);
    frozenLeavingEl = null;
    scrubHeader(resolveHeaderEl()); // clear a fade-in leftover / interrupted fade-out
    frozenHeaderEl = null;
  }

  /**
   * Called before the leaving view starts its leave transition. In both directions
   * the leaving view is frozen at its painted position so the upcoming scrollTop
   * change doesn't drag it during the cross-fade. `position: relative; top` keeps
   * it in grid flow (the stack cell still reserves max(leaving, entering)) and
   * leaves `transform` for fade-slide.
   * - Forward / back-to-top (target 0): write scrollTop = 0 now (0 always lands)
   *   and freeze here, synchronously — the entering view paints at the top from
   *   frame 0.
   * - Back-restore (target > 0): the scrollTop write is deferred to onEnter (after
   *   the scroller is sized), so the freeze is applied THERE in the same frame;
   *   freezing here — before that write — would displace the view for one frame.
   */
  function onBeforeLeave(el) {
    const scrollEl = unref(scrollElRef);
    const oldScroll = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;

    savedScrollTop = oldScroll;

    if (targetScroll > 0) {
      // Back-restore: capture the leaving view; onEnter freezes it together with
      // the scrollTop = T write (same frame, before first paint).
      frozenLeavingEl = el;
      return;
    }

    // Forward / back-to-top: freeze the leaving view and land scrollTop = 0 now.
    if (oldScroll > 0 && scrollEl) {
      el.style.position = 'relative';
      el.style.top = `-${oldScroll}px`;
      frozenLeavingEl = el;
      scrollEl.scrollTop = 0;

      // Header was scrolled out of view; now that scrollTop is 0 it would otherwise
      // pop back in at full opacity. Fade the bar in (0 → 1) in lock-step with the
      // entering view instead. No freeze — it is already at its resting top position.
      const headerEl = resolveHeaderEl();
      if (headerEl && oldScroll >= headerHeight(headerEl)) {
        fadeHeader(headerEl, {
          transform: 'translate3d(0, 0, 0)',
          fromOpacity: '0',
          toOpacity: '1',
          transition: 'opacity var(--transition-in-out) 100ms',
        });
      }
    }
  }

  /**
   * Called when the entering view starts its enter transition. One rAF lets Vue
   * finish laying out the stacked views, then we size the modal and (on
   * back-restore) land the scroll at T in the mandated order:
   * height → reflow → scrollTop → clip (§3.6).
   */
  function onEnter() {
    requestAnimationFrame(() => {
      const scrollEl = unref(scrollElRef);
      const targetScroll = unref(pendingScrollRestore) ?? 0;

      // Back-restore lands the destination at offset T. The scroller is at its
      // final explicit height by the time this runs (so it is never clamped to 0),
      // and this rAF runs before the first paint of the entering view (so it lands
      // at T with no flash-then-jump). Forward / back-to-top already wrote 0 in
      // onBeforeLeave. Freeze the leaving view in the SAME frame so it stays put
      // during the cross-fade: top = T - oldScroll counteracts the scroll delta.
      const writeScroll = () => {
        if (targetScroll > 0 && savedScrollTop !== targetScroll && scrollEl) {
          // Land the scroll first, then read back the value the browser settled on.
          // If the destination shrank since the scroll was saved, scrollTop clamps to
          // maxScroll < targetScroll; the freeze offset and header hold must use the
          // LANDED value, otherwise they're displaced by (targetScroll - landed) px.
          scrollEl.scrollTop = targetScroll;
          const landed = scrollEl.scrollTop;

          if (frozenLeavingEl) {
            frozenLeavingEl.style.position = 'relative';
            frozenLeavingEl.style.top = `${landed - savedScrollTop}px`;
          }

          // Header is about to scroll out of view. Hold it at the top with a transform
          // (counteracting the landed scroll) and fade the bar out so it doesn't pop.
          // Released in onAfterLeave once it is off-screen.
          const headerEl = resolveHeaderEl();
          if (headerEl && savedScrollTop < headerHeight(headerEl) && landed >= headerHeight(headerEl)) {
            fadeHeader(headerEl, {
              transform: `translate3d(0, ${landed}px, 0)`,
              toOpacity: '0',
              transition: 'opacity var(--transition-fast-leave)',
            });
            frozenHeaderEl = headerEl;
          }
        }
      };

      if (setNavHeight) {
        // Modal: setNavHeight sizes clip + scroller to the live stacked content
        // (max(leaving, entering)) and runs writeScroll between the forced reflow
        // and the clip spring — height → reflow → scrollTop → clip.
        setNavHeight(writeScroll);
      } else {
        // Fixed-height scroller (AudioSourceLayout): no resize, scroll lands directly.
        writeScroll();
      }
    });
  }

  /**
   * Called after the leaving view has fully left the DOM. Synchronous: clear the
   * frozen-leaving reference (its inline offset vanished with the element) and
   * signal restore completion so the consumer clears pendingScrollRestore.
   */
  function onAfterLeave() {
    const shouldSignalRestore = unref(pendingScrollRestore) !== null;

    frozenLeavingEl = null;
    savedScrollTop = 0;
    scrubHeader(frozenHeaderEl); // release the fade-out hold (header is now off-screen)
    frozenHeaderEl = null;
    if (shouldSignalRestore) onScrollRestored?.();
  }

  return {
    prepareNavigation,
    onBeforeLeave,
    onEnter,
    onAfterLeave,
  };
}
