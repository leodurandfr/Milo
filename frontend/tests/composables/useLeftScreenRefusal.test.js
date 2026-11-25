// frontend/tests/composables/useLeftScreenRefusal.test.js
/**
 * A settings write refused once its screen left is said by SettingsModal under
 * the view that came next, from the settings store. What breaks if its
 * lifetime stops holding: a notice the reader has already seen comes back on
 * every later opening of Settings, hours after the tap it concerned; or the
 * refusal of a screen closed with the modal is cleared by that very close and
 * the control moves back with no word of why; or a modal opened on a deep link
 * (Screen after a kiosk restart, Multiroom when a speaker presents itself)
 * clears it before it is ever drawn.
 *
 * The host stands for SettingsModal: a view ref the composable follows, set
 * during setup for a deep link, and a child screen writing through
 * useSettingsAPI. The settings store is the real one; HTTP goes through the
 * apiCall mock.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, ref, nextTick } from 'vue';
import { mount } from '@vue/test-utils';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { useLeftScreenRefusal } from '@/composables/useLeftScreenRefusal';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';

const BULK = {
  language: 'english',
  volume_steps: { step_interface_db: 2 },
  rotary_steps: { step_rotary_db: 2 },
  bt_remote_steps: { step_bt_remote_db: 3 },
  ir_remote_steps: { step_ir_remote_db: 2 },
  dock_apps: { enabled_apps: ['radio'] },
  radio_settings: { shazam_enabled: true },
};

function mountModal({ openOn = 'home', screen = null } = {}) {
  let view;
  let api;
  const Screen = defineComponent({
    setup() {
      api = useSettingsAPI();
    },
    render: () => h('div'),
  });
  const wrapper = mount(defineComponent({
    setup() {
      view = ref('home');
      useLeftScreenRefusal(view);
      // SettingsModal's initialView watch runs its goTo during setup.
      view.value = openOn;
    },
    render: () => h('div', screen ? [h(Screen)] : []),
  }));
  return { wrapper, view, api: () => api };
}

describe('a write refused after its screen left', () => {
  beforeEach(() => {
    resetApiCallMock();
    apiCall.get.mockImplementation(async (url) => (url === '/api/settings/bulk' ? ok(BULK) : ok({})));
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('is said until the reader moves to another view', async () => {
    const store = useSettingsStore();
    const { view } = mountModal();
    store.updateLeftScreenRefusal('spotify did not start');
    await nextTick();
    expect(store.leftScreenRefusal).toBe('spotify did not start');

    view.value = 'network';
    await nextTick();

    expect(store.leftScreenRefusal).toBeNull();
  });

  it('once seen, goes with the modal and is not said at the next opening', () => {
    const store = useSettingsStore();
    const first = mountModal();
    store.updateLeftScreenRefusal('spotify did not start');

    first.wrapper.unmount();
    mountModal();

    expect(store.leftScreenRefusal).toBeNull();
  });

  it('from a screen closed with the modal, is said at the next opening', async () => {
    const store = useSettingsStore();
    const { wrapper, api } = mountModal({ screen: true });
    const detail = { message: 'Failed to update apps: spotify did not start', operations_log: [] };
    apiCall.put.mockResolvedValueOnce(fail(detail, 500));

    api().debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: ['radio', 'spotify'] }, 500);
    wrapper.unmount();
    await vi.waitFor(() => expect(store.leftScreenRefusal).toBe(detail.message));
    mountModal();

    expect(store.leftScreenRefusal).toBe(detail.message);
  });

  it('answered while the modal was closed, is said on a deep-linked opening too', async () => {
    const store = useSettingsStore();
    store.updateLeftScreenRefusal('spotify did not start');

    const { view } = mountModal({ openOn: 'screen' });
    await nextTick();
    expect(store.leftScreenRefusal).toBe('spotify did not start');

    view.value = 'home';
    await nextTick();
    expect(store.leftScreenRefusal).toBeNull();
  });
});
