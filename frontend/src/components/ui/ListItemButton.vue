<!-- frontend/src/components/ui/ListItemButton.vue -->
<template>
  <component :is="clickable ? 'button' : 'div'"
    :class="['list-item-button', `list-item-button--${variant}`, { 'list-item-button--clickable': clickable }]" @click="handleClick">
    <!-- Icon on the left -->
    <div class="list-item-button__icon">
      <slot name="icon"></slot>
    </div>

    <!-- Title -->
    <span class="list-item-button__title heading-3">{{ title }}</span>

    <!-- Right-side action (toggle, caret, or nothing) -->
    <div v-if="showCaret || $slots.action" class="list-item-button__action">
      <slot name="action">
        <SvgIcon v-if="showCaret" name="caretRight" :size="24" color="var(--color-text-light)" />
      </slot>
    </div>
  </component>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';

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
.list-item-button {
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
.list-item-button--default {
  background: var(--color-background-neutral);
}



/* Outlined variant (LanguageSettings) */
.list-item-button--outlined {
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);

}



/* Active state (LanguageSettings) */
.list-item-button--outlined.active {
  background: var(--color-background-neutral);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

/* Clickable state (with caret-right) */
.list-item-button--clickable {
  cursor: pointer;
}

/* Left icon */
.list-item-button__icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.list-item-button__icon :deep(img),
.list-item-button__icon :deep(svg) {
  width: 40px;
  height: 40px;
}

/* Title */
.list-item-button__title {
  flex: 1;
  color: var(--color-text);
}

/* Title in text-secondary for outlined non-active */
.list-item-button--outlined .list-item-button__title {
  color: var(--color-text-secondary);
}

/* Title in normal text for outlined active */
.list-item-button--outlined.active .list-item-button__title {
  color: var(--color-text);
}

/* Right action */
.list-item-button__action {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Responsive - Mobile */
@media (max-aspect-ratio: 4/3) {
  .list-item-button__icon {
    width: 32px;
    height: 32px;
  }

  .list-item-button__icon :deep(img),
  .list-item-button__icon :deep(svg) {
    width: 32px;
    height: 32px;
  }
}
</style>