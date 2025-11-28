<!-- frontend/src/components/ui/ListItemButton.vue -->
<template>
  <button type="button" :class="['list-item-button', `list-item-button--${variant}`]" @click="$emit('click', $event)">
    <!-- Icon on the left -->
    <div v-if="$slots.icon" class="list-item-button__icon">
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
  </button>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';

defineProps({
  title: {
    type: String,
    required: true
  },
  showCaret: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'outlined'].includes(value)
  }
});

defineEmits(['click']);
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
  cursor: pointer;
}

/* Default variant (SettingsModal) */
.list-item-button--default {
  background: var(--color-background-neutral);
}

/* Outlined variant (LanguageSettings, ApplicationsSettings) */
.list-item-button--outlined {
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);
}

/* Active state */
.list-item-button--outlined.active {
  background: var(--color-background-neutral);
  box-shadow: inset 0 0 0 2px var(--color-brand);
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