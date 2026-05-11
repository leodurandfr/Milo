/**
 * WiFi regulatory domain country codes.
 *
 * Curated list of ISO 3166-1 alpha-2 codes supported as wireless regulatory
 * domains on the Pi. Labels are produced via `Intl.DisplayNames` so no manual
 * i18n map is needed — mirrors the pattern used by `constants/countries.js`.
 */
import { getTranslatedCountryName, bcp47For } from '@/constants/countries';

const WIFI_COUNTRY_CODES = [
  'AF', 'AR', 'AU', 'AT', 'BY', 'BE', 'BA', 'BR', 'BG', 'CA',
  'CL', 'CN', 'CO', 'HR', 'CZ', 'DK', 'DO', 'EC', 'EE', 'FI',
  'FR', 'DE', 'GR', 'HU', 'IN', 'ID', 'IE', 'IL', 'IT', 'JP',
  'LV', 'MX', 'NL', 'NZ', 'NO', 'PE', 'PH', 'PL', 'PT', 'RO',
  'RU', 'SA', 'RS', 'SK', 'SI', 'ZA', 'KR', 'ES', 'SE', 'CH',
  'TW', 'TH', 'TN', 'TR', 'AE', 'UG', 'UA', 'GB', 'US', 'UY',
  'VE',
];

/**
 * Language-to-country mapping for pre-selection in setup wizard.
 * Only includes unambiguous mappings (one language = one country).
 */
export const LANGUAGE_TO_COUNTRY = {
  french: 'FR',
  german: 'DE',
  italian: 'IT',
  hindi: 'IN',
  chinese: 'CN',
};

/**
 * Generate Dropdown options for the WiFi country selector.
 *
 * @param {string} language - Milō language code (e.g. 'french', 'english')
 * @returns {Array<{label: string, value: string}>}
 */
export function wifiCountryOptions(language) {
  const bcp47 = bcp47For(language);
  return WIFI_COUNTRY_CODES
    .map((code) => ({
      label: getTranslatedCountryName(language, code, code),
      value: code,
    }))
    .sort((a, b) => a.label.localeCompare(b.label, bcp47));
}
