import { ref, computed } from 'vue'

/**
 * Composable for managing navigation stack within modals/views
 * Enables proper back navigation and direct navigation to sub-views
 *
 * @param {string} initialView - The default/home view name
 * @returns {Object} Navigation state and methods
 */
export function useNavigationStack(initialView = 'home') {
  const stack = ref([{ view: initialView, params: {} }])

  const currentView = computed(() => stack.value[stack.value.length - 1]?.view || initialView)
  const currentParams = computed(() => stack.value[stack.value.length - 1]?.params || {})
  const canGoBack = computed(() => stack.value.length > 1)

  /**
   * Push a new view onto the stack
   */
  function push(view, params = {}) {
    stack.value.push({ view, params })
  }

  /**
   * Go back to the previous view
   */
  function back() {
    if (stack.value.length > 1) {
      stack.value.pop()
    }
  }

  /**
   * Reset to initial view (clear stack)
   */
  function reset() {
    stack.value = [{ view: initialView, params: {} }]
  }

  /**
   * Navigate directly to a view (with home in history)
   * Creates a stack: [home, targetView]
   */
  function goTo(view, params = {}) {
    stack.value = [{ view: initialView, params: {} }, { view, params }]
  }

  /**
   * Replace current view without adding to history
   */
  function replace(view, params = {}) {
    if (stack.value.length > 0) {
      stack.value[stack.value.length - 1] = { view, params }
    } else {
      stack.value = [{ view, params }]
    }
  }

  return {
    currentView,
    currentParams,
    canGoBack,
    push,
    back,
    reset,
    goTo,
    replace
  }
}
