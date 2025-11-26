<!-- frontend/src/components/ui/MessageContent.vue -->
<template>
  <div class="message-content">
    <LoadingSpinner v-if="loading" :size="64" />
    <slot></slot>
  </div>
</template>

<script setup>
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'

defineProps({
  loading: {
    type: Boolean,
    default: false
  }
})
</script>

<style scoped>
.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
  text-align: center;
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
