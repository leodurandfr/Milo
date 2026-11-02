// frontend/tests/composables/useLoadingIndicator.test.js
/**
 * useLoadingIndicator is the main button's spinner on both players and the
 * lyrics bar. A session opening spins at once: held back a second, a CD
 * inserted into the active source showed pause, then the spinner, then pause.
 * A track change still waits the second, or the spinner flashes on every skip.
 *
 * The real store is driven through its own `source/state` handler; a host
 * component is mounted only for the composable's lifecycle.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick, computed } from 'vue';
import { mount } from '@vue/test-utils';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLoadingIndicator } from '@/composables/useLoadingIndicator';
import { WAIT_INDICATOR_DELAY_MS } from '@/composables/useDelayedFlag';
import { publishState, makeSession } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

describe('useLoadingIndicator', () => {
  let store;

  function mountIndicator() {
    let spinning;
    mount(defineComponent({
      setup() {
        spinning = useLoadingIndicator(computed(() => store.systemState.session?.phase ?? null));
        return () => h('div');
      },
    }));
    return spinning;
  }

  const publish = (session) => publishState(store, { source: 'cd', service: 'running', session });

  beforeEach(() => {
    vi.useFakeTimers();
    store = useUnifiedAudioStore();
  });
  afterEach(() => vi.useRealTimers());

  it('spins at once for a session that opens loading', async () => {
    const spinning = mountIndicator();

    publish(makeSession({ id: 'disc', phase: 'loading' }));
    await nextTick();

    expect(spinning.value).toBe(true);

    publish(makeSession({ id: 'disc', phase: 'playing' }));
    await nextTick();
    expect(spinning.value).toBe(false);
  });

  it('holds a track change back a second, as before', async () => {
    const spinning = mountIndicator();
    publish(makeSession({ id: 'disc', phase: 'playing' }));
    await nextTick();

    publish(makeSession({ id: 'disc', phase: 'loading' }));
    await nextTick();
    expect(spinning.value).toBe(false);

    vi.advanceTimersByTime(WAIT_INDICATOR_DELAY_MS);
    expect(spinning.value).toBe(true);
  });

  it('spins at once again when another session opens', async () => {
    const spinning = mountIndicator();
    publish(makeSession({ id: 'first', phase: 'playing' }));
    await nextTick();

    publish(makeSession({ id: 'second', phase: 'loading' }));
    await nextTick();

    expect(spinning.value).toBe(true);
  });
});
