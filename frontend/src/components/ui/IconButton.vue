<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <component :is="clickable ? 'button' : 'div'"
    :class="['icon-button', `icon-button--${variant}`, { 'icon-button--clickable': clickable }]" @click="handleClick">
    <!-- Icon on the left -->
    <div class="icon-button__icon">
      <slot name="icon"></slot>
    </div>

    <!-- Title -->
    <span class="icon-button__title text-body">{{ title }}</span>

    <!-- Right-side action (toggle, caret, or nothing) -->
    <div v-if="showCaret || $slots.action" class="icon-button__action">
      <slot name="action">
        <Icon v-if="showCaret" name="caretRight" :size="24" color="var(--color-text-light)" />
      </slot>
    </div>
  </component>
</template>

<script setup>
import Icon from '@/components/ui/Icon.vue';

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  showCaret: {
    type: Boolean,
    default: false
  },
  clickable: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'outlined'].includes(value)
  }
});

const emit = defineEmits(['click']);

function handleClick(event) {
  // If it's a toggle, don't emit click (the toggle handles its own event)
  if (props.clickable) {
    emit('click', event);
  }
}
</script>

<style scoped>
.icon-button {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  border-radius: var(--radius-05);
  transition: all var(--transition-fast);
  width: 100%;
  text-align: left;
}

/* Default variant (SettingsModal) */
.icon-button--default {
  background: var(--color-background-neutral);
}

/* .icon-button--default.icon-button--clickable:hover {
  background: var(--color-background-strong);
} */

/* Outlined variant (LanguageSettings) */
.icon-button--outlined {
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);

}

/* .icon-button--outlined.icon-button--clickable:hover {
  background: var(--color-background-strong);
} */

/* Active state (LanguageSettings) */
.icon-button--outlined.active {
  background: var(--color-background-neutral);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

/* Clickable state (with caret-right) */
.icon-button--clickable {
  cursor: pointer;
}

/* Left icon */
.icon-button__icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.icon-button__icon :deep(img),
.icon-button__icon :deep(svg) {
  width: 40px;
  height: 40px;
}

/* Title */
.icon-button__title {
  flex: 1;
  color: var(--color-text);
}

/* Title in text-secondary for outlined non-active */
.icon-button--outlined .icon-button__title {
  color: var(--color-text-secondary);
}

/* Title in normal text for outlined active */
.icon-button--outlined.active .icon-button__title {
  color: var(--color-text);
}

/* Right action */
.icon-button__action {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Responsive - Mobile */
@media (max-aspect-ratio: 4/3) {
  .icon-button__icon {
    width: 32px;
    height: 32px;
  }

  .icon-button__icon :deep(img),
  .icon-button__icon :deep(svg) {
    width: 32px;
    height: 32px;
  }
}
</style>