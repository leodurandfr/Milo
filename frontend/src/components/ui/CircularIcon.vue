<!-- frontend/src/components/ui/CircularIcon.vue -->
<template>
  <button
    class="circular-icon"
    :class="[`circular-icon--${variant}`]"
    :disabled="disabled"
    @click="handleClick"
  >
    <LoadingSpinner v-if="loading" :size="24" />
    <Icon
      v-else
      :name="icon"
      :color="color"
      responsive
      class="circular-icon__icon"
    />
  </button>
</template>

<script setup>
import Icon from '@/components/ui/Icon.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const props = defineProps({
  icon: {
    type: String,
    required: true
  },
  variant: {
    type: String,
    default: 'light',
    validator: (value) => ['light', 'dark', 'overlay', 'primary', 'secondary', 'toggle', 'background-light'].includes(value)
  },
  disabled: {
    type: Boolean,
    default: false
  },
  color: {
    type: String,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['click']);

function handleClick() {
  emit('click');
}
</script>

<style scoped>
.circular-icon {
  width: 48px;
  height: 48px;
  border: none;
  border-radius: var(--radius-04);
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
  background: var(--color-background);
}

.circular-icon--dark {
  background: var(--color-background-neutral-12);
}

.circular-icon--overlay {
  background: rgba(0, 0, 0, 0.12);
}

.circular-icon--overlay .circular-icon__icon {
  color: var(--color-text-contrast);
}

.circular-icon--primary {
  background: var(--color-brand);
}

.circular-icon--primary .circular-icon__icon {
  color: var(--color-text-contrast);
}

.circular-icon--secondary {
  background: var(--color-background-strong);
}

.circular-icon--secondary .circular-icon__icon {
  color: var(--color-text-secondary);
}

.circular-icon--toggle {
  background: var(--color-background-neutral);
  -webkit-box-shadow: inset 0px 0px 0px 2px var(--color-brand);
  -moz-box-shadow: inset 0px 0px 0px 2px var(--color-brand);
  box-shadow: inset 0px 0px 0px 2px var(--color-brand);
}

.circular-icon--toggle .circular-icon__icon {
  color: var(--color-brand);
}

.circular-icon--background-light {
  background: var(--color-background-neutral-12);
}

.circular-icon--background-light .circular-icon__icon {
  color: var(--color-text-contrast);
}

.circular-icon:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-aspect-ratio: 4/3) {
  .circular-icon {
    width: 40px;
    height: 40px;
    border-radius: var(--radius-03);
  }
}
</style>