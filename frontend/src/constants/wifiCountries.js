/**
 * WiFi regulatory domain country codes
 *
 * Maps ISO 3166-1 alpha-2 codes to existing countries.* i18n keys.
 * Sorted alphabetically by translated label at runtime in wifiCountryOptions().
 */

const WIFI_COUNTRIES = [
  { code: 'AF', i18nKey: 'afghanistan' },
  { code: 'AR', i18nKey: 'argentina' },
  { code: 'AU', i18nKey: 'australia' },
  { code: 'AT', i18nKey: 'austria' },
  { code: 'BY', i18nKey: 'belarus' },
  { code: 'BE', i18nKey: 'belgium' },
  { code: 'BA', i18nKey: 'bosnia' },
  { code: 'BR', i18nKey: 'brazil' },
  { code: 'BG', i18nKey: 'bulgaria' },
  { code: 'CA', i18nKey: 'canada' },
  { code: 'CL', i18nKey: 'chile' },
  { code: 'CN', i18nKey: 'china' },
  { code: 'CO', i18nKey: 'colombia' },
  { code: 'HR', i18nKey: 'croatia' },
  { code: 'CZ', i18nKey: 'czechia' },
  { code: 'DK', i18nKey: 'denmark' },
  { code: 'DO', i18nKey: 'dominican_republic' },
  { code: 'EC', i18nKey: 'ecuador' },
  { code: 'EE', i18nKey: 'estonia' },
  { code: 'FI', i18nKey: 'finland' },
  { code: 'FR', i18nKey: 'france' },
  { code: 'DE', i18nKey: 'germany' },
  { code: 'GR', i18nKey: 'greece' },
  { code: 'HU', i18nKey: 'hungary' },
  { code: 'IN', i18nKey: 'india' },
  { code: 'ID', i18nKey: 'indonesia' },
  { code: 'IE', i18nKey: 'ireland' },
  { code: 'IL', i18nKey: 'israel' },
  { code: 'IT', i18nKey: 'italy' },
  { code: 'JP', i18nKey: 'japan' },
  { code: 'LV', i18nKey: 'latvia' },
  { code: 'MX', i18nKey: 'mexico' },
  { code: 'NL', i18nKey: 'netherlands' },
  { code: 'NZ', i18nKey: 'new_zealand' },
  { code: 'NO', i18nKey: 'norway' },
  { code: 'PE', i18nKey: 'peru' },
  { code: 'PH', i18nKey: 'philippines' },
  { code: 'PL', i18nKey: 'poland' },
  { code: 'PT', i18nKey: 'portugal' },
  { code: 'RO', i18nKey: 'romania' },
  { code: 'RU', i18nKey: 'russia' },
  { code: 'SA', i18nKey: 'saudi_arabia' },
  { code: 'RS', i18nKey: 'serbia' },
  { code: 'SK', i18nKey: 'slovakia' },
  { code: 'SI', i18nKey: 'slovenia' },
  { code: 'ZA', i18nKey: 'south_africa' },
  { code: 'KR', i18nKey: 'south_korea' },
  { code: 'ES', i18nKey: 'spain' },
  { code: 'SE', i18nKey: 'sweden' },
  { code: 'CH', i18nKey: 'switzerland' },
  { code: 'TW', i18nKey: 'taiwan' },
  { code: 'TH', i18nKey: 'thailand' },
  { code: 'TN', i18nKey: 'tunisia' },
  { code: 'TR', i18nKey: 'turkey' },
  { code: 'AE', i18nKey: 'uae' },
  { code: 'UG', i18nKey: 'uganda' },
  { code: 'UA', i18nKey: 'ukraine' },
  { code: 'GB', i18nKey: 'uk' },
  { code: 'US', i18nKey: 'usa' },
  { code: 'UY', i18nKey: 'uruguay' },
  { code: 'VE', i18nKey: 'venezuela' },
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
 * Generate Dropdown options from WiFi countries with i18n labels.
 * @param {Function} t - i18n translation function
 * @returns {Array} Array of {label, value} objects for Dropdown component
 */
export function wifiCountryOptions(t) {
  return WIFI_COUNTRIES
    .map(c => ({
      label: t(`countries.${c.i18nKey}`),
      value: c.code,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}
