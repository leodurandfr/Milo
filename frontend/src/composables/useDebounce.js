import { onBeforeUnmount } from 'vue';

/**
 * Returns a debounced version of the given function.
 * Automatically clears the pending timer on component unmount.
 *
 * Timer-primitive layer: like useTimer, this composable manages its own
 * cleanup, so it uses raw window.* timers directly (the window.* prefix marks
 * the deliberate raw usage — see the no-restricted-globals rule in
 * eslint.config.mjs).
 *
 * @param {Function} fn - The function to debounce
 * @param {number} delay - Delay in milliseconds (default 400)
 * @returns {{ debounced: Function, cancel: Function }}
 */
export function useDebounce(fn, delay = 400) {
  let timer = null;

  function debounced(...args) {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = null;
      fn(...args);
    }, delay);
  }

  function cancel() {
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  onBeforeUnmount(cancel);

  return { debounced, cancel };
}
