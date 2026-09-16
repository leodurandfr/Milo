// frontend/src/services/i18n.js - Translation service with standardized codes
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';
import { bcp47For } from '@/constants/countries';
import { logger } from '@/services/logger';

/**
 * Which form index `count` selects, per language, for a `singular | plural`
 * string. French and Hindi put 0 in the singular; Chinese has one form.
 */
const PLURAL_RULES = {
  french: (n) => (n < 2 ? 0 : 1),
  hindi: (n) => (n < 2 ? 0 : 1),
  chinese: () => 1,
};
const PLURAL_RULES_DEFAULT = (n) => (n === 1 ? 0 : 1);

class I18nService {
  constructor() {
    this.currentLanguage = ref('english');
    this.translations = new Map();
    this.fallbackLanguage = 'english';
    this.isInitialized = false;
  }

  async loadTranslations(language) {
    if (this.translations.has(language)) {
      return;
    }

    try {
      let translations;

      if (language === 'french') {
        translations = (await import('../locales/french.json')).default;
      } else if (language === 'english') {
        translations = (await import('../locales/english.json')).default;
      } else if (language === 'spanish') {
        translations = (await import('../locales/spanish.json')).default;
      } else if (language === 'hindi') {
        translations = (await import('../locales/hindi.json')).default;
      } else if (language === 'chinese') {
        translations = (await import('../locales/chinese.json')).default;
      } else if (language === 'portuguese') {
        translations = (await import('../locales/portuguese.json')).default;
      } else if (language === 'italian') {
        translations = (await import('../locales/italian.json')).default;
      } else if (language === 'german') {
        translations = (await import('../locales/german.json')).default;
      }

      if (translations) {
        this.translations.set(language, translations);
      }
    } catch (error) {
      logger.error('i18n', `Error loading translations for ${language}`, error);
    }
  }

  // Helper to get nested value from object using dot notation
  getNestedValue(obj, path) {
    return path.split('.').reduce((current, key) => current?.[key], obj);
  }

  /**
   * Pick the form matching `count` in a `singular | plural` string.
   *
   * Reduced to the two forms Milō's strings actually carry. A locale absent
   * from the table takes the `n === 1` rule; a string carrying no `|` is
   * returned whole, which is how Chinese and Hindi — whose nouns do not
   * inflect here — keep a single form.
   */
  selectPlural(template, params) {
    if (typeof template !== 'string' || !template.includes('|')) return template;
    if (typeof params?.count !== 'number') return template;

    const forms = template.split('|').map(form => form.trim());
    const rule = PLURAL_RULES[this.currentLanguage.value] || PLURAL_RULES_DEFAULT;
    return forms[Math.min(rule(params.count), forms.length - 1)];
  }

  // Helper to interpolate parameters into translation strings
  interpolate(template, params) {
    if (!params || typeof template !== 'string') return template;

    // A count reaches the screen as a number, so it is grouped the way the
    // language groups thousands — 10 069, 10,069 or 10,069 by Indian grouping.
    const locale = bcp47For(this.currentLanguage.value);
    return template.replace(/\{(\w+)\}/g, (match, key) => {
      if (!params.hasOwnProperty(key)) return match;
      const value = params[key];
      return typeof value === 'number' ? value.toLocaleString(locale) : value;
    });
  }

  // The document tag drives hyphenation, glyph fallback and what a screen
  // reader announces, so it follows the UI language rather than staying on the
  // value index.html was shipped with.
  applyDocumentLanguage() {
    document.documentElement.lang = bcp47For(this.currentLanguage.value);
  }

  t(key, params = {}) {
    // Load translations for current language
    const translations = this.translations.get(this.currentLanguage.value);

    if (translations) {
      const value = this.getNestedValue(translations, key);
      if (value !== undefined) {
        return this.interpolate(this.selectPlural(value, params), params);
      }
    }

    // Fallback to English if not found
    if (this.currentLanguage.value !== this.fallbackLanguage) {
      const fallbackTranslations = this.translations.get(this.fallbackLanguage);
      if (fallbackTranslations) {
        const fallbackValue = this.getNestedValue(fallbackTranslations, key);
        if (fallbackValue !== undefined) {
          return this.interpolate(this.selectPlural(fallbackValue, params), params);
        }
      }
    }

    // Return key if no translation found
    return key;
  }

  // Initialize language from the server
  async initializeLanguage() {
    if (this.isInitialized) return;

    // English only: it is the fallback t() reads when a key is missing, so it is
    // needed whatever the server answers. The active language loads just below.
    await this.loadTranslations('english');

    const result = await apiCall.get('/api/settings/language', {
      category: 'i18n',
      message: 'Error initializing language from server',
      checkStatus: true
    });
    if (result.ok) {
      const serverLanguage = result.data.language;
      await this.loadTranslations(serverLanguage);
      this.currentLanguage.value = serverLanguage;
    }
    this.applyDocumentLanguage();
    this.isInitialized = true;
  }

  // Change language via API (automatic WebSocket broadcast)
  async setLanguage(language) {
    const result = await apiCall.put('/api/settings/language', { language }, {
      category: 'i18n',
      message: 'Error setting language',
      checkStatus: true
    });
    return result.ok;
  }

  // Called from WebSocket events
  async handleLanguageChanged(newLanguage) {
    if (newLanguage !== this.currentLanguage.value) {
      await this.loadTranslations(newLanguage);
      this.currentLanguage.value = newLanguage;
      this.applyDocumentLanguage();
    }
  }

  getAvailableLanguages() {
    return [
      { code: 'french', name: 'Français', flag: '🇫🇷' },
      { code: 'english', name: 'English', flag: '🇺🇸' },
      { code: 'spanish', name: 'Español', flag: '🇪🇸' },
      { code: 'hindi', name: 'हिन्दी', flag: '🇮🇳' },
      { code: 'chinese', name: '中文', flag: '🇨🇳' },
      { code: 'portuguese', name: 'Português', flag: '🇵🇹' },
      { code: 'italian', name: 'Italiano', flag: '🇮🇹' },
      { code: 'german', name: 'Deutsch', flag: '🇩🇪' }
    ];
  }

  getCurrentLanguage() {
    return this.currentLanguage.value;
  }
}

// Singleton instance
export const i18n = new I18nService();

// Composable for use inside components
export function useI18n() {
  return {
    t: i18n.t.bind(i18n),
    setLanguage: i18n.setLanguage.bind(i18n),
    currentLanguage: i18n.currentLanguage,
    getAvailableLanguages: i18n.getAvailableLanguages.bind(i18n),
    getCurrentLanguage: i18n.getCurrentLanguage.bind(i18n)
  };
}