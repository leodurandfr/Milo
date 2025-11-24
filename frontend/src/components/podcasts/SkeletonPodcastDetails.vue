<template>
  <div class="skeleton-podcast-details">
    <!-- Header matching PodcastDetails -->
    <div class="skeleton-details-header">
      <div class="skeleton-podcast-image"></div>
      <div class="skeleton-header-info">
        <div class="skeleton-text-line skeleton-title"></div>
        <div class="skeleton-text-line skeleton-publisher"></div>
        <div class="skeleton-text-line skeleton-meta"></div>
        <div class="skeleton-button"></div>
      </div>
    </div>

    <!-- Episodes section -->
    <div class="skeleton-episodes-section">
      <div class="skeleton-text-line skeleton-section-title"></div>
      <div class="skeleton-episodes-list">
        <SkeletonEpisodeCard v-for="i in 5" :key="`skeleton-episode-${i}`" />
      </div>
    </div>
  </div>
</template>

<script setup>
import SkeletonEpisodeCard from './SkeletonEpisodeCard.vue'
</script>

<style scoped>
/* Container matching PodcastDetails */
.skeleton-podcast-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* Header matching PodcastDetails */
.skeleton-details-header {
  display: flex;
  gap: var(--space-04);
}

/* Image matching PodcastDetails (150px × 150px) */
.skeleton-podcast-image {
  width: 150px;
  height: 150px;
  border-radius: var(--radius-04);
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

/* Header info */
.skeleton-header-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Text lines */
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

.skeleton-title {
  width: 300px;
  height: 32px;
}

.skeleton-publisher {
  width: 200px;
  height: 20px;
}

.skeleton-meta {
  width: 150px;
}

.skeleton-section-title {
  width: 200px;
  height: 24px;
  margin-bottom: var(--space-03);
}

/* Button skeleton */
.skeleton-button {
  width: 120px;
  height: 40px;
  border-radius: var(--radius-03);

  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

/* Episodes section */
.skeleton-episodes-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.skeleton-episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Shimmer animation */
@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
