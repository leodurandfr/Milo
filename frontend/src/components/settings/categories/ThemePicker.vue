<!-- frontend/src/components/settings/categories/ThemePicker.vue -->
<template>
  <div class="theme-picker" role="radiogroup">
    <button
      v-for="option in options"
      :key="option.value"
      v-press
      type="button"
      role="radio"
      class="theme-option"
      :class="{ active: option.value === modelValue }"
      :aria-checked="option.value === modelValue"
      @click="select(option.value)"
    >
      <div class="thumb-frame">
        <div class="thumb">
          <!-- Auto is both pictures, the dark one clipped on the diagonal -->
          <div
            v-for="look in looks(option.value)"
            :key="look"
            class="mock"
            :class="[`mock--${look}`, { 'mock--split': option.value === 'auto' && look === 'dark' }]"
            aria-hidden="true"
          >
            <!-- A corner of the screen: its ground, a settings card and the
                 dock's glass, all repainted by the theme -->
            <div class="mock-panel">
              <div class="mock-bar mock-bar--title"></div>
              <div class="mock-row">
                <div class="mock-bar mock-bar--label"></div>
                <div class="mock-toggle"><div class="mock-knob"></div></div>
              </div>
              <div class="mock-row">
                <div class="mock-bar mock-bar--secondary"></div>
              </div>
            </div>
            <div class="mock-dock">
              <img v-for="icon in dockIcons" :key="icon" :src="icon" alt="" class="mock-app" draggable="false" />
            </div>
          </div>
        </div>
      </div>
      <span class="theme-label" :class="option.value === modelValue ? 'heading-4' : 'text-body-medium'">
        {{ option.label }}
      </span>
    </button>
  </div>
</template>

<script setup>
import radioIcon from '@/assets/app-icons/radio.svg';
import musicLibraryIcon from '@/assets/app-icons/music-library.svg';
import podcastIcon from '@/assets/app-icons/podcast.svg';

defineProps({
  modelValue: { type: String, default: null },
  // [{ value: 'light' | 'dark' | 'auto', label }]
  options: { type: Array, required: true }
});

const emit = defineEmits(['change']);

const dockIcons = [radioIcon, musicLibraryIcon, podcastIcon];

function looks(value) {
  return value === 'auto' ? ['light', 'dark'] : [value];
}

function select(value) {
  emit('change', value);
}
</script>

<style scoped>
.theme-picker {
  display: flex;
  justify-content: center;
  gap: var(--space-05);
}

.theme-option {
  width: 136px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}

.thumb-frame {
  width: 100%;
  padding: 3px;
  border-radius: calc(var(--radius-02) + 3px);
  box-shadow: 0 0 0 2px transparent;
  transition: box-shadow var(--transition-fast);
}

.theme-option.active .thumb-frame {
  box-shadow: 0 0 0 2px var(--color-brand);
}

/* Everything inside is sized in cqw, so the picture is the same at every
   tile width. */
.thumb {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: var(--radius-02);
  overflow: hidden;
  container-type: inline-size;
}

/* Over the pictures, so the light one keeps an edge on a white section. */
.thumb::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  box-shadow: inset 0 0 0 1px var(--color-border);
  pointer-events: none;
}

.mock {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: var(--color-ground);
}

.mock--light {
  color-scheme: light;
}

.mock--dark {
  color-scheme: dark;
}

.mock--split {
  clip-path: polygon(62% 0, 100% 0, 100% 100%, 38% 100%);
}

/* A section card running off the right and bottom edges. */
.mock-panel {
  position: absolute;
  top: 9cqw;
  left: 44cqw;
  width: 66cqw;
  height: 62cqw;
  padding: 6cqw;
  border-radius: 5cqw;
  background: var(--color-surface);
}

.mock-bar {
  height: 3cqw;
  border-radius: var(--radius-full);
  background: var(--color-text);
}

.mock-bar--title {
  width: 24cqw;
  height: 3.8cqw;
}

.mock-bar--label {
  width: 17cqw;
}

.mock-bar--secondary {
  width: 15cqw;
  background: var(--color-text-tertiary);
}

.mock-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 44cqw;
  height: 14cqw;
  margin-top: 3cqw;
  padding: 0 3.5cqw;
  border-radius: 3cqw;
  background: var(--color-inset);
}

.mock-bar--title + .mock-row {
  margin-top: 5cqw;
}

.mock-toggle {
  position: relative;
  width: 14cqw;
  height: 8.4cqw;
  border-radius: var(--radius-full);
  background: var(--color-brand);
}

.mock-knob {
  position: absolute;
  top: 0.9cqw;
  right: 0.9cqw;
  width: 6.6cqw;
  height: 6.6cqw;
  border-radius: var(--radius-full);
  background: var(--color-thumb);
}

/* The dock's glass running off the left edge. */
.mock-dock {
  position: absolute;
  left: -15cqw;
  bottom: 5cqw;
  display: flex;
  gap: 3cqw;
  padding: 3cqw;
  border-radius: var(--radius-full);
  background: var(--color-shell);
  backdrop-filter: blur(3cqw);
  -webkit-backdrop-filter: blur(3cqw);
}

.mock-app {
  width: 15cqw;
  height: 15cqw;
  border-radius: 3.5cqw;
}

/* heading-4 sets a shorter line than text-body-medium: the taller line holds both,
   so the label does not jump when it is selected. */
.theme-label {
  display: flex;
  align-items: center;
  min-height: var(--line-height-body-medium);
  color: var(--color-text-secondary);
  transition: color var(--transition-fast);
}

.theme-option.active .theme-label {
  color: var(--color-text);
}

@media (max-aspect-ratio: 4/3) {
  .theme-picker {
    gap: var(--space-04);
  }

  .theme-option {
    width: 96px;
  }
}
</style>
