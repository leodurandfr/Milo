/**
 * Auto-cleaning timer composable.
 *
 * Wraps setTimeout/setInterval with a registry that clears every active
 * handle on component unmount. Use this everywhere a timer is created
 * inside a component or another composable.
 *
 * Note: when called from a composable that is itself called from a
 * component, `onUnmounted` registers on the component host — same
 * semantics as any other composable. Don't call this outside a component
 * lifecycle (e.g. from a plain module init) — the handles would never
 * be cleared.
 *
 * Usage:
 *   const timer = useTimer();
 *   const handle = timer.setTimeout(() => { ... }, 600);
 *   const interval = timer.setInterval(() => { ... }, 1000);
 *   timer.clear(handle);     // optional, manual clear
 *   timer.clearAll();        // optional, manual mass-clear
 *   // onUnmounted: automatic clearAll()
 */
import { onUnmounted } from 'vue';

export function useTimer() {
  const timeouts = new Set();
  const intervals = new Set();

  function setTimeoutInternal(fn, delay) {
    const id = window.setTimeout(() => {
      timeouts.delete(id);
      fn();
    }, delay);
    timeouts.add(id);
    return id;
  }

  function setIntervalInternal(fn, delay) {
    const id = window.setInterval(fn, delay);
    intervals.add(id);
    return id;
  }

  function clear(id) {
    if (timeouts.has(id)) {
      window.clearTimeout(id);
      timeouts.delete(id);
    }
    if (intervals.has(id)) {
      window.clearInterval(id);
      intervals.delete(id);
    }
  }

  function clearAll() {
    timeouts.forEach((id) => window.clearTimeout(id));
    intervals.forEach((id) => window.clearInterval(id));
    timeouts.clear();
    intervals.clear();
  }

  onUnmounted(clearAll);

  return {
    setTimeout: setTimeoutInternal,
    setInterval: setIntervalInternal,
    clear,
    clearAll,
  };
}
