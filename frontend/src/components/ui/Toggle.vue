<!-- Toggle component with size variants -->
<template>
  <div class="toggle-container" :class="{ 'toggle-container--disabled': disabled }">
    <h2 v-if="title" class="heading-2">{{ title }}</h2>

    <!-- No press scale: the knob widening is the press feedback. -->
    <label :class="['toggle', `toggle--${variant}`, `toggle--${size}`, { 'toggle--pressed': pressed || isHeld }]"
      @pointerdown="startPress">
      <input type="checkbox" :checked="modelValue" @change="handleToggle" :disabled="disabled">
      <span class="slider"></span>
    </label>
  </div>
</template>

<script setup>
import { usePointerHold } from '@/composables/usePointerHold';

const props = defineProps({
  modelValue: {
    type: Boolean,
    required: true
  },
  title: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'primary',
    validator: (value) => ['primary', 'secondary'].includes(value)
  },
  size: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'compact'].includes(value)
  },
  // Held by whatever carries it and takes the pointer in its place — a
  // ListItemButton row — so the knob widens while the row is held.
  pressed: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

// Held while the pointer that pressed it is down.
const { held: isHeld, press } = usePointerHold();

function startPress(event) {
  if (!props.disabled) press(event);
}

function handleToggle(event) {
  const newValue = event.target.checked;
  emit('update:modelValue', newValue);
  emit('change', newValue);
}
</script>

<style scoped>
.toggle-container {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.toggle-container h2 {
  margin: 0;
  color: var(--color-text);
  transition: color var(--transition-fast);
}

.toggle-container--disabled h2 {
  color: var(--color-text-tertiary);
}

.toggle {
  position: relative;
  display: inline-block;
}

/* The knob is a pill that stretches toward the far side while pressed. Its
   width and its position travel on one curve, so the edge it is anchored to
   (left when off, right when on) stays put through the whole press. */

/* iOS's proportions: the track 2.2 times as wide as it is tall, the knob 1.6
   times as wide as it is tall and 3px inside, its travel 0.6 of its width. */

/* Default - Desktop: 68x34, a 42x28 knob */
.toggle--default {
  width: 68px;
  height: 34px;
  --knob-width: 42px;
  --knob-height: 28px;
  --knob-stretch: 4px;
}

/* Compact - Desktop: 66x30, a 38x24 knob */
.toggle--compact {
  width: 66px;
  height: 30px;
  --knob-width: 38px;
  --knob-height: 24px;
  --knob-stretch: 4px;
}

.toggle input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  border-radius: var(--radius-full);
  transition: background-color var(--transition-fast);
}

.slider:before {
  position: absolute;
  content: "";
  left: 3px;
  top: 3px;
  width: var(--knob-width);
  height: var(--knob-height);
  background-color: var(--color-thumb);
  box-shadow: var(--shadow-knob);
  border-radius: var(--radius-full);
  transition:
    transform var(--transition-spring-light),
    width var(--transition-spring-light);
}

.toggle--pressed:not(:has(input:disabled)) .slider:before {
  width: calc(var(--knob-width) + var(--knob-stretch));
}

.toggle input:checked + .slider:before {
  transform: translateX(calc(var(--toggle-width) - var(--knob-width) - 6px));
}

.toggle--pressed:not(:has(input:disabled)) input:checked + .slider:before {
  transform: translateX(calc(var(--toggle-width) - var(--knob-width) - var(--knob-stretch) - 6px));
}

.toggle--default { --toggle-width: 68px; }
.toggle--compact { --toggle-width: 66px; }

/* Colors */
.toggle--primary .slider,
.toggle--secondary .slider {
  background-color: var(--color-fill-soft);
}

.toggle--primary input:checked + .slider {
  background-color: var(--color-brand);
}

.toggle--secondary input:checked + .slider {
  background-color: var(--color-fill);
}

/* Disabled */
.toggle:has(input:disabled) {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

input:disabled + .slider {
  cursor: not-allowed;
}

/* Responsive: one size on a phone, the compact one */
@media (max-aspect-ratio: 4/3) {
  .toggle--default {
    width: 66px;
    height: 30px;
    --toggle-width: 66px;
    --knob-width: 38px;
    --knob-height: 24px;
    --knob-stretch: 4px;
  }
}
</style>