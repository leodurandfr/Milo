<!-- frontend/src/components/ui/NoticeBox.vue -->
<!-- An inline notice: an error, a warning, or the placeholder of a list that
     has nothing in it. An error says what failed in the slot, in the reader's
     language, and can carry the server's own words folded under it (`detail`):
     they name the field or the file, but in English and for whoever reads the
     logs, so they are there to open, not to read first. -->
<template>
  <div class="notice-box text-mono-medium" :class="`notice-box--${kind}`" :role="ROLES[kind]">
    <slot />
    <details v-if="detail" class="notice-box__detail">
      <summary class="text-mono-small">{{ t('common.details') }}</summary>
      <p class="notice-box__detail-text text-mono-small">{{ detail }}</p>
    </details>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';

defineProps({
  kind: {
    type: String,
    default: 'error',
    validator: (value) => ['error', 'warning', 'empty'].includes(value)
  },
  detail: {
    type: String,
    default: null
  }
});

const { t } = useI18n();

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

.notice-box__detail {
  margin-top: var(--space-02);
}

.notice-box__detail summary {
  cursor: pointer;
}

.notice-box__detail-text {
  margin: var(--space-01) 0 0;
  white-space: pre-line;
}
</style>
