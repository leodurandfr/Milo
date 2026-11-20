// frontend/tests/pure/countryOptions.test.js
/**
 * What a radio country dropdown offers while the list is not in
 * (constants/countries.js::pendingCountryOptions, read by RadioSource,
 * SearchView and ManageStation through radioStore.countriesStatus).
 *
 * A failed load must leave a line that asks again: a dropdown stuck on a
 * disabled "Loading…" that nothing is loading is the defect this pins. And
 * while the list is loading, or not asked yet, nothing can be picked — a
 * pickable line there would become a country filter.
 */
import { describe, it, expect } from 'vitest';
import { pendingCountryOptions, RETRY_COUNTRIES } from '@/constants/countries';

const LABELS = { loading: 'loading', unavailable: 'unavailable', retry: 'retry' };

const pickable = (options) => options.filter(option => !option.disabled);

describe('pendingCountryOptions', () => {
  it('offers the retry line, and only it, once the list failed', () => {
    const options = pendingCountryOptions('failed', LABELS);

    expect(options.length).toBeGreaterThan(1);
    expect(pickable(options).map(option => option.value)).toEqual([RETRY_COUNTRIES]);
  });

  it.each(['idle', 'loading'])('offers nothing to pick while %s', (status) => {
    const options = pendingCountryOptions(status, LABELS);

    expect(options.length).toBeGreaterThan(0);
    expect(pickable(options)).toEqual([]);
    expect(options.map(option => option.value)).not.toContain(RETRY_COUNTRIES);
  });
});
