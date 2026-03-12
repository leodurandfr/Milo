<!-- frontend/src/components/setup/LanguageStep.vue -->
<template>
  <div class="language-step">
    <h2 class="heading-2">{{ t('setup.language.title') }}</h2>

    <div class="language-list">
      <ListItemButton
        v-for="lang in availableLanguages"
        :key="lang.code"
        :title="lang.name"
        variant="background"
        action="radio"
        :model-value="modelValue === lang.code"
        @click="selectLanguage(lang.code)"
      >
        <template #icon>
          <img :src="getFlagIcon(lang.code)" :alt="lang.name" />
        </template>
      </ListItemButton>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n, i18n } from '@/services/i18n';
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
  german: germanyIcon,
};

const props = defineProps({
  modelValue: {
    type: String,
    default: 'english',
  },
});

const emit = defineEmits(['update:modelValue']);

const { t, getAvailableLanguages } = useI18n();
const availableLanguages = computed(() => getAvailableLanguages());

function getFlagIcon(code) {
  return flagIcons[code] || '';
}

async function selectLanguage(code) {
  emit('update:modelValue', code);
  // Instant preview: switch UI language locally (not persisted yet)
  await i18n.handleLanguageChanged(code);
}
</script>

<style scoped>
.language-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  flex: 1;
}

.language-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
  overflow-y: auto;
}

@media (max-aspect-ratio: 4/3) {
  .language-list {
    grid-template-columns: 1fr;
  }
}
</style>
