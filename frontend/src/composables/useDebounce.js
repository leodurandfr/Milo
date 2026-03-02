import { onBeforeUnmount } from 'vue'

/**
 * Returns a debounced version of the given function.
 * Automatically clears the pending timer on component unmount.
 *
 * @param {Function} fn - The function to debounce
 * @param {number} delay - Delay in milliseconds (default 400)
 * @returns {{ debounced: Function, cancel: Function }}
 */
export function useDebounce(fn, delay = 400) {
  let timer = null

  function debounced(...args) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      fn(...args)
    }, delay)
  }

  function cancel() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  onBeforeUnmount(cancel)

  return { debounced, cancel }
}
