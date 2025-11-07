<!-- frontend/src/components/ui/IconButtonFloating.vue -->
<template>
  <button 
    class="icon-button-floating" 
    @click="$emit('click')"
    :aria-label="ariaLabel"
  >
    <Icon :name="iconName" :color="color" responsive />
  </button>
</template>

<script setup>
import Icon from './Icon.vue';

defineProps({
  iconName: {
    type: String,
    required: true
  },
  ariaLabel: {
    type: String,
    required: true
  },
  color: {
    type: String,
    default: null
  }
});

defineEmits(['click']);
</script>

<style scoped>
.icon-button-floating {
  position: absolute;
  background: var(--color-background-neutral-50);
  border: none;
  border-radius: 50%;
  width: 64px;
  height: 64px;
  cursor: pointer;
  color: var(--color-text);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  z-index: 5001;
}

.icon-button-floating::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.66;
  background: var(--stroke-glass);
  border-radius: 50%;
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .icon-button-floating {
    width: 48px;
    height: 48px;
  }
}
</style>