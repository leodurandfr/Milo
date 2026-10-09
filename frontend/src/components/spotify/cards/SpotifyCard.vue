<template>
  <div v-press class="playlist-card" :class="{ 'byline-only': !lines.heading && lines.byline }"
    @click="$emit('click')">
    <LazyImage :src="item.image || ''" :fallback="musicPlaceholder" :alt="title" lazy skeleton
      class="playlist-cover" :class="{ round: item.kind === 'artist' }">
      <transition name="loading-fade">
        <div v-if="opening" class="card-loading-overlay">
          <LoadingSpinner :size="48" />
        </div>
      </transition>
    </LazyImage>
    <div v-if="lines.heading || lines.byline" class="playlist-info">
      <p v-if="lines.heading" class="playlist-name heading-4">{{ lines.heading }}</p>
      <p v-if="lines.byline" class="playlist-owner text-body">{{ lines.byline }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import LazyImage from '@/components/ui/LazyImage.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import { useCardOpening } from '@/composables/useSpotifyOpening';
import { musicPlaceholder } from '@/constants/placeholders';
import { cardLines } from '@/utils/spotifyCard';

const props = defineProps({
  // A card as /api/spotify/home lists it: one of Spotify's home ({ uri, kind,
  // name, subtitle, image }) or a library playlist ({ uri, name, owner, image }).
  // No track count: the library's is wrong for every playlist Spotify
  // regenerates. A home card whose cover carries its name (`name_in_cover`)
  // writes its byline alone.
  item: {
    type: Object,
    required: true,
  },
});

defineEmits(['click']);

const { t } = useI18n();

const title = computed(() => props.item.name || t('spotify.untitledPlaylist'));
const lines = computed(() => cardLines(props.item, t));
const opening = useCardOpening(() => props.item.uri);
</script>

<style scoped>
/* The card's box — a square cover, the gap, a heading-4 name, then a
   text-body byline — is what SpotifyShelfRow reserves for a row not
   drawn yet: change one, change both (tests/architecture/spotifyShelfEstimate). */
.playlist-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  cursor: pointer;
  min-width: 0;
}

.playlist-card.byline-only {
  gap: var(--space-02);
}

.playlist-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-02);
  background: var(--color-surface-glass);
}

.playlist-cover.round {
  border-radius: var(--radius-full);
}




.playlist-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.playlist-name,
.playlist-owner {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.playlist-name {
  color: var(--color-text);
}

.playlist-owner {
  color: var(--color-text-secondary);
}
</style>
