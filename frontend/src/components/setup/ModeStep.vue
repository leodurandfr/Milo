<!-- frontend/src/components/setup/ModeStep.vue -->
<template>
  <div class="mode-step">
    <div class="mode-step__list">
      <ListItemButton
        variant="background"
        action="radio"
        :model-value="modelValue === 'server'"
        @click="emit('update:modelValue', 'server')"
      >
        <template #icon>
          <AppIcon name="milo" :size="40" />
        </template>
        <template #title>
          <div class="mode-step__option">
            <span>{{ t('setup.mode.server') }}</span>
            <span class="text-mono mode-step__description">{{ t('setup.mode.serverDescription') }}</span>
          </div>
        </template>
      </ListItemButton>

      <ListItemButton
        variant="background"
        action="radio"
        :model-value="modelValue === 'client'"
        @click="emit('update:modelValue', 'client')"
      >
        <template #icon>
          <AppIcon name="milo-client" :size="40" />
        </template>
        <template #title>
          <div class="mode-step__option">
            <span>{{ t('setup.mode.client') }}</span>
            <span class="text-mono mode-step__description">{{ t('setup.mode.clientDescription') }}</span>
          </div>
        </template>
      </ListItemButton>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import AppIcon from '@/components/ui/AppIcon.vue';

const { t } = useI18n();

defineProps({
  modelValue: {
    type: String,
    default: 'server',
  },
});

const emit = defineEmits(['update:modelValue']);
</script>

<style scoped>
.mode-step {
  display: flex;
  flex-direction: column;
}

.mode-step__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.mode-step__option {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding: var(--space-01) 0;
}

.mode-step__description {
  color: var(--color-text-secondary);
  font-size: 11px;
}
</style>
