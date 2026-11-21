// frontend/tests/composables/networkSelectorCountry.test.js
/**
 * The WiFi list guesses the regulatory domain from the language only where it
 * draws the country picker that shows the guess — the wizard.
 *
 * Settings › Network and the satellite adoption draw the same list without its
 * picker. What breaks if the guess leaks there: opening a settings page writes
 * `PUT /api/network/wifi/country`, which runs the privileged
 * `milo-set-wifi-country` (`iw reg set` and a cmdline.txt write) with no
 * gesture — a unit set to BE under a French interface is moved to FR the
 * first time a country read fails, and over the recovery AP of a set-up unit
 * the write is refused and banners on every opening.
 *
 * The list is mounted as its screens mount it; what is asserted is the write
 * it sends (through the apiCall boundary), nothing about its DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { i18n } from '@/services/i18n';
import { useNetwork } from '@/composables/useNetwork';
import NetworkSelector from '@/components/network/NetworkSelector.vue';

function countryWrites() {
  return apiCall.put.mock.calls.filter(([url]) => url === '/api/network/wifi/country');
}

describe('NetworkSelector country guess', () => {
  let wrapper;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetApiCallMock();
    vi.spyOn(i18n, 'getCurrentLanguage').mockReturnValue('french');
    apiCall.get.mockImplementation((url) => {
      if (url === '/api/network/wifi/country') return Promise.resolve(ok({ data: { country_code: '' } }));
      if (url === '/api/network/wifi/networks') return Promise.resolve(ok({ data: [] }));
      if (url === '/api/network/wifi/saved') return Promise.resolve(ok({ data: [] }));
      return Promise.resolve(ok({ data: {} }));
    });
    apiCall.put.mockResolvedValue(ok({ status: 'success' }));
    // The country is module state, read once per page: each case starts unset.
    useNetwork().country.value = '';
  });

  afterEach(() => {
    wrapper?.unmount();
    vi.restoreAllMocks();
  });

  function mountList(props) {
    wrapper = mount(NetworkSelector, {
      props,
      global: { directives: { press: {} }, stubs: { WifiCountrySelector: true } },
    });
    return flushPromises();
  }

  it('writes no country from a list drawn without its picker', async () => {
    await mountList({ showCountry: false, showLabel: false, action: 'connect' });

    expect(apiCall.get).toHaveBeenCalledWith('/api/network/wifi/country', expect.anything());
    expect(countryWrites()).toEqual([]);
  });

  it('guesses the country where the picker shows it', async () => {
    await mountList({ action: 'connect' });

    expect(countryWrites().map(([, body]) => body)).toEqual([{ country_code: 'FR' }]);
  });
});
