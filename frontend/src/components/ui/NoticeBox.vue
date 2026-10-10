<!-- frontend/src/components/ui/NoticeBox.vue -->
<!-- An inline notice: an error, a warning, or the placeholder of a list that
     has nothing in it. -->
<template>
  <div class="notice-box text-mono-medium" :class="`notice-box--${kind}`" :role="ROLES[kind]">
    <slot />
  </div>
</template>

<script setup>
defineProps({
  kind: {
    type: String,
    default: 'error',
    validator: (value) => ['error', 'warning', 'empty'].includes(value)
  }
});

// Errors and warnings appear after an action, so a screen reader announces
// them; an empty list's placeholder is just content.
const ROLES = { error: 'alert', warning: 'status', empty: undefined };
</script>

<style scoped>
.notice-box {
  padding: var(--space-03);
  border-radius: var(--radius-04);
  overflow-wrap: anywhere;
}

.notice-box--error {
  background: var(--color-error-subtle);
  color: var(--color-error);
}

.notice-box--warning {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.notice-box--empty {
  padding: var(--space-05);
  background: var(--color-inset);
  border: 2px dashed var(--color-border);
  color: var(--color-text-secondary);
  text-align: center;
}
</style>
