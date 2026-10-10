<!-- The card a settings screen or a modal panel is built from: a title (or a header slot), an optional description, content -->
<template>
  <section class="section-card">
    <slot name="header">
      <h2 v-if="title" class="heading-2">{{ title }}</h2>
    </slot>
    <p v-if="description" class="section-card__description text-body-medium">{{ description }}</p>
    <slot />
  </section>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    default: ''
  },
  description: {
    type: String,
    default: ''
  }
});
</script>

<style scoped>
.section-card {
  background: var(--color-surface);
  border-radius: var(--radius-06);
  padding: var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.section-card__description {
  color: var(--color-text-secondary);
  margin: 0;
}

@media (max-aspect-ratio: 4/3) {
  .section-card {
    border-radius: var(--radius-05);
  }
}
</style>
