// Directive v-press pour feedback visuel au clic (150ms minimum)
// Usage:
//   <button v-press.light>       → press léger (scale 0.97) - cards, inputs
//   <button v-press>             → press standard (scale 0.92) - buttons
//   <button v-press.strong>      → press fort (scale 0.88) - dock, playback, toggles
//   <button v-press="condition"> → conditionnel (actif si truthy)

function setupPress(el, binding) {
  const baseClass = binding.modifiers.strong
    ? 'interactive-press-strong'
    : binding.modifiers.light
      ? 'interactive-press-light'
      : 'interactive-press'

  el.classList.add(baseClass)
  el._pressBaseClass = baseClass

  el._pressHandler = () => {
    if (el.disabled) return
    el.classList.add('pressed')
    setTimeout(() => el.classList.remove('pressed'), 150)
  }

  el.addEventListener('pointerdown', el._pressHandler, { passive: true })
}

function cleanupPress(el) {
  if (el._pressHandler) {
    el.removeEventListener('pointerdown', el._pressHandler)
    delete el._pressHandler
  }
  if (el._pressBaseClass) {
    el.classList.remove(el._pressBaseClass, 'pressed')
    delete el._pressBaseClass
  }
}

export const vPress = {
  mounted(el, binding) {
    if (binding.value === false) return
    setupPress(el, binding)
  },

  updated(el, binding) {
    const wasActive = !!el._pressHandler
    const shouldBeActive = binding.value !== false

    if (wasActive && !shouldBeActive) {
      cleanupPress(el)
    } else if (!wasActive && shouldBeActive) {
      setupPress(el, binding)
    } else if (wasActive && shouldBeActive && el._pressBaseClass) {
      // Re-apply base class if Vue's :class binding removed it during re-render
      if (!el.classList.contains(el._pressBaseClass)) {
        el.classList.add(el._pressBaseClass)
      }
    }
  },

  unmounted(el) {
    cleanupPress(el)
  }
}
