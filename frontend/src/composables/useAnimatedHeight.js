// frontend/src/composables/useAnimatedHeight.js
import { onMounted, onUnmounted, nextTick, watch } from 'vue';

/**
 * Drives the modal's morphing height through a SINGLE writer, setTargetHeight().
 *
 * Two nodes share one height target (px):
 * - `.modal-clip`     carries the ANIMATED height (CSS spring) — may overshoot
 *                     harmlessly; it only over-reveals/over-masks a few px.
 * - `.modal-scroller` carries an EXPLICIT height = the same target, written with
 *                     transition:none so it lands instantly. Its clientHeight is
 *                     therefore independent of the clip spring, which is what keeps
 *                     maxScroll correct from frame 0 (a scrollTop write never gets
 *                     clamped to 0 mid-spring).
 *
 * Because both heights flow through the same setTargetHeight(), they can never
 * diverge — there is no height lock, no unlock timer, no double-spring to guard.
 *
 * @param {Ref<HTMLElement|null>} contentRef - inner content element to observe
 * @param {Object} options
 * @param {Ref<HTMLElement|null>} options.clipRef     - `.modal-clip` (animated height)
 * @param {Ref<HTMLElement|null>} options.scrollerRef - `.modal-scroller` (explicit height)
 * @param {number} [options.threshold=2]  - min px change before the observer re-springs
 * @param {boolean} [options.skipFirstResize=true] - init without spring (modal open)
 * @param {Function} [options.getExtraHeight] - extra px to add (scroller padding)
 * @param {Function} [options.getMaxHeight]   - max available height (viewport cap)
 * @returns {{ setTargetHeight: Function, requestHeightDelta: Function, resetFirstResize: Function }}
 */
export function useAnimatedHeight(contentRef, options = {}) {
  const {
    clipRef = null,
    scrollerRef = null,
    threshold = 2,
    skipFirstResize = true,
    getExtraHeight = null,
    getMaxHeight = null,
  } = options;

  let resizeObserver = null;
  let isFirstResize = skipFirstResize;
  // Last target written to the CLIP (the animated anchor). The scroller is always
  // written exactly; the clip skips sub-threshold deltas, so this tracks the clip.
  let currentTargetPx = 0;

  function clampPx(px) {
    let v = px;
    if (getMaxHeight) {
      const max = getMaxHeight();
      if (max && max < Infinity) v = Math.min(v, max);
    }
    return Math.max(0, v);
  }

  // Natural content height (+ padding), UNclamped. Used when setTargetHeight is
  // called with no explicit target (navigation measures the live stacked content).
  function measureContentPx() {
    const h = contentRef.value ? contentRef.value.offsetHeight : 0;
    return h + (getExtraHeight ? getExtraHeight() : 0);
  }

  /**
   * The single height writer. Order is load-bearing on WebKit (§3.6):
   *   1. scroller explicit height (transition:none → lands instantly)
   *   2. forced reflow (flush scroller geometry)
   *   3. beforeClip() — the navigation's scrollTop write, after maxScroll is final
   *   4. clip height (spring, unless `immediate`)
   * The reflow at step 2 flushes the scroller's clientHeight/scrollHeight so the
   * scrollTop write in step 3 lands un-clamped; the clip spring in step 4 follows.
   *
   * @param {number|null} px - target height; null → measure live content
   * @param {Object} [opts]
   * @param {boolean} [opts.immediate=false] - write the clip without a spring
   * @param {(() => void)|null} [opts.beforeClip=null] - runs AFTER the reflow and
   *   BEFORE the clip write, so a scrollTop write lands in the mandated order
   *   height → reflow → scrollTop → clip (§3.6, the one load-bearing ordering).
   */
  function setTargetHeight(px = null, { immediate = false, beforeClip = null } = {}) {
    const scroller = scrollerRef?.value;
    if (!scroller) return;

    const target = clampPx(px == null ? measureContentPx() : px);

    // 1. Scroller: explicit final height. transition:none lives in CSS.
    scroller.style.height = `${target}px`;

    // 2. Forced reflow — flush clientHeight/scrollHeight before any scrollTop
    //    write (load-bearing on WebKit, §3.6).
    void scroller.offsetHeight;

    // 3. Scroll restore slots here: maxScroll is final and the clip is not yet
    //    re-sprung, so the write is in the exact mandated order.
    beforeClip?.();

    // 4. Clip: animated height (spring from CSS), or instant on init/immediate.
    //    Skip sub-threshold deltas so jitter (e.g. a 1px nav delta, or an integer
    //    offsetHeight target vs a fractional observer target) doesn't fire the
    //    1.6s spring — §2.2 "Delta nul / <2px". The scroller height + scroll
    //    restore above already ran, so maxScroll/scrollTop stay exact. Advance the
    //    anchor only when we actually move the clip (no sub-threshold drift).
    const clip = clipRef?.value;
    if (clip && (immediate || Math.abs(target - currentTargetPx) > threshold)) {
      if (immediate) {
        const prev = clip.style.transition;
        clip.style.transition = 'none';
        clip.style.height = `${target}px`;
        void clip.offsetHeight; // commit the no-transition write
        clip.style.transition = prev;
      } else {
        clip.style.height = `${target}px`;
      }
      currentTargetPx = target;
    }
  }

  /**
   * Pre-announce a height change before a child animation (accordions) so the clip
   * springs in lock-step with the child's own CSS animation. Trivial wrapper over
   * setTargetHeight: predict the post-change content height from the CURRENT
   * (pre-change) content + delta, then let setTargetHeight clamp to the viewport.
   *
   * Basing the prediction off measured content (not currentTargetPx) keeps an
   * at-max overflow pinned to max when the delta doesn't bring content below the
   * cap — without it, a collapse would dip below max then spring back.
   *
   * There is no lock to schedule; the ResizeObserver reconciles the real height
   * once the child animation settles.
   *
   * @param {number} delta - px change (positive expand, negative collapse)
   */
  function requestHeightDelta(delta) {
    setTargetHeight(measureContentPx() + delta);
  }

  function setupObserver() {
    if (resizeObserver) resizeObserver.disconnect();

    resizeObserver = new ResizeObserver(entries => {
      if (!entries[0]) return;

      const raw = entries[0].contentRect.height + (getExtraHeight ? getExtraHeight() : 0);

      // First resize (modal open / data loaded): initialize without a spring.
      if (isFirstResize) {
        setTargetHeight(raw, { immediate: true });
        isFirstResize = false;
        return;
      }

      // Skip sub-threshold jitter (compare against the clamped last target).
      if (Math.abs(clampPx(raw) - currentTargetPx) > threshold) {
        setTargetHeight(raw);
      }
    });

    if (contentRef.value) resizeObserver.observe(contentRef.value);
  }

  function disconnectObserver() {
    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }
  }

  function resetFirstResize() {
    isFirstResize = true;
  }

  // Re-observe when the content element toggles (v-if on modal open/close).
  watch(contentRef, (newRef) => {
    if (newRef) setupObserver();
    else disconnectObserver();
  });

  onMounted(async () => {
    await nextTick();
    if (contentRef.value) setupObserver();
  });

  onUnmounted(disconnectObserver);

  return {
    setTargetHeight,
    requestHeightDelta,
    resetFirstResize,
  };
}
