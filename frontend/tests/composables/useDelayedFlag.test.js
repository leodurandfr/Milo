// frontend/tests/composables/useDelayedFlag.test.js
/**
 * useDelayedFlag is what keeps the transport spinner, the artwork veil and the
 * progress bar's dimmed look off a track change: every skip passes through
 * `loading` for a few hundred milliseconds, and an indicator bound straight to
 * it flashed on each one. If this fails, the flash is back on every player.
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useDelayedFlag, WAIT_INDICATOR_DELAY_MS } from '@/composables/useDelayedFlag';

function mountFlag(source) {
  let flag;
  const wrapper = mount(defineComponent({
    setup() {
      flag = useDelayedFlag(source);
      return () => h('div');
    },
  }));
  return { flag, wrapper };
}

describe('useDelayedFlag', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('never rises for a wait shorter than the delay', async () => {
    const waiting = ref(false);
    const { flag } = mountFlag(waiting);

    waiting.value = true;
    await nextTick();
    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS - 1);
    waiting.value = false;
    await nextTick();
    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS);

    expect(flag.value).toBe(false);
  });

  it('rises once the wait outlasts the delay, and drops the moment it ends', async () => {
    const waiting = ref(false);
    const { flag } = mountFlag(waiting);

    waiting.value = true;
    await nextTick();
    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS);
    expect(flag.value).toBe(true);

    waiting.value = false;
    await nextTick();
    expect(flag.value).toBe(false);
  });

  it('counts a wait already under way at mount', () => {
    const { flag } = mountFlag(ref(true));

    expect(flag.value).toBe(false);
    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS);
    expect(flag.value).toBe(true);
  });

  it('restarts the count for each wait, so two short ones never add up', async () => {
    const waiting = ref(true);
    const { flag } = mountFlag(waiting);

    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS / 2);
    waiting.value = false;
    await nextTick();
    waiting.value = true;
    await nextTick();
    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS / 2);

    expect(flag.value).toBe(false);
  });
});
