// frontend/tests/composables/useDebouncedAutosave.test.js
/**
 * What breaks when these fail: ManageStation's edit form loses the last change
 * to a favorite station. Typing a new name and pressing Back within the 500 ms
 * debounce unmounts the form, and a save cancelled with its timer is never
 * sent — the list keeps the old name, the station plays the old stream, and
 * nothing says so.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock } from '../helpers/apiCallMock';
import { useDebouncedAutosave } from '@/composables/useDebouncedAutosave';

const DELAY_MS = 500;

function save() {
  return apiCall.post('/api/radio/favorites/modify-metadata', new FormData(), {
    category: 'radio',
    message: 'Auto-save failed',
  });
}

function mountHost() {
  let autosave;
  const wrapper = mount(defineComponent({
    setup() {
      autosave = useDebouncedAutosave(save, DELAY_MS);
    },
    render: () => h('div'),
  }));
  return { autosave, wrapper };
}

describe('useDebouncedAutosave', () => {
  beforeEach(() => {
    resetApiCallMock();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('a save still pending when the host unmounts is sent once', () => {
    const { autosave, wrapper } = mountHost();

    autosave.trigger();
    vi.advanceTimersByTime(200);
    wrapper.unmount();

    expect(apiCall.post).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });

  it('a burst of triggers sends one save after the delay', () => {
    const { autosave } = mountHost();

    autosave.trigger();
    vi.advanceTimersByTime(100);
    autosave.trigger();
    vi.advanceTimersByTime(100);
    autosave.trigger();
    expect(apiCall.post).not.toHaveBeenCalled();

    vi.advanceTimersByTime(DELAY_MS);
    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });

  it('nothing pending at unmount sends nothing', () => {
    const { autosave, wrapper } = mountHost();

    autosave.trigger();
    vi.advanceTimersByTime(DELAY_MS);
    expect(apiCall.post).toHaveBeenCalledTimes(1);
    wrapper.unmount();

    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });

  it('a host that never triggered sends nothing at unmount', () => {
    const { wrapper } = mountHost();

    wrapper.unmount();

    expect(apiCall.post).not.toHaveBeenCalled();
  });

  it('a cancelled save is not sent on unmount', () => {
    const { autosave, wrapper } = mountHost();

    autosave.trigger();
    autosave.cancel();
    wrapper.unmount();
    vi.advanceTimersByTime(2000);

    expect(apiCall.post).not.toHaveBeenCalled();
  });

  it('flush sends the pending save at once and only once', async () => {
    const { autosave } = mountHost();

    autosave.trigger();
    expect(autosave.isPending()).toBe(true);
    await autosave.flush();

    expect(apiCall.post).toHaveBeenCalledTimes(1);
    expect(autosave.isPending()).toBe(false);
    vi.advanceTimersByTime(2000);
    expect(apiCall.post).toHaveBeenCalledTimes(1);
  });
});
