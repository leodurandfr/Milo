// Directive v-press for visual press feedback (150ms minimum)
// Uses pixel-based shrinking for consistent visual effect across all element sizes
// Usage:
//   <button v-press>             → standard press (4px shrink)
//   <button v-press="condition"> → conditional (active if truthy)
//   <div v-press.flat>           → dims without shrinking (a full-width row,
//                                  where 4px of a small side reads as a lurch)
//   <div v-press.media>          → a card: only its `.press-target` (the cover)
//                                  shrinks, by 4px of its own size; the card,
//                                  and the text beside the cover, keep theirs
//
// Purely visual: activation is the browser's own click, so a scroll, a fling or
// the tap that stops one never activates anything the native rules would not.
//
// The shrink must not shrink the hit box with it: a press on the element's last
// pixels found the pointer outside it at release, and the browser sent the click
// to the parent (a mouse: the common ancestor of down and up; a finger: whatever
// lies under the tap), so the element pressed and did nothing. While pressed, an
// invisible pseudo-element (`.press-reach`) reaches back to the unshrunk box.

const PRESS_SHRINK_PX = 4
const PRESS_MIN_VISIBLE_MS = 150
// The press curve (--transition-press) overshoots its target by 16.3 %.
const PRESS_OVERSHOOT = 1.163
// A touch's click follows its pointerup by a frame or two; the reach outlives
// the release by this much so the tap still lands on it.
const PRESS_REACH_LINGER_MS = 300

// From the border box the ResizeObserver reports, never a read of the layout:
// a page mounting or coming back holds hundreds of these, and a read between two
// writes lays the page out again each time.
function updateScale(el, width, height) {
  // Out of the document (a page its KeepAlive kept) or not rendered: nothing to
  // measure, and the scale it had is the one it will need back.
  if (!width && !height) return

  const avgDimension = (width + height) / 2

  // Prevent extreme scaling on tiny elements
  const scale = avgDimension < 16
    ? 0.95
    // Clamp to reasonable range (0.85 to 0.98)
    : Math.max(0.85, Math.min(0.98, (avgDimension - PRESS_SHRINK_PX) / avgDimension))

  const value = scale.toFixed(4)
  if (el.style.getPropertyValue('--press-scale') !== value) {
    el.style.setProperty('--press-scale', value)
  }

  // How far the unshrunk edge lies outside the shrunk box, at the curve's
  // deepest point, in the element's own (scaled) coordinates.
  const deepest = 1 - (1 - scale) * PRESS_OVERSHOOT
  const reach = `${Math.ceil(Math.max(width, height) / 2 * (1 / deepest - 1)) + 1}px`
  if (el.style.getPropertyValue('--press-reach') !== reach) {
    el.style.setProperty('--press-reach', reach)
  }
}

function setupPress(el, modifiers) {
  if (modifiers.flat) {
    el.style.setProperty('--press-scale', '1')
  } else if (modifiers.media) {
    setupMedia(el)
  } else {
    // Only the element itself shrinks here: a flat row keeps its box, and a
    // card's box stays put around its shrinking cover.
    el._pressReaches = true
    observeScale(el, el)
  }
  listenPress(el)
}

// The cover can be swapped by a re-render (a liked row's drawing for an image),
// so the target is looked up again on every update; the scale lands on the card,
// where the cover inherits it.
function setupMedia(el) {
  el.classList.add('press-media')
  const target = el.querySelector('.press-target')
  if (target === el._pressTarget) return
  el._pressObserver?.disconnect()
  delete el._pressObserver
  el._pressTarget = target
  if (target) observeScale(el, target)
}

function observeScale(el, measured) {
  // Its first report arrives before the first paint, so no press can come
  // before the scale.
  const observer = new ResizeObserver(([entry]) => {
    // borderBoxSize is Safari 15.4+; the build targets Safari 14, which measures.
    const box = entry.borderBoxSize?.[0]
    if (box) {
      updateScale(el, box.inlineSize, box.blockSize)
    } else {
      const rect = measured.getBoundingClientRect()
      updateScale(el, rect.width, rect.height)
    }
  })
  observer.observe(measured, { box: 'border-box' })
  el._pressObserver = observer
}

function listenPress(el) {
  el.classList.add('interactive-press')

  const dropReach = () => {
    window.clearTimeout(el._pressReachTimer)
    el.classList.remove('press-reach')
  }
  el._pressDropReach = dropReach

  // Held until release, with a minimum so quick taps still show feedback.
  // Raw window timer (window.* prefix): a directive has no component lifecycle,
  // so useTimer() can't be used here. Fire-and-forget CSS-class removal,
  // harmless if the element is already gone.
  const release = () => {
    if (el._pressPointerId == null) return
    el._pressPointerId = null
    document.removeEventListener('scroll', el._pressScrollHandler, true)
    if (el._pressReaches) {
      window.clearTimeout(el._pressReachTimer)
      el._pressReachTimer = window.setTimeout(dropReach, PRESS_REACH_LINGER_MS)
    }
    const remaining = PRESS_MIN_VISIBLE_MS - (performance.now() - el._pressStart)
    if (remaining <= 0) {
      el.classList.remove('pressed')
    } else {
      el._pressReleaseTimer = window.setTimeout(() => el.classList.remove('pressed'), remaining)
    }
  }

  // Any scroller moving during the press means the gesture is a scroll (or the
  // tail of a fling under the finger): drop the visual at once. Scroll events
  // don't bubble, hence the capture phase on document.
  el._pressScrollHandler = () => {
    release()
    dropReach()
    el.classList.remove('pressed')
  }

  el._pressHandler = (e) => {
    if (el.disabled || !e.isPrimary) return
    // A quick second tap must not lose its state to the first one's timer.
    window.clearTimeout(el._pressReleaseTimer)
    el._pressPointerId = e.pointerId
    el._pressStart = performance.now()
    el.classList.add('pressed')
    if (el._pressReaches) {
      window.clearTimeout(el._pressReachTimer)
      el.classList.add('press-reach')
    }
    document.addEventListener('scroll', el._pressScrollHandler, { capture: true, passive: true })
  }

  el._pressEndHandler = (e) => {
    if (e.pointerId !== el._pressPointerId) return
    release()
    // The browser took the gesture (a scroll): no click is coming.
    if (e.type === 'pointercancel') dropReach()
  }

  el.addEventListener('pointerdown', el._pressHandler, { passive: true })
  el.addEventListener('pointerup', el._pressEndHandler, { passive: true })
  el.addEventListener('pointercancel', el._pressEndHandler, { passive: true })
  // A mouse leaving the element releases it, as :active does.
  el.addEventListener('pointerleave', el._pressEndHandler, { passive: true })
  // The click has landed: the reach has done its work.
  el.addEventListener('click', dropReach, { passive: true })
}

function cleanupPress(el) {
  if (el._pressObserver) {
    el._pressObserver.disconnect()
    delete el._pressObserver
  }
  delete el._pressTarget
  if (el._pressHandler) {
    el.removeEventListener('pointerdown', el._pressHandler)
    el.removeEventListener('pointerup', el._pressEndHandler)
    el.removeEventListener('pointercancel', el._pressEndHandler)
    el.removeEventListener('pointerleave', el._pressEndHandler)
    el.removeEventListener('click', el._pressDropReach)
    document.removeEventListener('scroll', el._pressScrollHandler, true)
    window.clearTimeout(el._pressReleaseTimer)
    window.clearTimeout(el._pressReachTimer)
    delete el._pressHandler
    delete el._pressEndHandler
    delete el._pressScrollHandler
    delete el._pressPointerId
    delete el._pressStart
    delete el._pressReleaseTimer
    delete el._pressReachTimer
    delete el._pressDropReach
  }
  delete el._pressReaches
  el.classList.remove('interactive-press', 'press-media', 'pressed', 'press-reach')
  el.style.removeProperty('--press-scale')
  el.style.removeProperty('--press-reach')
}

export const vPress = {
  mounted(el, binding) {
    if (binding.value === false) return
    setupPress(el, binding.modifiers)
  },

  updated(el, binding) {
    const wasActive = !!el._pressHandler
    const shouldBeActive = binding.value !== false

    if (wasActive && !shouldBeActive) {
      cleanupPress(el)
    } else if (!wasActive && shouldBeActive) {
      setupPress(el, binding.modifiers)
    } else if (wasActive && shouldBeActive) {
      // Re-apply class if Vue's :class binding removed it during re-render
      if (!el.classList.contains('interactive-press')) {
        el.classList.add('interactive-press')
      }
      if (binding.modifiers.media) setupMedia(el)
    }
  },

  unmounted(el) {
    cleanupPress(el)
  }
}
