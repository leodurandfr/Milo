/**
 * WiFi regulatory domain country codes.
 *
 * Every country the kernel's regulatory database (wireless-regdb's `db.txt`,
 * the `regulatory.db` the Pi's cfg80211 loads) defines rules for, in its own
 * order, which is the alphabetical one — 182 codes. A curated subset
 * left Singapore and Morocco out, so someone there had to pick a wrong country
 * to get past the wizard's network step. `AN` is the database's (the former
 * Netherlands Antilles, named Curaçao by Intl). A code the database does not
 * know is applied as the world domain, the most restrictive, so the list is
 * this one and no wider.
 *
 * Labels are produced via `Intl.DisplayNames` so no manual i18n map is
 * needed — mirrors the pattern used by `constants/countries.js`.
 */
import { getTranslatedCountryName, bcp47For } from '@/constants/countries';

const WIFI_COUNTRY_CODES = [
  'AD', 'AE', 'AF', 'AI', 'AL', 'AM', 'AN', 'AR', 'AS', 'AT', 'AU', 'AW',
  'AZ', 'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BL', 'BM', 'BN', 'BO',
  'BR', 'BS', 'BT', 'BW', 'BY', 'BZ', 'CA', 'CF', 'CH', 'CI', 'CL', 'CN',
  'CO', 'CR', 'CU', 'CX', 'CY', 'CZ', 'DE', 'DK', 'DM', 'DO', 'DZ', 'EC',
  'EE', 'EG', 'ES', 'ET', 'FI', 'FM', 'FO', 'FR', 'GB', 'GD', 'GE', 'GF',
  'GH', 'GI', 'GL', 'GP', 'GR', 'GT', 'GU', 'GY', 'HK', 'HN', 'HR', 'HT',
  'HU', 'ID', 'IE', 'IL', 'IM', 'IN', 'IQ', 'IR', 'IS', 'IT', 'JM', 'JO',
  'JP', 'KE', 'KH', 'KN', 'KP', 'KR', 'KW', 'KY', 'KZ', 'LB', 'LC', 'LI',
  'LK', 'LS', 'LT', 'LU', 'LV', 'MA', 'MC', 'MD', 'ME', 'MF', 'MH', 'MK',
  'MN', 'MO', 'MP', 'MQ', 'MR', 'MT', 'MU', 'MV', 'MW', 'MX', 'MY', 'NA',
  'NG', 'NI', 'NL', 'NO', 'NP', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH',
  'PK', 'PL', 'PM', 'PR', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU',
  'RW', 'SA', 'SE', 'SG', 'SI', 'SK', 'SM', 'SN', 'SR', 'SV', 'SX', 'SY',
  'TC', 'TD', 'TG', 'TH', 'TN', 'TR', 'TT', 'TW', 'TZ', 'UA', 'UG', 'US',
  'UY', 'UZ', 'VA', 'VC', 'VE', 'VI', 'VN', 'VU', 'WF', 'WS', 'YE', 'YT',
  'ZA', 'ZW',
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
