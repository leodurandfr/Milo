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
 * A host component is mounted only for `provide`; nothing is rendered or
 * asserted on the DOM. Reading `currentPage` stands for the source rendering it.
 */
import { describe, it, expect } from 'vitest';
import { defineComponent } from 'vue';
import { mount } from '@vue/test-utils';
import { useNavigationStack } from '@/composables/useNavigationStack';

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
