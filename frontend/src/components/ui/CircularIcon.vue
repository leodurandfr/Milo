<!-- frontend/src/components/ui/CircularIcon.vue -->
<template>
  <button 
    class="circular-icon"
    :class="[`circular-icon--${variant}`]"
    :disabled="disabled"
    @click="handleClick"
  >
    <Icon 
      :name="icon" 
      :color="color"
      responsive
      class="circular-icon__icon"
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
.circular-icon {
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

.circular-icon__icon {
  color: var(--color-text-light);
}

.circular-icon--light {
  background: var(--color-background-strong);
}

.circular-icon--dark {
  background: var(--color-background-neutral-12);
}

.circular-icon:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-aspect-ratio: 4/3) {
  .circular-icon {
    width: 32px;
    height: 32px;
  }
}
</style>