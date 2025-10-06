<!-- frontend/src/components/settings/categories/LanguageSettings.vue -->
<template>
  <section class="settings-section">
    <div class="language-grid">
      <button v-for="language in availableLanguages" :key="language.code"
        @click="selectLanguage(language.code)"
        :class="['language-button', { active: currentLanguage === language.code }]">
        <span class="language-flag">{{ language.flag }}</span>
        <span class="language-name heading-2">{{ language.name }}</span>
      </button>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';

const { getAvailableLanguages, getCurrentLanguage } = useI18n();
const { on } = useWebSocket();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

async function selectLanguage(languageCode) {
  await updateSetting('language', { language: languageCode });
}

// WebSocket listener
const handleLanguageChanged = (msg) => {
  const newLanguage = msg.data?.language;
  if (newLanguage) {
    i18n.handleLanguageChanged(newLanguage);
    // Mettre à jour le store
    settingsStore.updateLanguage(newLanguage);
  }
};

onMounted(() => {
  // Plus besoin de charger la langue, elle est déjà dans le store
  on('settings', 'language_changed', handleLanguageChanged);
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.language-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.language-button {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02) var(--space-02) var(--space-02) var(--space-03);
  background: var(--color-background-strong);
  border: 2px solid transparent;
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
  width: 100%;
  text-align: left;
}

.language-button:hover {
  background: var(--color-background);
  border-color: var(--color-background-glass);
}

.language-button.active {
  background: var(--color-background);
  border-color: var(--color-brand);
}

.language-flag {
  font-size: 20px;
  width: 24px;
  text-align: center;
}

.language-name {
  flex: 1;
  color: var(--color-text);
  font-size: var(--font-size-body);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .language-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
