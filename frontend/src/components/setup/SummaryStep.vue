<!-- frontend/src/components/setup/SummaryStep.vue -->
<template>
  <div class="summary-step">
    <h2 class="heading-2">{{ t('setup.summary.title') }}</h2>

    <div class="summary-table">
      <div class="summary-row">
        <span class="summary-label text-mono">{{ t('setup.summary.language') }}</span>
        <span class="summary-value heading-3">{{ languageLabel }}</span>
      </div>
      <div class="summary-row">
        <span class="summary-label text-mono">{{ t('setup.summary.audioCard') }}</span>
        <span class="summary-value heading-3">{{ audioLabel }}</span>
      </div>
      <div class="summary-row">
        <span class="summary-label text-mono">{{ t('setup.summary.screen') }}</span>
        <span class="summary-value heading-3">{{ screenLabel }}</span>
      </div>
    </div>

    <p class="text-mono text-secondary reboot-message">
      {{ t('setup.summary.rebootMessage') }}
    </p>

    <p v-if="error" class="text-mono error-message">{{ error }}</p>

    <!-- Rebooting state -->
    <div v-if="isRebooting" class="rebooting-section">
      <LoadingSpinner />
      <p class="heading-3">{{ t('setup.summary.rebooting') }}</p>
      <p class="text-mono text-secondary">{{ t('setup.summary.rebootingDescription') }}</p>
    </div>

    <Button
      v-else
      variant="brand"
      :loading="isApplying"
      @click="$emit('apply')"
    >
      {{ t('setup.summary.applyAndReboot') }}
    </Button>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const { t } = useI18n();

defineProps({
  languageLabel: {
    type: String,
    default: '',
  },
  audioLabel: {
    type: String,
    default: '',
  },
  screenLabel: {
    type: String,
    default: '',
  },
  isApplying: {
    type: Boolean,
    default: false,
  },
  isRebooting: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: null,
  },
});

defineEmits(['apply']);
</script>

<style scoped>
.summary-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-04);
  flex: 1;
}

.summary-table {
  width: 100%;
  max-width: 400px;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  background: var(--color-background);
  border-radius: var(--radius-03);
  padding: var(--space-04);
}

.summary-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.summary-label {
  color: var(--color-text-secondary);
}

.reboot-message {
  color: var(--color-text-secondary);
  text-align: center;
  max-width: 320px;
}

.error-message {
  color: var(--color-important);
  text-align: center;
}

.rebooting-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-06) 0;
  text-align: center;
}

.text-secondary {
  color: var(--color-text-secondary);
}
</style>
