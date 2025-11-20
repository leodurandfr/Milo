<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <button
    class="icon-button"
    :class="[
      `icon-button--${type}`,
      `icon-button--${size}`,
      { 'icon-button--loading': loading }
    ]"
    :disabled="disabled"
    @click="handleClick"
  >
    <LoadingSpinner v-if="loading" :size="iconSize" />
    <SvgIcon
      v-else
      :name="icon"
      :size="iconSize"
      :color="color || iconColor"
    />
  </button>
</template>

<script setup>
import { computed } from 'vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const props = defineProps({
  icon: {
    type: String,
    required: true
  },
  type: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'light', 'dark', 'rounded'].includes(value)
  },
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['small', 'medium', 'large'].includes(value)
  },
  loading: {
    type: Boolean,
    default: false
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

// Icon sizes based on button size
const iconSize = computed(() => {
  const sizes = {
    small: 16,
    medium: 32,
    large: 32
  };
  return sizes[props.size];
});

// Icon color based on type (if not overridden by color prop)
const iconColor = computed(() => {
  if (props.type === 'dark') {
    return 'var(--color-text-contrast)';
  } else if (props.type === 'rounded') {
    return 'var(--color-text)';
  } else if (props.type === 'light') {
    return 'var(--color-text-light)';
  } else {
    // default
    return 'var(--color-text-secondary)';
  }
});

function handleClick(event) {
  if (!props.disabled && !props.loading) {
    emit('click', event);
  }
}
</script>

<style scoped>
.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

/* === SIZES (Desktop) === */
.icon-button--small {
  padding: 8px;
  border-radius: var(--radius-04);
}

.icon-button--medium {
  padding: 8px;
  border-radius: var(--radius-04);
}

.icon-button--large {
  padding: 12px;
  border-radius: var(--radius-04);
}

/* === SIZES (Mobile) === */
@media (max-aspect-ratio: 4/3) {
  .icon-button--small {
    padding: 8px;
    border-radius: var(--radius-03);
  }

  .icon-button--medium {
    padding: 6px;
    border-radius: var(--radius-03);
  }

  .icon-button--large {
    /* Auto-downgrade to medium size on mobile */
    padding: 6px;
    border-radius: var(--radius-03);
  }
}

/* === TYPES === */
.icon-button--light {
  background: var(--color-background-neutral-12);
}

.icon-button--default {
  background: var(--color-background-strong);
}

.icon-button--dark {
  background: var(--color-background-contrast-12);
}

.icon-button--rounded {
  background: var(--color-background-neutral-50);
  border-radius: 50% !important;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

/* Glass border effect for rounded type */
.icon-button--rounded::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  border-radius: 50%;
  z-index: -1;
  pointer-events: none;
}

/* === STATES === */
.icon-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.icon-button--loading {
  pointer-events: none;
}
</style>
