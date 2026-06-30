// Directive v-press for visual press feedback (150ms minimum)
// Uses pixel-based shrinking for consistent visual effect across all element sizes
// Usage:
//   <button v-press>             → standard press (4px shrink)
//   <button v-press="condition"> → conditional (active if truthy)

const PRESS_SHRINK_PX = 4

// Past this travel, the gesture is a scroll, not a press. Lower on the vertical
// axis since our scrollers pan vertically — cancel the press before a small
// downward drift becomes a synthesized click.
const PRESS_MOVE_CANCEL_PX = 10
const PRESS_MOVE_CANCEL_CROSS_PX = 7

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

  el._pressHandler = (e) => {
    if (el.disabled) return
    // Capture pointer to ensure click fires even after scale transform shrinks hit area
    el.setPointerCapture(e.pointerId)
    el._pressPointerId = e.pointerId
    el._pressStartX = e.clientX
    el._pressStartY = e.clientY
    el._pressStart = performance.now()
    el.classList.add('pressed')
  }

  // Held until release, with a 150ms minimum so quick taps still show feedback.
  // Raw window timer (window.* prefix): a directive has no component lifecycle,
  // so useTimer() can't be used here. Fire-and-forget CSS-class removal,
  // harmless if the element is already gone.
  const releasePressed = () => {
    const remaining = 150 - (performance.now() - el._pressStart)
    if (remaining <= 0) {
      el.classList.remove('pressed')
    } else {
      window.setTimeout(() => el.classList.remove('pressed'), remaining)
    }
  }

  el._pressClickHandler = () => {
    el._pressNativeClick = true
  }

  // On touch, the browser suppresses the native click when the finger is
  // released outside the element, even with pointer capture. A press started
  // on the button should still activate it, so replay the click ourselves —
  // unless a pointercancel fired (scroll took over the gesture).
  el._pressUpHandler = (e) => {
    if (e.pointerId !== el._pressPointerId) return
    el._pressPointerId = null
    releasePressed()
    const rect = el.getBoundingClientRect()
    const inside = e.clientX >= rect.left && e.clientX <= rect.right
      && e.clientY >= rect.top && e.clientY <= rect.bottom
    if (inside) return // native click will fire
    el._pressNativeClick = false
    // Browsers where click follows pointer capture fire it right after
    // pointerup, before this timeout — only synthesize if it never came.
    window.setTimeout(() => {
      if (!el._pressNativeClick && !el.disabled) el.click()
    }, 0)
  }

  el._pressCancelHandler = (e) => {
    if (e.pointerId !== el._pressPointerId) return
    el._pressPointerId = null
    releasePressed()
  }

  // A captured touch pointer doesn't reliably fire pointercancel when a scroll
  // starts, so detect the scroll ourselves: once the finger travels past the
  // threshold, drop the press (no synthesized click) and release the pointer.
  el._pressMoveHandler = (e) => {
    if (e.pointerId !== el._pressPointerId) return
    if (Math.abs(e.clientX - el._pressStartX) <= PRESS_MOVE_CANCEL_PX
      && Math.abs(e.clientY - el._pressStartY) <= PRESS_MOVE_CANCEL_CROSS_PX) return
    el._pressPointerId = null
    if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId)
    releasePressed()
  }

  el.addEventListener('pointerdown', el._pressHandler, { passive: true })
  el.addEventListener('pointermove', el._pressMoveHandler, { passive: true })
  el.addEventListener('pointerup', el._pressUpHandler, { passive: true })
  el.addEventListener('pointercancel', el._pressCancelHandler, { passive: true })
  el.addEventListener('click', el._pressClickHandler, { passive: true })
}

function cleanupPress(el) {
  if (el._pressObserver) {
    el._pressObserver.disconnect()
    delete el._pressObserver
  }
  if (el._pressHandler) {
    el.removeEventListener('pointerdown', el._pressHandler)
    el.removeEventListener('pointermove', el._pressMoveHandler)
    el.removeEventListener('pointerup', el._pressUpHandler)
    el.removeEventListener('pointercancel', el._pressCancelHandler)
    el.removeEventListener('click', el._pressClickHandler)
    delete el._pressHandler
    delete el._pressMoveHandler
    delete el._pressUpHandler
    delete el._pressCancelHandler
    delete el._pressClickHandler
    delete el._pressPointerId
    delete el._pressNativeClick
    delete el._pressStart
    delete el._pressStartX
    delete el._pressStartY
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
