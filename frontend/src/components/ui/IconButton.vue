<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <button
    v-press
    class="icon-button"
    :class="[
      `icon-button--${variant}`,
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
  variant: {
    type: String,
    default: 'background-strong',
    validator: (value) => ['background-strong', 'on-dark', 'on-grey', 'rounded'].includes(value)
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

// Pass size identifier to SvgIcon for responsive CSS sizing
const iconSize = computed(() => {
  return props.size;
});

// Icon color based on variant (if not overridden by color prop)
const iconColor = computed(() => {
  if (props.variant === 'on-grey') {
    return 'var(--color-text-contrast)';
  } else if (props.variant === 'rounded') {
    return 'var(--color-text)';
  } else if (props.variant === 'on-dark') {
    return 'var(--color-text-contrast)';
  } else {
    // background-strong
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
  transition: background-color var(--transition-fast), var(--transition-press);
  position: relative;
}

/* === SIZES (Desktop) === */
.icon-button--small {
  padding: 8px;
  border-radius: var(--radius-04);
}

.icon-button--medium {
  padding: 10px;
  border-radius: var(--radius-04);
}

.icon-button--large {
  padding: 14px;
  border-radius: var(--radius-05);
}

/* === SIZES (Mobile) === */
@media (max-aspect-ratio: 4/3) {
  .icon-button--small {
    padding: 6px;
    border-radius: var(--radius-03);
  }

  .icon-button--medium {
    padding: 8px;
    border-radius: var(--radius-03);
  }

  .icon-button--large {
    padding: 12px;
    border-radius: var(--radius-04);
  }

  .icon-button--rounded {
    padding: 12px;
  }
}

/* === VARIANTS === */
.icon-button--background-strong {
  background: var(--color-background-strong);
  color: var(--color-text-secondary);
}

.icon-button--on-dark {
  background: var(--color-background-neutral-12);
  color: var(--color-text-contrast);
}

.icon-button--on-grey {
  background: var(--color-background-contrast-12);
  color: var(--color-text-contrast);
}

.icon-button--rounded {
  background: var(--color-background-neutral-50);
  border-radius: 50% !important;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  width: fit-content;
  aspect-ratio: 1 / 1;
  color: var(--color-text);
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

/* === LOADING states (preserves variant styling) === */
.icon-button--background-strong.icon-button--loading {
  background: var(--color-background-strong);
  color: var(--color-text-secondary);
}

.icon-button--on-dark.icon-button--loading {
  background: var(--color-background-neutral-12);
  color: var(--color-text-contrast);
}

.icon-button--on-grey.icon-button--loading {
  background: var(--color-background-contrast-12);
  color: var(--color-text-contrast);
}

.icon-button--rounded.icon-button--loading {
  background: var(--color-background-neutral-50);
  color: var(--color-text);
}
</style>
