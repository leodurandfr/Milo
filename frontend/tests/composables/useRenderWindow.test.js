// frontend/tests/composables/useRenderWindow.test.js
/**
 * useRenderWindow mounts a long track list (Spotify's Liked Songs, a Navidrome
 * playlist, a genre, the queue) a chunk at a time. What breaks, silently:
 *
 * - the window never grows, or grows past the list — rows missing at the end
 *   of a long playlist, with no error;
 * - going back to a list remounts it with only its first chunk, so the scroll
 *   position the navigation stack restores is clamped to that chunk's end and
 *   the place in the list is lost — including when the same list was opened a
 *   second time further down the stack (an artist → its album → that artist);
 * - the observer watches the viewport instead of the box the list scrolls in,
 *   where the look-ahead margin reaches nothing and the next rows only mount
 *   once the end is already on screen.
 *
 * Host components are mounted only to give the composables a lifecycle and a
 * sentinel element; nothing is asserted on the DOM.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useRenderWindow, RENDER_CHUNK } from '@/composables/useRenderWindow';
import { useNavigationStack } from '@/composables/useNavigationStack';

const observers = [];

class FakeIntersectionObserver {
  constructor(callback, options) {
    this.callback = callback;
    this.options = options;
    this.target = null;
    observers.push(this);
  }
  observe(target) { this.target = target; }
  unobserve() { this.target = null; }
  disconnect() { this.target = null; }
}

/** The live observer reports its sentinel in reach, as a scroll would. */
async function scrollToEnd() {
  const live = observers.filter((o) => o.target);
  live[live.length - 1].callback([{ isIntersecting: true }]);
  await nextTick();
  await nextTick();
}

const tracks = (n) => Array.from({ length: n }, (_, i) => ({ uri: `spotify:track:${i}` }));

/** The list view: the window over `items`, its sentinel rendered while there is more. */
function listView(items, sink) {
  return defineComponent({
    setup() {
      const api = useRenderWindow(items);
      sink.api = api;
      return () => h('div', [api.hasMore.value ? h('div', { ref: api.sentinelRef }) : null]);
    },
  });
}

/** A source holding a navigation stack, remounting its list view per entry. */
function mountSource(items) {
  const sink = {};
  const List = listView(items, sink);
  const view = ref(0);
  const Source = defineComponent({
    setup() {
      sink.nav = useNavigationStack('list');
      return () => h(List, { key: view.value });
    },
  });
  const wrapper = mount(Source, { attachTo: document.body });
  const remount = async () => { view.value += 1; await nextTick(); await nextTick(); };
  return { sink, wrapper, remount };
}

let originalObserver;

beforeEach(() => {
  observers.length = 0;
  originalObserver = globalThis.IntersectionObserver;
  globalThis.IntersectionObserver = FakeIntersectionObserver;
});

afterEach(() => {
  globalThis.IntersectionObserver = originalObserver;
  document.body.innerHTML = '';
});

describe('useRenderWindow', () => {
  it('mounts a chunk at a time, up to the last row and never past it', async () => {
    const total = RENDER_CHUNK * 2 + 7;
    const { sink, wrapper } = mountSource(tracks(total));
    await nextTick();

    expect(sink.api.visible.value).toHaveLength(RENDER_CHUNK);
    await scrollToEnd();
    expect(sink.api.visible.value).toHaveLength(RENDER_CHUNK * 2);
    await scrollToEnd();
    expect(sink.api.visible.value).toHaveLength(total);
    expect(sink.api.hasMore.value).toBe(false);
    expect(sink.api.visible.value.at(-1).uri).toBe(`spotify:track:${total - 1}`);
    wrapper.unmount();
  });

  it('brings back the rows a list had when going back to it, and only to that entry', async () => {
    const { sink, wrapper, remount } = mountSource(tracks(RENDER_CHUNK * 5));
    await nextTick();
    await scrollToEnd();
    await scrollToEnd();
    const reached = sink.api.visible.value.length;
    expect(reached).toBeGreaterThan(RENDER_CHUNK);

    // The same list opened again further down the stack starts from its first rows…
    sink.nav.push('list');
    await remount();
    expect(sink.api.visible.value).toHaveLength(RENDER_CHUNK);

    // …and going back finds the first entry's rows, not the second's.
    sink.nav.back();
    await remount();
    expect(sink.api.visible.value).toHaveLength(reached);
    wrapper.unmount();
  });

  it('watches the sentinel against the box the list scrolls in, past one that only clips', async () => {
    const box = (overflow, scrollHeight) => {
      const el = document.createElement('div');
      el.style.cssText = overflow;
      Object.defineProperty(el, 'scrollHeight', { value: scrollHeight });
      Object.defineProperty(el, 'clientHeight', { value: 800 });
      return el;
    };
    const scroller = box('overflow-y: auto', 4000);
    // overflow-x alone computes overflow-y to auto: a box as tall as its content.
    const clip = box('overflow-x: hidden; overflow-y: auto', 800);
    scroller.appendChild(clip);
    document.body.appendChild(scroller);
    const sink = {};
    const wrapper = mount(listView(tracks(RENDER_CHUNK + 1), sink), { attachTo: clip });
    await nextTick();

    const live = observers.filter((o) => o.target);
    expect(live).toHaveLength(1);
    expect(live[0].options.root).toBe(scroller);
    wrapper.unmount();
  });
});
