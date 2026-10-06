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
/* Off, a translucent disc (the ground of a Toggle that is off), so it reads
   on any panel; on, the brand with the thumb's dot. */
.radio {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background: var(--color-fill-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color var(--transition-fast), opacity var(--transition-fast), var(--transition-press);
  cursor: pointer;
  flex-shrink: 0;
  border: none;
  padding: 0;
}

.radio--active {
  background: var(--color-brand);
}

/* The dot keeps its size and fades: in from half its size on the spring,
   out to half its size without one, so it never shrinks to a speck. */
.radio__dot {
  width: 12px;
  height: 12px;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
  box-shadow: var(--shadow-knob);
  opacity: 0;
  transform: scale(0.5);
  transition:
    transform var(--transition-fast-leave),
    opacity var(--transition-fast-leave);
}

.radio--active .radio__dot {
  opacity: 1;
  transform: none;
  transition:
    transform var(--transition-spring-light),
    opacity var(--transition-fast);
}

.radio:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}
</style>
