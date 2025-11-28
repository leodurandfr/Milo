<!-- frontend/src/components/settings/categories/LanguageSettings.vue -->
<template>
  <section class="settings-section">
    <div class="language-grid">
      <ListItemButton
        v-for="language in availableLanguages"
        :key="language.code"
        :title="language.name"
        :variant="currentLanguage === language.code ? 'active' : 'inactive'"
        @click="selectLanguage(language.code)"
      >
        <template #icon>
          <img :src="getFlagIcon(language.code)" :alt="language.name" />
        </template>
      </ListItemButton>
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
import ListItemButton from '@/components/ui/ListItemButton.vue';

import franceIcon from '@/assets/flags-icons/france.svg';
import unitedKingdomIcon from '@/assets/flags-icons/united-kingdom.svg';
import spainIcon from '@/assets/flags-icons/spain.svg';
import indiaIcon from '@/assets/flags-icons/india.svg';
import chinaIcon from '@/assets/flags-icons/china.svg';
import portugalIcon from '@/assets/flags-icons/portugal.svg';
import italyIcon from '@/assets/flags-icons/italy.svg';
import germanyIcon from '@/assets/flags-icons/germany.svg';

const flagIcons = {
  french: franceIcon,
  english: unitedKingdomIcon,
  spanish: spainIcon,
  hindi: indiaIcon,
  chinese: chinaIcon,
  portuguese: portugalIcon,
  italian: italyIcon,
  german: germanyIcon
};

const { getAvailableLanguages, getCurrentLanguage } = useI18n();
const { on } = useWebSocket();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

function getFlagIcon(languageCode) {
  return flagIcons[languageCode] || '';
}

async function selectLanguage(languageCode) {
  await updateSetting('language', { language: languageCode });
}

// WebSocket listener
const handleLanguageChanged = (msg) => {
  const newLanguage = msg.data?.language;
  if (newLanguage) {
    i18n.handleLanguageChanged(newLanguage);
    // Update store
    settingsStore.updateLanguage(newLanguage);
  }
};

onMounted(() => {
  on('settings', 'language_changed', handleLanguageChanged);
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.language-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
    .settings-section {
    border-radius: var(--radius-05);
  }
  .language-grid {
    grid-template-columns: 1fr;
  }
  
}
</style>
