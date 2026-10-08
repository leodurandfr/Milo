<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <button
    v-press
    class="icon-button"
    :class="[
      `icon-button--${variant}`,
      `icon-button--${size}`,
      { 'icon-button--loading': loading },
      GLASS.includes(variant) ? 'icon-button--glass-plate glass-shell' : ''
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
  // `glass` is a round glass plate; `glass-on-contrast` the same plate on a
  // contrast surface (Lyrics), where it takes the glass's dark tone and a
  // white glyph.
  // Chosen by the caller rather than read from useDarkSurface(): that counter
  // stays raised for a modal opened over Lyrics, which would flip that modal's
  // own close button.
  variant: {
    type: String,
    default: 'control',
    validator: (value) => ['control', 'on-contrast', 'glass', 'glass-on-contrast', 'brand', 'ghost'].includes(value)
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

const GLASS = ['glass', 'glass-on-contrast'];

/** The glyph's ink per variant, unless the caller passes `color`. */
const INKS = {
  control: 'var(--color-text)',
  'on-contrast': 'var(--color-text-on-contrast)',
  glass: 'var(--color-text)',
  'glass-on-contrast': 'var(--color-text-on-contrast)',
  brand: 'var(--color-text-on-brand)',
  // A ghost has no plate: it takes the ink of whatever it is drawn on.
  ghost: 'currentColor'
};

// Pass size identifier to SvgIcon for responsive CSS sizing
const iconSize = computed(() => {
  return props.size;
});

const iconColor = computed(() => INKS[props.variant]);

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
}

/* === VARIANTS === */
.icon-button--control {
  background: var(--color-fill-faint);
  color: var(--color-text);
}

.icon-button--on-contrast {
  background: var(--color-glint);
  color: var(--color-text-on-contrast);
}

.icon-button--brand {
  background: var(--color-brand);
  color: var(--color-text-on-brand);
}

/* Icon-only, no pill background — a flat padding regardless of size (this
   comes after the SIZES blocks above so it wins their padding by cascade
   order at both desktop and mobile). */
.icon-button--ghost {
  background: transparent;
  padding: var(--space-02);
  color: inherit;
}

.icon-button--glass-plate {
  border-radius: 50% !important;
  width: fit-content;
  aspect-ratio: 1 / 1;
  color: var(--color-text);
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

/* The glass plate on a contrast surface: the glass's dark tone in both
   themes. */
.icon-button--glass-on-contrast {
  --glass-tone: var(--color-shell-on-contrast);
  color: var(--color-text-on-contrast);
}

/* Disable press opacity for semi-transparent backgrounds (scale only) */
.icon-button--glass-plate.interactive-press:active,
.icon-button--glass-plate.interactive-press.pressed {
  opacity: 1 !important;
}

/* === STATES === */
.icon-button:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

.icon-button--loading {
  pointer-events: none;
}

/* === LOADING states (preserves variant styling) === */
.icon-button--control.icon-button--loading {
  background: var(--color-fill-faint);
  color: var(--color-text);
}

.icon-button--on-contrast.icon-button--loading {
  background: var(--color-glint);
  color: var(--color-text-on-contrast);
}

.icon-button--glass.icon-button--loading {
  color: var(--color-text);
}

.icon-button--glass-on-contrast.icon-button--loading {
  color: var(--color-text-on-contrast);
}

.icon-button--brand.icon-button--loading {
  background: var(--color-brand);
  color: var(--color-text-on-brand);
}

/* A ghost has no plate to keep, so it dims the ink it inherits. */
.icon-button--ghost.icon-button--loading {
  background: transparent;
  color: color-mix(in srgb, currentColor 50%, transparent);
}
</style>
