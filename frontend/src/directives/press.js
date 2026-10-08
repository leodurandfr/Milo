// Directive v-press for visual press feedback (150ms minimum)
// Uses pixel-based shrinking for consistent visual effect across all element sizes
// Usage:
//   <button v-press>             → standard press (4px shrink)
//   <button v-press="condition"> → conditional (active if truthy)
//
// Purely visual: activation is the browser's own click, so a scroll, a fling or
// the tap that stops one never activates anything the native rules would not.

const PRESS_SHRINK_PX = 4
const PRESS_MIN_VISIBLE_MS = 150

function updateScale(el) {
  const rect = el.getBoundingClientRect()
  const avgDimension = (rect.width + rect.height) / 2

  // Prevent extreme scaling on tiny elements
  if (avgDimension < 16) {
    el.style.setProperty('--press-scale', '0.95')
    return
  }

  const scale = (avgDimension - PRESS_SHRINK_PX) / avgDimension

  // Clamp to reasonable range (0.85 to 0.98)
  const clampedScale = Math.max(0.85, Math.min(0.98, scale))

  el.style.setProperty('--press-scale', clampedScale.toFixed(4))
}

function setupPress(el) {
  updateScale(el)

  const observer = new ResizeObserver(() => updateScale(el))
  observer.observe(el)
  el._pressObserver = observer

  el.classList.add('interactive-press')

  // Held until release, with a minimum so quick taps still show feedback.
  // Raw window timer (window.* prefix): a directive has no component lifecycle,
  // so useTimer() can't be used here. Fire-and-forget CSS-class removal,
  // harmless if the element is already gone.
  const release = () => {
    if (el._pressPointerId == null) return
    el._pressPointerId = null
    document.removeEventListener('scroll', el._pressScrollHandler, true)
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
    el.classList.remove('pressed')
  }

  el._pressHandler = (e) => {
    if (el.disabled || !e.isPrimary) return
    // A quick second tap must not lose its state to the first one's timer.
    window.clearTimeout(el._pressReleaseTimer)
    el._pressPointerId = e.pointerId
    el._pressStart = performance.now()
    el.classList.add('pressed')
    document.addEventListener('scroll', el._pressScrollHandler, { capture: true, passive: true })
  }

  el._pressEndHandler = (e) => {
    if (e.pointerId === el._pressPointerId) release()
  }

  el.addEventListener('pointerdown', el._pressHandler, { passive: true })
  el.addEventListener('pointerup', el._pressEndHandler, { passive: true })
  el.addEventListener('pointercancel', el._pressEndHandler, { passive: true })
  // A mouse leaving the element releases it, as :active does.
  el.addEventListener('pointerleave', el._pressEndHandler, { passive: true })
}

function cleanupPress(el) {
  if (el._pressObserver) {
    el._pressObserver.disconnect()
    delete el._pressObserver
  }
  if (el._pressHandler) {
    el.removeEventListener('pointerdown', el._pressHandler)
    el.removeEventListener('pointerup', el._pressEndHandler)
    el.removeEventListener('pointercancel', el._pressEndHandler)
    el.removeEventListener('pointerleave', el._pressEndHandler)
    document.removeEventListener('scroll', el._pressScrollHandler, true)
    window.clearTimeout(el._pressReleaseTimer)
    delete el._pressHandler
    delete el._pressEndHandler
    delete el._pressScrollHandler
    delete el._pressPointerId
    delete el._pressStart
    delete el._pressReleaseTimer
  }
  el.classList.remove('interactive-press', 'pressed')
  el.style.removeProperty('--press-scale')
}

export const vPress = {
  mounted(el, binding) {
    if (binding.value === false) return
    setupPress(el)
  },

  updated(el, binding) {
    const wasActive = !!el._pressHandler
    const shouldBeActive = binding.value !== false

    if (wasActive && !shouldBeActive) {
      cleanupPress(el)
    } else if (!wasActive && shouldBeActive) {
      setupPress(el)
    } else if (wasActive && shouldBeActive) {
      // Re-apply class if Vue's :class binding removed it during re-render
      if (!el.classList.contains('interactive-press')) {
        el.classList.add('interactive-press')
      }
    }
  },

  unmounted(el) {
    cleanupPress(el)
  }
}
