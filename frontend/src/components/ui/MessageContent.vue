<!-- frontend/src/components/ui/MessageContent.vue -->
<template>
  <div class="message-content">
    <!-- Loading spinner OU icône (mutuellement exclusifs) -->
    <LoadingSpinner v-if="loading" :size="64" />
    <SvgIcon v-else-if="icon" :name="icon" :size="64" color="var(--color-background-medium-16)" />

    <!-- Contenu toujours visible (même en loading) -->
    <p v-if="title" class="heading-2">{{ title }}</p>
    <p v-if="subtitle" class="text-mono">{{ subtitle }}</p>
    <Button v-if="ctaLabel" :variant="ctaVariant" @click="ctaClick">
      {{ ctaLabel }}
    </Button>
  </div>
</template>

<script setup>
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import Button from '@/components/ui/Button.vue'

defineProps({
  loading: {
    type: Boolean,
    default: false
  },
  icon: {
    type: String,
    default: null
  },
  title: {
    type: String,
    default: null
  },
  subtitle: {
    type: String,
    default: null
  },
  ctaLabel: {
    type: String,
    default: null
  },
  ctaVariant: {
    type: String,
    default: 'brand'
  },
  ctaClick: {
    type: Function,
    default: null
  }
})
</script>

<style scoped>
.message-content {
  display: flex;
  min-height: 280px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
  text-align: center;
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
}

.message-content :deep(p),
.message-content :deep(.text-mono),
.message-content :deep(.heading-2) {
  color: var(--color-text-secondary);
}

.message-content :deep(.loading-spinner) {
  color: var(--color-text-secondary);
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}
</style>
