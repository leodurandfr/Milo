<template>
  <!-- Card variant (default) -->
  <div v-if="variant === 'card'" class="skeleton-podcast-card variant-card">
    <div class="skeleton-card-image shimmer"></div>
    <div class="skeleton-text-line shimmer skeleton-tag"></div>
    <div class="skeleton-text-line shimmer skeleton-title"></div>
    <div class="skeleton-text-line shimmer skeleton-publisher"></div>
  </div>

  <!-- Row variant -->
  <div v-else class="skeleton-podcast-card variant-row">
    <div class="skeleton-row-image shimmer"></div>
    <div class="skeleton-row-content">
      <div class="skeleton-row-info">
        <div class="skeleton-text-line shimmer skeleton-name"></div>
        <div class="skeleton-text-line shimmer skeleton-publisher"></div>
        <div class="skeleton-text-line shimmer skeleton-meta"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  variant: {
    type: String,
    default: 'card',
    validator: (value) => ['card', 'row'].includes(value)
  }
})
</script>

<style scoped>
/* === SHARED === */
.skeleton-podcast-card {
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
}

.skeleton-text-line {
  height: 16px;
  border-radius: var(--radius-02);
}

/* === CARD VARIANT === */
.variant-card {
  display: flex;
  flex-direction: column;
  padding: var(--space-03);
  gap: var(--space-03);
}

.variant-card .skeleton-card-image {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-02);
  flex-shrink: 0;
}

.variant-card .skeleton-tag {
  width: 40%;
}

.variant-card .skeleton-title {
  width: 90%;
}

.variant-card .skeleton-publisher {
  width: 60%;
}

/* === ROW VARIANT === */
.variant-row {
  display: flex;
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
  gap: var(--space-03);
}

.variant-row .skeleton-row-image {
  width: 128px;
  height: 128px;
  flex-shrink: 0;
  border-radius: var(--radius-02);
}

.variant-row .skeleton-row-content {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: var(--space-03);
  align-items: center;
}

.variant-row .skeleton-row-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.variant-row .skeleton-name {
  width: 80%;
  height: 20px;
}

.variant-row .skeleton-publisher {
  width: 60%;
}

.variant-row .skeleton-meta {
  width: 40%;
}

/* === ROW VARIANT MOBILE === */
@media (max-aspect-ratio: 4/3) {
  .variant-row .skeleton-row-image {
    width: 64px;
    height: 64px;
  }

  .variant-row .skeleton-meta {
    display: none;
  }
}
</style>
