<!-- frontend/src/components/setup/ScreenStep.vue -->
<template>
  <div class="screen-step">
    <div class="screen-list">
      <ListItemButton
        v-for="screen in screens"
        :key="screen.value"
        :title="screenLabel(screen)"
        variant="background"
        action="radio"
        :model-value="modelValue === screen.value"
        @click="emit('update:modelValue', screen.value)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import ListItemButton from '@/components/ui/ListItemButton.vue';

const { t } = useI18n();

defineProps({
  modelValue: {
    type: String,
    default: 'none',
  },
  screens: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['update:modelValue']);

function screenLabel(screen) {
  return screen.value === 'none' ? t('setup.screen.none') : screen.label;
}
</script>

<style scoped>
.screen-step {
  display: flex;
  flex-direction: column;
}

.screen-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}
</style>
