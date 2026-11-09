// frontend/tests/stores/settingsStore.test.js
/**
 * The kiosk's wake shield (App.vue) is drawn from `isScreenSleeping`, which the
 * `settings/screen_sleep_changed` delta keeps current. A delta missed — a page
 * loaded while the panel slept, a backend restart that lit it — left the shield
 * wrong until the next one: down over a dark panel, so the touch that wakes it
 * also pressed whatever sat under the finger; or up over a lit one, swallowing
 * the first touch. `resync()` therefore re-reads the panel's state.
 *
 * The store is the real one; HTTP goes through the apiCall mock, answered per
 * URL because `resync()` issues its reads in parallel.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

const kiosk = vi.hoisted(() => ({ value: true }));

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => kiosk.value }));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { useSettingsStore } from '@/stores/settingsStore';

// The categories doLoadAllSettings dereferences; the rest may stay absent.
const BULK = {
  language: 'english',
  volume_steps: { step_interface_db: 2 },
  rotary_steps: { step_rotary_db: 2 },
  bt_remote_steps: { step_bt_remote_db: 2 },
  ir_remote_steps: { step_ir_remote_db: 2 },
  dock_apps: { enabled_apps: [] },
};

function serve(screenSleep) {
  apiCall.get.mockImplementation(async (url) => {
    if (url === '/api/settings/bulk') return ok(BULK);
    if (url === '/api/settings/screen-sleep') return screenSleep;
    return ok({});
  });
}

describe('settingsStore — the screen sleep snapshot', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    kiosk.value = true;
    setActivePinia(createPinia());
    store = useSettingsStore();
  });

  it('resync raises the shield the server reports on the kiosk, lowers it once the panel is lit, and never raises it on a remote browser', async () => {
    serve(ok({ status: 'success', sleeping: true }));
    expect(await store.resync()).toBe(true);
    expect(store.isScreenSleeping).toBe(true);

    serve(ok({ status: 'success', sleeping: false }));
    expect(await store.resync()).toBe(true);
    expect(store.isScreenSleeping).toBe(false);

    kiosk.value = false;
    serve(ok({ status: 'success', sleeping: true }));
    expect(await store.resync()).toBe(true);
    expect(store.isScreenSleeping).toBe(false);
  });

  it('a remote browser lowers a stale shield', async () => {
    store.updateScreenSleeping(true);
    kiosk.value = false;
    serve(ok({ status: 'success', sleeping: true }));
    await store.resync();
    expect(store.isScreenSleeping).toBe(false);
  });

  it('a read that fails leaves the shield where it was and fails the resync', async () => {
    store.updateScreenSleeping(true);
    serve(fail());
    expect(await store.resync()).toBe(false);
    expect(store.isScreenSleeping).toBe(true);
  });
});

/**
 * The delta itself: `screen_sleep_changed` reaches every browser, and the shield
 * (App.vue `.sleep-shield`, fixed and transparent over the whole page) is drawn
 * whenever the store says the screen sleeps — this gate is the only thing
 * keeping it off a remote browser. If the store took the delta there, a Mac or
 * a phone would swallow its first click after every sleep of the Pi's panel.
 */
describe('settingsStore — the screen sleep delta', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    kiosk.value = true;
    setActivePinia(createPinia());
    store = useSettingsStore();
  });

  it('screen_sleep_changed is ignored off the kiosk', () => {
    kiosk.value = false;
    store.updateScreenSleeping(true);
    expect(store.isScreenSleeping).toBe(false);
  });

  it('screen_sleep_changed raises the shield on the kiosk', () => {
    store.updateScreenSleeping(true);
    expect(store.isScreenSleeping).toBe(true);
  });

  it('a remote browser still drops a stale shield', () => {
    store.updateScreenSleeping(true);
    kiosk.value = false;
    store.updateScreenSleeping(false);
    expect(store.isScreenSleeping).toBe(false);
  });
});

/**
 * The server stores only the enabled dock apps, in order; the store keeps every
 * app's place so a disabled one comes back where it was. Broken, a feature
 * reordered in Dock settings would snap back to the default order on the next
 * save or resync, and the dock would draw it there.
 */
describe('settingsStore — the dock order', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useSettingsStore();
  });

  it('a disabled feature keeps its place while the enabled ones take the server order', () => {
    const roster = [...store.featureOrder];
    const [first, second, third, ...rest] = roster;
    store.updateDockApps(['spotify', third, ...rest, first]);

    expect(store.featureOrder).toEqual([third, second, ...rest, first]);
    expect(store.buildEnabledAppsArray()).toEqual(['spotify', third, ...rest, first]);
  });

  it('features follow every source, whatever order the server sends them in', () => {
    const [feature] = store.featureOrder;
    store.updateDockApps([feature, 'radio', 'spotify']);
    expect(store.buildEnabledAppsArray()).toEqual(['radio', 'spotify', feature]);
  });
});
