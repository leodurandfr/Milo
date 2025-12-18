<!-- frontend/src/components/ui/ButtonGroup.vue -->
<!-- Reusable button group for single-select options -->
<template>
  <div
    class="button-group"
    :class="[
      `button-group--${size}`,
      `button-group--mobile-${mobileLayout}`,
      { 'button-group--last-full': lastFullWidth }
    ]"
  >
    <Button
      v-for="option in options"
      :key="option.value"
      :variant="modelValue === option.value ? 'outline' : 'background-strong'"
      :size="size"
      :disabled="disabled || option.disabled"
      @click="selectOption(option.value)"
    >
      {{ option.label }}
    </Button>
  </div>
</template>

<script setup>
import Button from './Button.vue';

const props = defineProps({
  modelValue: {
    type: [String, Number, Boolean],
    default: null
  },
  options: {
    type: Array,
    required: true
    // Expected format: [{ label: 'Label', value: 'value', disabled?: boolean }]
  },
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['medium', 'small'].includes(value)
  },
  mobileLayout: {
    type: String,
    default: 'wrap',
    validator: (value) => ['wrap', 'column', 'column-reverse', 'grid-3'].includes(value)
  },
  lastFullWidth: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

function selectOption(value) {
  if (value !== props.modelValue && !props.disabled) {
    emit('update:modelValue', value);
    emit('change', value);
  }
}
</script>

<style scoped>
/* Base layout */
.button-group {
  display: flex;
  gap: var(--space-02);
  flex-wrap: wrap;
}

.button-group :deep(.btn) {
  flex: 1;
  white-space: nowrap;
}

/* Mobile layouts */
@media (max-aspect-ratio: 4/3) {
  /* Wrap (default) - keep flex wrap */
  .button-group--mobile-wrap {
    flex-wrap: wrap;
  }

  /* Column - stack vertically */
  .button-group--mobile-column {
    flex-direction: column;
  }

  /* Column reverse - stack vertically reversed */
  .button-group--mobile-column-reverse {
    flex-direction: column-reverse;
  }

  /* Grid 3 columns */
  .button-group--mobile-grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
  }

  /* Last item full width (for grid-3) */
  .button-group--mobile-grid-3.button-group--last-full :deep(.btn:last-child) {
    grid-column: 1 / -1;
  }
}
</style>
