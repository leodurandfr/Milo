// frontend/src/composables/useDebouncedAutosave.js
import { onBeforeUnmount } from 'vue';
import { useTimer } from '@/composables/useTimer';

/**
 * Debounced auto-save for a form that saves as the user edits.
 * A save still pending on unmount is flushed (sent immediately), never
 * dropped — leaving the screen must not lose the last edit. That is the
 * difference with useDebounce, which cancels on unmount and suits a search.
 *
 * @param {() => Promise<unknown>} save - Must read nothing that dies with the
 *   component, since a flush on unmount runs it after the screen is gone
 * @param {number} delayMs
 * @returns {{ trigger: Function, flush: Function, cancel: Function, isPending: Function }}
 */
export function useDebouncedAutosave(save, delayMs) {
  const timer = useTimer();
  let handle = null;

  function cancel() {
    if (handle !== null) {
      timer.clear(handle);
      handle = null;
    }
  }

  function flush() {
    cancel();
    return save();
  }

  function trigger() {
    cancel();
    handle = timer.setTimeout(() => {
      handle = null;
      save();
    }, delayMs);
  }

  const isPending = () => handle !== null;

  // Runs before useTimer's onUnmounted(clearAll), which would cancel the save.
  onBeforeUnmount(() => {
    if (isPending()) flush();
  });

  return { trigger, flush, cancel, isPending };
}
