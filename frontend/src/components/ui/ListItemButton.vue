<!-- frontend/src/components/ui/ListItemButton.vue -->
<template>
  <button type="button" :class="['list-item-button', `list-item-button--${variant}`, { 'interactive-press': action !== 'toggle' }]" :disabled="disabled" @click="handleClick">
    <!-- Icon on the left -->
    <div v-if="$slots.icon" class="list-item-button__icon">
      <slot name="icon"></slot>
    </div>

    <!-- Title -->
    <span class="list-item-button__title heading-3">{{ title }}</span>

    <!-- Right-side action -->
    <div v-if="action !== 'none'" class="list-item-button__action">
      <SvgIcon v-if="action === 'caret'" name="caretRight" :size="24" class="caret-icon" />
      <Toggle v-else-if="action === 'toggle'" :model-value="modelValue" size="compact" :disabled="disabled" @click.stop @update:model-value="(val) => emit('update:modelValue', val)" />
    </div>
  </button>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';
import Toggle from '@/components/ui/Toggle.vue';

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'active', 'inactive'].includes(value)
  },
  action: {
    type: String,
    default: 'none',
    validator: (value) => ['none', 'caret', 'toggle'].includes(value)
  },
  modelValue: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['click', 'update:modelValue']);

function handleClick(event) {
  if (props.disabled) return;
  emit('click', event);
}
</script>

<style scoped>
.list-item-button {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  border-radius: var(--radius-05);
  transition: all var(--transition-fast);
  width: 100%;
  text-align: left;
}

/* Cursor: pointer only for clickable buttons (not toggle) */
.list-item-button:has(.caret-icon) {
  cursor: pointer;
}

/* Default variant is always clickable */
.list-item-button--default {
  cursor: pointer;
}

.list-item-button:disabled {
  cursor: not-allowed;
}

.list-item-button:disabled .list-item-button__title {
  color: var(--color-text-light);
}

/* Default variant (navigation items - SettingsModal) */
.list-item-button--default {
  background: var(--color-background-neutral);
}

/* Active & Inactive variants (base style) */
.list-item-button--active,
.list-item-button--inactive {
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);
}

/* Active WITHOUT toggle (selection): brand border */
.list-item-button--active:not(:has(.toggle-container)) {
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

/* Inactive: secondary text color */
.list-item-button--inactive .list-item-button__title {
  color: var(--color-text-secondary);
}

/* Active: normal text color */
.list-item-button--active .list-item-button__title {
  color: var(--color-text);
}

/* Left icon */
.list-item-button__icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.list-item-button__icon :deep(img),
.list-item-button__icon :deep(svg) {
  width: 40px;
  height: 40px;
}

/* Title */
.list-item-button__title {
  flex: 1;
  color: var(--color-text);
  transition: color var(--transition-fast);
}

/* Right action */
.list-item-button__action {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.caret-icon {
  color: var(--color-text-light);
}

/* Responsive - Mobile */
@media (max-aspect-ratio: 4/3) {
  .list-item-button__icon {
    width: 32px;
    height: 32px;
  }

  .list-item-button__icon :deep(img),
  .list-item-button__icon :deep(svg) {
    width: 32px;
    height: 32px;
  }
}
</style>
