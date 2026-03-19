<!-- frontend/src/components/setup/ModeStep.vue -->
<template>
  <div class="mode-step">
    <div class="mode-step__list">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        class="mode-step__card"
        @click="emit('update:modelValue', option.value)"
      >
        <div class="mode-step__header">
          <div class="mode-step__icon">
            <AppIcon :name="option.icon" :size="40" />
          </div>
          <span class="mode-step__title heading-3">{{ t(option.titleKey) }}</span>
          <Radio :model-value="modelValue === option.value" class="mode-step__radio" />
        </div>
        <span class="mode-step__description text-mono">{{ t(option.descriptionKey) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import AppIcon from '@/components/ui/AppIcon.vue';
import Radio from '@/components/ui/Radio.vue';

const { t } = useI18n();

defineProps({
  modelValue: {
    type: String,
    default: 'server',
  },
});

const emit = defineEmits(['update:modelValue']);

const options = [
  { value: 'server', icon: 'milo', titleKey: 'setup.mode.server', descriptionKey: 'setup.mode.serverDescription' },
  { value: 'client', icon: 'milo-client', titleKey: 'setup.mode.client', descriptionKey: 'setup.mode.clientDescription' },
];
</script>

<style scoped>
.mode-step {
  display: flex;
  flex-direction: column;
}

.mode-step__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.mode-step__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-04);
  border-radius: var(--radius-05);
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);
  width: 100%;
  text-align: left;
  cursor: pointer;
  border: none;
}

.mode-step__header {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.mode-step__icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text);
  border-radius: var(--radius-03);
  overflow: hidden;
}

.mode-step__icon :deep(img),
.mode-step__icon :deep(svg) {
  width: 40px;
  height: 40px;
}

.mode-step__icon :deep(path[fill="#F7F7F7"]) {
  fill: #FFFFFF;
}

.mode-step__title {
  flex: 1;
  color: var(--color-text);
}

.mode-step__radio {
  pointer-events: none;
  flex-shrink: 0;
}

.mode-step__description {
  color: var(--color-text-secondary);
}

/* Responsive - Mobile */
@media (max-aspect-ratio: 4/3) {
  .mode-step__card {
    border-radius: var(--radius-04);
  }

  .mode-step__icon {
    width: 36px;
    height: 36px;
    border-radius: var(--radius-02);
  }

  .mode-step__icon :deep(img),
  .mode-step__icon :deep(svg) {
    width: 36px;
    height: 36px;
  }
}
</style>
