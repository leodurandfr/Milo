import { ref, computed, provide, onActivated, defineComponent, markRaw, toRaw } from 'vue';

/**
 * Injection key: a getter for the current entry's `state`, an object the view
 * shown for that entry may keep its own things in (how many rows of a long list
 * are mounted — `useRenderWindow`). Read once, in the view's setup, it is the
 * view's own entry for good; a view evicted from the page cache and gone back
 * to remounts onto the same object.
 */
export const NAVIGATION_ENTRY_STATE = Symbol('navigationEntryState');

// Never reused: a page is cached under its entry's id, so an entry popped and
// a new one pushed for the same view (album A, then album B) never share one.
let lastEntryId = 0;
const entry = (view, params = {}) => ({ id: `entry-${++lastEntryId}`, view, params, scrollTop: 0, state: {} });

// One component per entry, named after its id, rendering its default slot for
// that entry alone: KeepAlive's `include` matches component names, so the
// stack's ids are what it keeps, and a popped entry's page unmounts (its loads
// aborted, its watchers gone) instead of waiting in the cache.
const shells = new WeakMap();
function shellOf(e) {
  const raw = toRaw(e);
  if (!shells.has(raw)) {
    shells.set(raw, markRaw(defineComponent({
      name: raw.id,
      setup(_, { slots }) {
        return () => slots.default?.({ entry: e })[0];
      },
    })));
  }
  return shells.get(raw);
}

/**
 * Runs `fn` when a page the cache kept is shown again (going back to it), never
 * on its first mount — for a page whose data can have moved while it was covered.
 */
export function onPageReturn(fn) {
  let mounted = false;
  onActivated(() => {
    if (mounted) fn();
    mounted = true;
  });
}

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
  // The current entry's identity: the content key, and the key its page is
  // cached under.
  const currentKey = computed(() => stack.value[stack.value.length - 1].id);
  // The page that renders it: <component :is="currentPage" v-slot="{ entry }">.
  const currentPage = computed(() => shellOf(stack.value[stack.value.length - 1]));

  // Popped entries, kept until the pages settle (pagesSettled): unmounting a
  // page mid-leave cuts its leave short. Whatever is in here goes at the next
  // settle, rendered or not, so nothing outlives one transition.
  const departed = ref(new Set());
  // KeepAlive's `include`: what may stay mounted.
  const keptKeys = computed(() => [...stack.value.map((e) => e.id), ...departed.value]);

  // Only a page drawn through its shell is kept (Settings' stack keeps none,
  // and never settles).
  function depart(entries) {
    for (const e of entries) {
      if (shells.has(toRaw(e))) departed.value.add(e.id);
    }
  }

  /** No page is leaving any more: the popped ones may unmount. */
  function pagesSettled() {
    departed.value.clear();
  }

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
      depart([stack.value.pop()]);
      const restoredEntry = stack.value[stack.value.length - 1];
      const savedScroll = restoredEntry?.scrollTop ?? 0;
      pendingScrollRestore.value = savedScroll > 0 ? savedScroll : null;
    }
  }

  /**
   * Reset to initial view (clear stack, clear any pending restore signal).
   */
  function reset() {
    depart(stack.value);
    stack.value = [entry(initialView)];
    pendingScrollRestore.value = null;
  }

  /**
   * Navigate directly to a view (with home in history).
   * Creates a stack: [home, targetView]
   */
  function goTo(view, params = {}) {
    depart(stack.value);
    stack.value = [entry(initialView), entry(view, params)];
    pendingScrollRestore.value = null;
  }

  return {
    currentView,
    currentParams,
    currentKey,
    currentPage,
    keptKeys,
    pagesSettled,
    canGoBack,
    pendingScrollRestore,
    push,
    back,
    reset,
    goTo,
  };
}
