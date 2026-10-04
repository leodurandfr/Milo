import { ref, computed, provide } from 'vue';

/**
 * Injection key: a getter for the current entry's `state`, an object the view
 * shown for that entry may keep its own things in (how many rows of a long list
 * are mounted — `useRenderWindow`). Read once, in the view's setup, it is the
 * view's own entry for good; going back remounts the view onto the same object.
 */
export const NAVIGATION_ENTRY_STATE = Symbol('navigationEntryState');

const entry = (view, params = {}) => ({ view, params, scrollTop: 0, state: {} });

/**
 * Composable for managing navigation stack within modals/views.
 * Enables proper back navigation, direct navigation to sub-views,
 * and optional scroll position save/restore across navigations.
 *
 * @param {string} initialView - The default/home view name
 * @param {Object} [options] - Optional configuration
 * @param {import('vue').Ref<HTMLElement|null>} [options.scrollElRef] - Scroll container ref.
 *   When provided, scrollTop is captured on push() and signalled for restore on back().
 * @returns {Object} Navigation state and methods
 */
export function useNavigationStack(initialView = 'home', { scrollElRef = null } = {}) {
  const stack = ref([entry(initialView)]);

  const currentView = computed(
    () => stack.value[stack.value.length - 1]?.view || initialView
  );
  const currentParams = computed(
    () => stack.value[stack.value.length - 1]?.params || {}
  );
  const canGoBack = computed(() => stack.value.length > 1);

  provide(NAVIGATION_ENTRY_STATE, () => stack.value[stack.value.length - 1].state);

  /**
   * Pending scroll position to restore after the next entering transition completes.
   * Set by back() when the destination entry has a saved scrollTop > 0.
   * Null means no restore needed (forward nav or back to top-positioned view).
   * Must be cleared by the consumer after applying.
   */
  const pendingScrollRestore = ref(null);

  /**
   * Push a new view onto the stack, saving the current scroll position of the leaving view.
   */
  function push(view, params = {}) {
    const currentEntry = stack.value[stack.value.length - 1];
    if (currentEntry && scrollElRef?.value) {
      currentEntry.scrollTop = scrollElRef.value.scrollTop;
    }
    stack.value.push(entry(view, params));
  }

  /**
   * Go back to the previous view, signalling the saved scroll position for restoration.
   */
  function back() {
    if (stack.value.length > 1) {
      stack.value.pop();
      const restoredEntry = stack.value[stack.value.length - 1];
      const savedScroll = restoredEntry?.scrollTop ?? 0;
      pendingScrollRestore.value = savedScroll > 0 ? savedScroll : null;
    }
  }

  /**
   * Reset to initial view (clear stack, clear any pending restore signal).
   */
  function reset() {
    stack.value = [entry(initialView)];
    pendingScrollRestore.value = null;
  }

  /**
   * Navigate directly to a view (with home in history).
   * Creates a stack: [home, targetView]
   */
  function goTo(view, params = {}) {
    stack.value = [entry(initialView), entry(view, params)];
    pendingScrollRestore.value = null;
  }

  return {
    currentView,
    currentParams,
    canGoBack,
    pendingScrollRestore,
    push,
    back,
    reset,
    goTo,
  };
}
