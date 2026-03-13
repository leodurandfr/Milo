<!-- frontend/src/components/setup/WelcomeStep.vue -->
<template>
  <div class="welcome-step">
    <AppIcon name="milo" size="large" class="welcome-step__icon" />
    <h1 class="heading-1">{{ t('setup.welcome.title') }}</h1>
    <p class="text-mono text-secondary">{{ t('setup.welcome.subtitle') }}</p>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import AppIcon from '@/components/ui/AppIcon.vue';

const { t } = useI18n();
</script>

<style scoped>
.welcome-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  gap: var(--space-02);
  text-align: center;
}

.welcome-step__icon {
  margin-bottom: var(--space-03);
}

.text-secondary {
  color: var(--color-text-secondary);
}
</style>
