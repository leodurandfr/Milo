<template>
  <div class="player-info-text">
    <p class="player-info-title heading-2">{{ title }}</p>
    <p v-if="secondary" class="player-info-secondary text-body">{{ secondary }}</p>
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
</style>
