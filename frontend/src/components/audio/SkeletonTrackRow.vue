<!--
  TrackRow's placeholder: the row's box and its lines (each bar in a line of
  the text class it stands for), so a tracklist holds its height when the rows
  arrive.
-->
<template>
  <div class="skeleton-track-row" aria-hidden="true">
    <div class="skeleton-track-row__index">
      <span class="skeleton-track-row__position text-mono-large">
        <span class="skeleton-text-line shimmer skeleton-track-row__number"></span>&#8203;
      </span>
      <div v-if="cover" class="skeleton-track-row__cover shimmer"></div>
    </div>

    <div class="skeleton-track-row__main">
      <span class="skeleton-track-row__line text-body-medium">
        <span class="skeleton-text-line shimmer skeleton-track-row__title"></span>&#8203;
      </span>
      <span v-if="artist" class="skeleton-track-row__line text-body-small">
        <span class="skeleton-text-line shimmer skeleton-track-row__artist"></span>&#8203;
      </span>
    </div>

    <span class="skeleton-track-row__line skeleton-track-row__end text-mono-medium">
      <span class="skeleton-text-line shimmer skeleton-track-row__duration"></span>&#8203;
    </span>
  </div>
</template>

<script setup>
defineProps({
  // TrackRow's showCover: the 40px thumbnail after the number.
  cover: {
    type: Boolean,
    default: false,
  },
  // TrackRow's showArtist: the second line.
  artist: {
    type: Boolean,
    default: false,
  },
});
</script>

<style scoped>
/* TrackRow's box: change one, change both. */
.skeleton-track-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-04) 0;
  border-bottom: 1px solid var(--color-border);
  min-width: 0;
}

.skeleton-track-row__index {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.skeleton-track-row__position {
  width: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.skeleton-track-row__cover {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-02);
}

.skeleton-track-row__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.skeleton-track-row__line {
  display: flex;
  align-items: center;
  width: 100%;
}

.skeleton-track-row__end {
  flex-shrink: 0;
  width: auto;
}

.skeleton-track-row__number {
  width: 16px;
}

.skeleton-track-row__title {
  width: 45%;
}

.skeleton-track-row__artist {
  width: 25%;
  height: 12px;
}

.skeleton-track-row__duration {
  width: 36px;
  height: 12px;
}
</style>
