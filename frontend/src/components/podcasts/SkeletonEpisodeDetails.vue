<template>
  <div class="skeleton-episode-details">
    <!-- Header matching EpisodeDetails -->
    <div class="skeleton-details-header">
      <div class="skeleton-episode-image"></div>
      <div class="skeleton-header-info">
        <div class="skeleton-text-line skeleton-title"></div>
        <div class="skeleton-text-line skeleton-podcast-link"></div>
        <div class="skeleton-text-line skeleton-meta"></div>
        <div class="skeleton-button"></div>
      </div>
    </div>

    <!-- Description section -->
    <div class="skeleton-description">
      <div class="skeleton-text-line skeleton-section-title"></div>
      <div class="skeleton-text-line skeleton-description-line-1"></div>
      <div class="skeleton-text-line skeleton-description-line-2"></div>
      <div class="skeleton-text-line skeleton-description-line-3"></div>
      <div class="skeleton-text-line skeleton-description-line-4"></div>
    </div>
  </div>
</template>

<style scoped>
/* Container matching EpisodeDetails */
.skeleton-episode-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* Header matching EpisodeDetails */
.skeleton-details-header {
  display: flex;
  gap: var(--space-04);
}

/* Image matching EpisodeDetails (240px × 240px) */
.skeleton-episode-image {
  width: 240px;
  height: 240px;
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
  width: 350px;
  height: 32px;
}

.skeleton-podcast-link {
  width: 250px;
  height: 20px;
}

.skeleton-meta {
  width: 180px;
}

.skeleton-section-title {
  width: 150px;
  height: 24px;
}

/* Button skeleton */
.skeleton-button {
  width: 140px;
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

/* Description section */
.skeleton-description {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.skeleton-description-line-1 {
  width: 100%;
}

.skeleton-description-line-2 {
  width: 95%;
}

.skeleton-description-line-3 {
  width: 88%;
}

.skeleton-description-line-4 {
  width: 70%;
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
