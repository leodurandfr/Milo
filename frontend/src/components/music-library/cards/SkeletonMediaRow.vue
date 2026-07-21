<template>
  <div class="skeleton-media-row">
    <div class="skeleton-media-cover shimmer" :class="{ rounded: roundedCover }"></div>
    <div class="skeleton-media-details">
      <div class="skeleton-text-line shimmer skeleton-title"></div>
      <div class="skeleton-text-line shimmer skeleton-subtitle"></div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  // Circular cover, matching MediaRow's artist rows.
  roundedCover: {
    type: Boolean,
    default: false,
  },
});
</script>

<style scoped>
.skeleton-media-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02);
}

.skeleton-media-cover {
  flex-shrink: 0;
  width: 60px;
  height: 60px;
  border-radius: var(--radius-02);
}

.skeleton-media-cover.rounded {
  border-radius: 50%;
}

.skeleton-media-details {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.skeleton-title {
  width: 60%;
}

.skeleton-subtitle {
  width: 35%;
}
</style>
