<!-- frontend/src/components/setup/LanguageStep.vue -->
<template>
  <div class="language-step">
    <div class="language-list">
      <ListItemButton
        v-for="lang in availableLanguages"
        :key="lang.code"
        :title="lang.name"
        action="radio"
        :model-value="modelValue === lang.code"
        @click="selectLanguage(lang.code)"
      >
        <template #icon>
          <img :src="languageFlag(lang.code)" :alt="lang.name" />
        </template>
      </ListItemButton>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n, i18n } from '@/services/i18n';
import ListItemButton from '@/components/ui/ListItemButton.vue';

import { languageFlag } from '@/constants/languageFlags';

const props = defineProps({
  modelValue: {
    type: String,
    default: 'english',
  },
});

const emit = defineEmits(['update:modelValue']);

const { getAvailableLanguages } = useI18n();
const availableLanguages = computed(() => getAvailableLanguages());

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
}

.language-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}
</style>
