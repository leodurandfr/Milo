// frontend/tests/composables/useSettingsAPI.test.js
/**
 * What breaks when these fail: a settings screen that moved before its write
 * landed never hears that it did not. SpotifySettings hides its "restart to
 * apply" button on a restart the server reports as failed (`reload_success:
 * false`), and DockSettings keeps a tile toggled for a function that never
 * changed, because the debounced PUT's rejection was swallowed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { useSettingsAPI } from '@/composables/useSettingsAPI';

function mountHost() {
  let api;
  const wrapper = mount(defineComponent({
    setup() {
      api = useSettingsAPI();
    },
    render: () => h('div'),
  }));
  return { api, wrapper };
}

describe('useSettingsAPI', () => {
  beforeEach(() => {
    resetApiCallMock();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('a setting saved but not applied reports applied=false', async () => {
    const { api } = mountHost();
    apiCall.put.mockResolvedValueOnce(ok({ status: 'success', reload_success: false }));

    const result = await api.updateSetting('spotify-settings', { apply_now: true });

    expect(result.applied).toBe(false);
  });

  it('a setting the server applied reports applied=true', async () => {
    const { api } = mountHost();
    apiCall.put.mockResolvedValueOnce(ok({ status: 'success', reload_success: true }));

    const result = await api.updateSetting('spotify-settings', { apply_now: true });

    expect(result.applied).toBe(true);
  });

  it('a debounced write that fails calls its onError', async () => {
    const { api } = mountHost();
    apiCall.put.mockRejectedValueOnce(new Error('Failed to update apps'));
    const onError = vi.fn();

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio'] }, 500, { onError });
    await vi.advanceTimersByTimeAsync(500);

    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
  });

  it('a failed write leaves the screen alone while a newer one is queued', async () => {
    const { api } = mountHost();
    let reject;
    apiCall.put.mockReturnValueOnce(new Promise((_, r) => { reject = r; }));
    const onError = vi.fn();

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio'] }, 500, { onError });
    await vi.advanceTimersByTimeAsync(500);
    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['spotify'] }, 500, { onError });
    reject(new Error('Failed to update apps'));
    await vi.advanceTimersByTimeAsync(0);

    expect(onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(500);
    expect(apiCall.put).toHaveBeenCalledTimes(2);
  });

  it('a cancelled write is not reported as applied', async () => {
    const { api } = mountHost();
    apiCall.put.mockResolvedValueOnce({ ok: false, data: null, error: null });

    const result = await api.updateSetting('spotify-settings', { apply_now: true });

    expect(result.applied).toBe(false);
  });

  it('a debounced write flushed on unmount that fails still calls its onError', async () => {
    const { api, wrapper } = mountHost();
    apiCall.put.mockRejectedValueOnce(new Error('Network error'));
    const onError = vi.fn();

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio'] }, 500, { onError });
    wrapper.unmount();

    expect(apiCall.put).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
  });
});
