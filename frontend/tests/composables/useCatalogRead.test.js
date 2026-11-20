// frontend/tests/composables/useCatalogRead.test.js
/**
 * useCatalogRead holds what a music-library page (album, artist, playlist,
 * genre) read from the catalog. What breaks: a page left for another while its
 * read was on its way drawn over the one now shown, a failure forgotten by the
 * retry before its answer lands (the error card swapped for a skeleton and
 * back, on every press while Navidrome is still down), and a new page opened
 * on the last one's error.
 *
 * A host component is mounted only for the composable's lifecycle.
 */
import { describe, it, expect } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useCatalogRead } from '@/composables/useCatalogRead';

/** A read the test answers by hand, per key, in any order. */
function manualReader() {
  const pending = [];
  const read = (key) => new Promise((resolve) => pending.push({ key, resolve }));
  const answer = async (key, result) => {
    const index = pending.findIndex((p) => p.key === key);
    pending.splice(index, 1)[0].resolve(result);
    await nextTick();
    await Promise.resolve();
  };
  return { read, answer, pending };
}

function mountRead(key, read) {
  let state;
  const wrapper = mount(defineComponent({
    setup() {
      state = useCatalogRead(() => key.value, read);
      return () => h('div');
    },
  }));
  return { ...state, unmount: () => wrapper.unmount() };
}

describe('useCatalogRead', () => {
  it('keeps the page now shown when an earlier page\'s read lands last', async () => {
    const key = ref('al-1');
    const reader = manualReader();
    const state = mountRead(key, reader.read);

    key.value = 'al-2';
    await nextTick();
    await reader.answer('al-2', { value: { id: 'al-2' }, error: null });
    await reader.answer('al-1', { value: { id: 'al-1' }, error: null });

    expect(state.value.value).toEqual({ id: 'al-2' });
    expect(state.loading.value).toBe(false);
  });

  it('tells a read it is superseded once the page asks for another', async () => {
    // A genre's walk asks before each page; without this it read them all.
    const key = ref('Jazz');
    const reads = [];
    mountRead(key, (k, isCurrent) => {
      reads.push(isCurrent);
      return new Promise(() => {});
    });
    expect(reads[0]()).toBe(true);

    key.value = 'Rock';
    await nextTick();

    expect(reads[0]()).toBe(false);
    expect(reads[1]()).toBe(true);
  });

  it('tells a read its page is gone once the page closes', async () => {
    // The library keys each page by its navigation entry, so a page never sees
    // its key change: closing it is how it is left, and a genre left at once
    // went on reading every page of it behind the user.
    const reads = [];
    const state = mountRead(ref('Jazz'), (k, isCurrent) => {
      reads.push(isCurrent);
      return new Promise(() => {});
    });
    expect(reads[0]()).toBe(true);

    state.unmount();

    expect(reads[0]()).toBe(false);
  });

  it('stops a genre\'s walk between two pages once its page closes', async () => {
    // The walk as fetchGenreSongs makes it: a page, then ask before the next.
    const pages = [];
    let answerFirst;
    const walk = async (genre, isCurrent) => {
      for (let offset = 0; offset < 5000; offset += 500) {
        pages.push(offset);
        if (offset === 0) await new Promise((resolve) => { answerFirst = resolve; });
        if (!isCurrent()) return { value: null, error: null };
      }
      return { value: [], error: null };
    };
    const state = mountRead(ref('Jazz'), walk);

    state.unmount();
    answerFirst();
    await Promise.resolve();
    await Promise.resolve();

    expect(pages).toEqual([0]);
  });

  it('keeps the failure on screen while a retry is on its way', async () => {
    const key = ref('al-1');
    const reader = manualReader();
    const state = mountRead(key, reader.read);
    await reader.answer('al-1', { value: null, error: 'unavailable' });

    state.reload();
    expect(state.loading.value).toBe(true);
    expect(state.error.value).toBe('unavailable');

    await reader.answer('al-1', { value: { id: 'al-1' }, error: null });
    expect(state.error.value).toBeNull();
    expect(state.value.value).toEqual({ id: 'al-1' });
  });

  it('opens another page from a clean slate, not on the last one\'s failure', async () => {
    const key = ref('al-1');
    const reader = manualReader();
    const state = mountRead(key, reader.read);
    await reader.answer('al-1', { value: null, error: 'unavailable' });

    key.value = 'al-2';
    await nextTick();

    expect(state.error.value).toBeNull();
    expect(state.value.value).toBeNull();
    expect(state.loading.value).toBe(true);
  });
});
