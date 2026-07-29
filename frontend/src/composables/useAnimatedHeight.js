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
 * @returns {{ setTargetHeight: Function, requestHeightDelta: Function, springClipDelta: Function, resetFirstResize: Function, endFirstResize: Function }}
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
  // While this timestamp is in the future, observer callbacks update ONLY the scroller
  // (to the live content) and leave the clip alone — it's mid native-spring to a
  // pre-set target and must finish its curve (incl. bounce) uninterrupted. See
  // springClipDelta().
  let scrollerFollowsUntil = 0;

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
   * An intentional request means the modal is past its opening phase, so it ends
   * the first-resize window here: otherwise, expanding an accordion during the
   * modal's ~1s open animation would let the following observer callback overwrite
   * this spring with a first-resize immediate snap (instant, no spring), and a
   * window that expires mid-animation would jerk from immediate to spring.
   *
   * @param {number} delta - px change (positive expand, negative collapse)
   */
  function requestHeightDelta(delta) {
    isFirstResize = false;
    scrollerFollowsUntil = 0;  // reclaim the clip if a collapse spring is mid-flight (re-open)
    setTargetHeight(measureContentPx() + delta);
  }

  /**
   * Companion to requestHeightDelta for when the CHILD animates its OWN height on the
   * SAME spring curve as the clip (e.g. a multiroom zone whose wrapper springs 0 ↔ full
   * over --transition-spring-light). Springs ONLY the clip to the target — a native CSS
   * spring, so it keeps the bounce — and, for `durationMs`, has the observer keep the
   * SCROLLER matched to the live content instead of writing the clip (which must finish
   * its curve uninterrupted). Symmetric: expand and collapse both go through here.
   *
   * Because the child rides the same curve, clip and content are equal at every frame
   * (no gap, nothing cut to the target early — the scroller tracks the live reflow).
   * Unlike requestHeightDelta, the scroller is NOT set to the target up front: that
   * would clip the still-reflowing content on collapse, and reveal empty space on expand.
   *
   * @param {number} delta - px change (positive expand, negative collapse).
   * @param {number} [durationMs=900] - how long the scroller follows the reflow. Must
   *   outlast the child's own spring (820ms): on expand the content overshoots ABOVE the
   *   target and its residual wobble exceeds `threshold` on tall children, which would
   *   re-spring the clip mid-curve. On collapse the height clamps at 0 much earlier.
   */
  function springClipDelta(delta, durationMs = 900) {
    isFirstResize = false;
    const clip = clipRef?.value;
    if (!clip) return;
    const target = clampPx(measureContentPx() + delta);
    clip.style.height = `${target}px`;
    currentTargetPx = target;
    scrollerFollowsUntil = performance.now() + durationMs;
  }

  function setupObserver() {
    if (resizeObserver) resizeObserver.disconnect();

    resizeObserver = new ResizeObserver(entries => {
      if (!entries[0]) return;

      const raw = entries[0].contentRect.height + (getExtraHeight ? getExtraHeight() : 0);

      if (isFirstResize) {
        setTargetHeight(raw, { immediate: true });
        return;
      }

      // A child is reflowing its own height while the clip springs to a pre-set target
      // (springClipDelta). Keep the scroller matched to the live content so the reflow
      // isn't clipped, but leave the clip alone to finish its native spring/bounce.
      if (performance.now() < scrollerFollowsUntil) {
        const scroller = scrollerRef?.value;
        if (scroller) {
          scroller.style.height = `${clampPx(raw)}px`;
          void scroller.offsetHeight;
        }
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

  function endFirstResize() {
    isFirstResize = false;
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
    springClipDelta,
    resetFirstResize,
    endFirstResize,
  };
}
