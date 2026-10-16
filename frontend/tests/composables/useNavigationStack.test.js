// frontend/tests/composables/useNavigationStack.test.js
/**
 * useNavigationStack decides which pages of a source's navigation stay mounted:
 * its `keptKeys` is the KeepAlive's `include`, matched against the name of the
 * shell `currentPage` draws each entry through.
 *
 * What breaks if these fail: a page gone back to is built anew instead of shown
 * again (a kept id missing), or a popped page stays mounted with its watchers and
 * loads running, holding a cache slot the stack needs (an id never let go) — or a
 * page is unmounted mid-leave and its leave cut short (let go before the pages
 * settled).
 *
 * The kept-pages tests mount a host only for `provide`; reading `currentPage`
 * stands for the source rendering it. The navigated-to page tests render the
 * shells in a KeepAlive, as a source does, and find a pending page by the
 * attribute whenPageReady keys on; they assert what mounted and what ran, never
 * the markup.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { defineComponent, h, KeepAlive, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { useNavigationStack, onPageReturn, whenPageReady } from '@/composables/useNavigationStack';

function host() {
  let nav;
  mount(defineComponent({
    setup() {
      nav = useNavigationStack('home');
      return () => null;
    },
  }));
  // The source renders the page of every entry that reaches the top.
  const show = () => nav.currentPage.value;
  show();
  return { nav, show };
}

const stackKeys = (nav, ...keys) => expect([...nav.keptKeys.value].sort()).toEqual([...keys].sort());

describe('useNavigationStack — kept pages', () => {
  it('keeps a popped page until the pages settle, then lets it go', () => {
    const { nav, show } = host();
    const home = nav.currentKey.value;
    nav.push('album', { id: 'a' });
    show();
    const album = nav.currentKey.value;

    nav.back();
    stackKeys(nav, home, album);

    nav.pagesSettled();
    stackKeys(nav, home);
  });

  it('lets every entry go at one settle when two navigations land in one tick', () => {
    const { nav, show } = host();
    nav.push('search');
    show();
    nav.push('album', { id: 'a' });
    show();

    // Music Library: back to search, then a storage loss resets out of it.
    nav.back();
    nav.reset();
    show();

    nav.pagesSettled();
    stackKeys(nav, nav.currentKey.value);
  });

  it('keeps nothing for a stack whose pages are not drawn through their shell', () => {
    let nav;
    mount(defineComponent({
      setup() {
        nav = useNavigationStack('home');
        return () => null;
      },
    }));
    nav.push('general');
    nav.back();
    stackKeys(nav, nav.currentKey.value);
  });

  it('gives a new entry for the same view a new key, so its page is never reused', () => {
    const { nav, show } = host();
    nav.push('album', { id: 'a' });
    const first = nav.currentKey.value;
    show();
    nav.back();
    nav.push('album', { id: 'b' });
    expect(nav.currentKey.value).not.toBe(first);
  });

  it('draws an entry through one shell, named after its key', () => {
    const { nav, show } = host();
    nav.push('album', { id: 'a' });
    const shell = show();
    expect(shell.name).toBe(nav.currentKey.value);
    nav.push('artist', { id: 'x' });
    show();
    nav.back();
    expect(show()).toBe(shell);
  });
});

// A navigated-to page mounts its content a few frames after its shell. What
// breaks if these fail: a podcast or library page gone back to shows stale data
// (onPageReturn never fires on its first return), or a page opened and left at
// once builds itself during its own leave — the jank the deferral removes.
//
// A source's #pages, as SpotifySource renders them: one shell per entry in a
// KeepAlive, the page reading what it is given.
const mounted = [];
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount());
});

function source(Page) {
  let nav;
  mounted.push(mount(defineComponent({
    setup() {
      nav = useNavigationStack('home');
      return () => h(KeepAlive, { include: nav.keptKeys.value }, [
        h(nav.currentPage.value, { key: nav.currentKey.value }, { default: ({ entry }) => [h(Page, { view: entry.view })] }),
      ]);
    },
  }), { attachTo: document.body }));
  return nav;
}

// The page element of the entry on top: what AudioSourceLayout's transition hands
// to whenPageReady.
const pageOnTop = () => document.querySelector('[data-page-pending]');

const frames = async (n) => {
  for (let i = 0; i < n; i++) await new Promise((r) => requestAnimationFrame(r));
  await nextTick();
};

describe('useNavigationStack — a navigated-to page', () => {
  it('refreshes on its return only, though its content mounted after the shell was shown', async () => {
    const returns = [];
    const Page = defineComponent({
      props: { view: String },
      setup(props) {
        onPageReturn(() => returns.push(props.view));
        return () => null;
      },
    });
    const nav = source(Page);
    nav.push('album');
    await frames(5);
    expect(returns).toEqual([]);

    nav.push('artist');
    await frames(5);
    nav.back();
    await frames(5);
    expect(returns).toEqual(['album']);
  });

  it('mounts nothing once left before its content mounted, and mounts at once when gone back to', async () => {
    const built = [];
    const Page = defineComponent({
      props: { view: String },
      setup(props) {
        built.push(props.view);
        return () => null;
      },
    });
    const nav = source(Page);
    nav.push('album');
    await nextTick();
    nav.push('artist');
    await frames(5);
    expect(built).toEqual(['home', 'artist']);

    nav.back();
    await nextTick();
    await nextTick();
    expect(built).toEqual(['home', 'artist', 'album']);
  });

  it('lands a navigation once the page holds its content, and drops a landing whose page was left first', async () => {
    const built = [];
    const Page = defineComponent({
      props: { view: String },
      setup(props) {
        built.push(props.view);
        return () => null;
      },
    });
    const nav = source(Page);
    const landed = [];

    nav.push('album');
    await nextTick();
    whenPageReady(pageOnTop(), () => landed.push(built.includes('album')));
    await frames(5);
    expect(landed).toEqual([true]);

    nav.push('artist');
    await nextTick();
    whenPageReady(pageOnTop(), () => landed.push('artist, left before it was built'));
    nav.push('queue');
    await frames(5);
    nav.back();
    await frames(5);
    expect(landed).toEqual([true]);
  });
});
