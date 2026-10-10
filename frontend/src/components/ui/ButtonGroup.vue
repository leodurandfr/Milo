<!-- frontend/src/components/ui/ButtonGroup.vue -->
<!-- Single-select segmented control: the options sit in one track, and a thumb
     glides under the selected one. -->
<template>
  <div
    class="button-group"
    :class="[`button-group--${size}`, `button-group--${width}`, `button-group--mobile-${mobileLayout}`, { 'button-group--disabled': disabled }]"
  >
    <div ref="trackEl" class="button-group__track" role="group">
      <span
        class="button-group__thumb"
        :class="{ 'button-group__thumb--placed': placed, 'button-group__thumb--hidden': !thumb }"
        :style="thumbStyle"
        aria-hidden="true"
      ></span>
      <button
        v-for="option in options"
        :key="option.value"
        v-press
        type="button"
        class="button-group__option"
        :class="[size === 'small' ? 'heading-5' : 'heading-4', { 'button-group__option--active': modelValue === option.value }]"
        :data-value="option.value"
        :aria-pressed="modelValue === option.value"
        :disabled="disabled || option.disabled"
        @click="selectOption(option.value)"
      >
        {{ option.label }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';

const props = defineProps({
  modelValue: {
    type: [String, Number, Boolean],
    default: null
  },
  options: {
    type: Array,
    required: true
    // Expected format: [{ label: 'Label', value: 'value', disabled?: boolean }]
  },
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['medium', 'small'].includes(value)
  },
  // 'fill': the options share the row's width (tabs); 'hug': each is as wide
  // as its label, the group as wide as its options (filters beside a title).
  width: {
    type: String,
    default: 'fill',
    validator: (value) => ['fill', 'hug'].includes(value)
  },
  mobileLayout: {
    type: String,
    default: 'wrap',
    validator: (value) => ['wrap', 'row', 'column', 'column-reverse', 'grid-3', 'scroll'].includes(value)
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

const trackEl = ref(null);
const thumb = ref(null);
// The first placement lands without a glide, so a group never animates in
// from the track's corner.
const placed = ref(false);

const thumbStyle = computed(() => (thumb.value
  ? {
      width: `${thumb.value.width}px`,
      height: `${thumb.value.height}px`,
      transform: `translate(${thumb.value.x}px, ${thumb.value.y}px)`
    }
  : null));

// Measured from the option rather than computed from its index: the mobile
// layouts reflow the options into a column, a grid or a wrapped row, and the
// thumb follows whichever cell the selected one landed in.
function placeThumb() {
  const track = trackEl.value;
  if (!track) return;
  const active = [...track.querySelectorAll('.button-group__option')]
    .find((el) => el.classList.contains('button-group__option--active'));
  if (!active) {
    thumb.value = null;
    return;
  }
  // Hidden (display: none, under the expanded player): nothing to measure.
  // The ResizeObserver places it when it shows, still without a glide.
  if (!active.offsetWidth) return;
  thumb.value = { x: active.offsetLeft, y: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight };
  if (!placed.value) requestAnimationFrame(() => { placed.value = true; });
}

let resizeObserver = null;

onMounted(() => {
  placeThumb();
  resizeObserver = new ResizeObserver(placeThumb);
  resizeObserver.observe(trackEl.value);
});

onBeforeUnmount(() => resizeObserver?.disconnect());

watch(() => [props.modelValue, props.options], () => nextTick(placeThumb), { deep: true });

function selectOption(value) {
  if (value !== props.modelValue && !props.disabled) {
    emit('update:modelValue', value);
    emit('change', value);
  }
}
</script>

<style scoped>
.button-group {
  display: flex;
}

.button-group--hug {
  width: fit-content;
  max-width: 100%;
}

.button-group__track {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  padding: var(--space-01);
  background: var(--color-fill-faint);
}

.button-group--medium .button-group__track {
  border-radius: var(--radius-05);
}

.button-group--small .button-group__track {
  border-radius: var(--radius-05);
}

/* The panel showing through the track: white in light, the panel's own dark
   gray in dark. */
.button-group__thumb {
  position: absolute;
  top: 0;
  left: 0;
  background: var(--color-surface);
  box-shadow: var(--shadow-thumb);
  pointer-events: none;
  transition: opacity var(--transition-fast);
}

.button-group__thumb--placed {
  transition:
    transform var(--transition-spring-soft),
    width var(--transition-spring-soft),
    height var(--transition-spring-soft),
    opacity var(--transition-fast);
}

.button-group__thumb--hidden {
  opacity: 0;
}

/* Disabled: the group as it is, faded; a single option fades on its own. */
.button-group--disabled {
  opacity: var(--opacity-disabled);
}

.button-group__option {
  position: relative;
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  border: none;
  background: none;
  /* Every label in the primary ink, as iOS's segmented control: the thumb says
     which is chosen, and the track can lie over a source's wash, where the
     secondary falls under 4.5:1. */
  color: var(--color-text);
  cursor: pointer;
  transition: opacity var(--transition-fast), var(--transition-press);
}

.button-group--hug .button-group__option {
  flex: 0 0 auto;
}

.button-group__option:disabled {
  cursor: not-allowed;
}

.button-group:not(.button-group--disabled) .button-group__option:disabled {
  opacity: var(--opacity-disabled);
}

/* Track 48px / 40px, the option 4px inside it, radii concentric. */
.button-group--medium .button-group__option,
.button-group--medium .button-group__thumb {
  border-radius: var(--radius-04);
}

.button-group--medium .button-group__option {
  height: 40px;
  padding: 0 var(--space-04);
}

.button-group--small .button-group__option,
.button-group--small .button-group__thumb {
  border-radius: var(--radius-04);
}

.button-group--small .button-group__option {
  height: 32px;
  padding: 0 14px;
}

/* Mobile: one size, a 38px track, small included */
@media (max-aspect-ratio: 4/3) {
  .button-group__track {
    padding: 3px;
  }

  .button-group--medium .button-group__track,
  .button-group--small .button-group__track {
    border-radius: var(--radius-04);
  }

  .button-group--medium .button-group__option,
  .button-group--medium .button-group__thumb,
  .button-group--small .button-group__option,
  .button-group--small .button-group__thumb {
    border-radius: var(--radius-03);
  }

  .button-group--medium .button-group__option {
    height: 32px;
    padding: 0 var(--space-03);
  }

  .button-group--small .button-group__option {
    height: 32px;
    padding: 0 var(--space-03);
  }

  .button-group--mobile-wrap .button-group__track {
    flex-wrap: wrap;
  }

  /* Row - the options stay on one line, sharing the width. */
  .button-group--mobile-row .button-group__option {
    min-width: 0;
  }

  .button-group--mobile-column .button-group__track {
    flex-direction: column;
  }

  .button-group--mobile-column-reverse .button-group__track {
    flex-direction: column-reverse;
  }

  /* In a column, flex: 1's zero basis would override the option's height and
     collapse it to its text. */
  .button-group--mobile-column .button-group__option,
  .button-group--mobile-column-reverse .button-group__option {
    flex: none;
  }

  .button-group--mobile-grid-3 .button-group__track {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
  }

  /* Scroll - one row of options scrolling sideways, the scroller bleeding to
     the screen edges (assumes the app's standard --space-05 mobile content
     padding) while the track keeps its rounded ends inside it. */
  .button-group--mobile-scroll {
    overflow-x: auto;
    margin-left: calc(-1 * var(--space-05));
    margin-right: calc(-1 * var(--space-05));
    padding-left: var(--space-05);
    padding-right: var(--space-05);
    scrollbar-width: none;
    -ms-overflow-style: none;
  }

  .button-group--mobile-scroll::-webkit-scrollbar {
    display: none;
  }

  .button-group--mobile-scroll .button-group__track {
    flex: 0 0 auto;
    min-width: 100%;
  }

  .button-group--mobile-scroll .button-group__option {
    flex-shrink: 0;
  }
}
</style>
