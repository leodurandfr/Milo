<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <button 
    class="icon-button"
    :class="[`icon-button--${variant}`]"
    :disabled="disabled"
    @click="handleClick"
  >
    <Icon 
      :name="icon" 
      :color="color"
      responsive
      class="icon-button__icon"
    />
  </button>
</template>

<script setup>
import Icon from '@/components/ui/Icon.vue';

const props = defineProps({
  icon: {
    type: String,
    required: true
  },
  variant: {
    type: String,
    default: 'light',
    validator: (value) => ['light', 'dark'].includes(value)
  },
  disabled: {
    type: Boolean,
    default: false
  },
  color: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['click']);

function handleClick() {
  emit('click');
}
</script>

<style scoped>
.icon-button {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--radius-full);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: var(--transition-fast);
}

.icon-button__icon {
  color: var(--color-text-light);
}

.icon-button--light {
  background: var(--color-background-strong);
}

.icon-button--dark {
  background: var(--color-background-neutral-12);
}

.icon-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-aspect-ratio: 4/3) {
  .icon-button {
    width: 32px;
    height: 32px;
  }
}
</style>