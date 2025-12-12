// Directive v-press pour feedback visuel au clic (150ms minimum)
// Usage:
//   <button v-press>             → press subtil (scale 0.97)
//   <button v-press.strong>      → press fort (scale 0.92)
//   <button v-press="condition"> → conditionnel (actif si truthy)

function setupPress(el, binding) {
  const baseClass = binding.modifiers.strong
    ? 'interactive-press-strong'
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
    el.classList.remove(el._pressBaseClass)
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
    }
  },

  unmounted(el) {
    cleanupPress(el)
  }
}
