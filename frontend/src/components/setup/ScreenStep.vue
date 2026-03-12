<!-- frontend/src/components/setup/ScreenStep.vue -->
<template>
  <div class="screen-step">
    <h2 class="heading-2">{{ t('setup.screen.title') }}</h2>

    <div class="screen-list">
      <ListItemButton
        v-for="screen in screens"
        :key="screen.value"
        :title="screen.label"
        variant="background"
        action="radio"
        :model-value="modelValue === screen.value"
        @click="emit('update:modelValue', screen.value)"
      />
    </div>

    <p class="text-mono text-secondary reboot-note">
      {{ t('setup.screen.rebootNote') }}
    </p>
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
  screens: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['update:modelValue']);
</script>

<style scoped>
.screen-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  flex: 1;
}

.screen-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.reboot-note {
  color: var(--color-text-secondary);
  text-align: center;
}
</style>
