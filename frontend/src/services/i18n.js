// frontend/src/services/i18n.js - Service de traduction avec WebSocket
import { ref } from 'vue';
import axios from 'axios';

class I18nService {
  constructor() {
    this.currentLanguage = ref('français');
    this.translations = new Map();
    this.fallbackLanguage = 'français';
    this.isInitialized = false;
  }

  async loadTranslations(language) {
    if (language === 'français' || this.translations.has(language)) {
      return;
    }

    try {
      let translations;
      
      if (language === 'english') {
        translations = (await import('../locales/english.json')).default;
      } else if (language === 'español') {
        translations = (await import('../locales/spanish.json')).default;
      }
      
      if (translations) {
        this.translations.set(language, translations);
      }
    } catch (error) {
      console.error(`Error loading translations for ${language}:`, error);
    }
  }

  t(key) {
    // Français par défaut - retourner la clé telle quelle
    if (this.currentLanguage.value === 'français') {
      return key;
    }

    // Chercher dans les traductions chargées
    const translations = this.translations.get(this.currentLanguage.value);
    return translations?.[key] || key;
  }

  // Initialiser la langue depuis le serveur
  async initializeLanguage() {
    if (this.isInitialized) return;
    
    try {
      const response = await axios.get('/api/settings/language');
      if (response.data.status === 'success') {
        const serverLanguage = response.data.language;
        await this.loadTranslations(serverLanguage);
        this.currentLanguage.value = serverLanguage;
        console.log(`Language initialized from server: ${serverLanguage}`);
      }
    } catch (error) {
      console.error('Error initializing language from server:', error);
      // Garder le français par défaut
    } finally {
      this.isInitialized = true;
    }
  }

  // Changer la langue via API (diffusion WebSocket automatique)
  async setLanguage(language) {
    try {
      const response = await axios.post('/api/settings/language', { language });
      
      if (response.data.status === 'success') {
        // Le serveur va diffuser l'événement WebSocket
        // Notre langue sera mise à jour via handleLanguageChanged()
        console.log(`Language change requested: ${language}`);
        return true;
      }
      return false;
    } catch (error) {
      console.error('Error setting language:', error);
      return false;
    }
  }

  // Appelé depuis les événements WebSocket
  async handleLanguageChanged(newLanguage) {
    if (newLanguage !== this.currentLanguage.value) {
      await this.loadTranslations(newLanguage);
      this.currentLanguage.value = newLanguage;
      console.log(`Language synced from WebSocket: ${newLanguage}`);
    }
  }

  getAvailableLanguages() {
    return [
      { code: 'français', name: 'Français', flag: '🇫🇷' },
      { code: 'english', name: 'English', flag: '🇺🇸' },
      { code: 'español', name: 'Español', flag: '🇪🇸' }
    ];
  }

  getCurrentLanguage() {
    return this.currentLanguage.value;
  }
}

// Instance singleton
export const i18n = new I18nService();

// Composable pour l'utilisation dans les composants
export function useI18n() {
  return {
    t: i18n.t.bind(i18n),
    setLanguage: i18n.setLanguage.bind(i18n),
    currentLanguage: i18n.currentLanguage,
    getAvailableLanguages: i18n.getAvailableLanguages.bind(i18n),
    getCurrentLanguage: i18n.getCurrentLanguage.bind(i18n)
  };
}