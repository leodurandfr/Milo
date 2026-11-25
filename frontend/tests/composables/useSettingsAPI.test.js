// frontend/tests/composables/useSettingsAPI.test.js
/**
 * What breaks when these fail: a settings screen that moved before its write
 * landed keeps showing a value the server refused. The kiosk kept a theme, a
 * warm filter or an edge inset settings.json never held, the Shazam or Qobuz
 * switch showed a state never saved, and the only trace was a raw English
 * error line, because the rejection reached the global error handler. Every
 * settings screen now leans on the one undo here: the stored settings read
 * back, the screen's own copy put back on them, the server's detail held for
 * SettingsRefusal. SpotifySettings also hides its "restart to apply" button on
 * a restart the server reports as failed (`reload_success: false`).
 *
 * The settings store is the real one; HTTP goes through the apiCall mock.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';

// The categories doLoadAllSettings dereferences, with the server's Shazam
// switch on and its dock showing radio alone.
const BULK = {
  language: 'english',
  volume_steps: { step_interface_db: 2 },
  rotary_steps: { step_rotary_db: 2 },
  bt_remote_steps: { step_bt_remote_db: 3 },
  ir_remote_steps: { step_ir_remote_db: 2 },
  dock_apps: { enabled_apps: ['radio'] },
  radio_settings: { shazam_enabled: true },
};

function serveBulk() {
  apiCall.get.mockImplementation(async (url) => (url === '/api/settings/bulk' ? ok(BULK) : ok({})));
}

function mountHost(options) {
  let api;
  const wrapper = mount(defineComponent({
    setup() {
      api = useSettingsAPI(options);
    },
    render: () => h('div'),
  }));
  return { api, wrapper };
}

describe('useSettingsAPI', () => {
  beforeEach(() => {
    resetApiCallMock();
    serveBulk();
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
    expect(api.refusal.value).toBeNull();
  });

  it('a refused write puts the store back on the stored value and keeps the server detail', async () => {
    const store = useSettingsStore();
    const { api } = mountHost();
    store.updateRadioSettings({ shazam_enabled: false });
    apiCall.put.mockResolvedValueOnce(fail('Failed to save setting', 500));

    const result = await api.updateSetting('radio-settings', { shazam_enabled: false });

    expect(result.applied).toBe(false);
    expect(store.radioSettings.shazam_enabled).toBe(true);
    expect(api.refusal.value).not.toBeNull();
  });

  it('a refused write puts a screen copy back only once the stored settings are in', async () => {
    const store = useSettingsStore();
    const seen = [];
    const { api } = mountHost({ resync: () => seen.push(store.volumeSteps.step_bt_remote_db) });
    apiCall.put.mockResolvedValueOnce(fail('Failed to save setting', 500));

    await api.updateSetting('bt-remote-steps', { step_bt_remote_db: 5 });

    expect(seen).toEqual([3]);
  });

  it('a refused write does not reach the global error handler', async () => {
    const { api } = mountHost();
    apiCall.put.mockResolvedValueOnce(fail('Failed to save setting', 500));

    await expect(api.updateSetting('radio-settings', { shazam_enabled: false })).resolves.toEqual({ applied: false });
  });

  it('the next write clears the refusal it showed', async () => {
    const { api } = mountHost();
    apiCall.put.mockResolvedValueOnce(fail('Failed to save setting', 500));
    await api.updateSetting('radio-settings', { shazam_enabled: false });

    const pending = api.updateSetting('radio-settings', { shazam_enabled: true });

    expect(api.refusal.value).toBeNull();
    await pending;
  });

  it('a cancelled write is neither applied nor refused', async () => {
    const { api } = mountHost();
    apiCall.put.mockResolvedValueOnce({ ok: false, data: null, error: null });

    const result = await api.updateSetting('spotify-settings', { apply_now: true });

    expect(result.applied).toBe(false);
    expect(api.refusal.value).toBeNull();
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('a debounced write that fails puts the store back and says the route\'s sentence, not its log', async () => {
    const store = useSettingsStore();
    const { api } = mountHost();
    store.updateDockApps(['radio', 'spotify']);
    // What PUT /api/settings/dock-apps answers when a step fails.
    const detail = { message: 'Failed to update apps: spotify did not start', operations_log: ['Transitioning radio to none'] };
    apiCall.put.mockResolvedValueOnce(fail(detail, 500));

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio', 'spotify'] }, 500);
    await vi.advanceTimersByTimeAsync(500);

    await vi.waitFor(() => expect(store.dockApps.spotify).toBe(false));
    expect(api.refusal.value).toBe(detail.message);
    expect(store.leftScreenRefusal).toBeNull();
  });

  it('a refusal answered after a newer write was sent is not undone', async () => {
    const { api } = mountHost();
    let answer;
    apiCall.put
      .mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }))
      .mockReturnValueOnce(new Promise(() => {}));

    const dark = api.updateSetting('screen-theme', { theme: 'dark' });
    api.updateSetting('screen-theme', { theme: 'light' });
    answer(fail('Failed to save setting', 500));
    await dark;

    expect(apiCall.get).not.toHaveBeenCalled();
    expect(api.refusal.value).toBeNull();
  });

  it('a failed write leaves the screen alone while a newer one is queued', async () => {
    const { api } = mountHost();
    let answer;
    apiCall.put.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio'] }, 500);
    await vi.advanceTimersByTimeAsync(500);
    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['spotify'] }, 500);
    answer(fail('Failed to update apps', 500));
    await vi.advanceTimersByTimeAsync(0);

    expect(apiCall.get).not.toHaveBeenCalled();
    expect(api.refusal.value).toBeNull();
    await vi.advanceTimersByTimeAsync(500);
    expect(apiCall.put).toHaveBeenCalledTimes(2);
  });

  it('a debounced write flushed on unmount that fails still puts the store back', async () => {
    const store = useSettingsStore();
    const { api, wrapper } = mountHost();
    store.updateDockApps(['radio', 'spotify']);
    apiCall.put.mockResolvedValueOnce(fail('Network error', null));

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio', 'spotify'] }, 500);
    wrapper.unmount();

    expect(apiCall.put).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(store.dockApps.spotify).toBe(false));
  });

  it('a debounced write flushed on unmount that is refused is said for the view that came next', async () => {
    // Dock, Dock › Volume and the normalization switch debounce their writes:
    // left within 300-500 ms of a tap, the screen that would say the refusal
    // is gone, and SettingsModal says it from the store instead.
    const store = useSettingsStore();
    const { api, wrapper } = mountHost();
    const detail = { message: 'Failed to update apps: spotify did not start', operations_log: [] };
    apiCall.put.mockResolvedValueOnce(fail(detail, 500));

    api.debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio', 'spotify'] }, 500);
    wrapper.unmount();

    await vi.waitFor(() => expect(store.leftScreenRefusal).toBe(detail.message));
    expect(store.dockApps.spotify).toBe(false);
  });

  it('a write refused once its screen left is said for the next view too', async () => {
    const store = useSettingsStore();
    const { api, wrapper } = mountHost();
    let answer;
    apiCall.put.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));

    const written = api.updateSetting('radio-settings', { shazam_enabled: false });
    wrapper.unmount();
    answer(fail('Failed to save setting', 500));
    await written;

    expect(store.leftScreenRefusal).toBe('Failed to save setting');
  });
});
