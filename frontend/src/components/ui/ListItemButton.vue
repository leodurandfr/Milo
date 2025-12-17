<!-- frontend/src/components/ui/ListItemButton.vue -->
<template>
  <button
    type="button"
    v-press.light="action !== 'toggle' && action !== 'radio'"
    :class="['list-item-button', `list-item-button--${variant}`, { 'action-pressed': actionPressed }]"
    :disabled="disabled"
    @click="handleClick"
    @pointerdown="handlePointerDown"
  >
    <!-- Icon on the left -->
    <div v-if="$slots.icon" class="list-item-button__icon">
      <slot name="icon"></slot>
    </div>

    <!-- Title -->
    <span
      class="list-item-button__title heading-3"
      :class="{ 'list-item-button__title--inactive': isActionInactive }"
    >
      {{ title }}
    </span>

    <!-- Right-side action -->
    <div v-if="action !== 'none'" class="list-item-button__action">
      <SvgIcon v-if="action === 'caret'" name="caretRight" :size="24" class="caret-icon" />
      <Toggle v-else-if="action === 'toggle'" :model-value="modelValue" size="compact" :disabled="disabled" />
      <Radio v-else-if="action === 'radio'" :model-value="modelValue" :disabled="disabled" />
    </div>
  </button>
</template>

<script setup>
import { ref, computed } from 'vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Radio from '@/components/ui/Radio.vue';

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  variant: {
    type: String,
    default: 'background-neutral',
    validator: (value) => ['background-neutral', 'background'].includes(value)
  },
  action: {
    type: String,
    default: 'none',
    validator: (value) => ['none', 'caret', 'toggle', 'radio'].includes(value)
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

// Press state for action button animation
const actionPressed = ref(false);

// Text is inactive (secondary color) when toggle/radio action is OFF
const isActionInactive = computed(() => {
  return (props.action === 'toggle' || props.action === 'radio') && !props.modelValue;
});

// Handle press animation for toggle/radio actions
function handlePointerDown() {
  if (props.disabled) return;
  if (props.action !== 'toggle' && props.action !== 'radio') return;

  actionPressed.value = true;
  setTimeout(() => {
    actionPressed.value = false;
  }, 150);
}

function handleClick(event) {
  if (props.disabled) return;

  // For toggle/radio actions, also toggle the modelValue
  if (props.action === 'toggle' || props.action === 'radio') {
    emit('update:modelValue', !props.modelValue);
  }

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
  transition: background-color var(--transition-fast), box-shadow var(--transition-fast), var(--transition-press);
  width: 100%;
  text-align: left;
  cursor: pointer;
}

.list-item-button:disabled {
  cursor: not-allowed;
}

.list-item-button:disabled .list-item-button__title {
  color: var(--color-text-light);
}

/* Background-neutral variant (white background) */
.list-item-button--background-neutral {
  background: var(--color-background-neutral);
}

/* Background variant (grey background with border) */
.list-item-button--background {
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);
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

/* Inactive text (toggle/radio OFF) */
.list-item-button__title--inactive {
  color: var(--color-text-secondary);
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

/* Prevent double-toggle when clicking directly on Toggle/Radio */
.list-item-button__action :deep(.toggle),
.list-item-button__action :deep(.radio) {
  pointer-events: none;
  transition: var(--transition-press);
}

/* Press effect on Toggle/Radio when action-pressed */
.list-item-button.action-pressed .list-item-button__action :deep(.toggle),
.list-item-button.action-pressed .list-item-button__action :deep(.radio) {
  transform: scale(0.88);
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
