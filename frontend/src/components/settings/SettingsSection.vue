<!-- Shared settings card section - replaces duplicated .settings-section + .xxx-group pattern -->
<template>
  <section class="settings-section">
    <slot name="header">
      <h2 v-if="title" class="heading-2">{{ title }}</h2>
    </slot>
    <slot />
  </section>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    default: ''
  }
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
