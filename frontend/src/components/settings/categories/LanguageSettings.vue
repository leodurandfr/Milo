<!-- frontend/src/components/settings/categories/LanguageSettings.vue -->
<template>
  <SettingsSection>
    <div class="language-grid">
      <ListItemButton
        v-for="language in availableLanguages"
        :key="language.code"
        :title="language.name"
        variant="background"
        action="radio"
        :model-value="currentLanguage === language.code"
        @click="selectLanguage(language.code)"
      >
        <template #icon>
          <img :src="getFlagIcon(language.code)" :alt="language.name" />
        </template>
      </ListItemButton>
    </div>
  </SettingsSection>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

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
const { updateSetting } = useSettingsAPI();

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

function getFlagIcon(languageCode) {
  return flagIcons[languageCode] || '';
}

async function selectLanguage(languageCode) {
  await updateSetting('language', { language: languageCode });
}
</script>

<style scoped>
.language-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

@media (max-aspect-ratio: 4/3) {
  .language-grid {
    grid-template-columns: 1fr;
  }
}
</style>
