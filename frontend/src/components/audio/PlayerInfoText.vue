<template>
  <div class="player-info-text" :class="`player-info-text--${variant}`">
    <p class="player-info-title" :class="variant === 'line' ? 'text-body' : 'heading-2'">{{ title }}</p>
    <!-- The slot draws the same line in parts (the artist names as links). -->
    <p v-if="secondary" class="player-info-secondary text-body"><slot name="secondary">{{ secondary }}</slot></p>
  </div>
</template>

<script setup>
defineProps({
  /**
   * Main line (track title, episode name, station name).
   */
  title: {
    type: String,
    required: true
  },
  /**
   * Secondary line below the title (artist).
   */
  secondary: {
    type: String,
    default: null
  },
  /**
   * `card`: the kiosk card's lines, the title on up to three. `line`: the
   * phone's mini-bar, one line each and no gap — what the body draws there and
   * each cell of the swipe carousel, so the two cannot differ.
   */
  variant: {
    type: String,
    default: 'card',
    validator: (value) => ['card', 'line'].includes(value)
  }
})
</script>

<style scoped>
.player-info-text {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.player-info-title {
  color: var(--color-text-on-contrast);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.player-info-secondary {
  color: var(--color-text-on-contrast-secondary);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
}

/* One line each, cut where the caller's edge fade cuts it rather than by an
   ellipsis. */
.player-info-text--line {
  gap: 0;
}

.player-info-text--line .player-info-title,
.player-info-text--line .player-info-secondary {
  display: block;
  white-space: nowrap;
  text-overflow: clip;
}
</style>
