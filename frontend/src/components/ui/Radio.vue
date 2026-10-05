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
  background: transparent;
  box-shadow: inset 0 0 0 2px var(--color-fill-off);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color var(--transition-fast), box-shadow var(--transition-fast), var(--transition-press);
  cursor: pointer;
  flex-shrink: 0;
  border: none;
  padding: 0;
}

.radio--active {
  background: var(--color-brand);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

.radio__dot {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-full);
  transition: width var(--transition-fast), height var(--transition-fast);
}

/* Off, the ring is drawn and its middle left empty, so it reads on any panel,
   translucent ones included; on, the dot is the thumb on the brand. */
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
