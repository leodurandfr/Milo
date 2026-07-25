<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <button
    v-press
    class="icon-button"
    :class="[
      `icon-button--${variant}`,
      `icon-button--${size}`,
      { 'icon-button--loading': loading },
      variant === 'rounded' ? 'glass-surface glass-border' : ''
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
    validator: (value) => ['background-strong', 'on-dark', 'on-grey', 'rounded', 'brand', 'ghost'].includes(value)
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
  } else if (props.variant === 'brand') {
    return 'var(--color-text-contrast)';
  } else if (props.variant === 'ghost') {
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
  width: fit-content;
  aspect-ratio: 1 / 1;
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
  backdrop-filter: blur(var(--blur-02));
}

.icon-button--brand {
  background: var(--color-brand);
  color: var(--color-text-contrast);
}

/* Icon-only, no pill background — a flat padding regardless of size (this
   comes after the SIZES blocks above so it wins their padding by cascade
   order at both desktop and mobile). */
.icon-button--ghost {
  background: transparent;
  padding: var(--space-02);
}

.icon-button--rounded {
  --glass-radius: 50%;
  border-radius: 50% !important;
  width: fit-content;
  aspect-ratio: 1 / 1;
  color: var(--color-text);
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

.icon-button--rounded::before {
  opacity: 0.8;
}

/* Disable press opacity for semi-transparent backgrounds (scale only) */
.icon-button--rounded.interactive-press:active,
.icon-button--rounded.interactive-press.pressed {
  opacity: 1 !important;
}

/* === STATES === */
.icon-button:disabled {
  opacity: 0.24;
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

.icon-button--brand.icon-button--loading {
  background: var(--color-brand);
  color: var(--color-text-contrast);
}

.icon-button--ghost.icon-button--loading {
  background: transparent;
  color: var(--color-text-contrast-50);
}
</style>
