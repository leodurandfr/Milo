<!-- frontend/src/components/setup/AudioStep.vue -->
<template>
  <div class="audio-step">
    <h2 class="heading-2">{{ t('setup.audio.title') }}</h2>

    <div class="audio-list">
      <ListItemButton
        v-for="card in audioCards"
        :key="card.value"
        :title="card.label"
        variant="background"
        action="radio"
        :model-value="modelValue === card.value"
        @click="emit('update:modelValue', card.value)"
      />
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import ListItemButton from '@/components/ui/ListItemButton.vue';

const { t } = useI18n();

defineProps({
  modelValue: {
    type: String,
    default: 'none',
  },
  audioCards: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['update:modelValue']);
</script>

<style scoped>
.audio-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  flex: 1;
  min-height: 0;
}

.audio-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  overflow-y: auto;
}
</style>
