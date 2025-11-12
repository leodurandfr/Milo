<!-- frontend/src/components/ui/ButtonDropdown.vue -->
<template>
  <div class="button-dropdown">
    <select
      :value="modelValue"
      :class="selectClass"
      @change="handleChange"
    >
      <slot></slot>
    </select>
    <Icon name="caretDown" :size="24" class="dropdown-icon" />
  </div>
</template>

<script setup>
import Icon from '@/components/ui/Icon.vue';

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  selectClass: {
    type: String,
    default: 'text-body-small'
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

function handleChange(event) {
  emit('update:modelValue', event.target.value);
  emit('change', event);
}
</script>

<style scoped>
.button-dropdown {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  flex: 1;
}

select {
  width: 100%;
  padding: var(--space-03) var(--space-04);
  padding-right: calc(var(--space-04) + 24px + var(--space-02));
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
  transition: border-color var(--transition-fast);
  appearance: none;
  cursor: pointer;
  outline: none;
}

select:focus {
  border-color: var(--color-brand);
}

.dropdown-icon {
  position: absolute;
  right: var(--space-03);
  pointer-events: none;
  color: var(--color-text-secondary);
}
</style>
