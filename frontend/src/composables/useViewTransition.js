import { ref, unref } from 'vue';

/**
 * How a navigation treats the scroll, from where the header stands before and
 * after it. While the header is on screen at both ends, the scroll is kept
 * ('keep'), so the header does not move; otherwise the scroll moves to the
 * destination ('move'), and the header fades wherever that crosses its edge.
 *
 * @param {Object} geometry
 * @param {number} geometry.scroll - scrollTop before the navigation
 * @param {number} geometry.target - scrollTop the destination asks for (0 forward)
 * @param {number|null} geometry.headerBottom - the header's bottom edge in scroll
 *   coordinates; null when there is no header to keep still
 * @returns {'keep'|'move'}
 */
export function navigationMode({ scroll, target, headerBottom }) {
  if (headerBottom == null || scroll === target) return 'move';
  return scroll < headerBottom && target < headerBottom ? 'keep' : 'move';
}

/**
 * What a kept scroll needs once the destination is measured.
 * - 'none': the destination is long enough to hold the scroll.
 * - 'reserve': it is a little too short — the missing height is reserved below it.
 *   -1 still counts: a `min-height: 100%` wrapper can round one pixel under its scroller.
 * - 'glide': it fits without scrolling at all, so the scroller itself shrinks (a
 *   modal) and the scroll cannot stay; the header glides to its place instead.
 *
 * @param {number} scroll - the kept scrollTop
 * @param {number} destMax - the destination's own maximum scrollTop (negative when
 *   its content is shorter than the scroller)
 * @returns {'none'|'reserve'|'glide'}
 */
export function keepOutcome(scroll, destMax) {
  if (destMax >= scroll) return 'none';
  if (destMax >= -1) return 'reserve';
  return 'glide';
}

/**
 * Composable for scroll-aware view cross-fade transitions.
 *
 * Provides Vue <Transition> hooks that handle:
 * - Sizing the modal to the destination view (via setNavHeight, Modal only)
 * - Scroll save / restore across navigations, written SYNCHRONOUSLY
 * - Freezing the leaving view at its scroll offset during a cross-fade that moves
 *   the scroll, so it doesn't jump when scrollTop is rewritten
 * - Keeping the scroll, and so the header, where it is while the header is on
 *   screen at both ends of the navigation (navigationMode)
 * - Fading the persistent header bar in/out when a navigation's scroll crosses
 *   its edge, so the scrolling header doesn't pop (opt-in via headerRef)
 *
 * The NavigationHeader stays inside the scroller and scrolls with the content
 * (decision D1). Its title cross-fades via its own internal `header-fade`
 * transition — there is no header clone here. Because it scrolls, any scroll
 * write moves it: a navigation that resets scrollTop while the header is still
 * partly on screen would drop it into place in one frame. So such a navigation
 * keeps the scroll instead. A destination too short to hold it gets the missing
 * height reserved below it — less than one header, since the header was still on
 * screen — which melts as the user scrolls back up. One that fits with no scroll
 * at all (a modal shrinking to it) cannot hold any offset: the header glides.
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
 * @param {import('vue').Ref<HTMLElement|null>} [options.contentElRef]
 *   The scroller's content wrapper: measured for the destination's length and
 *   given the reserved height. Omitted (or no header) → the scroll is never kept.
 */
export function useViewTransition({
  scrollElRef,
  pendingScrollRestore = ref(null),
  onScrollRestored,
  setNavHeight = null,
  headerRef = null,
  contentElRef = null,
}) {
  let savedScrollTop = 0;
  let savedHeaderBottom = null; // header's bottom edge in scroll coordinates, before the nav
  let mode = 'move';            // navigationMode of the navigation in flight
  let keptLeavingEl = null;     // the leaving view of a 'keep' navigation, measured around
  let frozenLeavingEl = null; // leaving view frozen at its old offset during a scroll move
  let frozenHeaderEl = null;  // header transform-held + faded out during back-to-scrolled
  let reserve = null;         // { contentEl, scrollEl, base, destMax, height } while held

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

  // Scroll offset past which the header is entirely off screen — the padding
  // above it included, which its own height alone leaves out.
  function headerBottom(headerEl, scrollEl) {
    return headerEl.getBoundingClientRect().bottom
      - scrollEl.getBoundingClientRect().top + scrollEl.scrollTop;
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

  function fadeHeaderIn(el) {
    fadeHeader(el, {
      transform: 'translate3d(0, 0, 0)',
      fromOpacity: '0',
      toOpacity: '1',
      transition: 'opacity var(--transition-in-out) 100ms',
    });
  }

  // The reserve shrinks as the user scrolls back up and never grows, so the void
  // can't be scrolled into further than where the navigation left it; it is gone
  // once the destination holds the scroll on its own.
  function meltReserve() {
    if (!reserve) return;
    const excess = reserve.scrollEl.scrollTop - reserve.destMax;
    if (excess <= 0) {
      dropReserve();
    } else if (excess < reserve.height) {
      reserve.height = excess;
      reserve.contentEl.style.minHeight = `${reserve.base + excess}px`;
    }
  }

  function holdReserve(contentEl, scrollEl, base, destMax, height) {
    reserve = { contentEl, scrollEl, base, destMax, height };
    contentEl.style.minHeight = `${base + height}px`;
    scrollEl.addEventListener('scroll', meltReserve, { passive: true });
  }

  function dropReserve() {
    if (!reserve) return;
    reserve.contentEl.style.minHeight = '';
    reserve.scrollEl.removeEventListener('scroll', meltReserve);
    reserve = null;
  }

  // The destination's own length, read with the leaving view out of flow and no
  // reserve. Either may let the browser clamp scrollTop during the read, so a kept
  // scroll is written back afterwards (writeScroll).
  function measureDestination(scrollEl, contentEl) {
    dropReserve();
    const prevDisplay = keptLeavingEl.style.display;
    keptLeavingEl.style.display = 'none';
    const base = contentEl.offsetHeight;
    const style = getComputedStyle(scrollEl);
    const destMax = base + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
      - scrollEl.clientHeight;
    keptLeavingEl.style.display = prevDisplay;
    return { base, destMax };
  }

  /**
   * Runs AFTER the nav state mutation, BEFORE Vue patches the DOM.
   * Scrubs a residual offset left on the leaving view by an interrupted transition.
   * A held reserve stays: dropping it now would clamp the scroll the coming
   * navigation still reads; each path releases it once its own scroll is written.
   */
  function prepareNavigation() {
    clearOffset(frozenLeavingEl);
    frozenLeavingEl = null;
    keptLeavingEl = null;
    mode = 'move';
    scrubHeader(resolveHeaderEl()); // clear a fade-in / glide leftover / interrupted fade-out
    frozenHeaderEl = null;
  }

  /**
   * Called before the leaving view starts its leave transition. Decides the
   * navigation's mode and returns it (AudioSourceLayout's gradient follows it).
   * - Keep: nothing moves here; onEnter measures the destination.
   * - Forward / back-to-top (target 0): write scrollTop = 0 now (0 always lands)
   *   and freeze the leaving view at its painted position, synchronously — the
   *   entering view paints at the top from frame 0. `position: relative; top`
   *   keeps it in grid flow and leaves `transform` for fade-slide.
   * - Back-restore (target > 0): the scrollTop write is deferred to onEnter (after
   *   the scroller is sized), so the freeze is applied THERE in the same frame;
   *   freezing here — before that write — would displace the view for one frame.
   */
  function onBeforeLeave(el) {
    const scrollEl = unref(scrollElRef);
    const oldScroll = scrollEl?.scrollTop || 0;
    const targetScroll = unref(pendingScrollRestore) ?? 0;
    const headerEl = resolveHeaderEl();

    savedScrollTop = oldScroll;
    savedHeaderBottom = headerEl && scrollEl ? headerBottom(headerEl, scrollEl) : null;
    mode = navigationMode({
      scroll: oldScroll,
      target: targetScroll,
      headerBottom: unref(contentElRef) ? savedHeaderBottom : null,
    });

    if (mode === 'keep') {
      keptLeavingEl = el;
      return mode;
    }

    if (targetScroll > 0) {
      // Back-restore: capture the leaving view; onEnter freezes it together with
      // the scrollTop = T write (same frame, before first paint).
      frozenLeavingEl = el;
      return mode;
    }

    // Forward / back-to-top: freeze the leaving view and land scrollTop = 0 now.
    if (oldScroll > 0 && scrollEl) {
      el.style.position = 'relative';
      el.style.top = `-${oldScroll}px`;
      frozenLeavingEl = el;
      scrollEl.scrollTop = 0;
      dropReserve();

      // Header was scrolled out of view; now that scrollTop is 0 it would otherwise
      // pop back in at full opacity. Fade the bar in (0 → 1) in lock-step with the
      // entering view instead. No freeze — it is already at its resting top position.
      if (headerEl && oldScroll >= savedHeaderBottom) fadeHeaderIn(headerEl);
    }
    return mode;
  }

  /**
   * Called when the entering view starts its enter transition. One rAF lets Vue
   * finish laying out the stacked views, then we size the modal and land the
   * scroll in the mandated order: height → reflow → scrollTop → clip (§3.6).
   */
  function onEnter() {
    requestAnimationFrame(() => {
      const scrollEl = unref(scrollElRef);
      const contentEl = unref(contentElRef);
      const targetScroll = unref(pendingScrollRestore) ?? 0;

      let outcome = null;
      let destination = null;
      if (mode === 'keep' && keptLeavingEl && scrollEl && contentEl) {
        destination = measureDestination(scrollEl, contentEl);
        outcome = keepOutcome(savedScrollTop, destination.destMax);
        if (outcome === 'reserve') {
          holdReserve(contentEl, scrollEl, destination.base, destination.destMax,
            savedScrollTop - destination.destMax);
        }
      } else if (targetScroll > 0) {
        dropReserve(); // the restore below writes its own scroll
      }

      // Back-restore lands the destination at offset T. The scroller is at its
      // final explicit height by the time this runs (so it is never clamped to 0),
      // and this rAF runs before the first paint of the entering view (so it lands
      // at T with no flash-then-jump). Forward / back-to-top already wrote 0 in
      // onBeforeLeave. Freeze the leaving view in the SAME frame so it stays put
      // during the cross-fade: top = T - oldScroll counteracts the scroll delta.
      const writeScroll = () => {
        if (!scrollEl) return;

        if (outcome === 'glide') {
          // The destination fits with no scroll: the scroller shrinks to it, so the
          // offset cannot stay. Move to 0 as a forward navigation does, and hold the
          // header where it was painted, then let it slide down to its place.
          keptLeavingEl.style.position = 'relative';
          keptLeavingEl.style.top = `-${savedScrollTop}px`;
          frozenLeavingEl = keptLeavingEl;
          scrollEl.scrollTop = 0;
          const headerEl = resolveHeaderEl();
          headerEl.style.transition = 'none';
          headerEl.style.transform = `translate3d(0, -${savedScrollTop}px, 0)`;
          void headerEl.offsetHeight; // commit the held position before transitioning
          headerEl.style.transition = 'transform var(--transition-in-out)';
          headerEl.style.transform = 'translate3d(0, 0, 0)';
          return;
        }

        if (outcome) {
          // Kept: write the scroll back, in case the measurement clamped it.
          scrollEl.scrollTop = savedScrollTop;
          return;
        }

        if (targetScroll > 0 && savedScrollTop !== targetScroll) {
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

          const headerEl = resolveHeaderEl();
          if (!headerEl || savedHeaderBottom === null) return;
          if (savedScrollTop < savedHeaderBottom && landed >= savedHeaderBottom) {
            // Header is about to scroll out of view. Hold it at the top with a transform
            // (counteracting the landed scroll) and fade the bar out so it doesn't pop.
            // Released in onAfterLeave once it is off-screen.
            fadeHeader(headerEl, {
              transform: `translate3d(0, ${landed}px, 0)`,
              toOpacity: '0',
              transition: 'opacity var(--transition-fast-leave)',
            });
            frozenHeaderEl = headerEl;
          } else if (savedScrollTop >= savedHeaderBottom && landed < savedHeaderBottom) {
            // Header comes back into view part way down: fade it in where it lands.
            fadeHeaderIn(headerEl);
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
   * frozen offset and its reference — a page kept by a KeepAlive is this same
   * element when it is gone back to — and signal restore completion so the
   * consumer clears pendingScrollRestore.
   */
  function onAfterLeave(el) {
    const shouldSignalRestore = unref(pendingScrollRestore) !== null;

    clearOffset(el);
    frozenLeavingEl = null;
    keptLeavingEl = null;
    mode = 'move';
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
