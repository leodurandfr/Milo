// frontend/src/services/i18n.js - Translation service with standardized codes
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';

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

  // Helper to interpolate parameters into translation strings
  interpolate(template, params) {
    if (!params || typeof template !== 'string') return template;

    return template.replace(/\{(\w+)\}/g, (match, key) => {
      return params.hasOwnProperty(key) ? params[key] : match;
    });
  }

  t(key, params = {}) {
    // Load translations for current language
    const translations = this.translations.get(this.currentLanguage.value);

    if (translations) {
      const value = this.getNestedValue(translations, key);
      if (value !== undefined) {
        return this.interpolate(value, params);
      }
    }

    // Fallback to English if not found
    if (this.currentLanguage.value !== this.fallbackLanguage) {
      const fallbackTranslations = this.translations.get(this.fallbackLanguage);
      if (fallbackTranslations) {
        const fallbackValue = this.getNestedValue(fallbackTranslations, key);
        if (fallbackValue !== undefined) {
          return this.interpolate(fallbackValue, params);
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