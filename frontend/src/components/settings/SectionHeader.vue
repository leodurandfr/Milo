<!-- Shared section header - title with optional subtitle and action slot.
     Every settings section with a control beside its title goes through here;
     tests/architecture/sectionHeaderActions.test.js holds the actions to one style. -->
<template>
  <div class="section-header">
    <div class="section-header__title">
      <slot name="title">
        <h2 class="heading-2">{{ title }}</h2>
      </slot>
      <span v-if="subtitle" class="section-header__subtitle text-mono-medium">{{ subtitle }}</span>
    </div>
    <div v-if="$slots.actions" class="section-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    default: ''
  },
  subtitle: {
    type: String,
    default: ''
  }
});
</script>

<style scoped>
.section-header {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.section-header__title {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.section-header__subtitle {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  flex-shrink: 0;
}

.section-header__actions:empty {
  display: none;
}

@media (max-aspect-ratio: 4/3) {
  .section-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
