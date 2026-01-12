// Directive v-press for visual press feedback (150ms minimum)
// Uses pixel-based shrinking for consistent visual effect across all element sizes
// Usage:
//   <button v-press>             → standard press (4px shrink)
//   <button v-press="condition"> → conditional (active if truthy)

const PRESS_SHRINK_PX = 4

function updateScale(el) {
  const rect = el.getBoundingClientRect()
  const avgDimension = (rect.width + rect.height) / 2

  // Prevent extreme scaling on tiny elements
  if (avgDimension < 16) {
    el.style.setProperty('--press-scale', '0.95')
    return
  }

  // Calculate scale: (size - shrinkPx) / size
  const scale = (avgDimension - PRESS_SHRINK_PX) / avgDimension

  // Clamp to reasonable range (0.85 to 0.98)
  const clampedScale = Math.max(0.85, Math.min(0.98, scale))

  el.style.setProperty('--press-scale', clampedScale.toFixed(4))
}

function setupPress(el) {
  // Initial scale calculation
  updateScale(el)

  // Observe size changes
  const observer = new ResizeObserver(() => updateScale(el))
  observer.observe(el)
  el._pressObserver = observer

  el.classList.add('interactive-press')

  el._pressHandler = (e) => {
    if (el.disabled) return
    // Capture pointer to ensure click fires even after scale transform shrinks hit area
    el.setPointerCapture(e.pointerId)
    el.classList.add('pressed')
    setTimeout(() => el.classList.remove('pressed'), 150)
  }

  el.addEventListener('pointerdown', el._pressHandler, { passive: true })
}

function cleanupPress(el) {
  if (el._pressObserver) {
    el._pressObserver.disconnect()
    delete el._pressObserver
  }
  if (el._pressHandler) {
    el.removeEventListener('pointerdown', el._pressHandler)
    delete el._pressHandler
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
