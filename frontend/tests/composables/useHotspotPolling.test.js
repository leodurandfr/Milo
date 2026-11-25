// frontend/tests/composables/useHotspotPolling.test.js
/**
 * Settings › Multiroom draws the 'Milō' setup hotspots only while multiroom is
 * on and settled, and every poll tick is a WiFi scan on the server (some five
 * nmcli calls, a forced rescan on a unit on Ethernet). App.vue opens that
 * panel with multiroom off whenever a speaker presents itself, and the kiosk
 * may stay on it. What breaks if this stops holding: the server scans the air
 * every 10 s for a list nobody can see, or the list, once shown, is never
 * refreshed, or the poll outlives the panel.
 *
 * The discovery store is the real one; HTTP goes through the apiCall mock. The
 * last block reads the sources: a view that takes the poll itself, as
 * Settings › Multiroom did from its onMounted, polls whatever it draws.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { useHotspotPolling } from '@/composables/useHotspotPolling';

const SCAN_URL = '/api/discovery/wifi-speakers';

function scans() {
  return apiCall.get.mock.calls.filter(([url]) => url === SCAN_URL).length;
}

function mountHost(shown) {
  return mount(defineComponent({
    setup() {
      useHotspotPolling(() => shown.value);
    },
    render: () => h('div'),
  }));
}

describe('useHotspotPolling', () => {
  let wrapper;

  beforeEach(() => {
    vi.useFakeTimers();
    resetApiCallMock();
    apiCall.get.mockResolvedValue(ok({ status: 'success', data: { hotspots: [] } }));
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.useRealTimers();
  });

  it('scans nothing while the list is not drawn', async () => {
    wrapper = mountHost(ref(false));

    await vi.advanceTimersByTimeAsync(30000);

    expect(scans()).toBe(0);
  });

  it('scans at once when the list appears, then on every tick, and stops when it goes', async () => {
    const shown = ref(false);
    wrapper = mountHost(shown);

    shown.value = true;
    await vi.advanceTimersByTimeAsync(0);
    expect(scans()).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(scans()).toBe(3);

    shown.value = false;
    await vi.advanceTimersByTimeAsync(30000);
    expect(scans()).toBe(3);
  });

  it('stops with the view', async () => {
    wrapper = mountHost(ref(true));
    await vi.advanceTimersByTimeAsync(0);
    expect(scans()).toBe(1);

    wrapper.unmount();
    wrapper = null;
    await vi.advanceTimersByTimeAsync(30000);

    expect(scans()).toBe(1);
  });

  it('releases only the poll it took', async () => {
    // Shown, then hidden, then the view leaves: one start, one stop — a
    // second stop would release another view's hold on the shared poll.
    const shown = ref(true);
    const other = mountHost(ref(true));
    wrapper = mountHost(shown);
    await vi.advanceTimersByTimeAsync(0);

    shown.value = false;
    await vi.advanceTimersByTimeAsync(0);
    wrapper.unmount();
    wrapper = null;
    const before = scans();
    await vi.advanceTimersByTimeAsync(10000);

    expect(scans()).toBe(before + 1);
    other.unmount();
  });
});

describe('the hotspot poll is taken through useHotspotPolling only', () => {
  const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../../src');

  function vueFiles(dir) {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return vueFiles(path);
      return entry.name.endsWith('.vue') ? [path] : [];
    });
  }

  const views = vueFiles(SRC).map(path => ({
    file: relative(SRC, path),
    text: stripComments(readFileSync(path, 'utf8')),
  }));

  it('finds the view that polls', () => {
    expect(views.filter(({ text }) => /\buseHotspotPolling\(/.test(text)).map(({ file }) => file))
      .toContain('components/settings/categories/multiroom/MultiroomSettings.vue');
  });

  it('no view starts or stops it by hand', () => {
    expect(views.filter(({ text }) => /\.(start|stop)Polling\(/.test(text)).map(({ file }) => file)).toEqual([]);
  });
});
