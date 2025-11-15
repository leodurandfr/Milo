/**
 * Country name mapping for RadioBrowser API
 *
 * Maps API country names (long, verbose) to i18n translation keys.
 * This allows displaying user-friendly country names (e.g., "USA" instead of "The United States Of America")
 * while keeping the exact API names for filtering requests.
 *
 * Usage:
 * - API returns: "The United States Of America"
 * - Dropdown shows: t('countries.usa') → "USA" (EN) or "États-Unis" (FR)
 * - Filter uses: "The United States Of America" (exact API name)
 */

export const COUNTRY_NAME_TO_I18N_KEY = {
  // Top countries (6000+ stations)
  'The United States Of America': 'usa',
  'Germany': 'germany',
  'The Russian Federation': 'russia',
  'France': 'france',
  'Greece': 'greece',
  'China': 'china',
  'Australia': 'australia',
  'The United Kingdom Of Great Britain And Northern Ireland': 'uk',
  'Mexico': 'mexico',
  'Italy': 'italy',

  // Major countries (1000-2000 stations)
  'Canada': 'canada',
  'Spain': 'spain',
  'The Netherlands': 'netherlands',
  'Brazil': 'brazil',
  'India': 'india',
  'Poland': 'poland',

  // Medium countries (300-999 stations)
  'Argentina': 'argentina',
  'The United Arab Emirates': 'uae',
  'The Philippines': 'philippines',
  'Switzerland': 'switzerland',
  'Colombia': 'colombia',
  'Romania': 'romania',
  'Türkiye': 'turkey',
  'Uganda': 'uganda',
  'Belgium': 'belgium',
  'Indonesia': 'indonesia',
  'Chile': 'chile',
  'Serbia': 'serbia',
  'Hungary': 'hungary',
  'Ukraine': 'ukraine',
  'Austria': 'austria',

  // Smaller countries (80-299 stations)
  'Portugal': 'portugal',
  'Czechia': 'czechia',
  'Croatia': 'croatia',
  'Bulgaria': 'bulgaria',
  'Denmark': 'denmark',
  'Sweden': 'sweden',
  'Ireland': 'ireland',
  'Peru': 'peru',
  'New Zealand': 'new_zealand',
  'Slovakia': 'slovakia',
  'South Africa': 'south_africa',
  'Taiwan, Republic Of China': 'taiwan',
  'Bolivarian Republic Of Venezuela': 'venezuela',
  'Ecuador': 'ecuador',
  'Japan': 'japan',
  'Uruguay': 'uruguay',
  'Bosnia And Herzegovina': 'bosnia',
  'Finland': 'finland',
  'Afghanistan': 'afghanistan',
  'Israel': 'israel',
  'Slovenia': 'slovenia',
  'Saudi Arabia': 'saudi_arabia',
  'Estonia': 'estonia',
  'Latvia': 'latvia',
  'Norway': 'norway',
  'Thailand': 'thailand',
  'Belarus': 'belarus',
  'The Dominican Republic': 'dominican_republic',
  'The Republic Of Korea': 'south_korea',
  'Tunisia': 'tunisia'
};

/**
 * Get the i18n key for a country API name
 * @param {string} apiName - Country name as returned by RadioBrowser API
 * @returns {string} i18n key (e.g., "usa", "france") or the original name if not found
 */
export function getCountryI18nKey(apiName) {
  return COUNTRY_NAME_TO_I18N_KEY[apiName] || apiName.toLowerCase().replace(/\s+/g, '_');
}

/**
 * Get translated country name
 * @param {Function} t - i18n translation function
 * @param {string} apiName - Country name as returned by RadioBrowser API
 * @returns {string} Translated country name
 */
export function getTranslatedCountryName(t, apiName) {
  if (!apiName) return '';

  const i18nKey = getCountryI18nKey(apiName);
  const translationKey = `countries.${i18nKey}`;

  // Try to get translation, fallback to API name if not found
  const translated = t(translationKey);

  // If translation key is returned as-is (not found), return original API name
  return translated === translationKey ? apiName : translated;
}

/**
 * Generate dropdown options from API countries with translations
 * @param {Function} t - i18n translation function
 * @param {Array} apiCountries - Array of country objects from API: [{name: "France", stationcount: 2275}, ...]
 * @param {string} allCountriesLabel - Label for "All Countries" option
 * @returns {Array} Array of {label, value} objects for Dropdown component
 */
export function countryOptions(t, apiCountries, allCountriesLabel) {
  const options = [{ label: allCountriesLabel, value: '' }];

  apiCountries.forEach(country => {
    const apiName = country.name;
    const translatedName = getTranslatedCountryName(t, apiName);

    // Label: translated name (user-friendly)
    // Value: original API name (for exact filtering)
    options.push({ label: translatedName, value: apiName });
  });

  return options;
}
