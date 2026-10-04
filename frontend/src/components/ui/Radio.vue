<!-- frontend/src/components/ui/Radio.vue -->
<template>
  <button
    type="button"
    class="radio"
    :class="{ 'radio--active': modelValue }"
    :disabled="disabled"
    v-press
    @click="toggle"
  >
    <span class="radio__dot"></span>
  </button>
</template>

<script setup>
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue']);

function toggle() {
  if (!props.disabled) {
    emit('update:modelValue', !props.modelValue);
  }
}
</script>

<style scoped>
.radio {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background: var(--color-fill-off);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color var(--transition-fast), var(--transition-press);
  cursor: pointer;
  flex-shrink: 0;
  border: none;
  padding: 0;
}

.radio--active {
  background: var(--color-brand);
}

.radio__dot {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-full);
  background: var(--color-panel);
  transition: width var(--transition-fast), height var(--transition-fast);
}

/* Off, the dot is a hole in the ring; on, it is the thumb on the brand. */
.radio--active .radio__dot {
  width: 16px;
  height: 16px;
  background: var(--color-thumb);
}

.radio:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
