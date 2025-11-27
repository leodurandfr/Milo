<template>
  <!-- Card variant (default) -->
  <div v-if="variant === 'card'" class="skeleton-podcast-card variant-card">
    <div class="skeleton-card-image"></div>
    <div class="skeleton-text-line skeleton-tag"></div>
    <div class="skeleton-text-line skeleton-title"></div>
    <div class="skeleton-text-line skeleton-publisher"></div>
  </div>

  <!-- Row variant -->
  <div v-else class="skeleton-podcast-card variant-row">
    <div class="skeleton-row-image"></div>
    <div class="skeleton-row-content">
      <div class="skeleton-row-info">
        <div class="skeleton-text-line skeleton-name"></div>
        <div class="skeleton-text-line skeleton-publisher"></div>
        <div class="skeleton-text-line skeleton-meta"></div>
      </div>
      <div class="skeleton-button"></div>
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

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.skeleton-text-line {
  height: 16px;
  border-radius: var(--radius-02);
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
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
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
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
  padding: var(--space-03);
  gap: var(--space-03);
}

.variant-row .skeleton-row-image {
  width: 128px;
  height: 128px;
  flex-shrink: 0;
  border-radius: var(--radius-02);
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
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
  gap: var(--space-02);
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

.variant-row .skeleton-button {
  width: 100px;
  height: 40px;
  border-radius: var(--radius-03);
  flex-shrink: 0;
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
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
