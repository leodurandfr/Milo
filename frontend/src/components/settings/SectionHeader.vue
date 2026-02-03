<!-- Shared section header - title with optional subtitle and action slot -->
<template>
  <div class="section-header">
    <div class="section-header__title">
      <h2 class="heading-2">{{ title }}</h2>
      <span v-if="subtitle" class="section-header__subtitle text-mono">{{ subtitle }}</span>
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
    required: true
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
}

.section-header__subtitle {
  color: var(--color-text-secondary);
}

@media (max-aspect-ratio: 4/3) {
  .section-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
