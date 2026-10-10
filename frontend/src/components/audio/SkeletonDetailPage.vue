<!--
  A tracklist page waiting on its first answer — an album, a playlist — drawn
  in its own geometry: the header, then its rows on TrackList's card. An artist page lays its own
  out of SkeletonDetailHeader, since what follows its header is not a list.
-->
<template>
  <div class="skeleton-detail-page swap-skeleton" aria-hidden="true">
    <SkeletonDetailHeader :subtitle="subtitle" :shuffle="shuffle" />
    <TrackList>
      <SkeletonTrackRow v-for="i in rows" :key="i" :cover="cover" :artist="artist" />
    </TrackList>
  </div>
</template>

<script setup>
import SkeletonDetailHeader from './SkeletonDetailHeader.vue';
import SkeletonTrackRow from './SkeletonTrackRow.vue';
import TrackList from './TrackList.vue';

defineProps({
  // Enough to fill a screen; a shorter list then shrinks under its header.
  rows: {
    type: Number,
    default: 8,
  },
  // As the rows it stands in for: SkeletonTrackRow's `cover` and `artist`.
  cover: {
    type: Boolean,
    default: false,
  },
  artist: {
    type: Boolean,
    default: false,
  },
  // As the header it stands in for: SkeletonDetailHeader's `subtitle` and
  // `shuffle`.
  subtitle: {
    type: Boolean,
    default: false,
  },
  shuffle: {
    type: Boolean,
    default: false,
  },
});
</script>

<style scoped>
/* The pages' content-stack gap. */
.skeleton-detail-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}
</style>
