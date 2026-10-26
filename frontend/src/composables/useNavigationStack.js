import { ref, computed, provide, nextTick, onActivated, onDeactivated, onMounted, onBeforeUnmount, defineComponent, markRaw, toRaw, h } from 'vue';

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
// `deferred`: the page is navigated to, so its content mounts a few frames late
// (shellOf). The initial view never waits: it mounts with the source, is gone
// back to, or is what a reset (a profile picked, a storage lost) starts over on.
let lastEntryId = 0;
const entry = (view, params = {}, deferred = true) =>
  ({ id: `entry-${++lastEntryId}`, view, params, scrollTop: 0, state: {}, deferred });

// Set on a page's element while its content has not mounted yet.
const PENDING = 'data-page-pending';

// Frames a navigated-to page waits before mounting its content. Vue's
// <Transition> starts the leaving page's fade at its second frame (nextFrame),
// and a CSS opacity/transform transition runs on the compositor once started:
// mounting after it lets the leave play while the main thread builds the page.
// Measured on the panel (warm opens, first flip after the tap): a 50-row
// playlist 231 -> 45 ms, an artist 154 -> 51 ms. At two frames the mount landed
// on the frame the leave starts on, and held it.
const DEFER_FRAMES = 3;

// What waits for a pending page's content (whenPageReady), by its element.
const waiting = new WeakMap();

// One component per entry, named after its id, rendering its default slot for
// that entry alone: KeepAlive's `include` matches component names, so the
// stack's ids are what it keeps, and a popped entry's page unmounts (its loads
// aborted, its watchers gone) instead of waiting in the cache. Its own element
// (.nav-page) is what the page transition moves.
const shells = new WeakMap();
function shellOf(e) {
  const raw = toRaw(e);
  if (!shells.has(raw)) {
    shells.set(raw, markRaw(defineComponent({
      name: raw.id,
      setup(_, { slots }) {
        // Only the entry's first mount waits: one evicted from the cache and
        // gone back to is a back navigation, whose scroll restore needs the
        // page whole.
        const ready = ref(!raw.deferred);
        raw.deferred = false;
        const root = ref(null);
        let frame = null;
        const stop = () => {
          if (frame) cancelAnimationFrame(frame);
          frame = null;
        };
        const show = async () => {
          stop();
          ready.value = true;
          await nextTick();
          const callbacks = waiting.get(root.value) ?? [];
          waiting.delete(root.value);
          callbacks.forEach((fn) => fn());
        };
        const wait = (frames) => {
          frame = requestAnimationFrame(() => (frames > 1 ? wait(frames - 1) : show()));
        };
        if (!ready.value) {
          onMounted(() => wait(DEFER_FRAMES));
          // Left before its content mounted (a tap, then back at once): it
          // mounts nothing while out of sight, and at once if gone back to —
          // where what waited for it belongs to the navigation that left it.
          onDeactivated(() => {
            stop();
            waiting.delete(root.value);
          });
          onActivated(() => {
            if (!ready.value && !frame) show();
          });
          onBeforeUnmount(stop);
        }
        return () => h('div', { ref: root, class: 'nav-page', [PENDING]: ready.value ? null : '' },
          ready.value ? slots.default?.({ entry: e }) : []);
      },
    })));
  }
  return shells.get(raw);
}

/**
 * Runs `fn` once `el`, a page's element, holds its content: at once for any
 * element but a navigated-to page still waiting to mount it (shellOf), else
 * right after it has. For what measures the destination of a navigation.
 *
 * @returns {boolean} true when `fn` waits for the page
 */
export function whenPageReady(el, fn) {
  if (!el?.hasAttribute?.(PENDING)) {
    fn();
    return false;
  }
  if (!waiting.has(el)) waiting.set(el, []);
  waiting.get(el).push(fn);
  return true;
}

/**
 * Runs `fn` when a page the cache kept is shown again (going back to it), never
 * on its first mount — for a page whose data can have moved while it was covered.
 * Keyed on the page having been left, not on a first activation: a navigated-to
 * page mounts its content after its shell was activated, so it hears no
 * activation until it is gone back to.
 */
export function onPageReturn(fn) {
  let left = false;
  onDeactivated(() => {
    left = true;
  });
  onActivated(() => {
    if (left) fn();
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
 * @param {(params: Object) => *} [options.pageId] - What tells two pages of one view
 *   apart, from their params. Defaults to the params whole; a source whose params
 *   also carry what a page shows before it loads (a name, a cover) names its ids.
 * @returns {Object} Navigation state and methods
 */
export function useNavigationStack(initialView = 'home', { scrollElRef = null, pageId = (params) => params } = {}) {
  const stack = ref([entry(initialView, {}, false)]);

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

  const samePage = (e, view, params) =>
    e.view === view && JSON.stringify(pageId(e.params)) === JSON.stringify(pageId(params));

  /**
   * Push a new view onto the stack, saving the current scroll position of the leaving view.
   * The page already on screen is not pushed again (its album opened from the
   * player while on it), so back never lands on the page it leaves.
   *
   * @returns {boolean} whether the stack moved
   */
  function push(view, params = {}) {
    const currentEntry = stack.value[stack.value.length - 1];
    if (currentEntry && samePage(currentEntry, view, params)) return false;
    if (currentEntry && scrollElRef?.value) {
      currentEntry.scrollTop = scrollElRef.value.scrollTop;
    }
    stack.value.push(entry(view, params));
    return true;
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
    stack.value = [entry(initialView, {}, false)];
    pendingScrollRestore.value = null;
  }

  /**
   * Navigate directly to a view (with home in history).
   * Creates a stack: [home, targetView]
   */
  function goTo(view, params = {}) {
    depart(stack.value);
    stack.value = [entry(initialView, {}, false), entry(view, params)];
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
