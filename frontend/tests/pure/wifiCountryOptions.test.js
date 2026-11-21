// frontend/tests/pure/wifiCountryOptions.test.js
/**
 * The WiFi country dropdown (constants/wifiCountries.js::wifiCountryOptions),
 * read by the setup wizard's network step and Settings › Network.
 *
 * The country is required before the wizard goes on, so a country the list
 * lacks is a household that has to pick a wrong one — which sets the radio's
 * legal channels for another country. The list is the regulatory database's
 * whole set; what can break it is the naming: a code Intl cannot name shows
 * as two bare letters, and two codes under one name make the owner guess.
 */
import { describe, it, expect } from 'vitest';
import { wifiCountryOptions } from '@/constants/wifiCountries';

const LANGUAGES = ['english', 'french', 'spanish', 'italian', 'german', 'portuguese', 'hindi', 'chinese'];

describe('wifiCountryOptions', () => {
  it.each(LANGUAGES)('names every country of the regulatory database in %s', (language) => {
    const options = wifiCountryOptions(language);

    expect(options.length).toBeGreaterThan(0);
    const unnamed = options.filter(option => option.label === option.value);
    expect(unnamed).toEqual([]);
    expect(new Set(options.map(option => option.label)).size).toBe(options.length);
  });
});
